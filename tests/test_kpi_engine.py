import pytest

from kpi_engine import (
    INPUT_AMOUNT,
    INPUT_DIRECT,
    INPUT_RATE,
    apply_kpi_sales_to_pl,
    calculate_forecast,
    contribution_analysis,
    parse_number,
    scenario_value,
    segment_reconciliation,
)


def test_parse_number_handles_commas_blanks_and_negative_values():
    assert parse_number("1,234.5") == 1234.5
    assert parse_number("(200)") == -200
    assert parse_number("") is None
    assert parse_number("不明") is None


def test_kpi_rate_and_amount_methods():
    rate = {"base_value": 100, "input_method": INPUT_RATE, "standard_change_rate": 10}
    amount = {"base_value": 500, "input_method": INPUT_AMOUNT, "standard_change_amount": 100}
    assert scenario_value(rate, "standard") == pytest.approx(110)
    assert scenario_value(amount, "standard") == 600


def test_multiplication_multiple_items_and_three_scenarios():
    kpis = [
        {"kpi_name": "数量", "classification": "数量", "base_value": 10, "input_method": INPUT_DIRECT,
         "bearish_value": 9, "standard_value": 12, "bullish_value": 15},
        {"kpi_name": "単価", "classification": "単価", "base_value": 100, "input_method": INPUT_DIRECT,
         "bearish_value": 100, "standard_value": 110, "bullish_value": 120},
        {"kpi_name": "シェア", "classification": "比率、シェア", "base_value": 50, "input_method": INPUT_DIRECT,
         "bearish_value": 40, "standard_value": 60, "bullish_value": 80},
    ]
    items = [
        {"name": "本体", "kpi_names": ["数量", "単価"]},
        {"name": "対象売上", "kpi_names": ["数量", "単価", "シェア"]},
    ]
    result = calculate_forecast(kpis, items)
    assert result["totals"]["base"] == 1500
    assert result["totals"]["bearish"] == 1260
    assert result["totals"]["standard"] == 2112
    assert result["totals"]["bullish"] == 3240


def test_contribution_and_synergy_add_to_total_increase():
    kpis = [
        {"kpi_name": "数量", "classification": "数量", "base_value": 10, "input_method": INPUT_DIRECT,
         "standard_value": 12},
        {"kpi_name": "単価", "classification": "単価", "base_value": 100, "input_method": INPUT_DIRECT,
         "standard_value": 110},
    ]
    result = contribution_analysis(kpis, [{"name": "売上", "kpi_names": ["数量", "単価"]}])
    assert [row["contribution"] for row in result["contributions"]] == [200, 100]
    assert result["synergy"] == 20
    assert sum(row["contribution"] for row in result["contributions"]) + result["synergy"] == result["total_increase"]


def test_segment_difference():
    assert segment_reconciliation([400, "600"], "1,050")["difference"] == -50


def test_apply_kpi_only_changes_own_forecast():
    records = [
        {"fiscal_year": "2027年3月期", "result_type": "会社予想", "sales": 1000},
        {"fiscal_year": "2027年3月期", "result_type": "自分予想", "sales": 1100},
    ]
    updated = apply_kpi_sales_to_pl(records, "2027年3月期", 1200, "独自予想の営業利益率を入力", margin_rate=10)
    assert next(row for row in updated if row["result_type"] == "会社予想")["sales"] == 1000
    own = next(row for row in updated if row["result_type"] == "自分予想")
    assert own["sales"] == 1200
    assert own["operating_profit"] == 120
