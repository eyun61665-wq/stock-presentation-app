"""公式IRの本決算短信からPL・セグメント候補を組み立てる。"""
from __future__ import annotations

import re
from typing import Any

from data_sources import DataSourceError, download_pdf, fetch_ir_documents
from financial_parser import parse_detailed_pl, parse_segment_statements
from free_financial_parser import extract_pdf_layout, parse_layout_tables
from pdf_financial_extractor import extract_pdf_pages
from ai_financial_parser import AIFinancialParserError, OpenAIFinancialParser
from gemini_financial_parser import GeminiFinancialParser, GeminiFinancialParserError


EXCLUDED_ANNUAL_TITLE_WORDS = (
    "第1四半期", "第１四半期", "第2四半期", "第２四半期", "第3四半期", "第３四半期",
    "中間期", "業績予想", "差異", "修正", "訂正", "説明会", "有価証券報告書",
)

# 決算短信には詳細PL、決算説明資料にはセグメントが載ることが多い。
# どちらも年次資料として扱い、実際に数値を抽出できた資料だけを反映候補にする。
ANNUAL_FINANCIAL_TITLE_WORDS = (
    "決算短信",
    "決算説明資料",
    "決算補足資料",
    "決算概要",
)


def _period_key(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"(20\d{2})[^0-9]+(\d{1,2})", text)
    return f"{int(match.group(1)):04d}.{int(match.group(2))}" if match else text


def select_annual_documents(documents: list[dict[str, str]], max_documents: int = 6) -> list[dict[str, str]]:
    """IR一覧から本決算短信だけを新しい順のまま抽出する。"""
    selected: list[dict[str, str]] = []
    for document in documents:
        title = " ".join(str(document.get("title", "")).split())
        if (not any(word in title for word in ANNUAL_FINANCIAL_TITLE_WORDS)
                or any(word in title for word in EXCLUDED_ANNUAL_TITLE_WORDS)):
            continue
        # 「YYYY年M月期 決算短信・説明資料」「通期決算資料」を許容する。
        # 「2026年2月期決算説明資料」のように「通期」という語がない公式IRもある。
        # 第1～3四半期・中間期は EXCLUDED_ANNUAL_TITLE_WORDS で除外済み。
        if "通期" not in title and not re.search(
            r"(?:20\d{2}年|平成\d+年).{0,8}期.*決算(?:短信|説明資料|補足資料|概要)", title
        ):
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
    pages = extract_pdf_pages(content, max_pages=60)
    layouts = extract_pdf_layout(content, max_pages=60)
    if not pages or not any(page.strip() for page in pages):
        return {"pl_records": [], "segment_records": [], "segment_metrics": [], "single_segment_name": "", "warnings": [f"{source_name}は画像PDFまたは文字抽出できないPDFです。"]}
    combined_text = "\n".join(pages)
    single_match = re.search(
        r"(?:当社(?:グループ)?は)?\s*([^\n。、]{2,40}?事業)の単一セグメント",
        combined_text,
    )
    single_segment_name = ""
    if single_match:
        single_segment_name = re.sub(
            r"^当社(?:グループ)?は\s*", "", single_match.group(1)
        ).strip()
    standard = {
        "pl_records": parse_detailed_pl(pages, source_name, source_url, retrieved_at),
        "segment_records": parse_segment_statements(pages, source_name, source_url, retrieved_at),
        "segment_metrics": [],
        "single_segment_name": single_segment_name,
        "warnings": [],
    }
    layout_result = parse_layout_tables(layouts, source_name, source_url, retrieved_at)
    return _merge_free_results(standard, layout_result)


def _merge_free_results(primary: dict[str, Any], supplement: dict[str, Any]) -> dict[str, Any]:
    """従来解析を優先し、表レイアウト解析で空欄と未取得行を補う。"""
    result = dict(primary)

    pl_by_key = {
        (_period_key(row.get("fiscal_year")), str(row.get("result_type", "実績"))): dict(row)
        for row in primary.get("pl_records", [])
    }
    for row in supplement.get("pl_records", []):
        key = (_period_key(row.get("fiscal_year")), str(row.get("result_type", "実績")))
        current = pl_by_key.get(key, {})
        for field, value in row.items():
            if current.get(field) in (None, "") and value not in (None, ""):
                current[field] = value
        pl_by_key[key] = current
    result["pl_records"] = [pl_by_key[key] for key in sorted(pl_by_key)]

    segment_by_key = {
        (_period_key(row.get("fiscal_year")), str(row.get("result_type", "実績")), str(row.get("segment_name"))): dict(row)
        for row in primary.get("segment_records", [])
    }
    for row in supplement.get("segment_records", []):
        key = (_period_key(row.get("fiscal_year")), str(row.get("result_type", "実績")), str(row.get("segment_name")))
        current = segment_by_key.get(key, {})
        for field, value in row.items():
            if current.get(field) in (None, "") and value not in (None, ""):
                current[field] = value
        segment_by_key[key] = current
    result["segment_records"] = [segment_by_key[key] for key in sorted(segment_by_key)]

    metric_by_key = {
        (_period_key(row.get("fiscal_year")), str(row.get("result_type", "実績")), str(row.get("row_label"))): dict(row)
        for row in primary.get("segment_metrics", [])
    }
    for row in supplement.get("segment_metrics", []):
        key = (_period_key(row.get("fiscal_year")), str(row.get("result_type", "実績")), str(row.get("row_label")))
        metric_by_key.setdefault(key, dict(row))
    result["segment_metrics"] = [metric_by_key[key] for key in sorted(metric_by_key)]
    return result


def load_official_financials(
    ir_url: str,
    max_years: int = 3,
    refresh: bool = False,
) -> dict[str, Any]:
    """公式IR一覧から必要な年数が集まるまで本決算短信を解析する。"""
    documents = select_annual_documents(
        fetch_ir_documents(ir_url, refresh=refresh), max_documents=max_years * 2 + 2
    )
    if not documents:
        return {"pl_records": [], "segment_records": [], "segment_metrics": [], "documents": [], "warnings": ["本決算短信を見つけられませんでした。"]}

    pl_by_year: dict[str, dict[str, Any]] = {}
    presentation_pl_by_year: dict[str, dict[str, Any]] = {}
    segments_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    metrics_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
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
        if parsed["pl_records"] or parsed["segment_records"] or parsed.get("segment_metrics"):
            used_documents.append(document)
        warnings.extend(parsed["warnings"])
        # IRページは新しい順。後から古い短信を読んでも同年度を上書きしない。
        # 実績PLは決算短信だけを採用する。説明資料にある翌期計画を実績と混同しない。
        title_month_match = re.search(r"20\d{2}年\s*(\d{1,2})月期", document["title"])
        title_month = int(title_month_match.group(1)) if title_month_match else None
        if "決算短信" in document["title"]:
            for row in parsed["pl_records"]:
                if str(row.get("result_type", "実績")) == "実績":
                    period = _period_key(row["fiscal_year"])
                    period_match = re.search(r"20\d{2}\.(\d{1,2})", period)
                    if title_month and period_match and int(period_match.group(1)) != title_month:
                        continue
                    normalized = dict(row)
                    normalized["fiscal_year"] = period
                    pl_by_year.setdefault(period, normalized)
        else:
            # 説明資料のPLは、短信で確定した同一年度の空欄補完にだけ利用する。
            for row in parsed["pl_records"]:
                if str(row.get("result_type", "実績")) != "実績":
                    continue
                period = _period_key(row["fiscal_year"])
                period_match = re.search(r"20\d{2}\.(\d{1,2})", period)
                if title_month and period_match and int(period_match.group(1)) != title_month:
                    continue
                normalized = dict(row)
                normalized["fiscal_year"] = period
                current = presentation_pl_by_year.setdefault(period, {})
                current.update({key: value for key, value in normalized.items() if value not in (None, "")})
        for row in parsed["segment_records"]:
            normalized = dict(row)
            normalized["fiscal_year"] = _period_key(row["fiscal_year"])
            key = (normalized["fiscal_year"], str(row["segment_name"]))
            segments_by_key.setdefault(key, normalized)
        if parsed.get("single_segment_name") and not parsed["segment_records"]:
            segment_name = str(parsed["single_segment_name"])
            for row in parsed["pl_records"]:
                if str(row.get("result_type", "実績")) != "実績" or row.get("sales") in (None, ""):
                    continue
                normalized = {
                    "fiscal_year": _period_key(row["fiscal_year"]),
                    "result_type": "実績",
                    "segment_name": segment_name,
                    "sales": row.get("sales"),
                    "operating_profit": row.get("operating_profit"),
                    "source": "決算短信PDF（単一セグメント）",
                    "note": str(row.get("source_url") or ""),
                }
                key = (normalized["fiscal_year"], segment_name)
                segments_by_key.setdefault(key, normalized)
        for row in parsed.get("segment_metrics", []):
            normalized = dict(row)
            normalized["fiscal_year"] = _period_key(row["fiscal_year"])
            key = (
                normalized["fiscal_year"], str(row.get("result_type", "実績")),
                str(row["row_label"]),
            )
            metrics_by_key.setdefault(key, normalized)
    selected_years = sorted(pl_by_year)[-max_years:]
    if not selected_years:
        selected_years = sorted({key[0] for key in segments_by_key})[-max_years:]
    pl_records: list[dict[str, Any]] = []
    for year in selected_years:
        record = dict(pl_by_year[year])
        supplement = presentation_pl_by_year.get(year, {})
        for field, value in supplement.items():
            if record.get(field) in (None, "") and value not in (None, ""):
                record[field] = value
        pl_records.append(record)
    segment_records = [
        row for key, row in segments_by_key.items() if key[0] in selected_years
    ]
    segment_records.sort(key=lambda row: (str(row["fiscal_year"]), str(row["segment_name"])))
    return {
        "pl_records": pl_records,
        "segment_records": segment_records,
        "segment_metrics": [
            metrics_by_key[key] for key in sorted(metrics_by_key)
            if not selected_years or key[0] in selected_years
        ],
        "documents": used_documents,
        "warnings": warnings,
    }


def _load_official_financials_with_parser(
    ir_url: str,
    parser: OpenAIFinancialParser | GeminiFinancialParser,
    provider_label: str,
    max_years: int = 3,
    refresh: bool = False,
) -> dict[str, Any]:
    """通常解析で不足した公開PDFをAIへ送り、検証可能な候補を返す。"""
    documents = select_annual_documents(
        fetch_ir_documents(ir_url, refresh=refresh),
        max_documents=max_years + 2,
    )
    if not documents:
        return {
            "pl_records": [], "segment_records": [], "segment_metrics": [],
            "documents": [], "warnings": ["AI解析対象の本決算資料を見つけられませんでした。"],
        }
    pl_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    segments_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    metrics_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    used_documents: list[dict[str, str]] = []
    warnings: list[str] = []
    for document in documents:
        try:
            content, _metadata = download_pdf(document["url"], refresh=refresh)
            parsed = parser.parse_pdf(content, document["title"], document["url"])
        except (DataSourceError, OSError, ValueError, AIFinancialParserError, GeminiFinancialParserError) as exc:
            warnings.append(f"「{document['title']}」の{provider_label}解析を完了できませんでした：{exc}")
            continue
        if parsed["pl_records"] or parsed["segment_records"] or parsed["segment_metrics"]:
            used_documents.append(document)
        warnings.extend(parsed.get("warnings", []))
        for row in parsed["pl_records"]:
            key = (str(row["fiscal_year"]), str(row.get("result_type", "実績")))
            pl_by_key.setdefault(key, row)
        for row in parsed["segment_records"]:
            key = (
                str(row["fiscal_year"]), str(row.get("result_type", "実績")),
                str(row["segment_name"]),
            )
            segments_by_key.setdefault(key, row)
        for row in parsed["segment_metrics"]:
            key = (
                str(row["fiscal_year"]), str(row.get("result_type", "実績")),
                str(row["row_label"]),
            )
            metrics_by_key.setdefault(key, row)
        actual_years = {key[0] for key in pl_by_key if key[1] == "実績"}
        if len(actual_years) >= max_years:
            break
    return {
        "pl_records": [pl_by_key[key] for key in sorted(pl_by_key)],
        "segment_records": [segments_by_key[key] for key in sorted(segments_by_key)],
        "segment_metrics": [metrics_by_key[key] for key in sorted(metrics_by_key)],
        "documents": used_documents,
        "warnings": warnings,
    }


def load_official_financials_with_ai(
    ir_url: str,
    api_key: str,
    model: str,
    max_years: int = 3,
    refresh: bool = False,
) -> dict[str, Any]:
    """不足した公開PDFをOpenAIで補助解析する後方互換入口。"""
    return _load_official_financials_with_parser(
        ir_url, OpenAIFinancialParser(api_key, model=model), "OpenAI",
        max_years=max_years, refresh=refresh,
    )


def load_official_financials_with_gemini(
    ir_url: str,
    api_key: str,
    model: str,
    max_years: int = 3,
    refresh: bool = False,
) -> dict[str, Any]:
    """不足した公開PDFをGemini無料枠で補助解析する。"""
    return _load_official_financials_with_parser(
        ir_url, GeminiFinancialParser(api_key, model=model), "Gemini",
        max_years=max_years, refresh=refresh,
    )


def select_recent_pl_records(records: list[dict], max_actual_years: int = 3) -> list[dict]:
    """実績は直近N期に絞り、会社予想・自分予想は残す。"""
    actual_years = sorted({
        str(row.get("fiscal_year", "")) for row in records
        if str(row.get("result_type", "実績")) == "実績"
    })[-max_actual_years:]
    return [
        dict(row) for row in records
        if str(row.get("result_type", "実績")) != "実績"
        or str(row.get("fiscal_year", "")) in actual_years
    ]


def merge_imported_pl(existing: list[dict], imported: list[dict]) -> list[dict]:
    """取得した実績値を統合し、空欄で手入力値を消さない。"""
    by_key = {(str(row["fiscal_year"]), str(row.get("result_type", "実績"))): dict(row) for row in existing}
    for row in imported:
        result_type = str(row.get("result_type", "実績"))
        key = (str(row["fiscal_year"]), result_type)
        merged = dict(by_key.get(key, {}))
        merged.update({field: value for field, value in row.items() if value not in (None, "")})
        merged["fiscal_year"] = str(row["fiscal_year"])
        merged["result_type"] = result_type
        by_key[key] = merged
    return [by_key[key] for key in sorted(by_key)]


def merge_imported_segments(existing: list[dict], imported: list[dict]) -> list[dict]:
    """取得した実績年度だけを入れ替え、手入力した予想セグメントを残す。"""
    imported_years = {str(row["fiscal_year"]) for row in imported}
    kept = [
        dict(row) for row in existing
        if str(row.get("result_type", "実績")) != "実績" or str(row["fiscal_year"]) not in imported_years
    ]
    return [*kept, *[dict(row) for row in imported]]


def merge_imported_segment_metrics(existing: list[dict], imported: list[dict]) -> list[dict]:
    """取得した実績年度のKPIだけを更新し、手入力した予想KPIを残す。"""
    imported_years = {str(row["fiscal_year"]) for row in imported}
    kept = [
        dict(row) for row in existing
        if str(row.get("result_type", "実績")) != "実績" or str(row["fiscal_year"]) not in imported_years
    ]
    return [*kept, *[dict(row) for row in imported]]
