"""Technical indicators computed on OHLCV frames (pure pandas, no TA-Lib)."""

import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with indicator columns appended."""
    out = df.copy()
    close, high, low, vol = out["close"], out["high"], out["low"], out["volume"]

    out["ema12"] = close.ewm(span=12, adjust=False).mean()
    out["ema26"] = close.ewm(span=26, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()

    # RSI(14), Wilder smoothing
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = 100 - 100 / (1 + rs)

    # MACD
    macd = out["ema12"] - out["ema26"]
    out["macd"] = macd
    out["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = macd - out["macd_signal"]

    # ATR(14) as % of price — volatility gauge
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    out["atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False).mean() / close * 100

    # OBV and its 10-bar slope (volume-pressure confirmation)
    obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
    out["obv"] = obv
    out["obv_slope"] = obv.diff(10)

    # Volume z-score vs 20-bar window
    vmean = vol.rolling(20).mean()
    vstd = vol.rolling(20).std()
    out["vol_z"] = (vol - vmean) / vstd.replace(0, np.nan)

    # Rolling extremes for breakout logic
    out["high_20"] = high.rolling(20).max()
    out["low_20"] = low.rolling(20).min()

    # Momentum: % change over 6 and 24 bars
    out["mom_6"] = close.pct_change(6) * 100
    out["mom_24"] = close.pct_change(24) * 100

    return out
