#!/usr/bin/env python3
"""問題文の直後に、末尾の正解番号表から取り出した答えを挿入する。"""

import argparse
import re
import sys
from pathlib import Path

# 本文の問題見出し
QUESTION_BODY_RE = re.compile(r"【問\s*([０-９\d]+)\s*】")

# 正解番号表の行
ANSWER_KEY_QUESTION_RE = re.compile(r"^問[\s　]*([０-９\d]+)\s*$")
ANSWER_DIGIT_RE = re.compile(r"^[１-４1-4]$")
ANSWER_SECTION_SKIP_RE = re.compile(
    r"正解番号表|試験問題の正解|合格判定|◆|受験|番号|^―|年度問題|受験者"
)
# ページ区切り（例: — 1 —、― 13 ―）
PAGE_MARKER_RE = re.compile(
    r"^[—―－\-]\s*[０-９\d]+\s*[—―－\-]\s*$"
)

FULLWIDTH_DIGIT = str.maketrans("1234", "１２３４")
FULLWIDTH_TO_ASCII = str.maketrans("０１２３４５６７８９", "0123456789")


def to_int(s: str) -> int:
    return int(s.translate(FULLWIDTH_TO_ASCII))


def to_fullwidth_answer(d: str) -> str:
    return d.strip().translate(FULLWIDTH_DIGIT)


def find_answer_section_start(lines: list[str]) -> int:
    """正解番号表ブロック（問　１ から始まる表）の開始行インデックス。"""
    last_candidate: int | None = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not ANSWER_KEY_QUESTION_RE.match(s):
            continue
        if to_int(ANSWER_KEY_QUESTION_RE.match(s).group(1)) != 1:
            continue
        # 本文の【問】より後ろにある「問　１」だけを対象
        if not any("【問" in lines[j] for j in range(i)):
            continue
        # 直後に続けて「問　２」等が並ぶ表形式か確認
        following = [lines[j].strip() for j in range(i, min(i + 12, len(lines)))]
        question_labels = sum(
            1 for t in following if ANSWER_KEY_QUESTION_RE.match(t)
        )
        if question_labels >= 3:
            last_candidate = i
    if last_candidate is None:
        raise SystemExit("正解番号表の開始位置を特定できませんでした。")
    return last_candidate


def remove_page_markers(lines: list[str]) -> list[str]:
    """— N — 形式のページ区切り行を除去する。"""
    return [line for line in lines if not PAGE_MARKER_RE.match(line.strip())]


def parse_answer_key(lines: list[str]) -> dict[int, str]:
    """正解番号表から {問題番号: 答え} を抽出する。"""
    question_nums: list[int] = []
    answer_digits: list[str] = []

    for line in lines:
        s = line.strip()
        if not s or ANSWER_SECTION_SKIP_RE.search(s):
            continue
        m = ANSWER_KEY_QUESTION_RE.match(s)
        if m:
            question_nums.append(to_int(m.group(1)))
            continue
        if ANSWER_DIGIT_RE.match(s):
            answer_digits.append(s)

    if len(question_nums) != len(answer_digits):
        raise SystemExit(
            f"正解番号表の解析に失敗しました（問 {len(question_nums)} 件、答え {len(answer_digits)} 件）"
        )
    return {
        q: to_fullwidth_answer(a) for q, a in zip(question_nums, answer_digits)
    }


def merge_answers(body: str, answers: dict[int, str]) -> str:
    """各【問　N　】ブロックの末尾に「答え　N」を挿入する。"""
    matches = list(QUESTION_BODY_RE.finditer(body))
    if not matches:
        raise SystemExit("本文に【問　N　】が見つかりませんでした。")

    parts: list[str] = []
    parts.append(body[: matches[0].start()])

    for i, m in enumerate(matches):
        qnum = to_int(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[m.start() : end].rstrip()
        answer = answers.get(qnum)
        if answer is None:
            print(f"警告: 問{qnum} の答えがありません", file=sys.stderr)
        else:
            block += f"\n\n\n答え　{answer}\n\n\n"
        parts.append(block)

    return "".join(parts)


def build_answer_section(answer_lines: list[str]) -> str:
    """末尾に残す正解番号表ブロック（元の行をそのまま保持）。"""
    lines = remove_page_markers(answer_lines)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def process_file(input_path: Path) -> str:
    text = input_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    split_at = find_answer_section_start(lines)
    body_lines = remove_page_markers(lines[:split_at])
    answer_lines = lines[split_at:]

    body = "\n".join(body_lines)
    if body_lines:
        body += "\n"

    answers = parse_answer_key(answer_lines)
    result = merge_answers(body, answers).rstrip("\n")

    answer_section = build_answer_section(answer_lines)
    if answer_section:
        result += "\n\n" + answer_section.rstrip("\n")
    return result + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="正解番号表の答えを各問題の直後に挿入する"
    )
    parser.add_argument("--input", required=True, type=Path, help="入力 .txt ファイル")
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.is_file():
        raise SystemExit(f"ファイルが見つかりません: {input_path}")

    output = process_file(input_path)
    input_path.write_text(output, encoding="utf-8")
    print(input_path)


if __name__ == "__main__":
    main()
