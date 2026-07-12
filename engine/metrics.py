"""Performance metrics from equity curves: return, Sharpe, max drawdown."""

import math

import config


def total_return_pct(curve: list[float]) -> float:
    if not curve:
        return 0.0
    return (curve[-1] / config.INITIAL_BALANCE - 1) * 100


def sharpe(curve: list[float]) -> float:
    """Annualized Sharpe from per-cycle equity samples (rf = 0)."""
    if len(curve) < 3:
        return 0.0
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    cycles_per_year = 365 * 24 * 60 / config.CYCLE_MINUTES
    return mean / std * math.sqrt(cycles_per_year)


def max_drawdown_pct(curve: list[float]) -> float:
    """Max peak-to-trough drawdown, returned as a negative percentage."""
    if not curve:
        return 0.0
    peak = curve[0]
    worst = 0.0
    for x in curve:
        peak = max(peak, x)
        if peak > 0:
            worst = min(worst, (x - peak) / peak)
    return worst * 100


def decay_status(curve: list[float]) -> str:
    """Rolling-vs-baseline Sharpe health check, adapted from a factor-decay
    state machine: split the equity curve in half, compare second-half Sharpe
    to first-half Sharpe. "new" until there's enough history to trust either
    half; "healthy"/"warning"/"decayed" otherwise."""
    if len(curve) < 20:
        return "new"
    split = len(curve) // 2
    baseline = sharpe(curve[:split])
    rolling = sharpe(curve[split:])
    if baseline <= 0:
        return "healthy" if rolling >= 0 else "warning"
    ratio = rolling / baseline
    if ratio >= 0.7:
        return "healthy"
    if ratio >= 0.3:
        return "warning"
    return "decayed"


def summarize(strategy: str, curve: list[float], portfolio: dict) -> dict:
    trades = portfolio.get("total_trades", 0)
    wins = portfolio.get("winning_trades", 0)
    closed = trades - len(portfolio.get("positions", {}))
    return {
        "strategy": strategy,
        "return_pct": total_return_pct(curve),
        "sharpe": sharpe(curve),
        "max_dd_pct": max_drawdown_pct(curve),
        "trades": trades,
        "win_rate": (wins / closed * 100) if closed > 0 else 0.0,
        "open_positions": len(portfolio.get("positions", {})),
        "equity": curve[-1] if curve else config.INITIAL_BALANCE,
        "health": decay_status(curve),
    }
