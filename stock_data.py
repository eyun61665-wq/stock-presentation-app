"""株価カード用の安全な表示値。無料標準では手入力値を使う。"""
from __future__ import annotations

from calculations import market_capitalization


def valuation_snapshot(
    current_price: float | None,
    shares_million: float | None,
    eps: float | None = None,
    bps: float | None = None,
    annual_dividend: float | None = None,
) -> dict[str, float | None]:
    price = current_price or None
    return {"price": price, "market_cap": market_capitalization(price, shares_million) if price is not None and shares_million is not None else None,
            "per": price / eps if price is not None and eps not in (None, 0) else None,
            "pbr": price / bps if price is not None and bps not in (None, 0) else None,
            "dividend_yield": annual_dividend / price * 100 if price is not None and annual_dividend is not None else None}
