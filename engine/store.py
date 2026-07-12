"""Persistence: portfolio state (JSON), trade log + equity curves (CSV)."""

import csv
import json
from datetime import datetime, timezone

import config

STATE_FILE = config.DATA_DIR / "state.json"
TRADES_FILE = config.DATA_DIR / "trades.csv"
EQUITY_FILE = config.DATA_DIR / "equity.csv"


def load_state() -> dict:
    """{strategy_name: portfolio} — empty dict on first run."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def log_trade(strategy, symbol, action, qty, price, usd, pnl, pnl_pct, cash_after, reason):
    exists = TRADES_FILE.exists()
    with open(TRADES_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "strategy", "symbol", "action", "qty",
                        "price", "usd", "pnl", "pnl_pct", "cash_after", "reason"])
        w.writerow([
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            strategy, symbol, action, f"{qty:.10g}", f"{price:.10g}",
            f"{usd:.2f}", f"{pnl:.2f}", f"{pnl_pct:.2f}", f"{cash_after:.2f}", reason,
        ])


def log_equity(rows: dict[str, float]):
    """Append one equity sample per strategy for this cycle."""
    exists = EQUITY_FILE.exists()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(EQUITY_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "strategy", "equity"])
        for strategy, eq in rows.items():
            w.writerow([ts, strategy, f"{eq:.2f}"])


def load_equity_curves() -> dict[str, list[float]]:
    """{strategy: [equity samples in time order]}"""
    curves: dict[str, list[float]] = {}
    if not EQUITY_FILE.exists():
        return curves
    with open(EQUITY_FILE) as f:
        for row in csv.DictReader(f):
            curves.setdefault(row["strategy"], []).append(float(row["equity"]))
    return curves
