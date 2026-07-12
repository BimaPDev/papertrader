"""Pump.fun discovery: recently graduated tokens (+ near-graduation watchlist).

Uses the public advanced analytics feed for graduates (no API key) and the
frontend coins list for bonding-curve tokens approaching completion.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

import config

GRADUATED_URL = "https://advanced-api-v2.pump.fun/coins/graduated"
FRONTEND_COINS_URL = "https://frontend-api-v3.pump.fun/coins"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PaperTraderGraduationMonitor/1.0",
}


def _ms_to_iso(ms) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _age_minutes(ms) -> float | None:
    if ms is None:
        return None
    try:
        then = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 60.0


def fetch_graduated(limit: int | None = None) -> list[dict]:
    """Return recently graduated pump.fun coins, newest first.

    Each dict: mint, symbol, name, market_cap, volume, holders, graduation_at,
    age_minutes, sniper_pct, top_holders_pct, dev_holdings_pct, pool_address,
    twitter, telegram, website, source.
    """
    limit = limit or config.MONITOR_FETCH_LIMIT
    resp = requests.get(GRADUATED_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    coins = resp.json().get("coins") or []

    out = []
    for c in coins[:limit]:
        mint = c.get("coinMint") or ""
        if not mint or mint in config.EXCLUDED_TOKENS:
            continue
        grad_ms = c.get("graduationDate")
        age = _age_minutes(grad_ms)
        mcap = float(c.get("marketCap") or 0)
        holders = int(c.get("numHolders") or 0)
        sniper_pct = float(c.get("sniperOwnedPercentage") or 0)
        top_pct = float(c.get("topHoldersPercentage") or 0)
        dev_pct = float(c.get("devHoldingsPercentage") or 0)
        volume = float(c.get("volume") or 0)

        if age is not None and age > config.MONITOR_MAX_AGE_MINUTES:
            continue
        if mcap < config.MONITOR_MIN_MARKET_CAP_USD:
            continue
        if holders < config.MONITOR_MIN_HOLDERS:
            continue
        if sniper_pct > config.MONITOR_MAX_SNIPER_PCT:
            continue
        if top_pct > config.MONITOR_MAX_TOP_HOLDERS_PCT:
            continue
        if dev_pct > config.MONITOR_MAX_DEV_HOLDINGS_PCT:
            continue

        out.append({
            "mint": mint,
            "symbol": c.get("ticker") or mint[:6],
            "name": c.get("name") or "",
            "kind": "migrated",
            "market_cap": mcap,
            "volume": volume,
            "price": float(c.get("currentMarketPrice") or 0) or None,
            "holders": holders,
            "sniper_count": int(c.get("sniperCount") or 0),
            "sniper_pct": sniper_pct,
            "top_holders_pct": top_pct,
            "dev_holdings_pct": dev_pct,
            "graduation_at": _ms_to_iso(grad_ms),
            "created_at": _ms_to_iso(c.get("creationTime")),
            "age_minutes": round(age, 2) if age is not None else None,
            "pool_address": c.get("poolAddress"),
            "twitter": c.get("twitter") or None,
            "telegram": c.get("telegram") or None,
            "website": c.get("website") or None,
            "image_url": c.get("imageUrl") or None,
            "source": "pumpfun_migrated",
            "pumpfun_url": f"https://pump.fun/coin/{mint}",
            "gmgn_url": f"https://gmgn.ai/sol/token/{mint}",
        })
    return out


def fetch_near_graduation(limit: int | None = None) -> list[dict]:
    """Bonding-curve tokens close to graduating (complete=false, high mcap)."""
    limit = limit or config.MONITOR_FETCH_LIMIT
    params = {
        "offset": 0,
        "limit": max(limit * 3, 30),
        "sort": "market_cap",
        "order": "DESC",
        "includeNsfw": "false",
        "complete": "false",
    }
    resp = requests.get(FRONTEND_COINS_URL, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    coins = payload if isinstance(payload, list) else []

    out = []
    for c in coins:
        mint = c.get("mint") or ""
        if not mint or mint in config.EXCLUDED_TOKENS or c.get("complete"):
            continue
        if c.get("is_banned"):
            continue
        mcap = float(c.get("usd_market_cap") or c.get("market_cap") or 0)
        if mcap < config.MONITOR_NEAR_MIN_MARKET_CAP_USD:
            continue
        if mcap > config.MONITOR_NEAR_MAX_MARKET_CAP_USD:
            continue
        created_ms = c.get("created_timestamp")
        age = _age_minutes(created_ms)
        out.append({
            "mint": mint,
            "symbol": c.get("symbol") or mint[:6],
            "name": c.get("name") or "",
            "kind": "almost",
            "market_cap": mcap,
            "volume": None,
            "price": None,
            "holders": None,
            "sniper_count": None,
            "sniper_pct": None,
            "top_holders_pct": None,
            "dev_holdings_pct": None,
            "graduation_at": None,
            "created_at": _ms_to_iso(created_ms),
            "age_minutes": round(age, 2) if age is not None else None,
            "pool_address": c.get("pool_address"),
            "twitter": c.get("twitter") or None,
            "telegram": c.get("telegram") or None,
            "website": c.get("website") or None,
            "image_url": c.get("image_uri") or None,
            "source": "pumpfun_almost",
            "pumpfun_url": f"https://pump.fun/coin/{mint}",
            "gmgn_url": f"https://gmgn.ai/sol/token/{mint}",
        })
        if len(out) >= limit:
            break
    return out
