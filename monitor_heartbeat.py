"""Monitor / papertrader liveness heartbeats for Docker healthchecks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import config
from engine.store import atomic_write_json, load_json

HEARTBEAT_FILE = config.DATA_DIR / "heartbeat.json"


def touch_heartbeat(component: str, **extra) -> None:
    """Update heartbeat for a component (producer, worker, papertrader)."""
    try:
        data = load_json(HEARTBEAT_FILE, default={})
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[component] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    }
    atomic_write_json(HEARTBEAT_FILE, data)


def heartbeat_age_seconds(component: str) -> float | None:
    try:
        data = load_json(HEARTBEAT_FILE, default={})
    except Exception:
        return None
    entry = (data or {}).get(component) if isinstance(data, dict) else None
    if not entry or not entry.get("at"):
        return None
    try:
        ts = datetime.fromisoformat(str(entry["at"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


def is_fresh(component: str, max_age: float | None = None) -> bool:
    age = heartbeat_age_seconds(component)
    if age is None:
        return False
    limit = max_age if max_age is not None else getattr(
        config, "MONITOR_HEARTBEAT_MAX_AGE_SECONDS", 180
    )
    return age <= limit
