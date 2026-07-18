"""銘柄発表MVPの計算処理。

金額は百万円、株式数は百万株、株価・EPSは円、率は画面入力どおりの％で受け取る。
"""
from __future__ import annotations


def safe_divide(numerator: float, denominator: float) -> float | None:
    return None if denominator in (None, 0) else numerator / denominator


def sales_growth_rate(current_sales: float, prior_sales: float) -> float | None:
    return safe_divide(current_sales - prior_sales, prior_sales)


def operating_margin(operating_profit: float, sales: float) -> float | None:
    return safe_divide(operating_profit, sales)


def eps(net_income_million_yen: float, average_shares_million: float) -> float | None:
    """純利益（百万円）÷平均株式数（百万株）= EPS（円）。"""
    return safe_divide(net_income_million_yen, average_shares_million)


def market_capitalization(current_price_yen: float, shares_outstanding_million: float) -> float:
    """時価総額（億円）= 株価（円）×発行済株式数（百万株）÷100。"""
    return current_price_yen * shares_outstanding_million / 100


def catalyst_additional_sales(target_count: float, target_rate_percent: float,
                              capture_rate_percent: float, price_per_case_million: float) -> float:
    """追加売上高（百万円）。率は50なら50％として扱う。"""
    return target_count * target_rate_percent / 100 * capture_rate_percent / 100 * price_per_case_million


def catalyst_sales_from_rate(base_sales_million: float, impact_rate_percent: float) -> float:
    """基準売上（百万円）×影響率（％）から追加売上を計算する。"""
    return base_sales_million * impact_rate_percent / 100


def scenario_calculation(base_sales: float, existing_sales_growth_percent: float,
                         catalyst_sales: float, operating_margin_percent: float,
                         effective_tax_rate_percent: float, average_shares_million: float,
                         per: float) -> dict[str, float | None]:
    existing_sales = base_sales * (1 + existing_sales_growth_percent / 100)
    forecast_sales = existing_sales + catalyst_sales
    operating_profit = forecast_sales * operating_margin_percent / 100
    net_income = operating_profit * (1 - effective_tax_rate_percent / 100)
    forecast_eps = eps(net_income, average_shares_million)
    target_price = None if forecast_eps is None else forecast_eps * per
    return {"existing_sales": existing_sales, "forecast_sales": forecast_sales,
            "operating_profit": operating_profit, "net_income": net_income,
            "eps": forecast_eps, "target_price": target_price}
