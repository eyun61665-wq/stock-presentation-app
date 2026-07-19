import pytest

from mvp_ui import _five_period_records, _pl_calculated, _segment_matrix


def test_pl_calculated_handles_profit_rates_and_zero_sales():
    calculated = _pl_calculated({
        "sales": "2,000", "cost_of_sales": 1200, "sga_expenses": 500,
        "operating_profit": 200, "ordinary_profit": 180, "net_income": 100,
    })
    assert calculated["gross_profit"] == 800
    assert calculated["gross_margin"] == 40
    assert calculated["operating_margin"] == 10
    assert _pl_calculated({"sales": 0, "operating_profit": 10})["operating_margin"] is None


def test_five_periods_always_keep_three_actual_and_two_forecasts():
    periods = _five_period_records([
        {"fiscal_year": "2026年3月期", "result_type": "実績", "sales": 100},
    ])
    assert len(periods) == 5
    assert [row["result_type"] for row in periods] == ["実績", "実績", "実績", "会社予想", "自分予想"]
    assert periods[-2].get("sales") is None


def test_segment_matrix_has_company_and_own_forecast_columns_even_when_blank():
    matrix = _segment_matrix([
        {"fiscal_year": "2026年3月期", "result_type": "実績", "segment_name": "製造", "sales": 80, "display_order": 1},
        {"fiscal_year": "2026年3月期", "result_type": "実績", "segment_name": "サービス", "sales": 20, "display_order": 2},
    ])
    assert "会社予想 会社予想" in matrix.columns
    assert "独自予想 自分予想" in matrix.columns
    assert matrix.iloc[0]["2026年3月期 実績"] == pytest.approx(100)
