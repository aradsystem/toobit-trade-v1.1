import pandas as pd
import numpy as np


def enrich_ohlcv(rows):
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df

    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI14"] = 100 - (100 / (1 + rs))

    # MACD 12,26,9
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

    # Bollinger 20, 2
    mid = df["close"].rolling(20).mean()
    std = df["close"].rolling(20).std()
    df["BB_mid"] = mid
    df["BB_upper"] = mid + 2 * std
    df["BB_lower"] = mid - 2 * std

    df["ROC10"] = df["close"].pct_change(10) * 100
    return df


def snapshot_from_df(df) -> dict:
    """آخرین مقادیر اندیکاتور برای ذخیره در لاگ ورود"""
    if df is None or df.empty:
        return {}
    last = df.iloc[-1]
    def f(k):
        v = last.get(k)
        try:
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return None
            return float(v)
        except Exception:
            return None
    return {
        "close": f("close"),
        "RSI14": f("RSI14"),
        "MACD": f("MACD"),
        "MACD_signal": f("MACD_signal"),
        "MACD_hist": f("MACD_hist"),
        "BB_upper": f("BB_upper"),
        "BB_mid": f("BB_mid"),
        "BB_lower": f("BB_lower"),
        "EMA20": f("EMA20"),
        "EMA50": f("EMA50"),
    }
