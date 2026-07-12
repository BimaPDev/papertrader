"""Per-strategy virtual portfolio: fills with fees + slippage, SL/TP, equity."""

from datetime import datetime, timezone

import config
from engine.store import log_trade


def new_portfolio(strategy: str) -> dict:
    return {
        "strategy": strategy,
        "cash": config.INITIAL_BALANCE,
        "positions": {},        # symbol -> {qty, entry_price, entry_usd, opened_at}
        "total_trades": 0,
        "winning_trades": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _cost_multiplier_buy() -> float:
    return 1 + (config.FEE_PCT + config.SLIPPAGE_PCT) / 100


def _proceeds_multiplier_sell() -> float:
    return 1 - (config.FEE_PCT + config.SLIPPAGE_PCT) / 100


def equity(p: dict, prices: dict[str, float]) -> float:
    """Cash + mark-to-market value of open positions (last known price if absent)."""
    total = p["cash"]
    for sym, pos in p["positions"].items():
        px = prices.get(sym, pos["entry_price"])
        total += pos["qty"] * px
    return total


def open_position(
    p: dict,
    symbol: str,
    price: float,
    reason: str,
    *,
    max_positions: int | None = None,
) -> bool:
    limit = config.MAX_OPEN_POSITIONS if max_positions is None else max_positions
    if symbol in p["positions"] or len(p["positions"]) >= limit:
        return False
    usd = p["cash"] * config.POSITION_PCT / 100
    if usd < 1:
        return False
    fill_price = price * _cost_multiplier_buy()
    qty = usd / fill_price
    p["cash"] -= usd
    p["positions"][symbol] = {
        "qty": qty,
        "entry_price": fill_price,
        "entry_usd": usd,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    p["total_trades"] += 1
    log_trade(p["strategy"], symbol, "BUY", qty, fill_price, usd, 0.0, 0.0, p["cash"], reason)
    return True


def close_position(p: dict, symbol: str, price: float, reason: str) -> float | None:
    """Close and return realized PnL in USD, or None if no position."""
    pos = p["positions"].pop(symbol, None)
    if pos is None:
        return None
    proceeds = pos["qty"] * price * _proceeds_multiplier_sell()
    pnl = proceeds - pos["entry_usd"]
    pnl_pct = pnl / pos["entry_usd"] * 100 if pos["entry_usd"] else 0.0
    p["cash"] += proceeds
    if pnl > 0:
        p["winning_trades"] += 1
    log_trade(p["strategy"], symbol, "SELL", pos["qty"], price, proceeds, pnl, pnl_pct, p["cash"], reason)
    return pnl


def apply_stops(p: dict, prices: dict[str, float]) -> list[str]:
    """Enforce SL/TP on open positions. Returns symbols closed."""
    closed = []
    for symbol in list(p["positions"]):
        px = prices.get(symbol)
        if px is None:
            continue
        entry = p["positions"][symbol]["entry_price"]
        move_pct = (px - entry) / entry * 100
        if move_pct <= -config.STOP_LOSS_PCT:
            close_position(p, symbol, px, f"stop-loss {move_pct:.1f}%")
            closed.append(symbol)
        elif move_pct >= config.TAKE_PROFIT_PCT:
            close_position(p, symbol, px, f"take-profit {move_pct:.1f}%")
            closed.append(symbol)
    return closed
