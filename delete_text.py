#!/usr/bin/env python3
"""TXT からページ区切りマーカー・ページ番号行・書籍フッターを除去する。"""

import argparse
import re
from pathlib import Path

LEFT_PAGE_MARKER_RE = re.compile(r"^---\s*左ページ\s*---\s*$")
RIGHT_PAGE_MARKER_RE = re.compile(r"^---\s*右ページ\s*---\s*$")
RIGHT_PAGE_REPLACEMENT = "----------"
SPLIT_BLOCK = ["", "", RIGHT_PAGE_REPLACEMENT, "", ""]
SENTENCE_END_RE = re.compile(r"。\s*$")
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
    if LEFT_PAGE_MARKER_RE.match(s):
        return True
    if DIGIT_ONLY_LINE_RE.match(s):
        return True
    if CHAPTER_FOOTER_RE.match(s):
        return True
    if MOCK_EXAM_FOOTER_RE.match(s):
        return True
    return False


def _line_end_offsets(lines: list[str]) -> list[int]:
    """各行末（改行の直前）の文字位置。空リストなら []。"""
    offsets: list[int] = []
    pos = 0
    for i, line in enumerate(lines):
        pos += len(line)
        offsets.append(pos)
        if i < len(lines) - 1:
            pos += 1
    return offsets


def find_mid_sentence_split(lines: list[str]) -> int | None:
    """前半／後半の中央付近で、句点で終わる行の直後に分割する行インデックスを返す。"""
    if len(lines) < 2:
        return None
    offsets = _line_end_offsets(lines)
    mid = offsets[-1] // 2
    candidates: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if SENTENCE_END_RE.search(line.rstrip()):
            candidates.append((abs(offsets[i] - mid), i))
    if not candidates:
        return None
    return min(candidates)[1]


def insert_at(lines: list[str], after_index: int, block: list[str]) -> list[str]:
    return lines[: after_index + 1] + block + lines[after_index + 1 :]


def insert_quarter_splits(lines: list[str]) -> list[str]:
    """右ページ区切りの前後半を、それぞれ中央付近の句点でさらに分割する。"""
    sep_indices = [
        i for i, line in enumerate(lines) if line.strip() == RIGHT_PAGE_REPLACEMENT
    ]
    if len(sep_indices) != 1:
        return lines
    sep = sep_indices[0]
    before = lines[:sep]
    after = lines[sep + 1 :]

    insertions: list[tuple[int, list[str]]] = []
    split_before = find_mid_sentence_split(before)
    if split_before is not None:
        insertions.append((split_before, SPLIT_BLOCK))
    split_after = find_mid_sentence_split(after)
    if split_after is not None:
        insertions.append((sep + 1 + split_after, SPLIT_BLOCK))

    for index, block in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines = insert_at(lines, index, block)
    return lines


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


def process_line(line: str) -> str | None:
    s = line.strip()
    if RIGHT_PAGE_MARKER_RE.match(s):
        return RIGHT_PAGE_REPLACEMENT
    if should_delete_line(line):
        return None
    return line


def clean_text(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        processed = process_line(line)
        if processed is not None:
            kept.append(processed)
    kept = collapse_blank_lines(kept)
    kept = insert_quarter_splits(kept)
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
