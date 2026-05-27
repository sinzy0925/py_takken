"""
Gemini 3.1 Flash TTS: prompt.txt を読み、音声を WAV として保存またはブラウザでダウンロード。
https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-tts-preview
"""

from __future__ import annotations

import argparse
import html
import io
import logging
import os
import re
import shutil
import sys
import time
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from google import genai
from google.genai import types
from google.genai.errors import APIError

from api_key_manager import api_key_manager

BASE_DIR = Path(__file__).resolve().parent
PROMPT_FILE = BASE_DIR / "prompt.txt"
MODEL_ID = "gemini-3.1-flash-tts-preview"
DEFAULT_VOICE = "Kore"
# Gemini API 失敗時のリトライ上限（毎回 API キーを切り替える）
_TTS_MAX_ATTEMPTS = 3
_PAGE_SEPARATOR_RE = re.compile(r"^\s*----------\s*$", re.MULTILINE)
_FIRESHOT_NUM_RE = re.compile(r"FireShot\s+Capture\s+(\d+)", re.I)


def _retry_delay_sec() -> float:
    """音声取り出し失敗・429 時の待ち秒数。環境変数 TTS_RETRY_DELAY_SEC（既定 61）。"""
    return float(os.getenv("TTS_RETRY_DELAY_SEC", "61"))

LOG = logging.getLogger(__name__)


def setup_logging(verbose: int, *, quiet: bool) -> None:
    """CLI / uvicorn 起動前に一度だけ呼ぶ。"""
    if quiet:
        level = logging.WARNING
    elif verbose >= 1:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def read_prompt_text() -> str:
    if not PROMPT_FILE.is_file():
        raise FileNotFoundError(f"見つかりません: {PROMPT_FILE}")
    text = PROMPT_FILE.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"{PROMPT_FILE} が空です。")
    return text.strip()


def _mime_sample_rate(mime_type: str | None) -> int:
    if not mime_type:
        return 24_000
    m = re.search(r"rate=(\d+)", mime_type, re.I)
    if m:
        return int(m.group(1))
    return 24_000


def _collect_pcm_from_response(
    response: types.GenerateContentResponse,
) -> tuple[bytes, int]:
    if not response.candidates:
        raise RuntimeError("応答に candidates がありません。")
    cand = response.candidates[0]
    if not cand.content or not cand.content.parts:
        raise RuntimeError("応答に音声パートがありません。")

    chunks: list[bytes] = []
    sample_rate: int | None = None
    for part in cand.content.parts:
        if not part.inline_data or not part.inline_data.data:
            continue
        chunks.append(part.inline_data.data)
        sr = _mime_sample_rate(part.inline_data.mime_type)
        sample_rate = sample_rate or sr

    if not chunks:
        raise RuntimeError("インライン音声データが返されませんでした。")
    return b"".join(chunks), sample_rate or 24_000


def _log_response_summary(resp: types.GenerateContentResponse, elapsed_s: float) -> None:
    LOG.info("API 完了 (%.2f 秒)", elapsed_s)
    if not resp.candidates:
        LOG.warning("応答に candidates がありません")
        return
    c0 = resp.candidates[0]
    fr = getattr(c0, "finish_reason", None)
    LOG.info("finish_reason=%s", fr)
    parts = c0.content.parts if c0.content and c0.content.parts else []
    LOG.info("parts 数=%d", len(parts))
    for i, p in enumerate(parts):
        has_txt = bool(p.text)
        inl = p.inline_data
        nbytes = len(inl.data) if inl and inl.data else 0
        mime = inl.mime_type if inl else None
        LOG.debug(
            "  part[%d] text=%s inline_bytes=%d mime=%s",
            i,
            has_txt,
            nbytes,
            mime,
        )


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _speech_config() -> types.GenerateContentConfig:
    voice = os.getenv("VOICE_NAME", DEFAULT_VOICE).strip() or DEFAULT_VOICE
    return types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )


def _get_api_key_or_raise() -> str:
    key = api_key_manager.get_next_key_sync()
    if not key:
        raise RuntimeError(
            "利用可能な API キーがありません。"
            "tts/.env に GOOGLE_API_KEY_1 などを設定してください。"
        )
    return key


def synthesize_text_to_wav_bytes(text: str, *, label: str = "TTS") -> bytes:
    """テキストを音声化。失敗時は最大 _TTS_MAX_ATTEMPTS 回まで API キーを切り替えて再試行。"""
    t_all = time.perf_counter()
    if not text.strip():
        raise ValueError("読み上げテキストが空です。")

    voice = os.getenv("VOICE_NAME", DEFAULT_VOICE).strip() or DEFAULT_VOICE
    LOG.info(
        "%s 開始 model=%s voice=%s 文字数=%d",
        label,
        MODEL_ID,
        voice,
        len(text),
    )
    cfg = _speech_config()
    last_err: Exception | None = None

    for attempt in range(_TTS_MAX_ATTEMPTS):
        api_key = _get_api_key_or_raise()
        key_suffix = api_key_manager.last_used_key_info["key_snippet"]
        client = genai.Client(api_key=api_key)
        LOG.info(
            "API リクエスト送信中 (%d / %d 回目) key末尾=%s …",
            attempt + 1,
            _TTS_MAX_ATTEMPTS,
            key_suffix,
        )
        t_req = time.perf_counter()
        try:
            resp = client.models.generate_content(
                model=MODEL_ID,
                contents=text,
                config=cfg,
            )
        except APIError as e:
            last_err = e
            LOG.warning("API エラー (code=%s): %s", e.code, e.message or e)
            if attempt + 1 < _TTS_MAX_ATTEMPTS:
                delay = _retry_delay_sec()
                LOG.info(
                    "%.0f 秒待って API キーを切り替えて再試行します (現在 key末尾=%s)",
                    delay,
                    api_key_manager.last_used_key_info["key_snippet"],
                )
                time.sleep(delay)
                continue
            raise
        elapsed_req = time.perf_counter() - t_req
        _log_response_summary(resp, elapsed_req)

        try:
            pcm, rate = _collect_pcm_from_response(resp)
        except RuntimeError as e:
            last_err = e
            LOG.warning("音声データの取り出しに失敗: %s", e)
            if attempt + 1 >= _TTS_MAX_ATTEMPTS:
                LOG.error("リトライ上限に達しました")
                raise
            delay = _retry_delay_sec()
            LOG.info(
                "%.0f 秒待って API キーを切り替えて再試行します (現在 key末尾=%s)",
                delay,
                api_key_manager.last_used_key_info["key_snippet"],
            )
            time.sleep(delay)
            continue

        wav = pcm_to_wav_bytes(pcm, rate)
        total = time.perf_counter() - t_all
        LOG.info(
            "%s 成功 PCM=%d bytes sample_rate=%d Hz → WAV=%d bytes (合計 %.2f 秒)",
            label,
            len(pcm),
            rate,
            len(wav),
            total,
        )
        return wav

    assert last_err is not None
    raise last_err


def synthesize_to_wav_bytes() -> bytes:
    return synthesize_text_to_wav_bytes(read_prompt_text(), label="TTS (prompt.txt)")


def split_pages(text: str) -> list[str]:
    """'----------' 行で分割。区切り n 個 → n+1 ページ。"""
    parts = _PAGE_SEPARATOR_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def fireshot_capture_number(path: Path) -> int | None:
    m = _FIRESHOT_NUM_RE.search(path.name)
    return int(m.group(1)) if m else None


def list_input_txt_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"入力ディレクトリが見つかりません: {input_dir}")
    files = list(input_dir.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"*.txt がありません: {input_dir}")

    def sort_key(p: Path) -> tuple[int, str]:
        num = fireshot_capture_number(p)
        return (num if num is not None else 10**9, p.name)

    return sorted(files, key=sort_key)


def wav_output_path(output_dir: Path, capture_num: int, page_index: int) -> Path:
    return output_dir / f"{capture_num:03d}_p{page_index:02d}.wav"


def backup_dir_for(input_dir: Path) -> Path:
    return input_dir / "bup"


def move_txt_to_bup(txt_path: Path, bup_dir: Path) -> Path:
    """処理済みテキストを input_dir/bup/ へ移動（再実行時に TTS 対象外にする）。"""
    bup_dir.mkdir(parents=True, exist_ok=True)
    dest = bup_dir / txt_path.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(txt_path), str(dest))
    return dest


def run_batch(input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    txt_files = list_input_txt_files(input_dir)
    LOG.info(
        "バッチ開始: 入力=%s 出力=%s ファイル数=%d APIキー数=%d",
        input_dir.resolve(),
        output_dir.resolve(),
        len(txt_files),
        api_key_manager.key_count,
    )
    if api_key_manager.key_count == 0:
        raise RuntimeError(
            "GOOGLE_API_KEY_* が .env に設定されていません。"
        )

    bup_dir = backup_dir_for(input_dir)
    total_pages = 0
    moved_files = 0
    for file_idx, txt_path in enumerate(txt_files, start=1):
        capture_num = fireshot_capture_number(txt_path)
        if capture_num is None:
            LOG.warning("FireShot 番号を取得できないためスキップ: %s", txt_path.name)
            continue

        raw = txt_path.read_text(encoding="utf-8")
        pages = split_pages(raw)
        if not pages:
            LOG.warning("ページが0件のためスキップ（移動しません）: %s", txt_path.name)
            continue

        LOG.info(
            "[%d/%d] %s → %d ページ",
            file_idx,
            len(txt_files),
            txt_path.name,
            len(pages),
        )

        for page_idx, page_text in enumerate(pages, start=1):
            out_path = wav_output_path(output_dir, capture_num, page_idx)
            label = f"{capture_num:03d}_p{page_idx:02d}"
            LOG.info("ページ処理: %s (%d 文字)", label, len(page_text))
            wav = synthesize_text_to_wav_bytes(page_text, label=label)
            out_path.write_bytes(wav)
            api_key_manager.save_session()
            total_pages += 1
            LOG.info("保存: %s (%d bytes)", out_path.resolve(), len(wav))

        dest = move_txt_to_bup(txt_path, bup_dir)
        moved_files += 1
        LOG.info("処理済みとして移動: %s", dest.resolve())

    LOG.info(
        "バッチ完了: %d ファイル / %d ページの WAV / %d ファイルを bup へ移動",
        len(txt_files),
        total_pages,
        moved_files,
    )
    print(
        f"完了: {total_pages} ページを {output_dir.resolve()} に保存、"
        f"{moved_files} ファイルを {bup_dir.resolve()} に移動しました。"
    )


# --- CLI ---
def run_cli(output: Path | None) -> None:
    wav = synthesize_to_wav_bytes()
    out = output or (BASE_DIR / f"gemini_tts_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.wav")
    out.write_bytes(wav)
    api_key_manager.save_session()
    LOG.info("保存先: %s (%d bytes)", out.resolve(), len(wav))
    print(f"保存しました: {out}")


# --- Web ---
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    load_dotenv(BASE_DIR / ".env")
    yield


app = FastAPI(title="Gemini TTS (prompt.txt)", lifespan=_lifespan)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    try:
        preview = html.escape(read_prompt_text())
    except Exception as e:
        preview = html.escape(f"(読み込みエラー: {e})")
    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="utf-8"/><title>Gemini TTS</title></head>
<body>
  <h1>Gemini 3.1 Flash TTS（prompt.txt）</h1>
  <p>モデル: <code>{MODEL_ID}</code></p>
  <h2>prompt.txt の内容</h2>
  <pre style="white-space:pre-wrap;border:1px solid #ccc;padding:8px;">{preview}</pre>
  <p><a href="/download.wav">音声を生成してダウンロード（WAV）</a></p>
</body>
</html>"""


@app.get("/download.wav")
def download_wav() -> Response:
    LOG.info("HTTP: 音声ダウンロード要求を受信")
    try:
        data = synthesize_to_wav_bytes()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    name = f"gemini_tts_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.wav"
    return Response(
        content=data,
        media_type="audio/wav",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini TTS: prompt.txt を音声化")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="詳細ログ（-v で DEBUG。パート単位など）",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="警告以上だけ表示",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="HTTP サーバを起動（ブラウザから WAV をダウンロード）",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="--web 時のバインドアドレス",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="--web 時のポート",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="単体 CLI: 出力 WAV パス / バッチ: 出力ディレクトリ（--input と併用）",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="読み上げ元テキストのディレクトリ（例: no-text-txt1）",
    )
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    setup_logging(args.verbose, quiet=args.quiet)

    if args.input is not None:
        if args.output is None:
            parser.error("バッチ処理には --output（出力ディレクトリ）も指定してください。")
        try:
            run_batch(args.input, args.output)
        except Exception as e:
            print(f"エラー: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.web:
        import uvicorn

        uv_log = "debug" if args.verbose >= 1 else "info"
        if args.quiet:
            uv_log = "warning"
        LOG.info("Web サーバ起動 host=%s port=%s", args.host, args.port)
        uvicorn.run(
            "main:app",
            host=args.host,
            port=args.port,
            reload=False,
            log_level=uv_log,
        )
        return

    try:
        run_cli(args.output)
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
