"""アップロードPDFから根拠確認用の原文と数値候補を抽出する。"""
from __future__ import annotations

import re
from typing import Any


class PDFExtractionError(RuntimeError):
    pass


NUMBER_PATTERN = re.compile(r"(?:△|▲|-)?\(?\d[\d,]*(?:\.\d+)?\)?")
UNIT_PATTERN = re.compile(r"(?:単位|金額単位)\s*[:：]?\s*(円|千円|百万円|億円)")
YEAR_PATTERN = re.compile(r"(?:20\d{2}年(?:\d{1,2}月)?期|20\d{2}[./-]\d{1,2}[./-]\d{1,2})")

PL_KEYWORDS = (
    "売上高", "売上原価", "売上総利益", "販売費及び一般管理費", "営業利益", "経常利益",
    "税金等調整前当期純利益", "親会社株主に帰属する当期純利益", "当期純利益",
    "1株当たり当期純利益", "EPS", "業績予想",
)
SEGMENT_KEYWORDS = (
    "報告セグメント", "セグメント情報", "セグメント別", "事業別売上高", "外部顧客への売上高",
    "セグメント利益", "売上収益", "地域別売上高",
)


def parse_candidate_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("△", "-").replace("▲", "-")
    negative = text.startswith("(") and text.endswith(")")
    try:
        number = float(text.strip("()"))
    except ValueError:
        return None
    return -number if negative and number > 0 else number


def extract_pdf_candidates(content: bytes, keywords: tuple[str, ...]) -> dict[str, Any]:
    try:
        import fitz
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise PDFExtractionError("PDFを開けませんでした。ファイルが破損していないか確認してください。") from exc
    candidates: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    total_text = 0
    try:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            total_text += len(text.strip())
            pages.append({"page": page_number, "text": text})
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            unit_match = UNIT_PATTERN.search(text)
            unit = unit_match.group(1) if unit_match else ""
            year_match = YEAR_PATTERN.search(text)
            year = year_match.group(0) if year_match else ""
            for index, line in enumerate(lines):
                matched = next((keyword for keyword in keywords if keyword.lower() in line.lower()), None)
                if not matched:
                    continue
                start, end = max(0, index - 1), min(len(lines), index + 3)
                context = " / ".join(lines[start:end])
                numbers = NUMBER_PATTERN.findall(context)
                if not numbers:
                    candidates.append({
                        "採用": False, "項目": matched, "候補値": None, "単位": unit,
                        "年度": year, "ページ": page_number, "周辺原文": context,
                    })
                for number in numbers[:8]:
                    candidates.append({
                        "採用": False, "項目": matched, "候補値": parse_candidate_number(number),
                        "単位": unit, "年度": year, "ページ": page_number, "周辺原文": context,
                    })
    finally:
        document.close()
    image_pdf = total_text == 0
    return {
        "candidates": candidates,
        "pages": pages,
        "image_pdf": image_pdf,
        "message": "画像として保存されたPDFのため、自動抽出できません。テキスト付きPDFまたは該当ページの確認が必要です。" if image_pdf else "",
    }
