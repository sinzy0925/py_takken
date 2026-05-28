"""
Google Cloud Text-to-Speech（WaveNet / 標準音声）でテキストを MP3 化する。

Gemini API は使用しません。課金は Cloud TTS の文字数（SKU 9D01-5995-B545）に従います。

実行例（リポジトリルートから）:
  python tts/kakin_app.py --input no-text-txt1 --output mp3
  python tts/kakin_app.py --input no-text-txt1 --output mp3 --voice-type standard
  python tts/kakin_app.py --input no-text-txt1 --output mp3 --monthly-char-limit 1000000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from google.api_core import exceptions as gcp_exceptions
from google.cloud import texttospeech

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRETS_KAKIN_DIR = REPO_ROOT / "secrets_kakin"
DEFAULT_CREDENTIALS = SECRETS_KAKIN_DIR / "service-account.json"
DEFAULT_USAGE_FILE = REPO_ROOT / ".kakin_usage.json"
DEFAULT_MONTHLY_CHAR_LIMIT = 1_000_000
DEFAULT_API_DELAY_SEC = 5.0

_PAGE_SEPARATOR_RE = re.compile(r"^\s*----------\s*$", re.MULTILINE)
_FIRESHOT_NUM_RE = re.compile(r"FireShot\s+Capture\s+(\d+)", re.I)

LOG = logging.getLogger(__name__)

_TTS_API_ENABLE_URL = (
    "https://console.cloud.google.com/apis/library/texttospeech.googleapis.com"
)


class MonthlyCharLimitError(RuntimeError):
    """今月の文字数上限に達した、または次の合成で上限を超える。"""


class KakinUsageStore:
    """
    月次の文字数使用量を .kakin_usage.json に記録する（B案: 合計 + 実行ログ）。

    合成成功後に entries へ1件追加し、chars を更新する。
    """

    def __init__(self, path: Path, monthly_limit: int) -> None:
        self.path = path
        self.monthly_limit = monthly_limit

    @staticmethod
    def _month_key(when: datetime | None = None) -> str:
        dt = when or datetime.now(timezone.utc).astimezone()
        return dt.strftime("%Y-%m")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"使用量ファイルの読み込みに失敗しました: {self.path}") from e
        if not isinstance(raw, dict):
            raise RuntimeError(f"使用量ファイルの形式が不正です: {self.path}")
        return raw

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    def _month_bucket(self, data: dict, month: str) -> dict:
        bucket = data.get(month)
        if not isinstance(bucket, dict):
            bucket = {"chars": 0, "entries": []}
            data[month] = bucket
        if "entries" not in bucket or not isinstance(bucket["entries"], list):
            bucket["entries"] = []
        if "chars" not in bucket:
            bucket["chars"] = sum(
                int(e.get("chars", 0))
                for e in bucket["entries"]
                if isinstance(e, dict)
            )
        return bucket

    def current_month_chars(self) -> int:
        data = self._load()
        bucket = self._month_bucket(data, self._month_key())
        return int(bucket.get("chars", 0))

    def ensure_within_limit(self, additional_chars: int, label: str) -> None:
        """合成前に呼ぶ。上限超過なら API を呼ばずに終了する。"""
        if self.monthly_limit <= 0:
            return
        data = self._load()
        month = self._month_key()
        bucket = self._month_bucket(data, month)
        current = int(bucket.get("chars", 0))
        projected = current + additional_chars
        if projected > self.monthly_limit:
            raise MonthlyCharLimitError(
                f"月次文字数上限に達するため合成を中止しました（{label}）。\n"
                f"  今月 ({month}): {current:,} 文字 済み\n"
                f"  今回の予定: {additional_chars:,} 文字\n"
                f"  合計予定: {projected:,} 文字 > 上限 {self.monthly_limit:,} 文字\n"
                f"  記録ファイル: {self.path.resolve()}"
            )

    def record_success(self, char_count: int, label: str) -> int:
        """合成成功後に呼ぶ。entries に追加し chars を更新する。"""
        data = self._load()
        month = self._month_key()
        bucket = self._month_bucket(data, month)
        now_iso = self._now_iso()
        bucket["entries"].append(
            {"at": now_iso, "chars": char_count, "label": label}
        )
        bucket["chars"] = int(bucket.get("chars", 0)) + char_count
        bucket["updated_at"] = now_iso
        self._save(data)
        return int(bucket["chars"])

    def limit_enabled(self) -> bool:
        return self.monthly_limit > 0


def project_id_from_credentials(cred_path: Path) -> str | None:
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        pid = data.get("project_id")
        return pid if isinstance(pid, str) and pid.strip() else None
    except (OSError, json.JSONDecodeError):
        return None


def _format_tts_api_error(
    exc: gcp_exceptions.GoogleAPICallError,
    *,
    project_id: str | None = None,
) -> str:
    """PermissionDenied 等を日本語の対処案内付きメッセージに変換する。"""
    text = str(exc)
    if "SERVICE_DISABLED" in text or "has not been used in project" in text:
        project_hint = ""
        if project_id:
            project_hint = f"（プロジェクト ID: {project_id}）"
        else:
            m = re.search(r"project (\d+)", text)
            if m:
                project_hint = f"（プロジェクト番号 {m.group(1)}）"
        project_select = (
            f"プロジェクト「{project_id}」を選び"
            if project_id
            else "サービスアカウントと同じプロジェクトを選び"
        )
        return (
            "Cloud Text-to-Speech API が、このサービスアカウントの GCP プロジェクトで"
            f"有効になっていません{project_hint}。\n"
            f"  1. 次の URL を開き、{project_select}「有効にする」\n"
            f"     {_TTS_API_ENABLE_URL}\n"
            "  2. 課金がプロジェクトにリンクされているか確認\n"
            "  3. 有効化後、2〜5 分待ってから再実行\n"
            "  サービスアカウントの roles/cloudtts.user は API 有効化とは別設定です。"
        )
    if isinstance(exc, gcp_exceptions.PermissionDenied):
        return (
            "Cloud Text-to-Speech API へのアクセスが拒否されました。\n"
            "  - API が有効か\n"
            "  - サービスアカウントに roles/cloudtts.user があるか\n"
            "  を確認してください。"
        )
    return text


def setup_logging(verbose: int, *, quiet: bool) -> None:
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


def resolve_credentials(explicit: Path | None) -> Path:
    """サービスアカウント JSON のパスを決定し、GOOGLE_APPLICATION_CREDENTIALS を設定する。"""
    candidates: list[Path] = []

    if explicit is not None:
        candidates.append(explicit.expanduser())

    for env_name in ("GOOGLE_APPLICATION_CREDENTIALS", "KAKIN_CREDENTIALS"):
        env_val = os.getenv(env_name, "").strip()
        if env_val:
            candidates.append(Path(env_val).expanduser())

    if DEFAULT_CREDENTIALS.is_file():
        candidates.append(DEFAULT_CREDENTIALS)

    if SECRETS_KAKIN_DIR.is_dir():
        json_in_dir = sorted(SECRETS_KAKIN_DIR.glob("*.json"))
        if len(json_in_dir) == 1:
            candidates.append(json_in_dir[0])
        elif len(json_in_dir) > 1 and explicit is None and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            names = ", ".join(p.name for p in json_in_dir)
            raise FileNotFoundError(
                f"secrets_kakin/ に JSON が複数あります。--credentials で1つ指定してください: {names}"
            )

    for path in candidates:
        if path.is_file():
            resolved = path.resolve()
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(resolved)
            return resolved

    raise FileNotFoundError(
        "サービスアカウント JSON が見つかりません。\n"
        f"  - {SECRETS_KAKIN_DIR}/service-account.json を置く\n"
        "  - または --credentials / GOOGLE_APPLICATION_CREDENTIALS / KAKIN_CREDENTIALS を設定\n"
        "  手順: secrets_kakin/README.md を参照"
    )


def normalize_voice_type(value: str) -> str:
    v = value.strip().lower()
    if v in ("wavenet", "wave"):
        return "wavenet"
    if v in ("standard", "std"):
        return "standard"
    raise ValueError(f"--voice-type は wavenet または standard を指定してください: {value!r}")


def resolve_voice_name(
    client: texttospeech.TextToSpeechClient,
    voice_type: str,
    voice_name: str | None,
    language_code: str = "ja-JP",
    *,
    gcp_project_id: str | None = None,
) -> str:
    if voice_name:
        name = voice_name.strip()
        if voice_type == "wavenet" and "Wavenet" not in name and "wavenet" not in name.lower():
            LOG.warning("voice-type=wavenet ですが音声名に Wavenet が含まれていません: %s", name)
        if voice_type == "standard" and "Standard" not in name and "standard" not in name.lower():
            LOG.warning("voice-type=standard ですが音声名に Standard が含まれていません: %s", name)
        return name

    prefix = "ja-JP-Wavenet" if voice_type == "wavenet" else "ja-JP-Standard"
    try:
        response = client.list_voices(language_code=language_code)
    except gcp_exceptions.GoogleAPICallError as e:
        raise RuntimeError(_format_tts_api_error(e, project_id=gcp_project_id)) from e
    matches = [v.name for v in response.voices if v.name.startswith(prefix)]
    if not matches:
        raise RuntimeError(
            f"{language_code} の {voice_type} 音声が見つかりません。"
            " Cloud Text-to-Speech API が有効か確認してください。"
        )
    chosen = sorted(matches)[0]
    LOG.info("音声名未指定のため自動選択: %s（候補 %d 件）", chosen, len(matches))
    return chosen


def synthesize_text_to_mp3_bytes(
    client: texttospeech.TextToSpeechClient,
    text: str,
    *,
    voice_name: str,
    language_code: str = "ja-JP",
    speaking_rate: float = 1.0,
    label: str = "TTS",
    gcp_project_id: str | None = None,
) -> bytes:
    if not text.strip():
        raise ValueError("読み上げテキストが空です。")

    char_count = len(text)
    LOG.info(
        "%s 開始 voice=%s 文字数=%d（課金カウント対象）",
        label,
        voice_name,
        char_count,
    )

    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code=language_code,
        name=voice_name,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speaking_rate,
    )

    t0 = time.perf_counter()
    try:
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
    except gcp_exceptions.GoogleAPICallError as e:
        raise RuntimeError(_format_tts_api_error(e, project_id=gcp_project_id)) from e

    elapsed = time.perf_counter() - t0
    audio = response.audio_content
    if not audio:
        raise RuntimeError("音声データが空で返されました。")

    LOG.info(
        "%s 成功 MP3=%d bytes (%.2f 秒) 文字数=%d",
        label,
        len(audio),
        elapsed,
        char_count,
    )
    return audio


# --- バッチ用（main.py と同じ分割・ファイル命名） ---


def split_pages(text: str) -> list[str]:
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


def mp3_output_path(output_dir: Path, capture_num: int, page_index: int) -> Path:
    return output_dir / f"{capture_num:03d}_p{page_index:02d}.mp3"


def backup_dir_for(input_dir: Path) -> Path:
    return input_dir / "bup"


def move_txt_to_bup(txt_path: Path, bup_dir: Path) -> Path:
    bup_dir.mkdir(parents=True, exist_ok=True)
    dest = bup_dir / txt_path.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(txt_path), str(dest))
    return dest


def run_batch(
    client: texttospeech.TextToSpeechClient,
    input_dir: Path,
    output_dir: Path,
    *,
    voice_name: str,
    language_code: str,
    speaking_rate: float,
    move_to_bup: bool,
    gcp_project_id: str | None = None,
    usage: KakinUsageStore | None = None,
    api_delay_sec: float = 0.0,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    txt_files = list_input_txt_files(input_dir)
    LOG.info(
        "バッチ開始: 入力=%s 出力=%s ファイル数=%d voice=%s",
        input_dir.resolve(),
        output_dir.resolve(),
        len(txt_files),
        voice_name,
    )
    if api_delay_sec > 0:
        LOG.info("API 呼び出し後の待機: %.1f 秒", api_delay_sec)
    if usage is not None:
        month = KakinUsageStore._month_key()
        used = usage.current_month_chars()
        if usage.limit_enabled():
            LOG.info(
                "月次文字数: %s 月 %s / %s 文字 記録=%s",
                month,
                f"{used:,}",
                f"{usage.monthly_limit:,}",
                usage.path.resolve(),
            )
        else:
            LOG.info(
                "月次文字数: %s 月 %s 文字（上限なし） 記録=%s",
                month,
                f"{used:,}",
                usage.path.resolve(),
            )

    bup_dir = backup_dir_for(input_dir)
    total_pages = 0
    total_chars = 0
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
            out_path = mp3_output_path(output_dir, capture_num, page_idx)
            label = f"{capture_num:03d}_p{page_idx:02d}"
            char_count = len(page_text)
            if usage is not None:
                usage.ensure_within_limit(char_count, label)
            mp3 = synthesize_text_to_mp3_bytes(
                client,
                page_text,
                voice_name=voice_name,
                language_code=language_code,
                speaking_rate=speaking_rate,
                label=label,
                gcp_project_id=gcp_project_id,
            )
            out_path.write_bytes(mp3)
            total_pages += 1
            total_chars += char_count
            if usage is not None:
                month_total = usage.record_success(char_count, label)
                LOG.info(
                    "今月累計 %s 文字 保存: %s (%d bytes) ",
                    f"{month_total:,}",
                    out_path.resolve(),
                    len(mp3),
                )
            else:
                LOG.info("保存: %s (%d bytes)", out_path.resolve(), len(mp3))

            if api_delay_sec > 0:
                LOG.debug("API 待機 %.1f 秒 …", api_delay_sec)
                time.sleep(api_delay_sec)

        if move_to_bup:
            dest = move_txt_to_bup(txt_path, bup_dir)
            moved_files += 1
            LOG.info("処理済みとして移動: %s", dest.resolve())

    LOG.info(
        "バッチ完了: %d ページ / 合計 %d 文字（課金カウント目安）/ %d ファイルを bup へ移動",
        total_pages,
        total_chars,
        moved_files,
    )
    summary = (
        f"完了: {total_pages} ページを {output_dir.resolve()} に保存、"
        f"課金カウント目安 {total_chars:,} 文字、"
        f"{moved_files} ファイルを {bup_dir.resolve()} に移動しました。"
    )
    if usage is not None:
        summary += f" 今月累計 {usage.current_month_chars():,} 文字（{usage.path.name}）。"
    print(summary)
    return total_chars


def main() -> None:
    default_voice_type = "wavenet"
    env_voice_type = os.getenv("KAKIN_VOICE_TYPE", "").strip()
    if env_voice_type:
        try:
            default_voice_type = normalize_voice_type(env_voice_type)
        except ValueError as e:
            print(f"エラー: {e}", file=sys.stderr)
            sys.exit(2)

    parser = argparse.ArgumentParser(
        description="Cloud TTS（WaveNet/標準）: TXT を MP3 に変換（Gemini API 非使用）",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="読み上げ元テキストのディレクトリ（例: no-text-txt1）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="出力 MP3 のディレクトリ（例: mp3）",
    )
    parser.add_argument(
        "--voice-type",
        type=normalize_voice_type,
        default=default_voice_type,
        help="音声種別: wavenet / standard（既定: wavenet）。環境変数 KAKIN_VOICE_TYPE でも指定可",
    )
    parser.add_argument(
        "--voice-name",
        default=os.getenv("KAKIN_VOICE_NAME", "").strip() or None,
        help="音声名（例: ja-JP-Wavenet-D）。未指定時は ja-JP から自動選択",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=None,
        help=f"サービスアカウント JSON（既定: {DEFAULT_CREDENTIALS} または secrets_kakin/*.json）",
    )
    parser.add_argument(
        "--language",
        default=os.getenv("KAKIN_LANGUAGE", "ja-JP").strip() or "ja-JP",
        help="言語コード（既定: ja-JP）",
    )
    parser.add_argument(
        "--speaking-rate",
        type=float,
        default=float(os.getenv("KAKIN_SPEAKING_RATE", "1.0")),
        help="話速 0.25〜4.0（既定: 1.0）",
    )
    parser.add_argument(
        "--no-move-bup",
        action="store_true",
        help="処理後に TXT を bup/ へ移動しない",
    )
    default_monthly_limit = DEFAULT_MONTHLY_CHAR_LIMIT
    env_monthly = os.getenv("KAKIN_MONTHLY_CHAR_LIMIT", "").strip()
    if env_monthly:
        try:
            default_monthly_limit = int(env_monthly)
        except ValueError:
            parser.error(
                f"環境変数 KAKIN_MONTHLY_CHAR_LIMIT が整数ではありません: {env_monthly!r}"
            )
    parser.add_argument(
        "--monthly-char-limit",
        type=int,
        default=default_monthly_limit,
        metavar="N",
        help=(
            f"今月の合成文字数の上限（既定: {DEFAULT_MONTHLY_CHAR_LIMIT:,}）。"
            " 0 で上限チェックなし（記録は継続）。"
            " 環境変数 KAKIN_MONTHLY_CHAR_LIMIT でも指定可"
        ),
    )
    parser.add_argument(
        "--usage-file",
        type=Path,
        default=Path(os.getenv("KAKIN_USAGE_FILE", str(DEFAULT_USAGE_FILE))),
        help=f"使用量ログ JSON（既定: {DEFAULT_USAGE_FILE.name}）",
    )
    parser.add_argument(
        "--no-usage-log",
        action="store_true",
        help="使用量ファイルへの記録を行わない（上限チェックも無効）",
    )
    parser.add_argument(
        "--api-delay-sec",
        type=float,
        default=float(os.getenv("KAKIN_API_DELAY_SEC", str(DEFAULT_API_DELAY_SEC))),
        help=(
            f"synthesize_speech の成功ごとに待つ秒数（既定: {DEFAULT_API_DELAY_SEC}）。"
            " 0 で待機なし。環境変数 KAKIN_API_DELAY_SEC でも指定可"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="詳細ログ",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="警告以上だけ表示",
    )
    args = parser.parse_args()

    setup_logging(args.verbose, quiet=args.quiet)

    if not (0.25 <= args.speaking_rate <= 4.0):
        parser.error("--speaking-rate は 0.25 〜 4.0 の範囲で指定してください。")
    if args.monthly_char_limit < 0:
        parser.error("--monthly-char-limit は 0 以上の整数を指定してください。")
    if args.api_delay_sec < 0:
        parser.error("--api-delay-sec は 0 以上を指定してください。")

    voice_type = args.voice_type

    try:
        cred_path = resolve_credentials(args.credentials)
    except FileNotFoundError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    gcp_project_id = project_id_from_credentials(cred_path)
    LOG.info("認証: %s", cred_path)
    if gcp_project_id:
        LOG.info("GCP プロジェクト: %s", gcp_project_id)
    LOG.info("音声種別: %s", voice_type)

    client = texttospeech.TextToSpeechClient()
    try:
        voice_name = resolve_voice_name(
            client,
            voice_type,
            args.voice_name,
            language_code=args.language,
            gcp_project_id=gcp_project_id,
        )
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    LOG.info("使用音声: %s", voice_name)

    input_dir = args.input
    output_dir = args.output
    if not input_dir.is_absolute():
        input_dir = (Path.cwd() / input_dir).resolve()
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()

    usage: KakinUsageStore | None = None
    if not args.no_usage_log:
        usage_path = args.usage_file
        if not usage_path.is_absolute():
            usage_path = (Path.cwd() / usage_path).resolve()
        usage = KakinUsageStore(usage_path, args.monthly_char_limit)

    try:
        run_batch(
            client,
            input_dir,
            output_dir,
            voice_name=voice_name,
            language_code=args.language,
            speaking_rate=args.speaking_rate,
            move_to_bup=not args.no_move_bup,
            gcp_project_id=gcp_project_id,
            usage=usage,
            api_delay_sec=args.api_delay_sec,
        )
    except MonthlyCharLimitError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
