from forecast_service import apply_catalyst_to_self_forecast, catalyst_base


def test_catalyst_uses_same_year_company_forecast_and_preserves_blanks():
    entries = [
        {"fiscal_year": "2025.3", "result_type": "実績", "sales": 900, "operating_profit": 90},
        {"fiscal_year": "2026.3", "result_type": "会社予想", "sales": 1000,
         "operating_profit": None, "net_income": None},
    ]
    base, label = catalyst_base(entries, "2026.3")
    assert base["sales"] == 1000
    assert label == "2026.3 会社予想"

    updated, base_sales, _ = apply_catalyst_to_self_forecast(entries, "2026.3", 100, 10)
    own = next(row for row in updated if row["result_type"] == "自分予想")
    company = next(row for row in updated if row["result_type"] == "会社予想")
    assert base_sales == 1000
    assert own["sales"] == 1100
    assert own["operating_profit"] is None
    assert own["shares_outstanding"] == 10
    assert company["operating_profit"] is None


def test_catalyst_falls_back_to_latest_actual():
    entries = [
        {"fiscal_year": "2024.3", "result_type": "実績", "sales": 800},
        {"fiscal_year": "2025.3", "result_type": "実績", "sales": 900},
    ]
    updated, base_sales, label = apply_catalyst_to_self_forecast(entries, "2026.3", 100)
    assert base_sales == 900
    assert label == "2025.3 実績"
    assert next(row for row in updated if row["result_type"] == "自分予想")["sales"] == 1000
