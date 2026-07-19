"""yfinanceを使う任意・無料の日本株データ取得。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from typing import Any, Callable


class MarketDataError(RuntimeError):
    pass


def normalize_tse_symbol(stock_code: str) -> str:
    code = str(stock_code or "").strip()
    if not code.isdigit() or len(code) != 4:
        raise MarketDataError("証券コードは4桁の数字で入力してください。")
    return f"{code}.T"


def _pick(mapping: Any, *keys: str) -> Any:
    for key in keys:
        try:
            value = mapping.get(key)
        except (AttributeError, KeyError, TypeError):
            value = None
        if value not in (None, ""):
            return value
    return None


def fetch_market_data(stock_code: str, ticker_factory: Callable | None = None) -> dict[str, Any]:
    symbol = normalize_tse_symbol(stock_code)
    if ticker_factory is None:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise MarketDataError("yfinanceがインストールされていません。") from exc
        ticker_factory = yf.Ticker
    def load() -> tuple[Any, Any, Any]:
        ticker = ticker_factory(symbol)
        info = ticker.get_info() or {}
        fast = ticker.fast_info or {}
        history = ticker.history(period="5d", timeout=15)
        return info, fast, history

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        info, fast, history = executor.submit(load).result(timeout=25)
    except FutureTimeoutError as exc:
        raise MarketDataError("株価情報の取得がタイムアウトしました。時間をおいて再度お試しください。") from exc
    except Exception as exc:
        raise MarketDataError("株価情報を取得できませんでした。通信状況や一時的な取得制限を確認してください。") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    price = _pick(fast, "last_price", "lastPrice") or _pick(info, "currentPrice", "regularMarketPrice")
    if price is None and history is not None and not history.empty and "Close" in history:
        price = history["Close"].dropna().iloc[-1] if not history["Close"].dropna().empty else None
    market_cap = _pick(info, "marketCap") or _pick(fast, "market_cap", "marketCap")
    dividend = _pick(info, "dividendYield")
    dividend_pct = None if dividend is None else float(dividend) * 100 if abs(float(dividend)) <= 1 else float(dividend)
    shares = _pick(info, "sharesOutstanding") or _pick(fast, "shares", "sharesOutstanding")
    return {
        "company_name": _pick(info, "longName", "shortName") or "",
        "stock_code": str(stock_code),
        "symbol": symbol,
        "current_price": None if price is None else float(price),
        "market_cap_yen": None if market_cap is None else float(market_cap),
        "per": _pick(info, "trailingPE", "forwardPE"),
        "pbr": _pick(info, "priceToBook"),
        "dividend_yield": dividend_pct,
        "fiscal_year_end": str(_pick(info, "lastFiscalYearEnd", "nextFiscalYearEnd") or ""),
        "industry": _pick(info, "industry", "sector") or "",
        "description_candidate": str(_pick(info, "longBusinessSummary") or "")[:1200],
        "shares_outstanding_million": None if shares is None else float(shares) / 1_000_000,
        "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "yfinance",
    }
