"""企業に依存しないPL候補の取得と、安全な既存データへの統合。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from company_data_service import fetch_company_data, find_companies
from financial_document_service import load_official_financials, select_recent_pl_records
from ir_source_discovery import discover_ir_source
from jquants_client import JQuantsClient


PL_VALUE_FIELDS = (
    "sales", "cost_of_sales", "gross_profit", "sga_expenses", "operating_profit",
    "ordinary_profit", "net_income", "reported_eps", "shares_outstanding",
)


@dataclass
class PLImportResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    ir_url: str = ""


def _has_financial_value(row: dict[str, Any]) -> bool:
    return any(row.get(key) not in (None, "") for key in PL_VALUE_FIELDS)


def jquants_records(company_data: dict[str, Any]) -> list[dict[str, Any]]:
    """J-Quantsの本決算・会社予想を、部分欠損を許してPL形式へ変換する。"""
    records: list[dict[str, Any]] = []
    for row in company_data.get("financials", []):
        record = {
            "fiscal_year": str(row.get("fiscal_year") or "").strip(),
            "result_type": "実績",
            "sales": row.get("sales"),
            "operating_profit": row.get("operating_profit"),
            "ordinary_profit": row.get("ordinary_profit"),
            "net_income": row.get("net_income"),
            "reported_eps": row.get("eps"),
            "shares_outstanding": row.get("average_shares"),
            "source": "J-Quants財務サマリー",
            "basis_date": str(row.get("disclosed_date") or ""),
        }
        if record["fiscal_year"] and _has_financial_value(record):
            records.append(record)

    forecast = company_data.get("forecast") or {}
    forecast_record = {
        "fiscal_year": str(forecast.get("fiscal_year") or "").strip(),
        "result_type": "会社予想",
        "sales": forecast.get("forecast_sales"),
        "operating_profit": forecast.get("forecast_operating_profit"),
        "ordinary_profit": forecast.get("forecast_ordinary_profit"),
        "net_income": forecast.get("forecast_net_income"),
        "reported_eps": forecast.get("forecast_eps"),
        "shares_outstanding": forecast.get("average_shares"),
        "source": "J-Quants会社予想",
        "basis_date": str(forecast.get("disclosed_date") or ""),
    }
    if forecast_record["fiscal_year"] and _has_financial_value(forecast_record):
        records.append(forecast_record)
    return select_recent_pl_records(records, max_actual_years=3)


def merge_candidates(
    primary: list[dict[str, Any]], supplement: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """同じ年度・区分の候補を統合し、primaryの根拠ある値を優先する。"""
    by_period: dict[tuple[str, str], dict[str, Any]] = {}
    for row in supplement:
        key = (str(row.get("fiscal_year") or ""), str(row.get("result_type") or "実績"))
        by_period[key] = dict(row)
    for row in primary:
        key = (str(row.get("fiscal_year") or ""), str(row.get("result_type") or "実績"))
        merged = dict(by_period.get(key, {}))
        for field_name, value in row.items():
            if value not in (None, ""):
                merged[field_name] = value
        by_period[key] = merged
    return select_recent_pl_records(list(by_period.values()), max_actual_years=3)


def merge_with_existing(
    existing: list[dict[str, Any]],
    imported: list[dict[str, Any]],
    *,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """空欄だけを補完する。明示時のみ、取得値で保存済み値を更新する。"""
    by_period = {
        (str(row.get("fiscal_year") or ""), str(row.get("result_type") or "実績")): dict(row)
        for row in existing
    }
    for candidate in imported:
        key = (
            str(candidate.get("fiscal_year") or ""),
            str(candidate.get("result_type") or "実績"),
        )
        current = dict(by_period.get(key, {}))
        for field_name, value in candidate.items():
            if value in (None, ""):
                continue
            if overwrite or current.get(field_name) in (None, ""):
                current[field_name] = value
        current["fiscal_year"], current["result_type"] = key
        by_period[key] = current
    return list(by_period.values())


def acquire_pl_candidates(
    stock_code: str,
    company_name: str,
    *,
    saved_ir_url: str = "",
    jquants_api_key: str | None = None,
    refresh: bool = False,
    discover: Callable[..., dict[str, str]] = discover_ir_source,
    official_loader: Callable[..., dict[str, Any]] = load_official_financials,
    client_factory: Callable[[str], Any] = JQuantsClient,
) -> PLImportResult:
    """公式決算資料を優先し、任意のJ-Quantsで不足を補う。"""
    result = PLImportResult(ir_url=str(saved_ir_url or ""))
    official_records: list[dict[str, Any]] = []
    jquants_rows: list[dict[str, Any]] = []

    try:
        if not result.ir_url:
            discovered = discover(stock_code, company_name, refresh=refresh)
            result.ir_url = str(discovered.get("url") or "")
        if result.ir_url:
            official = official_loader(result.ir_url, max_years=3, refresh=refresh)
            official_records = select_recent_pl_records(official.get("pl_records", []), 3)
            result.warnings.extend(str(value) for value in official.get("warnings", []) if value)
            if official_records:
                result.sources.append("企業公式決算資料")
    except Exception as exc:  # 外部サイトの失敗で手入力画面を止めない。
        result.warnings.append(f"企業公式資料からPLを取得できませんでした：{exc}")

    if jquants_api_key:
        try:
            client = client_factory(jquants_api_key)
            candidates = find_companies(client, stock_code)
            code = "".join(char for char in str(stock_code) if char.isdigit())[:4]
            master = next(
                (row for row in candidates if str(row.get("stock_code") or "")[:4] == code),
                candidates[0] if candidates else None,
            )
            if master:
                jquants_rows = jquants_records(fetch_company_data(client, master))
                if jquants_rows:
                    result.sources.append("J-Quants")
            else:
                result.warnings.append("J-Quantsの企業マスターに該当銘柄がありませんでした。")
        except Exception as exc:  # 認証・通信失敗でも公式資料と手入力を利用できる。
            result.warnings.append(f"J-QuantsからPLを取得できませんでした：{exc}")

    result.records = merge_candidates(official_records, jquants_rows)
    if not result.records:
        result.warnings.append(
            "PL候補を自動取得できませんでした。決算資料PDFから候補を探すか、表へ直接入力してください。"
        )
    return result
