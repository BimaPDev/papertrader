"""Paper portfolio helpers for the Swarm sniper (monitor-owned)."""

from __future__ import annotations

import config
from engine.portfolio import apply_stops, equity, new_portfolio, open_position
from engine.store import load_state, log_equity, save_state
from fetchers.dexscreener import enrich_token
from swarm.models import OrchestratorVerdict


def position_key(token: dict) -> str:
    """Collision-safe key: SYMBOL:mintprefix."""
    symbol = (token.get("symbol") or "UNK").replace(",", "")[:16]
    mint = token.get("mint") or ""
    return f"{symbol}:{mint[:8]}"


def load_sniper() -> dict:
    state = load_state()
    name = config.MONITOR_PAPER_STRATEGY
    p = state.get(name) or new_portfolio(name)
    state[name] = p
    save_state(state)
    return p


def _save_sniper(p: dict):
    state = load_state()
    state[p["strategy"]] = p
    save_state(state)


def mint_from_position_key(key: str, positions_meta: dict) -> str | None:
    return (positions_meta.get(key) or {}).get("mint")


def maybe_buy(token: dict, verdict: OrchestratorVerdict) -> str | None:
    """Open a paper position if verdict clears the bar. Returns position key or None."""
    if not config.MONITOR_PAPER_TRADE:
        return None
    if verdict.verdict != "legit":
        return None
    if verdict.confidence < config.MONITOR_PAPER_MIN_CONFIDENCE:
        return None

    price = token.get("price") or token.get("price_usd")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        # Last-chance DexScreener price
        dx = enrich_token(token.get("mint") or "")
        price = dx.get("price_usd")
        if price:
            token["price"] = price
            if token.get("liquidity_usd") is None:
                token["liquidity_usd"] = dx.get("liquidity_usd")
    if price is None or price <= 0:
        return None

    key = position_key(token)
    p = load_sniper()
    # Stash mint on the position via a side map in portfolio
    meta = p.setdefault("position_meta", {})
    reason = (
        f"swarm legit conf={verdict.confidence} "
        f"stage={token.get('kind')} "
        + "; ".join(verdict.reasons[:3])
    )
    if open_position(p, key, float(price), reason):
        meta[key] = {
            "mint": token.get("mint"),
            "symbol": token.get("symbol"),
            "gmgn_url": token.get("gmgn_url"),
            "kind": token.get("kind"),
        }
        p["position_meta"] = meta
        _save_sniper(p)
        return key
    return None


def refresh_prices(p: dict) -> dict[str, float]:
    """DexScreener mark prices for open sniper positions, keyed by position key."""
    prices: dict[str, float] = {}
    meta = p.get("position_meta") or {}
    for key in list(p.get("positions") or {}):
        mint = (meta.get(key) or {}).get("mint")
        if not mint and ":" in key:
            # Can't recover full mint from key alone
            continue
        if not mint:
            continue
        dx = enrich_token(mint)
        px = dx.get("price_usd")
        if px:
            prices[key] = float(px)
    return prices


def manage_open_positions(console=None) -> dict:
    """Apply SL/TP using fresh marks; log equity. Returns the sniper portfolio."""
    p = load_sniper()
    if not p.get("positions"):
        return p
    prices = refresh_prices(p)
    closed = apply_stops(p, prices)
    # Drop meta for closed
    meta = p.get("position_meta") or {}
    for sym in closed:
        meta.pop(sym, None)
    p["position_meta"] = meta
    _save_sniper(p)
    eq = equity(p, prices)
    log_equity({p["strategy"]: eq})
    if console and closed:
        console.print(f"  [yellow]{p['strategy']}: stops closed {', '.join(closed)}[/yellow]")
    return p
