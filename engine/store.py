"""Persistence: portfolio state (JSON), trade log + equity curves (CSV).

JSON writes are atomic (temp + fsync + os.replace) with a .bak last-known-good
copy so a mid-write crash cannot leave a truncated file that silently resets
dedupe history.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import config

STATE_FILE = config.DATA_DIR / "state.json"
SNIPER_STATE_FILE = config.DATA_DIR / "sniper_state.json"
TRADES_FILE = config.DATA_DIR / "trades.csv"
EQUITY_FILE = config.DATA_DIR / "equity.csv"
SNIPER_EQUITY_FILE = config.DATA_DIR / "sniper_equity.csv"


class CorruptStateError(RuntimeError):
    """Raised when a JSON state file and its backup are both unreadable."""


def _backup_path(path: Path) -> Path:
    return path.parent / f"{path.name}.bak"


def atomic_write_json(path: Path, obj: dict | list) -> None:
    """Write JSON atomically: temp → fsync → replace; keep prior as .bak."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            bak = _backup_path(path)
            try:
                os.replace(path, bak)
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_json(path: Path, default=None):
    """Load JSON; fall back to .bak; raise CorruptStateError if both fail."""
    path = Path(path)
    bak = _backup_path(path)
    last_err: Exception | None = None
    found_any = False
    for candidate in (path, bak):
        if not candidate.exists():
            continue
        found_any = True
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            last_err = exc
            continue
    if not found_any:
        if default is not None:
            return default
        return {}
    raise CorruptStateError(f"corrupt JSON at {path} (and backup): {last_err}")


def load_state() -> dict:
    """{strategy_name: portfolio} — empty dict on first run."""
    try:
        data = load_json(STATE_FILE, default={})
    except CorruptStateError:
        raise
    return data if isinstance(data, dict) else {}


def save_state(state: dict):
    atomic_write_json(STATE_FILE, state)


def load_sniper_state() -> dict:
    """Isolated sniper portfolio file (never shared with hourly strategies)."""
    data = load_json(SNIPER_STATE_FILE, default={})
    return data if isinstance(data, dict) else {}


def save_sniper_state(portfolio: dict):
    atomic_write_json(SNIPER_STATE_FILE, portfolio)


def migrate_sniper_from_shared_state() -> dict | None:
    """One-time: pull Swarm sniper out of state.json into sniper_state.json."""
    name = config.MONITOR_PAPER_STRATEGY
    if SNIPER_STATE_FILE.exists():
        return None
    state = load_state()
    p = state.pop(name, None)
    if not p:
        return None
    save_sniper_state(p)
    save_state(state)
    return p


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


def log_equity(rows: dict[str, float], *, path: Path | None = None):
    """Append one equity sample per strategy for this cycle."""
    target = path or EQUITY_FILE
    exists = target.exists()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(target, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "strategy", "equity"])
        for strategy, eq in rows.items():
            w.writerow([ts, strategy, f"{eq:.2f}"])


def log_sniper_equity(equity_usd: float, strategy: str | None = None):
    """Append one sniper equity sample (intended ≤ hourly)."""
    log_equity(
        {strategy or config.MONITOR_PAPER_STRATEGY: equity_usd},
        path=SNIPER_EQUITY_FILE,
    )


def load_equity_curves(path: Path | None = None) -> dict[str, list[float]]:
    """{strategy: [equity samples in time order]}"""
    target = path or EQUITY_FILE
    curves: dict[str, list[float]] = {}
    if not target.exists():
        return curves
    with open(target) as f:
        for row in csv.DictReader(f):
            curves.setdefault(row["strategy"], []).append(float(row["equity"]))
    return curves


def load_sniper_equity_curve() -> list[float]:
    name = config.MONITOR_PAPER_STRATEGY
    return load_equity_curves(SNIPER_EQUITY_FILE).get(name, [])
