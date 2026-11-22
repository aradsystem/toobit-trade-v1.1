"""
اسکنر بازار Toobit USDT-M
- 24h: سریع از ticker (change و volume)
- 1h / 12h / 1w: روی نمادهای پرحجم، از OHLCV محاسبه می‌شود (کندتر)
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def _usdt_swap_symbols(markets: Dict[str, Any]) -> List[str]:
    out = []
    for symbol, market in markets.items():
        if not market.get("swap"):
            continue
        if market.get("quote") != "USDT":
            continue
        if not market.get("active", True):
            continue
        out.append(symbol)
    return out


def _safe_float(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _get_markets_and_tickers(exchange):
    """پشتیبانی از ExchangeService و خود ccxt"""
    if hasattr(exchange, "public_tickers"):
        markets = exchange.load_markets()
        tickers = exchange.public_tickers()
    else:
        markets = exchange.load_markets()
        tickers = exchange.fetch_tickers()
    return markets, tickers


def scan_from_tickers(
    exchange,
    sort_by: str = "change_desc",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    اسکن سریع ۲۴ ساعته از fetch_tickers
    sort_by:
      change_desc | change_asc | volume_desc | volume_asc
    """
    markets, tickers = _get_markets_and_tickers(exchange)
    rows: List[Dict[str, Any]] = []

    for symbol in _usdt_swap_symbols(markets):
        t = tickers.get(symbol) or {}
        change = _safe_float(t.get("percentage"))
        volume = _safe_float(t.get("quoteVolume"))
        last = _safe_float(t.get("last"))
        high = _safe_float(t.get("high"))
        low = _safe_float(t.get("low"))
        if last is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "last": last,
                "change": change,
                "quoteVolume": volume,
                "high": high,
                "low": low,
                "period": "24h",
                "source": "ticker",
            }
        )

    key_map = {
        "change_desc": (lambda r: r["change"] if r["change"] is not None else float("-inf"), True),
        "change_asc": (lambda r: r["change"] if r["change"] is not None else float("inf"), False),
        "volume_desc": (lambda r: r["quoteVolume"] if r["quoteVolume"] is not None else -1, True),
        "volume_asc": (lambda r: r["quoteVolume"] if r["quoteVolume"] is not None else float("inf"), False),
    }
    key_fn, reverse = key_map.get(sort_by, key_map["change_desc"])
    rows.sort(key=key_fn, reverse=reverse)
    return rows[: max(1, int(limit))]


def _period_to_ohlcv(period: str) -> tuple[str, int]:
    """
    برمی‌گرداند (timeframe, تعداد کندل لازم برای پوشش تقریبی دوره)
    """
    mapping = {
        "1h": ("15m", 8),    # ~2 ساعت برای پایداری
        "4h": ("1h", 6),
        "12h": ("1h", 14),
        "24h": ("1h", 26),
        "1d": ("1h", 26),
        "1w": ("4h", 45),    # ~7.5 روز
        "week": ("4h", 45),
    }
    return mapping.get(period, ("1h", 26))


def _change_from_ohlcv(rows: List[List[float]]) -> Optional[float]:
    if not rows or len(rows) < 2:
        return None
    first_close = _safe_float(rows[0][4])
    last_close = _safe_float(rows[-1][4])
    if first_close is None or last_close is None or first_close == 0:
        return None
    return ((last_close - first_close) / first_close) * 100.0


def _momentum_roc(rows: List[List[float]], lookback: int = 4) -> Optional[float]:
    """Rate of Change ساده روی close"""
    if not rows or len(rows) <= lookback:
        return None
    older = _safe_float(rows[-1 - lookback][4])
    newer = _safe_float(rows[-1][4])
    if older is None or newer is None or older == 0:
        return None
    return ((newer - older) / older) * 100.0


def scan_period(
    exchange,
    period: str = "24h",
    sort_by: str = "change_desc",
    limit: int = 50,
    universe_size: int = 80,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    برای دوره‌های غیر ۲۴ساعته:
    ابتدا پرحجم‌ترین نمادها را از ticker می‌گیرد،
    سپس OHLCV می‌گیرد و درصد تغییر / مومنتوم را حساب می‌کند.
    """
    if period in ("24h", "1d") and sort_by in (
        "change_desc",
        "change_asc",
        "volume_desc",
        "volume_asc",
    ):
        return scan_from_tickers(exchange, sort_by=sort_by, limit=limit)

    # پایه: پرحجم‌ترین‌ها برای کاهش تعداد درخواست
    liquid = scan_from_tickers(exchange, sort_by="volume_desc", limit=universe_size)
    tf, bars = _period_to_ohlcv(period)
    results: List[Dict[str, Any]] = []

    for i, base in enumerate(liquid):
        symbol = base["symbol"]
        if progress_cb and i % 10 == 0:
            progress_cb(f"در حال دریافت OHLCV ({i+1}/{len(liquid)}) …")
        try:
            # ExchangeService.ohlcv یا ccxt مستقیم
            if hasattr(exchange, "ohlcv"):
                ohlcv = exchange.ohlcv(symbol, timeframe=tf, limit=bars)
            else:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=bars)
        except Exception:
            continue
        change = _change_from_ohlcv(ohlcv)
        mom = _momentum_roc(ohlcv, lookback=min(4, max(1, len(ohlcv) // 3)))
        last = base.get("last")
        if ohlcv:
            last = _safe_float(ohlcv[-1][4], last)
        high = max((_safe_float(r[2], last) or last) for r in ohlcv) if ohlcv else base.get("high")
        low = min((_safe_float(r[3], last) or last) for r in ohlcv) if ohlcv else base.get("low")
        results.append(
            {
                "symbol": symbol,
                "last": last,
                "change": change,
                "momentum": mom,
                "quoteVolume": base.get("quoteVolume"),
                "high": high,
                "low": low,
                "period": period,
                "source": f"ohlcv:{tf}",
            }
        )

    key_map = {
        "change_desc": (lambda r: r["change"] if r.get("change") is not None else float("-inf"), True),
        "change_asc": (lambda r: r["change"] if r.get("change") is not None else float("inf"), False),
        "volume_desc": (lambda r: r["quoteVolume"] if r.get("quoteVolume") is not None else -1, True),
        "volume_asc": (lambda r: r["quoteVolume"] if r.get("quoteVolume") is not None else float("inf"), False),
        "momentum_desc": (lambda r: r["momentum"] if r.get("momentum") is not None else float("-inf"), True),
        "momentum_asc": (lambda r: r["momentum"] if r.get("momentum") is not None else float("inf"), False),
    }
    key_fn, reverse = key_map.get(sort_by, key_map["change_desc"])
    results.sort(key=key_fn, reverse=reverse)
    return results[: max(1, int(limit))]


# سازگاری با نسخه قبلی
def top_movers(exchange, limit: int = 100):
    return scan_from_tickers(exchange, sort_by="change_desc", limit=limit)
