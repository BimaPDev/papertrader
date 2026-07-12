"""Paper portfolio helpers for the Swarm sniper (monitor-owned).

Uses an isolated `data/sniper_state.json` so the hourly main.py loop cannot
clobber sniper buys (and vice versa). Equity samples go to sniper_equity.csv
at most once per hour.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import config
from engine.portfolio import apply_stops, close_position, equity, new_portfolio, open_position
from engine.store import (
    load_sniper_equity_curve,
    load_sniper_state,
    log_sniper_equity,
    migrate_sniper_from_shared_state,
    save_sniper_state,
)
from fetchers.dexscreener import enrich_token
from swarm.models import OrchestratorVerdict

_sniper_lock = threading.RLock()


def position_key(token: dict) -> str:
    """Collision-safe key: SYMBOL:mintprefix."""
    symbol = (token.get("symbol") or "UNK").replace(",", "")[:16]
    mint = token.get("mint") or ""
    return f"{symbol}:{mint[:8]}"


def load_sniper() -> dict:
    with _sniper_lock:
        migrate_sniper_from_shared_state()
        name = config.MONITOR_PAPER_STRATEGY
        p = load_sniper_state()
        if not p or p.get("strategy") != name:
            p = new_portfolio(name)
            p["position_meta"] = {}
            save_sniper_state(p)
        p.setdefault("position_meta", {})
        return p


def _save_sniper(p: dict):
    with _sniper_lock:
        save_sniper_state(p)


def mint_from_position_key(key: str, positions_meta: dict) -> str | None:
    return (positions_meta.get(key) or {}).get("mint")


def find_position_by_mint(p: dict, mint: str) -> str | None:
    """Return position key holding this mint, if any."""
    if not mint:
        return None
    meta = p.get("position_meta") or {}
    for key, info in meta.items():
        if (info or {}).get("mint") == mint and key in (p.get("positions") or {}):
            return key
    return None


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
        dx = enrich_token(token.get("mint") or "")
        price = dx.get("price_usd")
        if price:
            token["price"] = price
            if token.get("liquidity_usd") is None:
                token["liquidity_usd"] = dx.get("liquidity_usd")
    if price is None or price <= 0:
        return None

    key = position_key(token)
    with _sniper_lock:
        p = load_sniper()
        # Idempotent: already holding this key/mint
        if key in (p.get("positions") or {}):
            return None
        if find_position_by_mint(p, token.get("mint") or ""):
            return None

        meta = p.setdefault("position_meta", {})
        reason = (
            f"swarm legit conf={verdict.confidence} "
            f"stage={token.get('kind')} "
            + "; ".join(verdict.reasons[:3])
        )
        max_pos = getattr(config, "MONITOR_PAPER_MAX_POSITIONS", None)
        if open_position(
            p, key, float(price), reason,
            max_positions=max_pos if max_pos is not None else config.MAX_OPEN_POSITIONS,
        ):
            meta[key] = {
                "mint": token.get("mint"),
                "symbol": token.get("symbol"),
                "gmgn_url": token.get("gmgn_url"),
                "kind": token.get("kind"),
                "entry_wallet": token.get("wallet") or "",
                "source": token.get("source") or "",
            }
            p["position_meta"] = meta
            _save_sniper(p)
            return key
    return None


def maybe_sell_copy(token: dict) -> str | None:
    """Close a sniper position when the same copied wallet sells the mint."""
    if not config.MONITOR_PAPER_TRADE:
        return None
    if token.get("side") != "sell":
        return None
    mint = token.get("mint") or ""
    wallet = token.get("wallet") or ""
    if not mint or not wallet:
        return None

    price = token.get("price") or token.get("price_usd")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        dx = enrich_token(mint)
        price = dx.get("price_usd")
    if price is None or price <= 0:
        return None

    with _sniper_lock:
        p = load_sniper()
        key = find_position_by_mint(p, mint)
        if not key:
            return None
        meta = (p.get("position_meta") or {}).get(key) or {}
        entry_wallet = meta.get("entry_wallet") or ""
        # Only mirror exit when the same wallet that triggered the buy dumps
        if entry_wallet and entry_wallet != wallet:
            return None
        pnl = close_position(
            p, key, float(price),
            f"copy-sell wallet={wallet[:8]}…",
        )
        if pnl is None:
            return None
        (p.get("position_meta") or {}).pop(key, None)
        _save_sniper(p)
        return key


def refresh_prices(p: dict) -> dict[str, float]:
    """DexScreener mark prices for open sniper positions, keyed by position key."""
    prices: dict[str, float] = {}
    meta = p.get("position_meta") or {}
    for key in list(p.get("positions") or {}):
        mint = (meta.get(key) or {}).get("mint")
        if not mint:
            continue
        dx = enrich_token(mint)
        px = dx.get("price_usd")
        if px:
            prices[key] = float(px)
    return prices


def manage_open_positions(console=None) -> dict:
    """Apply SL/TP using fresh marks; log equity at most hourly."""
    with _sniper_lock:
        p = load_sniper()
        if not p.get("positions"):
            return p
        prices = refresh_prices(p)
        closed = apply_stops(p, prices)
        meta = p.get("position_meta") or {}
        for sym in closed:
            meta.pop(sym, None)
        p["position_meta"] = meta

        eq = equity(p, prices)
        now = datetime.now(timezone.utc)
        last = p.get("last_equity_log_at")
        should_log = True
        if last:
            try:
                prev = datetime.fromisoformat(last.replace("Z", "+00:00"))
                should_log = (now - prev).total_seconds() >= 3600
            except ValueError:
                should_log = True
        if should_log:
            log_sniper_equity(eq, p["strategy"])
            p["last_equity_log_at"] = now.isoformat(timespec="seconds")

        _save_sniper(p)
        if console and closed:
            console.print(f"  [yellow]{p['strategy']}: stops closed {', '.join(closed)}[/yellow]")
        return p


def sniper_portfolio_for_report() -> dict | None:
    """Load sniper portfolio for leaderboard merge (read-only)."""
    try:
        migrate_sniper_from_shared_state()
        p = load_sniper_state()
        if p and p.get("strategy"):
            return p
    except Exception:
        return None
    return None


def sniper_equity_samples() -> list[float]:
    return load_sniper_equity_curve()
