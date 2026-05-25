#!/usr/bin/env python3
"""no-text/ 内の見開き PNG を左右に分割して Gemini で文字起こしし、txt/ に保存する。"""

import argparse
import io
import re
import sys
from pathlib import Path

from PIL import Image

from extract_pdf_text import (
    GEMINI_PAGE_BREAK_MARKER,
    _get_gemini_api_key_for_request,
    _has_gemini_keys,
    _ocr_pages_gemini_with_retry,
    _reset_gemini_ocr_interval,
    _split_multi_page_ocr,
    _wait_before_gemini_ocr,
    api_key_manager,
)

# 幅/高さがこの値以上なら見開き（左右2ページ）とみなして中央で分割
SPREAD_MIN_ASPECT_RATIO = 1.5

_IMAGE_TRANSCRIBE_RULES = """\
ルール:
- 写っている文字だけを出力する（要約・説明・前置きは不要）
- 表・箇条書き・番号（①②など）の構造をできるだけ維持する
- 改行は原文の段落の区切りに近づける
- 判読できない文字は「□」とする（推測で補わない）
"""


def _build_image_transcribe_prompt(page_count: int, *, labels: list[str]) -> str:
    if page_count == 1:
        return (
            "この画像は Kindle 等の書籍画面のスクリーンショット1ページ分です。\n"
            "画像に写っている日本語テキストを、省略せず正確にプレーンテキストで書き起こしてください。\n\n"
            + _IMAGE_TRANSCRIBE_RULES
        )
    label_desc = "、".join(
        f"{i + 1}枚目={name}" for i, name in enumerate(labels)
    )
    return (
        f"この{page_count}枚の画像は、書籍の見開き画面を左右に分割したものです（{label_desc}）。\n"
        f"1枚目から{page_count}枚目の順に、各ページの日本語テキストを省略せず正確に書き起こしてください。\n"
        f"ページとページの区切りには、行全体が「{GEMINI_PAGE_BREAK_MARKER}」のみの行を1行入れてください。\n\n"
        + _IMAGE_TRANSCRIBE_RULES
    )


def _image_to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def split_spread_image(image_path: Path) -> tuple[list[bytes], list[str]]:
    """
    見開き画像を左ページ・右ページに分割する。
    単ページと判断した場合は1枚のみ返す。
    """
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        width, height = im.size

        if width / height < SPREAD_MIN_ASPECT_RATIO:
            return [_image_to_png_bytes(im)], ["ページ"]

        mid = width // 2
        left = im.crop((0, 0, mid, height))
        right = im.crop((mid, 0, width, height))
        return [
            _image_to_png_bytes(left),
            _image_to_png_bytes(right),
        ], ["左ページ", "右ページ"]


def format_transcription(parts: list[str], labels: list[str]) -> str:
    if len(parts) == 1:
        return parts[0].strip()
    sections: list[str] = []
    for label, text in zip(labels, parts):
        body = text.strip()
        if body:
            sections.append(f"--- {label} ---\n\n{body}")
    return "\n\n".join(sections)


def natural_sort_key(path: Path) -> list:
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def collect_png_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise SystemExit(f"入力ディレクトリが見つかりません: {input_dir}")
    files = sorted(input_dir.glob("*.png"), key=natural_sort_key)
    if not files:
        raise SystemExit(f"PNG がありません: {input_dir / '*.png'}")
    return files


def transcribe_image(image_path: Path) -> str:
    images, labels = split_spread_image(image_path)
    prompt = _build_image_transcribe_prompt(len(images), labels=labels)

    _wait_before_gemini_ocr()
    api_key = _get_gemini_api_key_for_request()
    if not api_key:
        raise SystemExit("利用可能な Gemini API キーがありません。")

    raw = _ocr_pages_gemini_with_retry(images, api_key, prompt=prompt)
    parts = _split_multi_page_ocr(raw, len(images))
    return format_transcription(parts, labels)


def process_image(image_path: Path, output_dir: Path) -> Path:
    txt_path = output_dir / f"{image_path.stem}.txt"
    text = transcribe_image(image_path)
    txt_path.write_text(text + "\n", encoding="utf-8")
    return txt_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="見開き PNG を左右分割して文字起こしし、txt に保存"
    )
    parser.add_argument(
        "--input",
        default="no-text",
        type=Path,
        help="入力 PNG ディレクトリ（既定: no-text）",
    )
    parser.add_argument(
        "--output",
        default="no-text-txt",
        type=Path,
        help="出力 TXT ディレクトリ（既定: no-text-txt）",
    )
    args = parser.parse_args()

    if not _has_gemini_keys():
        raise SystemExit(
            ".env に GOOGLE_API_KEY_1 など、または GEMINI_API_KEY を設定してください。"
        )

    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    png_files = collect_png_files(input_dir)
    total = len(png_files)
    _reset_gemini_ocr_interval()

    for n, image_path in enumerate(png_files, start=1):
        print(f"[{n}/{total}] {image_path.name} ...", file=sys.stderr)
        txt_path = process_image(image_path, output_dir)
        print(txt_path)

    if api_key_manager.key_count > 0:
        api_key_manager.save_session()

    print(f"完了: {total} 件 → {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
