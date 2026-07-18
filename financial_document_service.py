"""公式IRの本決算短信からPL・セグメント候補を組み立てる。"""
from __future__ import annotations

import re
from typing import Any

from data_sources import DataSourceError, download_pdf, fetch_ir_documents
from financial_parser import parse_detailed_pl, parse_segment_statements
from pdf_financial_extractor import extract_pdf_pages


EXCLUDED_ANNUAL_TITLE_WORDS = (
    "第1四半期", "第１四半期", "第2四半期", "第２四半期", "第3四半期", "第３四半期",
    "中間期", "業績予想", "差異", "修正", "訂正", "説明会", "有価証券報告書",
)


def select_annual_documents(documents: list[dict[str, str]], max_documents: int = 6) -> list[dict[str, str]]:
    """IR一覧から本決算短信だけを新しい順のまま抽出する。"""
    selected: list[dict[str, str]] = []
    for document in documents:
        title = " ".join(str(document.get("title", "")).split())
        if "決算短信" not in title or any(word in title for word in EXCLUDED_ANNUAL_TITLE_WORDS):
            continue
        # 「YYYY年M月期 決算短信」「通期決算短信」「平成...期 決算短信」を許容する。
        # 「2026年2月期決算短信」のように「通期」という語がない公式IRもある。
        # 第1～3四半期・中間期は EXCLUDED_ANNUAL_TITLE_WORDS で除外済み。
        if "通期" not in title and not re.search(r"(?:20\d{2}年|平成\d+年).{0,8}期.*決算短信", title):
            continue
        selected.append({"title": title, "url": document["url"]})
        if len(selected) >= max_documents:
            break
    return selected


def parse_financial_document(
    content: bytes,
    source_name: str,
    source_url: str,
    retrieved_at: str,
) -> dict[str, list[dict[str, Any]]]:
    pages = extract_pdf_pages(content)
    if not pages or not any(page.strip() for page in pages):
        return {"pl_records": [], "segment_records": [], "warnings": [f"{source_name}は画像PDFまたは文字抽出できないPDFです。"]}
    return {
        "pl_records": parse_detailed_pl(pages, source_name, source_url, retrieved_at),
        "segment_records": parse_segment_statements(pages, source_name, source_url, retrieved_at),
        "warnings": [],
    }


def load_official_financials(
    ir_url: str,
    max_years: int = 5,
    refresh: bool = False,
) -> dict[str, Any]:
    """公式IR一覧から必要な年数が集まるまで本決算短信を解析する。"""
    documents = select_annual_documents(fetch_ir_documents(ir_url, refresh=refresh), max_documents=max_years + 2)
    if not documents:
        return {"pl_records": [], "segment_records": [], "documents": [], "warnings": ["本決算短信を見つけられませんでした。"]}

    pl_by_year: dict[str, dict[str, Any]] = {}
    segments_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    used_documents: list[dict[str, str]] = []
    warnings: list[str] = []
    for document in documents:
        try:
            content, metadata = download_pdf(document["url"], refresh=refresh)
        except (DataSourceError, OSError, ValueError):
            # 公式IRでは古いPDFだけ移動・削除されていることがある。
            # 1件の失敗で全年度の取得を止めず、残りの資料を続けて確認する。
            warnings.append(
                f"「{document['title']}」を取得できませんでした。別の年度の資料を続けて確認しました。"
            )
            continue
        parsed = parse_financial_document(
            content,
            document["title"],
            document["url"],
            metadata["retrieved_at"],
        )
        if parsed["pl_records"] or parsed["segment_records"]:
            used_documents.append(document)
        warnings.extend(parsed["warnings"])
        # IRページは新しい順。後から古い短信を読んでも同年度を上書きしない。
        for row in parsed["pl_records"]:
            pl_by_year.setdefault(str(row["fiscal_year"]), row)
        for row in parsed["segment_records"]:
            key = (str(row["fiscal_year"]), str(row["segment_name"]))
            segments_by_key.setdefault(key, row)
        if len(pl_by_year) >= max_years:
            break

    selected_years = sorted(pl_by_year)[-max_years:]
    pl_records = [pl_by_year[year] for year in selected_years]
    segment_records = [
        row for key, row in segments_by_key.items() if key[0] in selected_years
    ]
    segment_records.sort(key=lambda row: (str(row["fiscal_year"]), str(row["segment_name"])))
    return {
        "pl_records": pl_records,
        "segment_records": segment_records,
        "documents": used_documents,
        "warnings": warnings,
    }


def merge_imported_pl(existing: list[dict], imported: list[dict]) -> list[dict]:
    """確認済みの実績年度だけを差し替え、予想と他年度を残す。"""
    by_key = {(str(row["fiscal_year"]), str(row.get("result_type", "実績"))): dict(row) for row in existing}
    for row in imported:
        by_key[(str(row["fiscal_year"]), "実績")] = dict(row)
    return [by_key[key] for key in sorted(by_key)]


def merge_imported_segments(existing: list[dict], imported: list[dict]) -> list[dict]:
    """取得した実績年度だけを入れ替え、手入力した予想セグメントを残す。"""
    imported_years = {str(row["fiscal_year"]) for row in imported}
    kept = [
        dict(row) for row in existing
        if str(row.get("result_type", "実績")) != "実績" or str(row["fiscal_year"]) not in imported_years
    ]
    return [*kept, *[dict(row) for row in imported]]
