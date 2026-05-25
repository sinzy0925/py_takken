#!/usr/bin/env python3
"""TXT からページ区切りマーカー・ページ番号行・書籍フッターを除去する。"""

import argparse
import re
from pathlib import Path

PAGE_MARKER_RE = re.compile(r"^---\s*(左|右)ページ\s*---\s*$")
DIGIT_ONLY_LINE_RE = re.compile(r"^[0-9０-９]+\s*$")
# 例: 第1章 宅建業法 19 / 第2章 権利関係 361
CHAPTER_FOOTER_RE = re.compile(
    r"^第[0-9０-９]+章\s+.+[0-9０-９]+\s*$"
)
# 例: 予想模擬試験 593（OCR ゆれ: 模試試験 など）
MOCK_EXAM_FOOTER_RE = re.compile(
    r"^予想模[擬拟試験・＆解説\s　]*[0-9０-９]+\s*$"
)


def should_delete_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if PAGE_MARKER_RE.match(s):
        return True
    if DIGIT_ONLY_LINE_RE.match(s):
        return True
    if CHAPTER_FOOTER_RE.match(s):
        return True
    if MOCK_EXAM_FOOTER_RE.match(s):
        return True
    return False


def collapse_blank_lines(lines: list[str]) -> list[str]:
    """連続する空行を1行にまとめる。"""
    out: list[str] = []
    for line in lines:
        if not line.strip():
            if out and out[-1].strip():
                out.append("")
        else:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def clean_text(text: str) -> str:
    kept = [line for line in text.splitlines() if not should_delete_line(line)]
    kept = collapse_blank_lines(kept)
    return "\n".join(kept) + "\n"


def collect_txt_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise SystemExit(f"入力ディレクトリが見つかりません: {input_dir}")
    files = sorted(input_dir.glob("*.txt"))
    if not files:
        raise SystemExit(f"TXT がありません: {input_dir / '*.txt'}")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="TXT から不要行を除去して保存")
    parser.add_argument(
        "--input",
        default="no-text-txt",
        type=Path,
        help="入力 TXT ディレクトリ（既定: no-text-txt）",
    )
    parser.add_argument(
        "--output",
        default="no-text-txt1",
        type=Path,
        help="出力 TXT ディレクトリ（既定: no-text-txt1）",
    )
    args = parser.parse_args()

    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    txt_files = collect_txt_files(input_dir)
    for n, txt_path in enumerate(txt_files, start=1):
        cleaned = clean_text(txt_path.read_text(encoding="utf-8"))
        out_path = output_dir / txt_path.name
        out_path.write_text(cleaned, encoding="utf-8")
        print(f"[{n}/{len(txt_files)}] {out_path}")

    print(f"完了: {len(txt_files)} 件 → {output_dir}")


if __name__ == "__main__":
    main()
