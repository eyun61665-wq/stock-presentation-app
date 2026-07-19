"""KPIの掛け算による売上予測と簡易寄与分解。"""
from __future__ import annotations

from math import prod
from typing import Any


SCENARIOS = ("bearish", "standard", "bullish")
SCENARIO_LABELS = {"bearish": "弱気", "standard": "標準", "bullish": "強気"}
INPUT_DIRECT = "予想値を直接入力"
INPUT_RATE = "基準値から増減率で計算"
INPUT_AMOUNT = "基準値から増減額で計算"
PERCENT_CLASS = "比率、シェア"


def parse_number(value: Any) -> float | None:
    """カンマ、全角記号、括弧マイナス、%を含む入力を安全に数値化する。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.translate(str.maketrans("０１２３４５６７８９．，－％", "0123456789.,-%"))
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("%", "").replace("△", "-").replace("▲", "-")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return -number if negative and number > 0 else number


def scenario_value(kpi: dict[str, Any], scenario: str) -> float | None:
    base = parse_number(kpi.get("base_value"))
    method = str(kpi.get("input_method") or INPUT_DIRECT)
    if method == INPUT_RATE:
        rate = parse_number(kpi.get(f"{scenario}_change_rate"))
        return None if base is None or rate is None else base * (1 + rate / 100)
    if method == INPUT_AMOUNT:
        amount = parse_number(kpi.get(f"{scenario}_change_amount"))
        return None if base is None or amount is None else base + amount
    return parse_number(kpi.get(f"{scenario}_value"))


def effective_value(kpi: dict[str, Any], value: float | None) -> float | None:
    """比率KPIだけ画面上の%値を内部の小数へ変換する。"""
    if value is None:
        return None
    return value / 100 if str(kpi.get("classification")) == PERCENT_CLASS else value


def revenue_for_item(item: dict[str, Any], kpis: list[dict[str, Any]], scenario: str | None = None) -> float | None:
    names = item.get("kpi_names") or []
    if isinstance(names, str):
        names = [name.strip() for name in names.split(",") if name.strip()]
    lookup = {str(kpi.get("kpi_name", "")).strip(): kpi for kpi in kpis}
    factors: list[float] = []
    for name in names:
        kpi = lookup.get(str(name).strip())
        if not kpi:
            return None
        raw = parse_number(kpi.get("base_value")) if scenario is None else scenario_value(kpi, scenario)
        value = effective_value(kpi, raw)
        if value is None:
            return None
        factors.append(value)
    return prod(factors) if factors else None


def calculate_forecast(kpis: list[dict[str, Any]], revenue_items: list[dict[str, Any]]) -> dict[str, Any]:
    item_results: list[dict[str, Any]] = []
    totals = {"base": 0.0, **{scenario: 0.0 for scenario in SCENARIOS}}
    complete = {key: True for key in totals}
    for item in revenue_items:
        result = {"name": str(item.get("name") or "売上項目")}
        base = revenue_for_item(item, kpis)
        result["base"] = base
        if base is None:
            complete["base"] = False
        else:
            totals["base"] += base
        for scenario in SCENARIOS:
            value = revenue_for_item(item, kpis, scenario)
            result[scenario] = value
            result[f"{scenario}_increase"] = None if base is None or value is None else value - base
            result[f"{scenario}_growth_rate"] = None if base in (None, 0) or value is None else (value / base - 1) * 100
            if value is None:
                complete[scenario] = False
            else:
                totals[scenario] += value
        item_results.append(result)
    normalized_totals = {key: value if complete[key] else None for key, value in totals.items()}
    return {"items": item_results, "totals": normalized_totals}


def contribution_analysis(
    kpis: list[dict[str, Any]], revenue_items: list[dict[str, Any]], scenario: str = "standard"
) -> dict[str, Any]:
    base_total = calculate_forecast(kpis, revenue_items)["totals"]["base"]
    forecast_total = calculate_forecast(kpis, revenue_items)["totals"].get(scenario)
    contributions: list[dict[str, Any]] = []
    for target in kpis:
        target_name = str(target.get("kpi_name") or "").strip()
        changed_kpis = []
        for kpi in kpis:
            copied = dict(kpi)
            if str(kpi.get("kpi_name") or "").strip() == target_name:
                copied[f"{scenario}_value"] = scenario_value(kpi, scenario)
                copied["input_method"] = INPUT_DIRECT
            else:
                copied[f"{scenario}_value"] = parse_number(kpi.get("base_value"))
                copied["input_method"] = INPUT_DIRECT
            changed_kpis.append(copied)
        only_one = calculate_forecast(changed_kpis, revenue_items)["totals"][scenario]
        amount = None if base_total is None or only_one is None else only_one - base_total
        contributions.append({"kpi_name": target_name, "contribution": amount})
    total_increase = None if base_total is None or forecast_total is None else forecast_total - base_total
    known = [row["contribution"] for row in contributions if row["contribution"] is not None]
    synergy = None if total_increase is None else total_increase - sum(known)
    return {
        "base_sales": base_total,
        "forecast_sales": forecast_total,
        "total_increase": total_increase,
        "contributions": contributions,
        "synergy": synergy,
    }


def segment_reconciliation(segment_sales: list[Any], company_sales: Any) -> dict[str, float | None]:
    values = [parse_number(value) for value in segment_sales]
    total = sum(value for value in values if value is not None)
    company = parse_number(company_sales)
    return {"segment_total": total, "company_sales": company, "difference": None if company is None else total - company}


def apply_kpi_sales_to_pl(
    records: list[dict[str, Any]], fiscal_year: str, sales: float | None,
    operating_method: str = "営業利益を直接入力", operating_value: float | None = None,
    margin_rate: float | None = None, incremental_margin_rate: float | None = None,
) -> list[dict[str, Any]]:
    """独自予想だけを更新し、会社予想を変更しない。"""
    output = [dict(row) for row in records]
    target = next(
        (row for row in output if row.get("fiscal_year") == fiscal_year and row.get("result_type") == "自分予想"),
        None,
    )
    if target is None:
        target = {"fiscal_year": fiscal_year, "result_type": "自分予想"}
        output.append(target)
    previous_sales = parse_number(target.get("sales"))
    target["sales"] = sales
    if operating_method == "独自予想の営業利益率を入力" and sales is not None and margin_rate is not None:
        target["operating_profit"] = sales * margin_rate / 100
    elif operating_method == "増収分に限界利益率を掛ける" and sales is not None and incremental_margin_rate is not None:
        base_profit = parse_number(target.get("operating_profit")) or 0
        base_sales = previous_sales or 0
        target["operating_profit"] = base_profit + (sales - base_sales) * incremental_margin_rate / 100
    elif operating_method == "営業利益を直接入力":
        target["operating_profit"] = operating_value
    return output
