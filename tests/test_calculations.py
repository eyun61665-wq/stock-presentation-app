import pytest

from calculations import (catalyst_additional_sales, catalyst_price_impact, catalyst_sales_from_rate, eps, market_capitalization,
                          operating_margin, scenario_calculation)


def test_eps_uses_million_yen_and_million_shares():
    assert eps(500, 10) == pytest.approx(50)


def test_market_capitalization_in_hundred_million_yen():
    assert market_capitalization(1_000, 10) == pytest.approx(100)


def test_operating_margin():
    assert operating_margin(200, 2_000) == pytest.approx(0.1)


def test_catalyst_additional_sales_uses_percent_input():
    assert catalyst_additional_sales(1_000, 50, 10, 2) == pytest.approx(100)


def test_catalyst_sales_from_base_sales_rate():
    assert catalyst_sales_from_rate(2_000, 5) == pytest.approx(100)


def test_catalyst_price_impact_is_shown_as_percent():
    result = catalyst_price_impact(100, 20, 30, 10, 20, 1_000)
    assert result["eps_uplift"] == pytest.approx(1.4)
    assert result["price_uplift"] == pytest.approx(28)
    assert result["impact_percent"] == pytest.approx(2.8)


def test_target_price():
    result = scenario_calculation(2_000, 0, 0, 10, 0, 10, 20)
    assert result["eps"] == pytest.approx(20)
    assert result["target_price"] == pytest.approx(400)


def test_eps_50_and_per_20_is_target_price_1000():
    result = scenario_calculation(5_000, 0, 0, 10, 0, 10, 20)
    assert result["eps"] == pytest.approx(50)
    assert result["target_price"] == pytest.approx(1_000)
