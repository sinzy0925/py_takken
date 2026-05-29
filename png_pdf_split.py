#!/usr/bin/env python3
"""番号付きサブフォルダ内の PNG を縦に連結し、フォルダごとに1つの PDF にする。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

from png_pdf import capture_sort_key, collect_png_files, to_rgb

SUBDIR_NUM_RE = re.compile(r"^\d+$")


def subdir_sort_key(path: Path) -> tuple[int, str]:
    if SUBDIR_NUM_RE.match(path.name):
        return (int(path.name), path.name)
    return (10**9, path.name.lower())


def find_png_subdirs(input_root: Path) -> list[Path]:
    subdirs = [p for p in input_root.iterdir() if p.is_dir()]
    subdirs = [p for p in subdirs if list(p.glob("*.png"))]
    if not subdirs:
        raise SystemExit(f"PNG を含むサブフォルダがありません: {input_root}")
    return sorted(subdirs, key=subdir_sort_key)


def stack_vertically(images: list[Image.Image]) -> Image.Image:
    rgb_images = [to_rgb(im) for im in images]
    width = max(im.width for im in rgb_images)
    height = sum(im.height for im in rgb_images)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    y = 0
    for im in rgb_images:
        x = (width - im.width) // 2
        canvas.paste(im, (x, y))
        y += im.height
    return canvas


def pngs_to_stacked_pdf(png_files: list[Path], pdf_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    images: list[Image.Image] = []
    try:
        for png_path in png_files:
            with Image.open(png_path) as im:
                images.append(im.copy())
        stacked = stack_vertically(images)
        stacked.save(pdf_path, "PDF", resolution=100.0)
    finally:
        for im in images:
            im.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="各サブフォルダ内の PNG を縦に連結して PDF に出力します。",
    )
    parser.add_argument(
        "--in",
        dest="input_dir",
        type=Path,
        required=True,
        help="番号付きサブフォルダ（1/, 2/, …）を含む親ディレクトリ",
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        type=Path,
        required=True,
        help="PDF の出力先ディレクトリ",
    )
    args = parser.parse_args()

    input_root = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not input_root.is_dir():
        raise SystemExit(f"入力ディレクトリがありません: {input_root}")

    output_dir.mkdir(parents=True, exist_ok=True)

    for subdir in find_png_subdirs(input_root):
        png_files = collect_png_files(subdir)
        pdf_path = output_dir / f"{subdir.name}.pdf"
        print(
            f"{subdir.name}/ ({len(png_files)} 枚) → {pdf_path.name}",
            file=sys.stderr,
        )
        pngs_to_stacked_pdf(png_files, pdf_path)

    print(f"完了: {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
