"""CoinGecko fetcher for major crypto (BTC/ETH/SOL/XRP/DOGE).

Uses /market_chart (hourly prices + volumes) and resamples to OHLCV so the
majors share the same frame shape as the Birdeye meme data.
"""

import os
import time

import pandas as pd
import requests
from dotenv import load_dotenv

import config

load_dotenv()

BASE = "https://api.coingecko.com/api/v3"


def _headers() -> dict:
    key = os.getenv("COINGECKO_API_KEY", "")
    return {"x-cg-demo-api-key": key} if key else {}


def ohlcv(coin_id: str, days_back: int = config.DAYS_BACK) -> pd.DataFrame:
    """Hourly OHLCV resampled from market_chart. Empty DataFrame on failure."""
    url = f"{BASE}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days_back}
    resp = requests.get(url, headers=_headers(), params=params, timeout=20)
    if resp.status_code != 200:
        return pd.DataFrame()
    data = resp.json()
    prices = data.get("prices") or []
    volumes = data.get("total_volumes") or []
    if not prices:
        return pd.DataFrame()

    p = pd.DataFrame(prices, columns=["ts", "price"])
    p["ts"] = pd.to_datetime(p["ts"], unit="ms", utc=True)
    p = p.set_index("ts")["price"]

    v = pd.DataFrame(volumes, columns=["ts", "volume"])
    v["ts"] = pd.to_datetime(v["ts"], unit="ms", utc=True)
    v = v.set_index("ts")["volume"]

    o = p.resample("1h").ohlc()
    # CoinGecko total_volumes is a rolling 24h figure; take per-bucket mean as
    # a relative activity proxy (levels aren't comparable to real bar volume,
    # but bar-to-bar changes are what the indicators use)
    o["volume"] = v.resample("1h").mean()
    o = o.dropna(subset=["close"])
    o["volume"] = o["volume"].ffill().fillna(0)
    return o


def fetch_major_markets() -> dict[str, pd.DataFrame]:
    """Fetch candles for every configured major. Returns {symbol: df}."""
    markets = {}
    for coin_id, symbol in config.MAJORS.items():
        df = ohlcv(coin_id)
        if df.empty or len(df) < 30:
            continue
        df.attrs.update(symbol=symbol, coin_id=coin_id, kind="major")
        markets[symbol] = df
        time.sleep(1.5)  # CoinGecko demo tier is ~30 req/min
    return markets
