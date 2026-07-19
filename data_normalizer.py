"""J-Quants APIデータをアプリの単位・表示形式へ正規化する。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
import re
import unicodedata


def normalize_code(code: str | int) -> str:
    digits = "".join(char for char in str(code) if char.isdigit())
    if len(digits) == 4:
        return f"{digits}0"
    if len(digits) == 5:
        return digits
    raise ValueError("証券コードは4桁または5桁で入力してください。")


def display_code(code: str | int) -> str:
    return normalize_code(code)[:4]


def to_million_yen(value: Any) -> float | None:
    return None if value in (None, "") else float(value) / 1_000_000


def to_million_shares(value: Any) -> float | None:
    return None if value in (None, "") else float(value) / 1_000_000


def value_of(row: dict, *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def normalize_master_row(row: dict) -> dict:
    code = value_of(row, "Code", "code")
    return {"code": normalize_code(code), "stock_code": display_code(code),
            "company_name": value_of(row, "CoName", "CompanyName", "company_name") or "",
            "company_name_en": value_of(row, "CoNameEn", "CompanyNameEnglish", "company_name_en") or "",
            # V2ではコードと名称が別フィールドの場合がある。文章生成には名称を優先する。
            "market": value_of(row, "MktNm", "MarketName", "MarketSegment", "Mkt", "MarketCode") or "",
            "sector17": value_of(row, "S17Nm", "Sector17Name", "Sector17", "S17", "Sector17Code") or "",
            "sector33": value_of(row, "S33Nm", "Sector33Name", "Sector33", "S33", "Sector33Code") or ""}


def canonical_company_name(value: str) -> str:
    """全半角空白・大小文字・株式会社表記の揺れを吸収して比較する。"""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = re.sub(r"\s+", "", text)
    return text.replace("株式会社", "").replace("(株)", "").replace("（株）", "")


def search_companies(master_rows: list[dict], query: str) -> list[dict]:
    term = canonical_company_name(query)
    if not term:
        return []
    matches = []
    for raw in master_rows:
        row = normalize_master_row(raw)
        if term.isdigit():
            try:
                if row["code"] == normalize_code(term) or row["stock_code"] == term:
                    matches.append(row)
            except ValueError:
                pass
        elif term in canonical_company_name(row["company_name"]) or term in canonical_company_name(row["company_name_en"]):
            matches.append(row)
    return matches


def _disclosure_key(row: dict) -> tuple[str, str, str]:
    return (
        str(value_of(row, "DiscDate", "DisclosedDate", "PubDate") or ""),
        str(value_of(row, "DiscTime", "DisclosedTime", "PubTime") or ""),
        str(value_of(row, "DiscNo", "RefNo", "DisclosedNumber") or ""),
    )


def extract_latest_daily_bar(rows: list[dict]) -> dict | None:
    available = [row for row in rows if value_of(row, "C", "Close") not in (None, "")]
    return max(available, key=lambda row: str(value_of(row, "Date", "date") or "")) if available else None


def extract_latest_fy_records(rows: list[dict], limit: int = 5) -> list[dict]:
    """FYの本決算だけを年度末ごとに最新開示へ絞る。"""
    latest: dict[str, dict] = {}
    for row in rows:
        if value_of(row, "CurPerType") != "FY":
            continue
        doc_type = str(value_of(row, "DocType") or "")
        if doc_type and "FinancialStatements" not in doc_type:
            continue
        period_end = str(value_of(row, "CurPerEn", "CurrentPeriodEndDate") or "")
        if not period_end:
            continue
        if period_end not in latest or _disclosure_key(row) > _disclosure_key(latest[period_end]):
            latest[period_end] = row
    return sorted(latest.values(), key=lambda row: str(value_of(row, "CurPerEn", "CurrentPeriodEndDate")))[-limit:]


def extract_latest_forecast(rows: list[dict]) -> dict | None:
    # V2では会社予想が FSales/FOP/FOdP/FNP/FEPS の短縮名で返る。
    # 旧レスポンス名も残し、既存の保存データやテストとの互換性を保つ。
    forecast_keys = (
        "FSales", "FOP", "FOdP", "FNP", "FEPS",
        "NxFSales", "NxFOP", "NxFOdP", "NxFNP", "NxFEPS",
        "FSSales", "ForecastSales", "FSOperatingProfit",
        "ForecastOperatingProfit", "FSProfit", "ForecastProfit",
    )
    candidates = [
        row for row in rows
        if any(value_of(row, key) not in (None, "") for key in forecast_keys)
    ]
    return max(candidates, key=_disclosure_key) if candidates else None


def normalize_financial_record(row: dict) -> dict:
    issued = to_million_shares(
        value_of(row, "ShOutFY", "TotalNumberOfIssuedShares", "IssuedShares")
    )
    treasury = to_million_shares(
        value_of(row, "TrShFY", "TreasuryStock", "NumberOfTreasuryStock")
    )
    net_issued = issued - treasury if issued is not None and treasury is not None else issued
    return {"fiscal_year": str(value_of(row, "CurPerEn", "CurrentPeriodEndDate") or ""),
            "disclosed_date": str(value_of(row, "DiscDate", "DisclosedDate", "PubDate") or ""),
            "sales": to_million_yen(value_of(row, "NetSales", "Sales")),
            "operating_profit": to_million_yen(value_of(row, "OP", "OperatingProfit")),
            "ordinary_profit": to_million_yen(value_of(row, "OdP", "OrdinaryProfit")),
            "net_income": to_million_yen(value_of(row, "NP", "Profit", "NetIncome")),
            "eps": value_of(row, "EarningsPerShare", "EPS"),
            "issued_shares": issued, "treasury_shares": treasury, "net_issued_shares": net_issued,
            "average_shares": to_million_shares(value_of(row, "AvgSh", "AverageNumberOfShares", "AverageShares")),
            "shares_source": "発行済株式数－自己株式数" if issued is not None and treasury is not None else "発行済株式数",
            "bps": value_of(row, "BPS", "BookValuePerShare"),
            "annual_dividend": value_of(row, "DivAnn", "DivFY", "AnnualDividendPerShare"),
            "forecast_sales": to_million_yen(value_of(row, "FSales", "NxFSales", "FSSales", "ForecastSales")),
            "forecast_operating_profit": to_million_yen(value_of(row, "FOP", "NxFOP", "FSOperatingProfit", "ForecastOperatingProfit")),
            "forecast_ordinary_profit": to_million_yen(value_of(row, "FOdP", "NxFOdP")),
            "forecast_net_income": to_million_yen(value_of(row, "FNP", "NxFNP", "FSProfit", "ForecastProfit")),
            "forecast_eps": value_of(row, "FEPS", "NxFEPS", "FSEarningsPerShare", "ForecastEarningsPerShare"),
            "forecast_annual_dividend": value_of(row, "FDivAnn", "NxFDivAnn", "FDivFY")}


def retrieved_at() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
