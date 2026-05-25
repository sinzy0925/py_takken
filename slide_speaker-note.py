#!/usr/bin/env python3
"""TXT の内容を Google スライドのスピーカーノートに書き込む。"""

import argparse
import re
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

DEFAULT_PRESENTATION_URL = (
    "https://docs.google.com/presentation/d/"
    "18WxMxSUKceVbsq8lAfDNyH88ZGFBAS5wsDalsBNlncs/edit"
)
DEFAULT_CREDENTIALS = Path("secrets/getstockdata05-service-account.json")
DEFAULT_INPUT_DIR = Path("no-text-txt1")
SCOPES = ("https://www.googleapis.com/auth/presentations",)


def parse_presentation_id(url: str) -> str:
    match = re.search(r"/presentation/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise SystemExit(f"プレゼン ID を URL から取得できません: {url}")
    return match.group(1)


def parse_slide_id_from_url(url: str) -> str | None:
    """URL の slide=id.xxx からスライド objectId を取得（id. なしでも可）。"""
    match = re.search(r"slide=id\.([^&#?]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def normalize_slide_object_id(slide_id: str) -> str:
    return slide_id.removeprefix("id.")


def find_slide_index_by_object_id(slides: list[dict], slide_id: str) -> int:
    target = normalize_slide_object_id(slide_id)
    for i, slide in enumerate(slides):
        if normalize_slide_object_id(slide["objectId"]) == target:
            return i
    raise SystemExit(
        f"URL で指定されたスライドが見つかりません: id.{target} "
        f"（全 {len(slides)} 枚）"
    )


def resolve_slide_offset(slides: list[dict], url: str, slide_start: int) -> int:
    """URL のスライド指定を優先し、なければ --slide-start（1始まり）。"""
    slide_id = parse_slide_id_from_url(url)
    if slide_id is not None:
        index = find_slide_index_by_object_id(slides, slide_id)
        print(
            f"開始スライド: {index + 1} 枚目（objectId=id.{normalize_slide_object_id(slide_id)}）",
            file=sys.stderr,
        )
        return index
    if slide_start < 1:
        raise SystemExit("--slide-start は 1 以上を指定してください。")
    return slide_start - 1


def parse_index_range(index_arg: str) -> list[int]:
    """例: 032-033 → [32, 33]（ファイル名の Capture 番号）。"""
    parts = index_arg.strip().split("-")
    if len(parts) != 2:
        raise SystemExit(
            f"--index の形式が不正です: {index_arg}（例: 032-033）"
        )
    start = int(parts[0])
    end = int(parts[1])
    if start > end:
        raise SystemExit(f"--index の範囲が不正です: {index_arg}")
    return list(range(start, end + 1))


def find_txt_for_capture(input_dir: Path, capture: int) -> Path:
    candidates = sorted(input_dir.glob(f"FireShot Capture {capture:03d}*.txt"))
    if not candidates:
        candidates = sorted(input_dir.glob(f"FireShot Capture {capture} *.txt"))
    if not candidates:
        raise SystemExit(
            f"Capture {capture:03d} に対応する TXT がありません: {input_dir}"
        )
    if len(candidates) > 1:
        print(f"警告: Capture {capture:03d} に複数 TXT があるため先頭を使用: {candidates[0].name}", file=sys.stderr)
    return candidates[0]


def get_notes_text_shape_ids(slide: dict) -> list[str]:
    """ノートページの本文プレースホルダ（空の TEXT_BOX も対象）。"""
    notes_page = slide.get("slideProperties", {}).get("notesPage")
    if not notes_page:
        return []
    body_ids: list[str] = []
    textbox_ids: list[str] = []
    for element in notes_page.get("pageElements", []):
        shape = element.get("shape")
        if not shape:
            continue
        placeholder = shape.get("placeholder", {})
        if placeholder.get("type") == "SLIDE_IMAGE":
            continue
        if placeholder.get("type") == "BODY":
            body_ids.append(element["objectId"])
            continue
        if shape.get("shapeType") == "TEXT_BOX":
            textbox_ids.append(element["objectId"])
    return body_ids or textbox_ids


def shape_has_text(shape_element: dict) -> bool:
    text = shape_element.get("shape", {}).get("text", {})
    for element in text.get("textElements", []):
        if element.get("textRun", {}).get("content", "").strip():
            return True
    return False


def build_replace_notes_requests(
    slide: dict, shape_ids: list[str], text: str
) -> list[dict]:
    notes_page = slide.get("slideProperties", {}).get("notesPage", {})
    elements_by_id = {
        el["objectId"]: el for el in notes_page.get("pageElements", [])
    }
    requests: list[dict] = []
    for shape_id in shape_ids:
        element = elements_by_id.get(shape_id, {})
        if shape_has_text(element):
            requests.append(
                {
                    "deleteText": {
                        "objectId": shape_id,
                        "textRange": {"type": "ALL"},
                    }
                }
            )
        requests.append(
            {
                "insertText": {
                    "objectId": shape_id,
                    "insertionIndex": 0,
                    "text": text,
                }
            }
        )
    return requests


def apply_speaker_notes(
    service,
    presentation_id: str,
    mappings: list[tuple[int, Path]],
    *,
    slide_offset: int = 0,
    slides: list[dict] | None = None,
) -> None:
    if slides is None:
        presentation = (
            service.presentations()
            .get(
                presentationId=presentation_id,
                fields="slides(objectId,slideProperties/notesPage/pageElements)",
            )
            .execute()
        )
        slides = presentation.get("slides", [])
        if not slides:
            raise SystemExit("スライドがありません。")

    requests: list[dict] = []
    for i, (capture, txt_path) in enumerate(mappings):
        slide_index = slide_offset + i
        if slide_index >= len(slides):
            raise SystemExit(
                f"スライド数が足りません: Capture {capture:03d} 用に "
                f"{slide_index + 1} 枚目が必要ですが、プレゼンは {len(slides)} 枚です。"
            )
        slide = slides[slide_index]
        text = txt_path.read_text(encoding="utf-8")
        shape_ids = get_notes_text_shape_ids(slide)
        if not shape_ids:
            raise SystemExit(
                f"スライド {slide_index + 1}（Capture {capture:03d}）にスピーカーノート用の"
                "テキスト枠が見つかりません。"
            )
        requests.extend(build_replace_notes_requests(slide, shape_ids, text))
        print(
            f"スライド {slide_index + 1} 枚目 (id.{normalize_slide_object_id(slide['objectId'])}) "
            f"← {txt_path.name} ({len(text)} 文字)",
            file=sys.stderr,
        )

    if not requests:
        raise SystemExit("更新リクエストがありません。")

    service.presentations().batchUpdate(
        presentationId=presentation_id,
        body={"requests": requests},
    ).execute()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TXT を Google スライドのスピーカーノートに書き込む"
    )
    parser.add_argument(
        "--index",
        required=True,
        help="Capture 番号の範囲（例: 032-034）",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_PRESENTATION_URL,
        help="Google スライドの URL（slide=id.xxx 付きならその枚目から書き込み）",
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_DIR,
        type=Path,
        help="TXT ディレクトリ（既定: no-text-txt1）",
    )
    parser.add_argument(
        "--credentials",
        default=DEFAULT_CREDENTIALS,
        type=Path,
        help="サービスアカウント JSON のパス",
    )
    parser.add_argument(
        "--slide-start",
        default=1,
        type=int,
        help="書き込み開始スライド番号（1始まり）。URL に slide= が無いときのみ有効（既定: 1）",
    )
    args = parser.parse_args()

    credentials_path = args.credentials.resolve()
    if not credentials_path.is_file():
        raise SystemExit(f"認証ファイルが見つかりません: {credentials_path}")

    input_dir = args.input.resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"入力ディレクトリが見つかりません: {input_dir}")

    capture_numbers = parse_index_range(args.index)
    mappings = [(n, find_txt_for_capture(input_dir, n)) for n in capture_numbers]
    presentation_id = parse_presentation_id(args.url)

    creds = service_account.Credentials.from_service_account_file(
        str(credentials_path),
        scopes=SCOPES,
    )
    service = build("slides", "v1", credentials=creds, cache_discovery=False)

    presentation = (
        service.presentations()
        .get(
            presentationId=presentation_id,
            fields="slides(objectId,slideProperties/notesPage/pageElements)",
        )
        .execute()
    )
    slides = presentation.get("slides", [])
    if not slides:
        raise SystemExit("スライドがありません。")

    slide_offset = resolve_slide_offset(slides, args.url, args.slide_start)
    apply_speaker_notes(
        service,
        presentation_id,
        mappings,
        slide_offset=slide_offset,
        slides=slides,
    )
    print(f"完了: {presentation_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
