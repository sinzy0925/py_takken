#!/usr/bin/env python3
"""ディレクトリ内の PNG を1つの PDF にまとめる。"""

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

CAPTURE_NUM_RE = re.compile(r"Capture\s+(\d+)", re.IGNORECASE)


def capture_sort_key(path: Path) -> tuple[int, str]:
    m = CAPTURE_NUM_RE.search(path.name)
    if m:
        return (int(m.group(1)), path.name.lower())
    return (10**9, path.name.lower())


def collect_png_files(input_dir: Path) -> list[Path]:
    pngs = sorted(input_dir.glob("*.png"), key=capture_sort_key)
    if not pngs:
        raise SystemExit(f"PNG がありません: {input_dir / '*.png'}")
    return pngs


def to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        return background
    return image.convert("RGB")


def resolve_pdf_path(output: Path, input_dir: Path) -> Path:
    if output.suffix.lower() == ".pdf":
        return output
    return output / f"{input_dir.name}.pdf"


def pngs_to_pdf(png_files: list[Path], pdf_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    images: list[Image.Image] = []
    try:
        for png_path in png_files:
            with Image.open(png_path) as im:
                images.append(to_rgb(im.copy()))

        first, *rest = images
        first.save(
            pdf_path,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=rest,
        )
    finally:
        for im in images:
            im.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ディレクトリ内の PNG を1つの PDF にまとめる"
    )
    parser.add_argument(
        "--input",
        default="no-text",
        type=Path,
        help="入力 PNG ディレクトリ（既定: no-text）",
    )
    parser.add_argument(
        "--output",
        default="no-text/pdf",
        type=Path,
        help="出力 PDF のパス、または出力ディレクトリ（既定: no-text/pdf）",
    )
    args = parser.parse_args()

    input_dir = args.input.resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"入力ディレクトリがありません: {input_dir}")

    png_files = collect_png_files(input_dir)
    pdf_path = resolve_pdf_path(args.output.resolve(), input_dir)

    print(f"PNG {len(png_files)} 枚 → {pdf_path}", file=sys.stderr)
    pngs_to_pdf(png_files, pdf_path)
    print(f"完了: {pdf_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
