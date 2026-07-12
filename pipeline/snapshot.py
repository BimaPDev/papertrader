"""Build cleaned per-asset snapshots from indicator-enriched OHLCV.

A snapshot is a plain dict of the latest state — the single shape every
strategy (rule-based or AI) consumes. Keeps the AI prompt compact instead of
dumping raw dataframes at the model.
"""

import math

import pandas as pd

from pipeline.indicators import add_indicators


def _f(x, nd=4):
    """Round, mapping NaN/inf to None so snapshots serialize cleanly."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def build_snapshot(symbol: str, df: pd.DataFrame) -> dict | None:
    """Latest-state snapshot for one asset, or None if data is unusable."""
    if df.empty or len(df) < 30:
        return None
    df = add_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last["close"])
    if price <= 0:
        return None

    snap = {
        "symbol": symbol,
        "kind": df.attrs.get("kind", "unknown"),      # "major" | "meme"
        "price": _f(price, 8),
        "bars": len(df),
        # trend
        "ema12": _f(last["ema12"], 8),
        "ema26": _f(last["ema26"], 8),
        "above_ema50": bool(price > last["ema50"]) if not math.isnan(last["ema50"]) else None,
        "ema_cross_up": bool(prev["ema12"] <= prev["ema26"] and last["ema12"] > last["ema26"]),
        "ema_cross_down": bool(prev["ema12"] >= prev["ema26"] and last["ema12"] < last["ema26"]),
        # oscillators
        "rsi": _f(last["rsi"], 1),
        "macd_hist": _f(last["macd_hist"], 8),
        "macd_hist_prev": _f(prev["macd_hist"], 8),
        # volatility / volume
        "atr_pct": _f(last["atr_pct"], 2),
        "vol_z": _f(last["vol_z"], 2),
        "obv_slope_pos": bool(last["obv_slope"] > 0) if not math.isnan(last["obv_slope"]) else None,
        # structure
        "breakout_20": bool(price >= last["high_20"]) if not math.isnan(last["high_20"]) else None,
        "breakdown_20": bool(price <= last["low_20"]) if not math.isnan(last["low_20"]) else None,
        # momentum
        "mom_6": _f(last["mom_6"], 2),
        "mom_24": _f(last["mom_24"], 2),
        "change_24h": _f((price / df["close"].iloc[-25] - 1) * 100, 2) if len(df) > 25 else None,
    }
    if snap["kind"] == "meme":
        snap["liquidity"] = _f(df.attrs.get("liquidity"), 0)
        snap["volume24h"] = _f(df.attrs.get("volume24h"), 0)
        snap["address"] = df.attrs.get("address")
    return snap


def build_all(markets: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """{symbol: snapshot} for every usable market."""
    snaps = {}
    for symbol, df in markets.items():
        s = build_snapshot(symbol, df)
        if s:
            snaps[symbol] = s
    return snaps
