"""Per-source exponential backoff for monitor fetchers."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

import config


@dataclass
class _Bucket:
    failures: int = 0
    open_until: float = 0.0
    last_error: str = ""


class SourceBackoff:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, source: str) -> bool:
        with self._lock:
            b = self._buckets.get(source) or _Bucket()
            return time.monotonic() >= b.open_until

    def remaining(self, source: str) -> float:
        with self._lock:
            b = self._buckets.get(source) or _Bucket()
            return max(0.0, b.open_until - time.monotonic())

    def success(self, source: str) -> None:
        with self._lock:
            self._buckets[source] = _Bucket()

    def failure(self, source: str, exc: BaseException | None = None, *, retry_after: float | None = None) -> float:
        """Record failure; return seconds until next allowed attempt."""
        with self._lock:
            b = self._buckets.get(source) or _Bucket()
            b.failures += 1
            b.last_error = str(exc)[:200] if exc else b.last_error
            if retry_after is not None and retry_after > 0:
                delay = float(retry_after)
            else:
                base = getattr(config, "MONITOR_SOURCE_BACKOFF_BASE_SECONDS", 30)
                cap = getattr(config, "MONITOR_SOURCE_BACKOFF_MAX_SECONDS", 600)
                delay = min(cap, base * (2 ** (b.failures - 1)))
                delay *= 0.5 + random.random()  # jitter 0.5x–1.5x
            b.open_until = time.monotonic() + delay
            self._buckets[source] = b
            return delay

    def failure_count(self, source: str) -> int:
        with self._lock:
            b = self._buckets.get(source)
            return b.failures if b else 0


BACKOFF = SourceBackoff()
