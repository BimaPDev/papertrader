#!/usr/bin/env python3
"""Docker healthcheck: exit 0 if heartbeat component is fresh."""

from __future__ import annotations

import sys

import config
from monitor_heartbeat import is_fresh


def main() -> int:
    component = sys.argv[1] if len(sys.argv) > 1 else "producer"
    max_age = float(sys.argv[2]) if len(sys.argv) > 2 else getattr(
        config, "MONITOR_HEARTBEAT_MAX_AGE_SECONDS", 180
    )
    # papertrader cycles hourly — allow longer
    if component == "papertrader" and len(sys.argv) <= 2:
        max_age = max(max_age, config.CYCLE_MINUTES * 60 * 2)
    ok = is_fresh(component, max_age=max_age)
    # Also accept worker freshness for monitor container
    if not ok and component == "producer":
        ok = is_fresh("worker", max_age=max_age)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
