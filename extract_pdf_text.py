#!/usr/bin/env python3
"""pdf/ 内の PDF を直列で文字起こしし、txt/ に .txt で保存する。"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import fitz

from api_key_manager import api_key_manager

QUESTION_LINE_RE = re.compile(r"【問\s*\d+\s*】")
PAGE_HEADER_BEFORE_QUESTION_RE = re.compile(
    r"(?:\n[　\s]+)?\n\d+\s*\n(?=\s*【問)",
)

MIN_TEXT_CHARS_PER_PAGE = 80
OCR_RENDER_SCALE = 2.0
GEMINI_MODEL = "gemini-3.1-flash-lite"
OCR_GEMINI_MAX_RETRIES = 3  # 初回失敗後のリトライ回数（別 API キーで再試行）
OCR_GEMINI_RETRY_DELAY_SECONDS = 5
OCR_GEMINI_INTERVAL_SECONDS = 6  # 文字起こし（Gemini 呼び出し）の間隔
OCR_GEMINI_PAGES_PER_REQUEST = 4  # 1 回の Gemini 呼び出しに含めるページ数
GEMINI_PAGE_BREAK_MARKER = "===PAGE_BREAK==="

_gemini_ocr_call_count = 0

_GEMINI_TRANSCRIBE_RULES = """\
ルール:
- 写っている文字だけを出力する（要約・説明・前置きは不要）
- 問題番号は【問　N　】の形式を維持（全角スペース含む）
- 選択肢番号（1〜4）およびア・イ・ウ・エを維持
- 改行は原文の段落・選択肢の区切りに近づける
- 判読できない文字は「□」とする（推測で補わない）
"""


def _build_gemini_transcribe_prompt(page_count: int) -> str:
    if page_count == 1:
        return (
            "この画像は宅地建物取引士試験の問題冊子の1ページです。\n"
            "画像に写っている日本語テキストを、省略せず正確にプレーンテキストで書き起こしてください。\n\n"
            + _GEMINI_TRANSCRIBE_RULES
        )
    return (
        f"この{page_count}枚の画像は、宅地建物取引士試験の問題冊子の連続するページです。\n"
        f"1枚目から{page_count}枚目の順に、各ページの日本語テキストを省略せず正確に書き起こしてください。\n"
        f"ページとページの区切りには、行全体が「{GEMINI_PAGE_BREAK_MARKER}」のみの行を1行入れてください。\n\n"
        + _GEMINI_TRANSCRIBE_RULES
    )


def _has_gemini_keys() -> bool:
    return api_key_manager.key_count > 0 or bool(
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    )


def _get_gemini_api_key_for_request() -> str | None:
    """OCR 1回ごとに次の API キーを返す（GOOGLE_API_KEY_N を順番にローテーション）。"""
    if api_key_manager.key_count > 0:
        return api_key_manager.get_next_key_sync()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _render_page_png(page: fitz.Page) -> bytes:
    pix = page.get_pixmap(matrix=fitz.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE))
    return pix.tobytes("png")


def _extract_gemini_text(response) -> str:
    text_parts: list[str] = []
    for candidate in response.candidates or []:
        if not candidate.content:
            continue
        for part in candidate.content.parts or []:
            if getattr(part, "text", None):
                text_parts.append(part.text)
    return "\n".join(text_parts).strip()


def _reset_gemini_ocr_interval() -> None:
    global _gemini_ocr_call_count
    _gemini_ocr_call_count = 0


def _wait_before_gemini_ocr() -> None:
    """連続する Gemini 文字起こしの間に待機する（初回は除く）。"""
    global _gemini_ocr_call_count
    if _gemini_ocr_call_count > 0:
        print(
            f"次の文字起こしまで {OCR_GEMINI_INTERVAL_SECONDS} 秒待機...",
            file=sys.stderr,
        )
        time.sleep(OCR_GEMINI_INTERVAL_SECONDS)
    _gemini_ocr_call_count += 1


def _get_gemini_error_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _format_gemini_error(exc: BaseException) -> str:
    code = _get_gemini_error_code(exc)
    if code is not None:
        return f"{type(exc).__name__}({code}): {exc}"
    return f"{type(exc).__name__}: {exc}"


def _split_multi_page_ocr(text: str, expected_pages: int) -> list[str]:
    if expected_pages == 1:
        return [text.strip()]

    parts = re.split(
        rf"\n\s*{re.escape(GEMINI_PAGE_BREAK_MARKER)}\s*\n",
        text,
    )
    parts = [p.strip() for p in parts]
    if len(parts) != expected_pages:
        parts = [p.strip() for p in text.split(GEMINI_PAGE_BREAK_MARKER)]
    if len(parts) != expected_pages:
        print(
            f"警告: ページ分割が期待と異なります（期待 {expected_pages}、実際 {len(parts)}）",
            file=sys.stderr,
        )
        if len(parts) < expected_pages:
            parts.extend([""] * (expected_pages - len(parts)))
        parts = parts[:expected_pages]
    return parts


def _ocr_pages_gemini(
    image_bytes_list: list[bytes],
    api_key: str,
    *,
    prompt: str | None = None,
) -> str:
    from google import genai
    from google.genai import types

    contents: list = []
    for image_bytes in image_bytes_list:
        contents.append(
            types.Part.from_bytes(data=image_bytes, mime_type="image/png")
        )
    contents.append(
        prompt or _build_gemini_transcribe_prompt(len(image_bytes_list))
    )

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
    )
    return _extract_gemini_text(response)


def _ocr_pages_gemini_with_retry(
    image_bytes_list: list[bytes],
    api_key: str,
    *,
    prompt: str | None = None,
) -> str:
    """Gemini 呼び出しがエラーなら、別 API キーで最大 OCR_GEMINI_MAX_RETRIES 回リトライする。"""
    last_error: BaseException | None = None
    current_key = api_key
    max_attempts = OCR_GEMINI_MAX_RETRIES + 1

    for attempt in range(max_attempts):
        try:
            return _ocr_pages_gemini(
                image_bytes_list, current_key, prompt=prompt
            )
        except Exception as e:
            last_error = e
            if attempt >= OCR_GEMINI_MAX_RETRIES:
                break
            next_key = _get_gemini_api_key_for_request()
            if not next_key:
                break
            current_key = next_key
            print(
                f"OCR 失敗 ({attempt + 1}/{max_attempts}): "
                f"{_format_gemini_error(e)} → "
                f"{OCR_GEMINI_RETRY_DELAY_SECONDS}秒待機後、別キーで再試行",
                file=sys.stderr,
            )
            time.sleep(OCR_GEMINI_RETRY_DELAY_SECONDS)

    assert last_error is not None
    raise last_error


def _ocr_page_easyocr(page: fitz.Page, reader) -> str:
    import numpy as np

    pix = page.get_pixmap(matrix=fitz.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    lines = reader.readtext(img, detail=0, paragraph=True)
    return "\n".join(lines)


def extract_text(
    pdf_path: Path,
    *,
    use_ocr: bool | None = None,
    ocr_backend: str = "gemini",
) -> str:
    doc = fitz.open(pdf_path)
    try:
        page_texts: list[str] = []
        ocr_pages: list[int] = []
        easyocr_reader = None

        if ocr_backend == "gemini" and not _has_gemini_keys():
            if use_ocr is True:
                raise SystemExit(
                    "画像ページの OCR に gemini を使うには .env に GOOGLE_API_KEY_1 など、"
                    "または GEMINI_API_KEY が必要です。"
                    " または --ocr-backend easyocr を指定してください。"
                )
            ocr_backend = "easyocr"
            print(
                "GEMINI_API_KEY 未設定のため easyocr を使用します。",
                file=sys.stderr,
            )

        pending_gemini_pages: list[tuple[int, fitz.Page]] = []

        def flush_gemini_ocr(*, force: bool = False) -> None:
            while pending_gemini_pages and (
                force
                or len(pending_gemini_pages) >= OCR_GEMINI_PAGES_PER_REQUEST
            ):
                batch = pending_gemini_pages[:OCR_GEMINI_PAGES_PER_REQUEST]
                del pending_gemini_pages[: len(batch)]
                page_nums = [idx + 1 for idx, _ in batch]
                label = (
                    f"{page_nums[0]}"
                    if len(page_nums) == 1
                    else f"{page_nums[0]}-{page_nums[-1]}"
                )
                print(
                    f"OCR (gemini) p.{label}/{doc.page_count} "
                    f"({len(page_nums)}枚/回) ...",
                    file=sys.stderr,
                )
                _wait_before_gemini_ocr()
                api_key = _get_gemini_api_key_for_request()
                if not api_key:
                    raise SystemExit("利用可能な Gemini API キーがありません。")
                images = [_render_page_png(page) for _, page in batch]
                raw = _ocr_pages_gemini_with_retry(images, api_key)
                page_texts.extend(_split_multi_page_ocr(raw, len(batch)))

        for i, page in enumerate(doc):
            text = (page.get_text() or "").strip()
            need_ocr = use_ocr is True or (
                use_ocr is not False and len(text) < MIN_TEXT_CHARS_PER_PAGE
            )
            if not need_ocr:
                flush_gemini_ocr(force=True)
                page_texts.append(page.get_text())
                continue

            ocr_pages.append(i + 1)

            if ocr_backend == "gemini":
                pending_gemini_pages.append((i, page))
                flush_gemini_ocr()
            else:
                flush_gemini_ocr(force=True)
                print(
                    f"OCR ({ocr_backend}) {i + 1}/{doc.page_count} ...",
                    file=sys.stderr,
                )
                if easyocr_reader is None:
                    import easyocr

                    print("EasyOCR モデルを読み込み中...", file=sys.stderr)
                    easyocr_reader = easyocr.Reader(["ja"], gpu=False)
                page_texts.append(_ocr_page_easyocr(page, easyocr_reader))

        flush_gemini_ocr(force=True)

        if ocr_pages:
            print(f"OCRしたページ ({ocr_backend}): {ocr_pages}", file=sys.stderr)

        return "\n".join(page_texts)
    finally:
        doc.close()


def _is_after_previous_answer(lines: list[str]) -> bool:
    i = len(lines) - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0:
        return False
    j = i
    while j >= 0:
        if QUESTION_LINE_RE.search(lines[j]):
            return i > j
        j -= 1
    return False


def format_extracted_text(text: str) -> str:
    text = text.replace("【間", "【問")
    text = PAGE_HEADER_BEFORE_QUESTION_RE.sub("\n", text)
    text = re.sub(r"\n[　\s]+\n(?=\s*【問)", "\n", text)

    lines = text.splitlines()
    out: list[str] = []
    question_count = 0

    for line in lines:
        if QUESTION_LINE_RE.search(line):
            if question_count > 0 and _is_after_previous_answer(out):
                while out and not out[-1].strip():
                    out.pop()
                out.extend(["", ""])
            question_count += 1
        out.append(line)

    return "\n".join(out) + "\n"


def collect_pdf_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise SystemExit(f"入力ディレクトリが見つかりません: {input_dir}")
    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"PDF がありません: {input_dir / '*.pdf'}")
    return pdfs


def txt_output_path(pdf_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{pdf_path.stem}.txt"


def process_pdf(
    pdf_path: Path,
    output_dir: Path,
    *,
    use_ocr: bool | None,
    ocr_backend: str,
) -> Path:
    txt_path = txt_output_path(pdf_path, output_dir)
    text = format_extracted_text(
        extract_text(pdf_path, use_ocr=use_ocr, ocr_backend=ocr_backend)
    )
    txt_path.write_text(text, encoding="utf-8")
    return txt_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="pdf/ 内の PDF を直列で文字起こしし、txt/ に保存"
    )
    parser.add_argument(
        "--input",
        default="pdf",
        type=Path,
        help="入力 PDF ディレクトリ（既定: pdf）",
    )
    parser.add_argument(
        "--output",
        default="txt",
        type=Path,
        help="出力 TXT ディレクトリ（既定: txt）",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="全ページを OCR で読み取る（未指定時はテキストの少ないページのみ OCR）",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="OCR を使わずテキスト層のみ抽出（画像 PDF ではほぼ空になる）",
    )
    parser.add_argument(
        "--ocr-backend",
        choices=("gemini", "easyocr"),
        default="gemini",
        help="OCR エンジン（既定: gemini。API キー未設定時は easyocr にフォールバック）",
    )
    args = parser.parse_args()

    if args.ocr and args.no_ocr:
        raise SystemExit("--ocr と --no-ocr は同時に指定できません")

    use_ocr: bool | None
    if args.ocr:
        use_ocr = True
    elif args.no_ocr:
        use_ocr = False
    else:
        use_ocr = None

    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = collect_pdf_files(input_dir)
    total = len(pdf_files)
    used_gemini = args.ocr_backend == "gemini" and _has_gemini_keys()
    _reset_gemini_ocr_interval()

    for n, pdf_path in enumerate(pdf_files, start=1):
        print(f"[{n}/{total}] {pdf_path.name}", file=sys.stderr)
        txt_path = process_pdf(
            pdf_path,
            output_dir,
            use_ocr=use_ocr,
            ocr_backend=args.ocr_backend,
        )
        print(txt_path)

    if used_gemini and api_key_manager.key_count > 0:
        api_key_manager.save_session()

    print(f"完了: {total} 件 → {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
