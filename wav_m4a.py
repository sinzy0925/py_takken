#!/usr/bin/env python3
"""mp4 / wav から音声を倍速・モノラル・m4a で順番に書き出す。"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

AUDIO_BITRATE = "128k"
INPUT_EXTENSIONS = (".mp4", ".wav")


def ensure_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit(
            "ffmpeg が見つかりません。PATH に追加するか、ffmpeg をインストールしてください。"
        )
    return path


def list_input_files(input_dir: Path) -> list[Path]:
    files: list[Path] = []
    for ext in INPUT_EXTENSIONS:
        files.extend(input_dir.glob(f"*{ext}"))
    return sorted(files, key=lambda p: p.name.lower())


def convert_to_m4a(
    ffmpeg: str,
    src: Path,
    dst: Path,
    *,
    atempo: float,
    mono: bool = True,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-nostats",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
    ]
    if src.suffix.lower() == ".mp4":
        cmd.append("-vn")
    cmd.extend(
        [
            "-filter:a",
            f"atempo={atempo}",
        ]
    )
    if mono:
        cmd.extend(["-ac", "1"])
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            str(dst),
        ]
    )
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mp4 / wav を倍速・モノラル（既定）・m4a に変換（フォルダ内を順番に処理）"
    )
    parser.add_argument(
        "--input",
        default="mp4",
        type=Path,
        help="入力ディレクトリ（既定: mp4）",
    )
    parser.add_argument(
        "--output",
        default="m4a",
        type=Path,
        help="出力ディレクトリ（既定: m4a）",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.5,
        metavar="RATE",
        help="再生速度の倍率（ffmpeg atempo。既定: 1.5）",
    )
    parser.add_argument(
        "--stereo",
        action="store_true",
        help="モノラル化しない（2ch のまま出力）",
    )
    args = parser.parse_args()

    if not 0.5 <= args.speed <= 2.0:
        raise SystemExit("--speed は 0.5 〜 2.0 の範囲で指定してください（ffmpeg atempo の制限）。")

    input_dir = args.input.resolve()
    output_dir = args.output.resolve()

    if not input_dir.is_dir():
        raise SystemExit(f"入力ディレクトリが見つかりません: {input_dir}")

    sources = list_input_files(input_dir)
    if not sources:
        exts = ", ".join(INPUT_EXTENSIONS)
        raise SystemExit(f"入力ファイルがありません（{exts}）: {input_dir}")

    ffmpeg = ensure_ffmpeg()
    output_dir.mkdir(parents=True, exist_ok=True)
    mono = not args.stereo

    for i, src in enumerate(sources, start=1):
        dst = output_dir / f"{src.stem}.m4a"
        ch = "mono" if mono else "stereo"
        print(
            f"[{i}/{len(sources)}] {src.name} → {dst.name} "
            f"({args.speed}x, {ch})",
            file=sys.stderr,
        )
        try:
            convert_to_m4a(ffmpeg, src, dst, atempo=args.speed, mono=mono)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                f"変換に失敗しました: {src.name} (終了コード {exc.returncode})"
            ) from exc

    print(f"完了: {len(sources)} 件 → {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
