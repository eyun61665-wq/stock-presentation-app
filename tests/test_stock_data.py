import pytest

from stock_data import valuation_snapshot


def test_valuation_snapshot_uses_bps_and_annual_dividend():
    snapshot = valuation_snapshot(1000, 10, eps=50, bps=500, annual_dividend=25)
    assert snapshot["market_cap"] == 100
    assert snapshot["per"] == 20
    assert snapshot["pbr"] == 2
    assert snapshot["dividend_yield"] == pytest.approx(2.5)
