"""無料のEDINET APIとXBRLから過去のPL・報告セグメントを取得する。"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Callable, Any

from edinet_client import EDINETClient, annual_reports
from xbrl_extractor import extract_xbrl


def parse_fiscal_year_end(value: str) -> tuple[int, int]:
    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", str(value))
    if not match:
        return 3, 31
    return int(match.group(1)), int(match.group(2))


def fiscal_period_ends(fiscal_year_end: str, max_years: int, today: date | None = None) -> list[date]:
    today = today or date.today()
    month, day = parse_fiscal_year_end(fiscal_year_end)
    try:
        latest = date(today.year, month, day)
    except ValueError:
        latest = date(today.year, month, 28)
    if latest > today:
        latest = latest.replace(year=latest.year - 1)
    return [latest.replace(year=latest.year - offset) for offset in range(max_years)]


def filing_search_dates(period_end: date) -> list[date]:
    """提出が集中する期末90日後を中心に、60～125日後を効率よく探索する。"""
    offsets = sorted(range(60, 126), key=lambda value: (abs(value - 90), value))
    return [period_end + timedelta(days=offset) for offset in offsets]


def find_annual_report_documents(
    edinet_code: str,
    fiscal_year_end: str,
    max_years: int,
    document_loader: Callable[[date], list[dict[str, Any]]],
    today: date | None = None,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for period_end in fiscal_period_ends(fiscal_year_end, max_years, today=today):
        found: list[dict[str, Any]] = []
        for target_date in filing_search_dates(period_end):
            matches = annual_reports(document_loader(target_date), edinet_code)
            if matches:
                found.extend(matches)
                break
        if found:
            reports.append(max(found, key=lambda row: str(row.get("submitDateTime", ""))))
    return reports


def load_edinet_financials(
    api_key: str,
    edinet_code: str,
    fiscal_year_end: str,
    max_years: int = 5,
    client: EDINETClient | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """有価証券報告書の標準XBRLだけを使い、推測せず取得結果を返す。"""
    if not api_key:
        return {
            "pl_records": [], "segment_records": [], "segment_metrics": [], "documents": [],
            "warnings": ["無料のEDINET APIキーが未設定のため、決算短信PDFだけで解析しました。"],
        }
    client = client or EDINETClient(api_key)
    reports = find_annual_report_documents(
        edinet_code, fiscal_year_end, max_years, client.documents, today=today,
    )
    pl_records: list[dict[str, Any]] = []
    segment_records: list[dict[str, Any]] = []
    documents: list[dict[str, str]] = []
    warnings: list[str] = []
    for report in reports:
        doc_id = str(report.get("docID", ""))
        try:
            values = extract_xbrl(client.xbrl_zip(doc_id))
        except (OSError, ValueError) as exc:
            warnings.append(f"EDINET書類 {doc_id} のXBRLを解析できませんでした：{exc}")
            continue
        fiscal_year = str(report.get("periodEnd") or values.get("period") or "")
        disclosed = str(report.get("submitDateTime", ""))[:10]
        source_url = f"https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100={doc_id}"
        row = {
            "fiscal_year": fiscal_year,
            "result_type": "実績",
            "source": "EDINET XBRL",
            "basis_date": disclosed,
            "source_name": str(report.get("docDescription") or "有価証券報告書"),
            "source_url": source_url,
            "page": None,
        }
        for key in (
            "sales", "cost_of_sales", "gross_profit", "sga_expenses", "operating_profit",
            "non_operating_income", "non_operating_expenses", "ordinary_profit",
            "extraordinary_income", "extraordinary_loss", "pretax_profit", "income_taxes",
            "net_income", "average_shares",
        ):
            row[key] = values.get(key)
        row["reported_eps"] = values.get("eps")
        row["shares_outstanding"] = values.get("average_shares")
        if fiscal_year and any(row.get(key) is not None for key in ("sales", "operating_profit", "net_income")):
            pl_records.append(row)
        for segment in values.get("segments", []):
            if not segment.get("segment_name"):
                continue
            segment_records.append({
                "fiscal_year": str(segment.get("fiscal_year") or fiscal_year),
                "result_type": "実績",
                "segment_name": segment["segment_name"],
                "sales": segment.get("sales"),
                "operating_profit": segment.get("operating_profit"),
                "source": "EDINET XBRL",
                "basis_date": disclosed,
                "note": f"{row['source_name']} {source_url}",
            })
        documents.append({"title": row["source_name"], "url": source_url})
    return {
        "pl_records": sorted(pl_records, key=lambda row: str(row["fiscal_year"])),
        "segment_records": sorted(
            segment_records,
            key=lambda row: (str(row["fiscal_year"]), str(row["segment_name"])),
        ),
        "segment_metrics": [],
        "documents": documents,
        "warnings": warnings,
    }
