"""Birdeye fetchers: trending Solana meme coin discovery + OHLCV candles."""

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

import config

load_dotenv()

BASE = "https://public-api.birdeye.so"
HEADERS = {"X-API-KEY": os.getenv("BIRDEYE_API_KEY", ""), "x-chain": "solana"}


def trending_meme_tokens(count: int = config.MEME_TOKEN_COUNT) -> list[dict]:
    """Return top trending Solana tokens passing liquidity/volume filters.

    Each entry: {"address", "symbol", "name", "liquidity", "volume24h"}.
    """
    url = f"{BASE}/defi/token_trending"
    params = {"sort_by": "volume24hUSD", "sort_type": "desc", "offset": 0, "limit": 50}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    tokens = resp.json().get("data", {}).get("tokens", []) or []

    picked = []
    for t in tokens:
        addr = t.get("address", "")
        liq = t.get("liquidity") or 0
        vol = t.get("volume24hUSD") or 0
        if addr in config.EXCLUDED_TOKENS:
            continue
        if liq < config.MIN_LIQUIDITY_USD or vol < config.MIN_VOLUME_24H_USD:
            continue
        picked.append({
            "address": addr,
            "symbol": t.get("symbol") or addr[:6],
            "name": t.get("name") or "",
            "liquidity": liq,
            "volume24h": vol,
        })
        if len(picked) >= count:
            break
    return picked


def ohlcv(token_address: str, days_back: int = config.DAYS_BACK,
          timeframe: str = config.CANDLE_TIMEFRAME) -> pd.DataFrame:
    """Fetch OHLCV candles for a Solana token. Columns: open/high/low/close/volume,
    DatetimeIndex in UTC. Empty DataFrame on failure."""
    now = datetime.now(timezone.utc)
    params = {
        "address": token_address,
        "type": timeframe,
        "time_from": int((now - timedelta(days=days_back)).timestamp()),
        "time_to": int(now.timestamp()),
    }
    resp = requests.get(f"{BASE}/defi/ohlcv", headers=HEADERS, params=params, timeout=15)
    if resp.status_code != 200:
        return pd.DataFrame()
    items = resp.json().get("data", {}).get("items", []) or []
    if not items:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "ts": datetime.fromtimestamp(i["unixTime"], tz=timezone.utc),
        "open": i["o"], "high": i["h"], "low": i["l"], "close": i["c"],
        "volume": i["v"],
    } for i in items]).set_index("ts").sort_index()
    return df


def fetch_meme_markets() -> dict[str, pd.DataFrame]:
    """Discover trending memes and fetch candles for each.
    Returns {symbol: df} with df.attrs carrying token metadata."""
    markets = {}
    for tok in trending_meme_tokens():
        df = ohlcv(tok["address"])
        if df.empty or len(df) < 30:
            continue
        df.attrs.update(tok, kind="meme")
        markets[tok["symbol"]] = df
        time.sleep(0.4)  # stay under Birdeye rate limits
    return markets
