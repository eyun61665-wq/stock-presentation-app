"""取得・検索・業務用データへの組み立てを担当するサービス層。"""
from __future__ import annotations

from calculations import market_capitalization
from data_normalizer import (extract_latest_daily_bar, extract_latest_forecast, extract_latest_fy_records,
                             normalize_financial_record, normalize_master_row, retrieved_at, search_companies, value_of)
from jquants_client import JQuantsClient


def find_companies(client: JQuantsClient, query: str) -> list[dict]:
    return search_companies(client.equities_master(), query)


def should_show_no_results(search_clicked: bool, api_succeeded: bool, candidates: list[dict]) -> bool:
    """通信エラーを「該当なし」と誤表示しないためのUI判断。"""
    return search_clicked and api_succeeded and not candidates


def find_project_by_code(projects: list[dict], code: str) -> dict | None:
    """4桁・5桁表記を吸収し、保存済みプロジェクトを更新順で探す。"""
    digits = "".join(char for char in str(code) if char.isdigit())
    if len(digits) not in (4, 5):
        return None
    display = digits[:4]
    return next(
        (
            project
            for project in projects
            if "".join(char for char in str(project.get("stock_code", "")) if char.isdigit())[:4]
            == display
        ),
        None,
    )


def build_pl_import_records(financials: list[dict]) -> list[dict]:
    """正規化済み本決算から、PL保存前の確認用レコードを作る。"""
    records = []
    for row in financials:
        required = ("fiscal_year", "sales", "operating_profit", "net_income", "average_shares")
        if any(row.get(key) in (None, "") for key in required):
            continue
        records.append({
            "fiscal_year": row["fiscal_year"],
            "result_type": "実績",
            "sales": row["sales"],
            "operating_profit": row["operating_profit"],
            "ordinary_profit": row.get("ordinary_profit"),
            "net_income": row["net_income"],
            "average_shares": row["average_shares"],
            "eps": row.get("eps"),
            "source": "J-Quants",
            "basis_date": row.get("disclosed_date", ""),
        })
    return records


def build_company_forecast_record(forecast: dict | None) -> dict | None:
    """J-Quantsの最新会社予想を、空欄を補完せずPL保存形式へ変換する。"""
    if not forecast:
        return None
    fiscal_year = str(forecast.get("fiscal_year") or "").strip()
    if not fiscal_year:
        return None
    values = {
        "sales": forecast.get("forecast_sales"),
        "operating_profit": forecast.get("forecast_operating_profit"),
        "ordinary_profit": forecast.get("forecast_ordinary_profit"),
        "net_income": forecast.get("forecast_net_income"),
        "shares_outstanding": forecast.get("average_shares"),
    }
    if not any(value not in (None, "") for value in values.values()):
        return None
    return {
        "fiscal_year": fiscal_year,
        "result_type": "会社予想",
        **values,
        "source": "J-Quants",
        "basis_date": str(forecast.get("disclosed_date") or ""),
    }


def fetch_company_data(client: JQuantsClient, master: dict) -> dict:
    code = master["code"]
    daily = extract_latest_daily_bar(client.daily_bars(code))
    financials = client.financial_summary(code)
    fy_records = [normalize_financial_record(row) for row in extract_latest_fy_records(financials)]
    forecast_raw = extract_latest_forecast(financials)
    forecast = normalize_financial_record(forecast_raw) if forecast_raw else None
    latest_fy = fy_records[-1] if fy_records else None
    close = float(value_of(daily, "C", "Close")) if daily else None
    shares = latest_fy["net_issued_shares"] if latest_fy else None
    market_cap = market_capitalization(close, shares) if close is not None and shares is not None else None
    current_per = close / float(latest_fy["eps"]) if close is not None and latest_fy and latest_fy["eps"] not in (None, 0, "0") else None
    forecast_per = close / float(forecast["forecast_eps"]) if close is not None and forecast and forecast["forecast_eps"] not in (None, 0, "0") else None
    bps = latest_fy.get("bps") if latest_fy else None
    annual_dividend = latest_fy.get("annual_dividend") if latest_fy else None
    pbr = close / float(bps) if close is not None and bps not in (None, 0, "0") else None
    dividend_yield = float(annual_dividend) / close * 100 if close not in (None, 0) and annual_dividend not in (None, "") else None
    return {"master": master, "price": {"close": close, "date": value_of(daily or {}, "Date", "date"), "retrieved_at": retrieved_at()},
            "financials": fy_records, "forecast": forecast, "share_info": latest_fy,
            "calculated": {"market_cap": market_cap, "current_per": current_per, "forecast_per": forecast_per,
                           "pbr": pbr, "dividend_yield": dividend_yield}}
