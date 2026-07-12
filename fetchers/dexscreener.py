"""DexScreener enrichment for Solana token mints (no API key)."""

from __future__ import annotations

import time

import requests

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PaperTraderGraduationMonitor/1.0",
}
BASE = "https://api.dexscreener.com/latest/dex/tokens"


def enrich_token(mint: str) -> dict:
    """Best Solana pair stats for a mint, or empty dict on failure/no pairs."""
    try:
        resp = requests.get(f"{BASE}/{mint}", headers=HEADERS, timeout=15)
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
    return {
        "dex_id": best.get("dexId"),
        "pair_address": best.get("pairAddress"),
        "dex_url": best.get("url"),
        "price_usd": float(best.get("priceUsd") or 0) or None,
        "liquidity_usd": liq_usd(best) or None,
        "volume_h1": float(vol.get("h1") or 0) or None,
        "volume_h24": float(vol.get("h24") or 0) or None,
        "price_change_h1": (best.get("priceChange") or {}).get("h1"),
        "pair_created_at": best.get("pairCreatedAt"),
    }


def enrich_many(tokens: list[dict], pause_s: float = 0.25) -> list[dict]:
    """Attach DexScreener fields onto each token dict (mutates copies)."""
    enriched = []
    for tok in tokens:
        row = dict(tok)
        dx = enrich_token(tok["mint"])
        row.update(dx)
        if row.get("price") is None and dx.get("price_usd") is not None:
            row["price"] = dx["price_usd"]
        enriched.append(row)
        time.sleep(pause_s)
    return enriched
