"""Timestamped monitor persistence: seen alerts, swarm-done, pending queue."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from engine.store import atomic_write_json, load_json

SEEN_FILE = config.DATA_DIR / "monitor_seen.json"
SWARM_DONE_FILE = config.DATA_DIR / "swarm_analyzed.json"
PENDING_FILE = config.DATA_DIR / "monitor_pending.json"

_lock = threading.RLock()

STAGE_RANK = {
    "new": 0,
    "copy": 0,
    "dex": 1,
    "dex_profile": 1,
    "dex_boost": 1,
    "almost": 2,
    "migrated": 3,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stage_rank(kind: str | None) -> int:
    return STAGE_RANK.get(kind or "", -1)


def normalize_alert_key(key: str) -> str:
    if not key:
        return key
    if not config.MONITOR_SEEN_BY_MINT:
        return key
    if key.startswith("copy:") or key.startswith("mint:"):
        return key
    if ":" in key:
        mint = key.split(":", 1)[1]
        return f"mint:{mint}" if mint else key
    return f"mint:{key}"


def _trim_by_time(records: dict, cap: int) -> dict:
    if len(records) <= cap:
        return records
    items = sorted(
        records.items(),
        key=lambda kv: (kv[1].get("at") or "", kv[0]),
    )
    keep = items[-cap:]
    return dict(keep)


# ── Seen (alert dedupe) ─────────────────────────────────────────────────────

def load_seen() -> dict[str, dict]:
    """Return {key: {at, kind?}} — migrates legacy list format."""
    with _lock:
        try:
            data = load_json(SEEN_FILE, default={"records": {}})
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        # New format
        if isinstance(data.get("records"), dict):
            return {normalize_alert_key(k): v for k, v in data["records"].items() if k}
        # Legacy: {"mints": ["mint:…", …], "updated_at": …}
        legacy = data.get("mints") or []
        stamp = data.get("updated_at") or _now()
        out = {}
        for k in legacy:
            nk = normalize_alert_key(str(k))
            if nk:
                out[nk] = {"at": stamp}
        return out


def save_seen(records: dict[str, dict]):
    with _lock:
        trimmed = _trim_by_time(
            {normalize_alert_key(k): v for k, v in records.items() if k},
            config.MONITOR_SEEN_CAP,
        )
        atomic_write_json(SEEN_FILE, {
            "updated_at": _now(),
            "records": trimmed,
        })


def mark_seen(key: str, *, kind: str | None = None) -> None:
    with _lock:
        records = load_seen()
        nk = normalize_alert_key(key)
        records[nk] = {"at": _now(), "kind": kind}
        save_seen(records)


def is_seen(key: str) -> bool:
    return normalize_alert_key(key) in load_seen()


# ── Swarm done (analysis dedupe, stage-aware) ───────────────────────────────

def load_swarm_done() -> dict[str, dict]:
    """Return {mint: {at, stage}} — migrates legacy list / CSV bootstrap."""
    with _lock:
        if SWARM_DONE_FILE.exists():
            try:
                data = load_json(SWARM_DONE_FILE, default={"records": {}})
            except Exception:
                data = {}
            if isinstance(data, dict) and isinstance(data.get("records"), dict):
                return dict(data["records"])
            if isinstance(data, dict) and isinstance(data.get("mints"), list):
                stamp = data.get("updated_at") or _now()
                return {m: {"at": stamp, "stage": "unknown"} for m in data["mints"] if m}
        return {}


def save_swarm_done(records: dict[str, dict]):
    with _lock:
        trimmed = _trim_by_time(records, config.MONITOR_SEEN_CAP)
        atomic_write_json(SWARM_DONE_FILE, {
            "updated_at": _now(),
            "records": trimmed,
        })


def mark_swarm_done(mint: str, stage: str) -> None:
    if not mint:
        return
    with _lock:
        records = load_swarm_done()
        records[mint] = {"at": _now(), "stage": stage}
        save_swarm_done(records)


def should_swarm(mint: str, kind: str) -> bool:
    """True if this mint/stage still needs analysis."""
    if not mint:
        return False
    if not config.MONITOR_SWARM_ONCE_PER_MINT:
        return True
    done = load_swarm_done().get(mint)
    if not done:
        return True
    prev = done.get("stage") or "unknown"
    if not getattr(config, "MONITOR_SWARM_RESCORE_ON_MIGRATE", True):
        return False
    # Allow exactly one upgrade re-score when stage rank increases (esp. → migrated)
    return stage_rank(kind) > stage_rank(prev)


# ── Pending analysis queue ──────────────────────────────────────────────────

def load_pending() -> dict[str, dict]:
    with _lock:
        try:
            data = load_json(PENDING_FILE, default={"jobs": {}})
        except Exception:
            return {}
        jobs = data.get("jobs") if isinstance(data, dict) else {}
        return dict(jobs) if isinstance(jobs, dict) else {}


def save_pending(jobs: dict[str, dict]):
    with _lock:
        atomic_write_json(PENDING_FILE, {
            "updated_at": _now(),
            "jobs": jobs,
        })


def job_id_for(token: dict) -> str:
    mint = token.get("mint") or "unknown"
    kind = token.get("kind") or "unknown"
    # Stage-aware: same mint can have almost then migrated jobs
    return f"{mint}:{kind}"


def enqueue_job(token: dict, *, reason: str = "new") -> str | None:
    """Persist a pending swarm job. Returns job id or None if duplicate pending."""
    mint = token.get("mint") or ""
    kind = token.get("kind") or ""
    if not mint:
        return None
    jid = job_id_for(token)
    with _lock:
        jobs = load_pending()
        # Deduplicate identical mint:kind still pending
        if jid in jobs:
            return None
        # Also skip if a higher-or-equal stage job already pending for mint
        for existing in jobs.values():
            if existing.get("mint") == mint and stage_rank(existing.get("kind")) >= stage_rank(kind):
                return None
        jobs[jid] = {
            "id": jid,
            "mint": mint,
            "kind": kind,
            "reason": reason,
            "enqueued_at": _now(),
            "token": token,
            "alert_key": token.get("_alert_key") or "",
        }
        save_pending(jobs)
        return jid


def pop_next_jobs(limit: int) -> list[dict]:
    """Return up to `limit` jobs sorted by swarm priority (does not remove yet)."""
    priority = {
        "migrated": 0,
        "almost": 1,
        "dex_boost": 2,
        "dex_profile": 3,
        "dex": 3,
        "copy": 4,
        "new": 5,
    }
    jobs = list(load_pending().values())
    jobs.sort(
        key=lambda j: (
            priority.get(j.get("kind"), 9),
            j.get("enqueued_at") or "",
        )
    )
    return jobs[:limit]


def ack_job(job_id: str) -> None:
    with _lock:
        jobs = load_pending()
        jobs.pop(job_id, None)
        save_pending(jobs)


def pending_count() -> int:
    return len(load_pending())
