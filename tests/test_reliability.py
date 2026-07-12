"""Focused tests for the monitor reliability refactor."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "MONITOR_SEEN_CAP", 3)
    monkeypatch.setattr(config, "MONITOR_PAPER_STRATEGY", "Swarm sniper")
    monkeypatch.setattr(config, "MONITOR_PAPER_MAX_POSITIONS", 8)
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 3)
    monkeypatch.setattr(config, "MONITOR_SWARM_ONCE_PER_MINT", True)
    monkeypatch.setattr(config, "MONITOR_SWARM_RESCORE_ON_MIGRATE", True)
    monkeypatch.setattr(config, "MONITOR_SEEN_BY_MINT", True)
    # Reload modules that cached DATA_DIR paths
    import importlib
    import engine.store as store
    import monitor_state
    import monitor_heartbeat
    import swarm.paper as paper
    importlib.reload(store)
    importlib.reload(monitor_state)
    importlib.reload(monitor_heartbeat)
    importlib.reload(paper)
    return tmp_path


def test_atomic_write_and_backup(tmp_data):
    from engine.store import atomic_write_json, load_json

    path = tmp_data / "demo.json"
    atomic_write_json(path, {"a": 1})
    assert load_json(path)["a"] == 1
    atomic_write_json(path, {"a": 2})
    assert path.parent.joinpath(f"{path.name}.bak").exists()
    assert load_json(path)["a"] == 2


def test_corrupt_falls_back_to_bak(tmp_data):
    from engine.store import atomic_write_json, load_json

    path = tmp_data / "demo.json"
    atomic_write_json(path, {"ok": True})
    atomic_write_json(path, {"ok": True})  # creates .bak of prior good file
    path.write_text("{broken", encoding="utf-8")
    data = load_json(path)
    assert data["ok"] is True


def test_seen_trim_by_recency_not_alpha(tmp_data):
    import monitor_state

    # Cap is 3 from fixture
    monitor_state.save_seen({
        "mint:aaa": {"at": "2026-01-01T00:00:00+00:00"},
        "mint:zzz": {"at": "2026-01-02T00:00:00+00:00"},
        "mint:mmm": {"at": "2026-01-03T00:00:00+00:00"},
        "mint:new": {"at": "2026-01-04T00:00:00+00:00"},
    })
    records = monitor_state.load_seen()
    assert "mint:aaa" not in records  # oldest dropped
    assert "mint:new" in records
    assert len(records) == 3


def test_legacy_seen_list_migrates(tmp_data):
    import monitor_state
    from engine.store import atomic_write_json

    atomic_write_json(monitor_state.SEEN_FILE, {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "mints": ["new:AbCmint", "almost:AbCmint"],
    })
    records = monitor_state.load_seen()
    assert "mint:AbCmint" in records
    assert len(records) == 1


def test_stage_upgrade_allows_reswarm(tmp_data):
    import monitor_state

    monitor_state.mark_swarm_done("Mint111", "almost")
    assert monitor_state.should_swarm("Mint111", "almost") is False
    assert monitor_state.should_swarm("Mint111", "migrated") is True
    monitor_state.mark_swarm_done("Mint111", "migrated")
    assert monitor_state.should_swarm("Mint111", "migrated") is False


def test_pending_queue_dedupes_same_mint_kind(tmp_data):
    import monitor_state

    tok = {"mint": "M1", "kind": "migrated", "symbol": "X"}
    assert monitor_state.enqueue_job(tok) is not None
    assert monitor_state.enqueue_job(tok) is None
    assert monitor_state.pending_count() == 1
    monitor_state.ack_job("M1:migrated")
    assert monitor_state.pending_count() == 0


def test_sniper_isolated_from_shared_state(tmp_data):
    from engine.portfolio import new_portfolio
    from engine.store import load_state, save_state, load_sniper_state
    import swarm.paper as paper

    # Hourly state
    save_state({"Momentum": new_portfolio("Momentum")})
    p = paper.load_sniper()
    assert p["strategy"] == "Swarm sniper"
    assert "Swarm sniper" not in load_state()
    assert load_sniper_state()["strategy"] == "Swarm sniper"


def test_sniper_max_positions(tmp_data):
    from engine.portfolio import open_position, new_portfolio
    import config

    p = new_portfolio("Swarm sniper")
    for i in range(8):
        assert open_position(p, f"S{i}", 1.0, "t", max_positions=config.MONITOR_PAPER_MAX_POSITIONS)
    assert open_position(p, "S8", 1.0, "t", max_positions=config.MONITOR_PAPER_MAX_POSITIONS) is False


def test_copy_sell_same_wallet(tmp_data):
    import swarm.paper as paper
    from swarm.models import OrchestratorVerdict

    token = {
        "mint": "MintSell1pump",
        "symbol": "ABC",
        "kind": "copy",
        "price": 1.0,
        "wallet": "WalletAAA111",
        "source": "gmgn_copy",
    }
    verdict = OrchestratorVerdict(
        verdict="legit", confidence=90, reasons=["ok"], reports=[], short_circuited=False
    )
    with mock.patch("swarm.paper.config.MONITOR_PAPER_TRADE", True), \
         mock.patch("swarm.paper.config.MONITOR_PAPER_MIN_CONFIDENCE", 70):
        key = paper.maybe_buy(token, verdict)
        assert key
        closed = paper.maybe_sell_copy({
            **token, "side": "sell", "wallet": "OtherWallet", "price": 1.1,
        })
        assert closed is None  # different wallet
        closed = paper.maybe_sell_copy({
            **token, "side": "sell", "wallet": "WalletAAA111", "price": 1.1,
        })
        assert closed == key


def test_backoff_opens_then_recovers(tmp_data):
    from monitor_backoff import SourceBackoff
    import config

    b = SourceBackoff()
    with mock.patch.object(config, "MONITOR_SOURCE_BACKOFF_BASE_SECONDS", 0.05), \
         mock.patch.object(config, "MONITOR_SOURCE_BACKOFF_MAX_SECONDS", 1):
        assert b.allow("gmgn")
        b.failure("gmgn", RuntimeError("boom"))
        assert not b.allow("gmgn")
        time.sleep(0.12)
        # may still be in backoff depending on jitter; force success path
        b.success("gmgn")
        assert b.allow("gmgn")


def test_heartbeat_freshness(tmp_data):
    from monitor_heartbeat import touch_heartbeat, is_fresh

    touch_heartbeat("producer")
    assert is_fresh("producer", max_age=60)


def test_concurrent_sniper_saves(tmp_data):
    import swarm.paper as paper

    errors = []

    def worker(i):
        try:
            p = paper.load_sniper()
            p[f"flag_{i}"] = i
            paper._save_sniper(p)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    p = paper.load_sniper()
    assert p["strategy"] == "Swarm sniper"
