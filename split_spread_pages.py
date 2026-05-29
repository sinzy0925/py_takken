#!/usr/bin/env python3
"""見開きスクリーンショットを中央で左右分割し、外側の余白を除去する。"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

WHITE_THRESHOLD = 250


def content_column_bounds(
    rgb: np.ndarray, threshold: int = WHITE_THRESHOLD
) -> tuple[int, int] | None:
    """非白ピクセルが存在する列の左右インデックス（含む）。真っ白なら None。"""
    mask = np.any(rgb < threshold, axis=2)
    cols = np.any(mask, axis=0)
    if not cols.any():
        return None
    xs = np.where(cols)[0]
    return int(xs[0]), int(xs[-1])


def split_and_trim_spread(
    image_path: Path,
    output_dir: Path,
    *,
    threshold: int = WHITE_THRESHOLD,
    suffix_left: str = "_left",
    suffix_right: str = "_right",
) -> tuple[Path, Path, list[str]]:
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]
    mid = w // 2

    left_half = arr[:, :mid]
    right_half = arr[:, mid:]

    left_bounds = content_column_bounds(left_half, threshold)
    right_bounds = content_column_bounds(right_half, threshold)

    if left_bounds is None:
        left_cropped = left_half
    else:
        left_cropped = left_half[:, left_bounds[0] :]

    if right_bounds is None:
        right_cropped = right_half
    else:
        right_cropped = right_half[:, : right_bounds[1] + 1]

    stem = image_path.stem
    out_left = output_dir / f"{stem}{suffix_left}{image_path.suffix}"
    out_right = output_dir / f"{stem}{suffix_right}{image_path.suffix}"

    warnings: list[str] = []
    if left_bounds is None:
        warnings.append("左半分にコンテンツなし（余白トリムをスキップ）")
    if right_bounds is None:
        warnings.append("右半分にコンテンツなし（余白トリムをスキップ）")

    Image.fromarray(left_cropped).save(out_left)
    Image.fromarray(right_cropped).save(out_right)
    return out_left, out_right, warnings


def collect_images(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.glob("*.png")))
            files.extend(sorted(p.glob("*.jpg")))
            files.extend(sorted(p.glob("*.jpeg")))
        elif p.is_file():
            files.append(p)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="見開き画像を中央で分割し、左ページは左余白・右ページは右余白を除去します。",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="入力画像ファイル、または画像を含むフォルダ",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="出力先（省略時は各入力と同じフォルダ）",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=WHITE_THRESHOLD,
        help=f"余白判定のしきい値 0-255（既定: {WHITE_THRESHOLD}）",
    )
    args = parser.parse_args()

    images = collect_images(args.inputs)
    if not images:
        raise SystemExit("処理対象の画像が見つかりませんでした。")

    for src in images:
        out_dir = args.output_dir if args.output_dir is not None else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        left_path, right_path, warnings = split_and_trim_spread(
            src,
            out_dir,
            threshold=args.threshold,
        )
        print(f"{src.name}")
        for msg in warnings:
            print(f"  ! {msg}")
        print(f"  → {left_path.name} ({left_path.stat().st_size // 1024} KB)")
        print(f"  → {right_path.name} ({right_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
