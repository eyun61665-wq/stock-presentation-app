import pandas as pd
import pytest

from market_data import MarketDataError, fetch_market_data, normalize_tse_symbol


def test_normalize_tse_symbol():
    assert normalize_tse_symbol("6651") == "6651.T"
    with pytest.raises(MarketDataError):
        normalize_tse_symbol("ABC")


def test_fetch_market_data_with_mock_ticker():
    class Ticker:
        fast_info = {"last_price": 1000}

        def get_info(self):
            return {"longName": "テスト株式会社", "marketCap": 10_000_000_000,
                    "trailingPE": 20, "priceToBook": 1.5, "dividendYield": 0.02,
                    "sharesOutstanding": 10_000_000}

        def history(self, **_kwargs):
            return pd.DataFrame({"Close": [990, 1000]})

    result = fetch_market_data("6651", ticker_factory=lambda _symbol: Ticker())
    assert result["current_price"] == 1000
    assert result["dividend_yield"] == 2
    assert result["shares_outstanding_million"] == 10
