"""DexScreener: Solana discovery (profiles/boosts) + per-mint pair enrichment.

No API key. Profile/boost endpoints ~60 req/min; pair lookup ~300 req/min.
https://dexscreener.com/solana
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

import config

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PaperTraderGraduationMonitor/1.0",
}
API = "https://api.dexscreener.com"
TOKENS_URL = f"{API}/latest/dex/tokens"

KIND_LABEL = {
    "dex": "DexScreener",
    "dex_boost": "DexBoost",
    "dex_profile": "DexProfile",
}


def enrich_token(mint: str) -> dict:
    """Best Solana pair stats for a mint, or empty dict on failure/no pairs."""
    try:
        resp = requests.get(f"{TOKENS_URL}/{mint}", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        pairs = resp.json().get("pairs") or []
    except requests.RequestException:
        return {}

    sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol_pairs:
        return {}

    def liq_usd(p):
        return float((p.get("liquidity") or {}).get("usd") or 0)

    best = max(sol_pairs, key=liq_usd)
    vol = best.get("volume") or {}
    base = best.get("baseToken") or {}
    # Prefer the non-SOL/USDC side as the meme symbol when possible
    quote = best.get("quoteToken") or {}
    stables = {"USDC", "USDT", "SOL", "WSOL", "USD1"}
    if base.get("symbol") in stables and quote.get("address"):
        # rare inverted pair
        pass
    return {
        "dex_id": best.get("dexId"),
        "pair_address": best.get("pairAddress"),
        "dex_url": best.get("url") or f"https://dexscreener.com/solana/{mint}",
        "price_usd": float(best.get("priceUsd") or 0) or None,
        "liquidity_usd": liq_usd(best) or None,
        "volume_h1": float(vol.get("h1") or 0) or None,
        "volume_h24": float(vol.get("h24") or 0) or None,
        "price_change_h1": (best.get("priceChange") or {}).get("h1"),
        "pair_created_at": best.get("pairCreatedAt"),
        "market_cap": float(best.get("marketCap") or best.get("fdv") or 0) or None,
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "mint": base.get("address") or mint,
    }


def enrich_many(tokens: list[dict], pause_s: float = 0.25) -> list[dict]:
    """Attach DexScreener fields onto each token dict (mutates copies)."""
    enriched = []
    for tok in tokens:
        row = dict(tok)
        dx = enrich_token(tok["mint"])
        row.update({k: v for k, v in dx.items() if v is not None})
        if row.get("price") is None and dx.get("price_usd") is not None:
            row["price"] = dx["price_usd"]
        enriched.append(row)
        time.sleep(pause_s)
    return enriched


def _age_minutes_from_ms(ms) -> float | None:
    if ms is None:
        return None
    try:
        then = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 60.0


def _links_map(links: list | None) -> dict:
    out = {"twitter": None, "telegram": None, "website": None}
    for link in links or []:
        if not isinstance(link, dict):
            continue
        typ = (link.get("type") or "").lower()
        url = link.get("url")
        if not url:
            continue
        if typ in ("twitter", "x") or "x.com" in url or "twitter.com" in url:
            out["twitter"] = url
        elif typ == "telegram" or "t.me" in url:
            out["telegram"] = url
        elif typ in ("website", "web") or typ == "":
            if out["website"] is None and not any(
                x in url for x in ("t.me", "x.com", "twitter.com", "reddit.com")
            ):
                out["website"] = url
    return out


def _get_list(path: str) -> list[dict]:
    try:
        resp = requests.get(f"{API}{path}", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except requests.RequestException:
        return []


def fetch_solana_markets(limit: int | None = None) -> list[dict]:
    """Discover Solana tokens from DexScreener latest profiles + boosts.

    Returns monitor-shaped dicts with kind dex_profile / dex_boost.
    """
    limit = limit or config.MONITOR_FETCH_LIMIT
    excluded = config.EXCLUDED_TOKENS

    sources: list[tuple[str, str, list[dict]]] = []
    if config.MONITOR_DEX_USE_PROFILES:
        sources.append(("dex_profile", "profile", _get_list("/token-profiles/latest/v1")))
    if config.MONITOR_DEX_USE_BOOSTS:
        sources.append(("dex_boost", "boost", _get_list("/token-boosts/latest/v1")))
        sources.append(("dex_boost", "boost_top", _get_list("/token-boosts/top/v1")))

    # Dedupe mints; prefer boost over profile when both appear
    by_mint: dict[str, dict] = {}
    for kind, src, items in sources:
        for item in items:
            if item.get("chainId") != "solana":
                continue
            mint = item.get("tokenAddress") or ""
            if not mint or mint in excluded:
                continue
            prev = by_mint.get(mint)
            if prev and prev.get("kind") == "dex_boost" and kind == "dex_profile":
                continue
            links = _links_map(item.get("links"))
            by_mint[mint] = {
                "mint": mint,
                "symbol": mint[:6],
                "name": (item.get("description") or "")[:80],
                "kind": kind,
                "source": f"dexscreener_{src}",
                "dex_url": item.get("url") or f"https://dexscreener.com/solana/{mint}",
                "gmgn_url": f"https://gmgn.ai/sol/token/{mint}",
                "pumpfun_url": f"https://pump.fun/coin/{mint}",
                "image_url": item.get("icon"),
                "twitter": links["twitter"],
                "telegram": links["telegram"],
                "website": links["website"],
                "boost_amount": item.get("amount") or item.get("totalAmount"),
            }

    # Enrich with live pair stats (rate-limit friendly)
    out: list[dict] = []
    for mint, row in list(by_mint.items()):
        if len(out) >= limit:
            break
        dx = enrich_token(mint)
        time.sleep(0.2)
        if not dx:
            continue
        age = _age_minutes_from_ms(dx.get("pair_created_at"))
        if age is not None and age > config.MONITOR_DEX_MAX_AGE_MINUTES:
            continue
        mcap = dx.get("market_cap")
        liq = dx.get("liquidity_usd")
        if mcap is not None and mcap < config.MONITOR_MIN_MARKET_CAP_USD:
            continue
        if liq is not None and liq < config.MONITOR_MIN_LIQUIDITY_USD:
            continue

        row = dict(row)
        row.update({k: v for k, v in dx.items() if v is not None})
        if dx.get("symbol"):
            row["symbol"] = dx["symbol"]
        if dx.get("name"):
            row["name"] = dx["name"]
        row["price"] = dx.get("price_usd")
        row["price_usd"] = dx.get("price_usd")
        row["volume"] = dx.get("volume_h1") or dx.get("volume_h24")
        row["age_minutes"] = round(age, 2) if age is not None else None
        out.append(row)

    return out
