"""Graduation + copy-trade monitor — trenches, wallet mirrors, rug/legit swarm.

Fast 15s producer loop: fetch → dedupe → enqueue → SL/TP.
Background worker: durable pending queue → swarm → mark seen/analyzed.

Usage:
  python monitor.py            # poll forever (+ background swarm worker)
  python monitor.py --once     # single poll (seeds seen set on first run)
  python monitor.py --seed     # mark current tokens/trades as seen without alerting
"""

from __future__ import annotations

import csv
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

import config
import monitor_state
from fetchers import gmgn as gmgn_fetcher
from fetchers.dexscreener import enrich_many, fetch_solana_markets
from fetchers.gmgn import KIND_LABEL
from fetchers.pumpfun import fetch_graduated, fetch_near_graduation
from monitor_backoff import BACKOFF
from monitor_heartbeat import touch_heartbeat
from swarm.orchestra import analyze_token
from swarm.paper import manage_open_positions, maybe_buy, maybe_sell_copy

console = Console()

LOG_FILE = config.DATA_DIR / "graduates.csv"
COPY_LOG_FILE = config.DATA_DIR / "copy_trades.csv"
VERDICT_FILE = config.DATA_DIR / "swarm_verdicts.csv"
LOG_FIELDS = [
    "timestamp", "kind", "symbol", "name", "mint", "market_cap", "liquidity_usd",
    "volume", "volume_h1", "holders", "sniper_pct", "top_holders_pct",
    "dev_holdings_pct", "age_minutes", "graduation_at", "dex_id", "dex_url",
    "gmgn_url", "pumpfun_url", "source", "rug_ratio", "launchpad",
]
COPY_LOG_FIELDS = [
    "timestamp", "side", "symbol", "name", "mint", "wallet", "trade_usd",
    "price", "tx_hash", "source", "investigation_quick", "rug_ratio",
    "top_holders_pct", "dev_holdings_pct", "liquidity_usd", "holders",
    "renounced_mint", "renounced_freeze", "is_honeypot", "is_wash_trading",
    "investigation_flags", "gmgn_url", "wallet_url",
]
VERDICT_FIELDS = [
    "timestamp", "kind", "symbol", "name", "mint", "verdict", "confidence",
    "short_circuited", "security_score", "holders_score", "traders_score",
    "liquidity_score", "social_score", "analysis_score_pct", "phishing_check",
    "creator_7d_realized_pnl", "creator_7d_win_rate", "traders_win_rate",
    "avg_buy_mc", "top1_pct", "top10_pct", "holder_count",
    "reasons", "bought", "position_key", "gmgn_url", "price",
    "wallet", "side", "investigation_quick",
]

_worker_stop = threading.Event()


def _alert_key(tok: dict) -> str:
    mint = tok.get("mint") or ""
    if config.MONITOR_SEEN_BY_MINT and mint:
        return f"mint:{mint}"
    return f"{tok.get('kind', 'migrated')}:{mint}"


def _bootstrap_swarm_done_from_csv() -> None:
    if monitor_state.load_swarm_done():
        return
    if not VERDICT_FILE.exists():
        return
    records: dict[str, dict] = {}
    try:
        with VERDICT_FILE.open(newline="") as f:
            for row in csv.DictReader(f):
                mint = (row.get("mint") or "").strip()
                if not mint:
                    continue
                stage = row.get("kind") or "unknown"
                prev = records.get(mint)
                if prev is None or monitor_state.stage_rank(stage) >= monitor_state.stage_rank(prev.get("stage")):
                    records[mint] = {
                        "at": row.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "stage": stage,
                    }
    except OSError:
        return
    if records:
        monitor_state.save_swarm_done(records)
        console.print(
            f"  [dim]swarm: loaded {len(records)} already-analyzed mint(s) "
            f"from {VERDICT_FILE.name}[/dim]"
        )


def _log_hits(hits: list[dict]):
    exists = LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for h in hits:
            w.writerow({"timestamp": ts, **h})


def _log_verdict(token: dict, verdict, bought: str | None):
    exists = VERDICT_FILE.exists()
    scores = {r.agent: r.score for r in verdict.reports}
    dist = token.get("holder_distribution") or {}
    creator_7d = token.get("creator_7d") or {}
    traders_proxy = token.get("traders_7d_proxy") or {}
    with open(VERDICT_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=VERDICT_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": token.get("kind"),
            "symbol": token.get("symbol"),
            "name": token.get("name"),
            "mint": token.get("mint"),
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "short_circuited": verdict.short_circuited,
            "security_score": scores.get("security"),
            "holders_score": scores.get("holders"),
            "traders_score": scores.get("traders"),
            "liquidity_score": scores.get("liquidity"),
            "social_score": scores.get("social"),
            "analysis_score_pct": token.get("analysis_score_pct"),
            "phishing_check": token.get("phishing_check"),
            "creator_7d_realized_pnl": creator_7d.get("realized_pnl"),
            "creator_7d_win_rate": creator_7d.get("win_rate"),
            "traders_win_rate": traders_proxy.get("win_rate"),
            "avg_buy_mc": token.get("avg_buy_mc"),
            "top1_pct": dist.get("top1_pct"),
            "top10_pct": dist.get("top10_pct"),
            "holder_count": dist.get("token_holders") or token.get("holders"),
            "reasons": " | ".join(verdict.reasons),
            "bought": bool(bought),
            "position_key": bought or "",
            "gmgn_url": token.get("gmgn_url"),
            "price": token.get("price") or token.get("price_usd"),
            "wallet": token.get("wallet") or "",
            "side": token.get("side") or "",
            "investigation_quick": token.get("investigation_quick") or "",
        })


def _log_copy_trades(trades: list[dict]):
    exists = COPY_LOG_FILE.exists()
    with open(COPY_LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COPY_LOG_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for t in trades:
            flags = t.get("investigation_flags") or []
            w.writerow({
                "timestamp": ts,
                "side": t.get("side"),
                "symbol": t.get("symbol"),
                "name": t.get("name"),
                "mint": t.get("mint"),
                "wallet": t.get("wallet"),
                "trade_usd": t.get("trade_usd"),
                "price": t.get("price") or t.get("price_usd"),
                "tx_hash": t.get("tx_hash"),
                "source": t.get("source"),
                "investigation_quick": t.get("investigation_quick"),
                "rug_ratio": t.get("rug_ratio"),
                "top_holders_pct": t.get("top_holders_pct"),
                "dev_holdings_pct": t.get("dev_holdings_pct"),
                "liquidity_usd": t.get("liquidity_usd"),
                "holders": t.get("holders"),
                "renounced_mint": t.get("renounced_mint"),
                "renounced_freeze": t.get("renounced_freeze"),
                "is_honeypot": t.get("is_honeypot"),
                "is_wash_trading": t.get("is_wash_trading"),
                "investigation_flags": ",".join(flags) if flags else "",
                "gmgn_url": t.get("gmgn_url"),
                "wallet_url": t.get("wallet_url"),
            })


def _fmt_usd(x) -> str:
    if x is None:
        return "—"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.1f}k"
    return f"${x:.0f}"


def _print_hits(hits: list[dict], title: str):
    if not hits:
        return
    table = Table(title=title, show_lines=False)
    table.add_column("Stage", style="cyan")
    table.add_column("Symbol", style="bold")
    table.add_column("MCap")
    table.add_column("Liq")
    table.add_column("Holders")
    table.add_column("Sniper%")
    table.add_column("Age")
    table.add_column("Links")
    for h in hits:
        age = h.get("age_minutes")
        age_s = f"{age:.0f}m" if age is not None else "—"
        links = h.get("gmgn_url") or h.get("dex_url") or h.get("pumpfun_url") or ""
        table.add_row(
            KIND_LABEL.get(h.get("kind"), h.get("kind") or ""),
            h.get("symbol") or "",
            _fmt_usd(h.get("market_cap")),
            _fmt_usd(h.get("liquidity_usd")),
            str(h.get("holders") if h.get("holders") is not None else "—"),
            f"{h.get('sniper_pct'):.1f}" if h.get("sniper_pct") is not None else "—",
            age_s,
            links,
        )
    console.print(table)
    for h in hits:
        link = h.get("gmgn_url") or h.get("pumpfun_url") or ""
        label = KIND_LABEL.get(h.get("kind"), h.get("kind"))
        tag = "STAGE↑" if h.get("_stage_upgrade") else "ALERT"
        console.print(
            f"  [green]{tag}[/green] {h['symbol']} ({label}) "
            f"via={h.get('source')} mint={h['mint']}  {link}"
        )


def _print_verdicts(rows: list[tuple[dict, object, str | None]]):
    if not rows:
        return
    table = Table(title="Swarm verdicts", show_lines=False)
    table.add_column("Stage", style="cyan")
    table.add_column("Symbol", style="bold")
    table.add_column("Verdict")
    table.add_column("Conf")
    table.add_column("Anal%")
    table.add_column("Phish")
    table.add_column("Win7d")
    table.add_column("AvgBuyMC")
    table.add_column("Top1%")
    table.add_column("Trd")
    table.add_column("Buy")
    for token, verdict, bought in rows:
        scores = {r.agent: r.score for r in verdict.reports}
        color = {
            "legit": "green",
            "suspicious": "yellow",
            "rug": "red",
        }.get(verdict.verdict, "white")
        phish = token.get("phishing_check") or "—"
        phish_s = f"[red]{phish}[/red]" if phish == "fail" else phish
        creator_7d = token.get("creator_7d") or {}
        traders_proxy = token.get("traders_7d_proxy") or {}
        wr = creator_7d.get("win_rate")
        if wr is None:
            wr = traders_proxy.get("win_rate")
        wr_s = f"{wr*100:.0f}%" if isinstance(wr, (int, float)) else "—"
        dist = token.get("holder_distribution") or {}
        top1 = dist.get("top1_pct")
        table.add_row(
            KIND_LABEL.get(token.get("kind"), token.get("kind") or ""),
            token.get("symbol") or "",
            f"[{color}]{verdict.verdict}[/{color}]",
            str(verdict.confidence),
            f"{token['analysis_score_pct']:.0f}" if token.get("analysis_score_pct") is not None else "—",
            phish_s,
            wr_s,
            _fmt_usd(token.get("avg_buy_mc")),
            f"{top1:.0f}" if top1 is not None else "—",
            str(scores.get("traders", "—")),
            bought or "—",
        )
    console.print(table)
    for token, verdict, _bought in rows:
        c7 = token.get("creator_7d") or {}
        dist = token.get("holder_distribution") or {}
        console.print(
            f"  [dim]{token.get('symbol')}: analysis={token.get('analysis_score_pct')}% "
            f"phish={token.get('phishing_check')} "
            f"creator_7d_pnl={c7.get('realized_pnl')} win={c7.get('win_rate')} "
            f"holders={dist.get('token_holders')} top10={dist.get('top10_pct')}% "
            f"avg_buy_mc={token.get('avg_buy_mc')}[/dim]"
        )
        if verdict.reasons:
            console.print(f"  [dim]reasons: {' | '.join(verdict.reasons[:4])}[/dim]")


def _print_copy_trades(trades: list[dict]):
    if not trades:
        return
    table = Table(title="Copy-trade wallet activity", show_lines=False)
    table.add_column("Side", style="cyan")
    table.add_column("Symbol", style="bold")
    table.add_column("USD")
    table.add_column("Quick")
    table.add_column("Rug")
    table.add_column("Top10%")
    table.add_column("Wallet")
    table.add_column("Flags")
    for t in trades:
        quick = t.get("investigation_quick") or "—"
        color = {
            "looks_real": "green",
            "caution": "yellow",
            "high_risk": "red",
            "fake_or_unknown": "red",
        }.get(quick, "white")
        wallet = t.get("wallet") or ""
        wallet_s = f"{wallet[:4]}…{wallet[-4:]}" if len(wallet) > 10 else wallet
        flags = t.get("investigation_flags") or []
        table.add_row(
            (t.get("side") or "").upper(),
            t.get("symbol") or "",
            _fmt_usd(t.get("trade_usd")),
            f"[{color}]{quick}[/{color}]",
            f"{t['rug_ratio']:.2f}" if t.get("rug_ratio") is not None else "—",
            f"{t['top_holders_pct']:.0f}" if t.get("top_holders_pct") is not None else "—",
            wallet_s,
            ",".join(flags[:3]) if flags else "—",
        )
    console.print(table)
    for t in trades:
        console.print(
            f"  [magenta]COPY[/magenta] {t.get('side')} {t.get('symbol')} "
            f"quick={t.get('investigation_quick')} mint={t.get('mint')}  "
            f"{t.get('gmgn_url') or ''}"
        )


def _dedupe(tokens: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for t in tokens:
        key = f"{t.get('kind', 'migrated')}:{t['mint']}"
        prev = by_key.get(key)
        if prev is None or str(t.get("source", "")).startswith("gmgn"):
            by_key[key] = t
    return list(by_key.values())


def _fetch_with_backoff(source: str, fn, *, label: str):
    if not BACKOFF.allow(source):
        rem = BACKOFF.remaining(source)
        console.print(f"  [dim]{label} backoff {rem:.0f}s remaining[/dim]")
        return None
    try:
        result = fn()
        BACKOFF.success(source)
        return result
    except Exception as exc:
        delay = BACKOFF.failure(source, exc)
        console.print(f"[red]{label} failed — backoff {delay:.0f}s[/red]")
        if BACKOFF.failure_count(source) <= 1:
            traceback.print_exc()
        return None


def collect_copy_candidates() -> list[dict]:
    if not config.MONITOR_COPY_ENABLED:
        return []
    if not gmgn_fetcher.available():
        console.print(
            "[yellow]Copy-trade monitor skipped — set GMGN_API_KEY in .env "
            "(https://gmgn.ai/ai)[/yellow]"
        )
        return []

    wallets = gmgn_fetcher.copy_wallets_from_config()
    if not wallets and not (
        config.MONITOR_COPY_USE_SMARTMONEY or config.MONITOR_COPY_USE_KOL
    ):
        console.print(
            "[dim]Copy-trade idle — add wallets to MONITOR_COPY_WALLETS "
            "or GMGN_COPY_WALLETS, or enable SMARTMONEY/KOL feeds[/dim]"
        )
        return []

    trades: list[dict] = []
    for wallet in wallets:
        rows = _fetch_with_backoff(
            f"gmgn_wallet:{wallet[:8]}",
            lambda w=wallet: gmgn_fetcher.fetch_wallet_activity(w),
            label=f"GMGN wallet {wallet[:8]}…",
        )
        if rows is None:
            continue
        console.print(
            f"  gmgn copy wallet {wallet[:4]}…{wallet[-4:]}: "
            f"{len(rows)} recent trades"
        )
        trades.extend(rows)

    if config.MONITOR_COPY_USE_SMARTMONEY:
        rows = _fetch_with_backoff(
            "gmgn_smartmoney",
            lambda: gmgn_fetcher.fetch_track_trades("smartmoney"),
            label="GMGN smartmoney",
        )
        if rows is not None:
            console.print(f"  gmgn smartmoney: {len(rows)} trades")
            trades.extend(rows)

    if config.MONITOR_COPY_USE_KOL:
        rows = _fetch_with_backoff(
            "gmgn_kol",
            lambda: gmgn_fetcher.fetch_track_trades("kol"),
            label="GMGN kol",
        )
        if rows is not None:
            console.print(f"  gmgn kol: {len(rows)} trades")
            trades.extend(rows)

    return trades


def investigate_copy_trades(trades: list[dict]) -> list[dict]:
    if not config.MONITOR_COPY_INVESTIGATE or not trades:
        return trades

    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") != "buy"]

    mint_order: list[str] = []
    for t in buys:
        mint = t.get("mint") or ""
        if mint and mint not in mint_order:
            mint_order.append(mint)
    mint_batch = mint_order[: config.MONITOR_COPY_MAX_INVESTIGATE]
    skipped_mints = len(mint_order) - len(mint_batch)
    if skipped_mints > 0:
        console.print(
            f"  [yellow]copy investigate capped at {len(mint_batch)}/"
            f"{len(mint_order)} mints this poll[/yellow]"
        )

    dossier_by_mint: dict[str, dict] = {}
    for mint in mint_batch:
        sample = next(t for t in buys if t.get("mint") == mint)
        console.print(f"  investigating {sample.get('symbol')} ({mint[:8]}…)…")
        result = _fetch_with_backoff(
            f"gmgn_invest:{mint[:8]}",
            lambda s=sample: gmgn_fetcher.investigate_token(s),
            label=f"investigate {sample.get('symbol')}",
        )
        if result is None:
            dossier_by_mint[mint] = {
                **sample,
                "investigation_quick": "unknown",
                "investigation_flags": ["investigate_failed"],
            }
        else:
            dossier_by_mint[mint] = result

    out: list[dict] = []
    for t in buys:
        mint = t.get("mint") or ""
        dossier = dossier_by_mint.get(mint)
        if not dossier:
            out.append({**t, "investigation_quick": t.get("investigation_quick") or "skipped"})
            continue
        out.append({
            **dossier,
            "wallet": t.get("wallet"),
            "tx_hash": t.get("tx_hash"),
            "trade_usd": t.get("trade_usd"),
            "side": t.get("side"),
            "seen_key": t.get("seen_key"),
            "source": t.get("source"),
            "price": t.get("price") if t.get("price") is not None else dossier.get("price"),
        })
    for t in sells:
        out.append({**t, "investigation_quick": t.get("investigation_quick") or "skipped"})
    return out


def collect_candidates() -> list[dict]:
    tokens: list[dict] = []

    rows = _fetch_with_backoff("pumpfun_graduated", fetch_graduated, label="pump.fun Migrated")
    if rows:
        tokens.extend(rows)

    if config.MONITOR_WATCH_NEAR_GRADUATION:
        rows = _fetch_with_backoff(
            "pumpfun_near", fetch_near_graduation, label="pump.fun Almost"
        )
        if rows:
            tokens.extend(rows)

    if config.MONITOR_USE_GMGN:
        if not gmgn_fetcher.available():
            console.print(
                "[yellow]GMGN skipped — set GMGN_API_KEY in .env "
                "(https://gmgn.ai/ai)[/yellow]"
            )
        else:
            gmgn_tokens = _fetch_with_backoff(
                "gmgn_trenches", gmgn_fetcher.fetch_trenches, label="GMGN trenches"
            )
            if gmgn_tokens is not None:
                by_kind: dict[str, int] = {}
                for t in gmgn_tokens:
                    by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
                parts = [
                    f"{KIND_LABEL.get(k, k)}={by_kind.get(k, 0)}"
                    for k in ("new", "almost", "migrated")
                ]
                console.print(
                    f"  gmgn trenches: {len(gmgn_tokens)} after filters "
                    f"({', '.join(parts)})"
                )
                tokens.extend(gmgn_tokens)

    if config.MONITOR_USE_DEXSCREENER:
        dex = _fetch_with_backoff(
            "dexscreener", fetch_solana_markets, label="DexScreener Solana"
        )
        if dex is not None:
            by_kind: dict[str, int] = {}
            for t in dex:
                by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
            console.print(
                f"  dexscreener solana: {len(dex)} after filters "
                f"(profiles={by_kind.get('dex_profile', 0)}, "
                f"boosts={by_kind.get('dex_boost', 0)})"
            )
            tokens.extend(dex)

    tokens = _dedupe(tokens)

    alert_kinds = set(getattr(config, "MONITOR_ALERT_KINDS", None) or [])
    if alert_kinds:
        before = len(tokens)
        tokens = [t for t in tokens if t.get("kind") in alert_kinds]
        dropped = before - len(tokens)
        if dropped:
            console.print(
                f"  [dim]filtered to {', '.join(sorted(alert_kinds))} "
                f"({len(tokens)} kept, {dropped} skipped)[/dim]"
            )

    if config.MONITOR_ENRICH_DEXSCREENER and tokens:
        need = [t for t in tokens if t.get("liquidity_usd") is None]
        have = [t for t in tokens if t.get("liquidity_usd") is not None]
        if need:
            need = enrich_many(need)
        tokens = have + need
        filtered = []
        for t in tokens:
            liq = t.get("liquidity_usd")
            if (
                t.get("kind") == "migrated"
                and liq is not None
                and liq < config.MONITOR_MIN_LIQUIDITY_USD
            ):
                continue
            filtered.append(t)
        tokens = filtered
    return tokens


def _enqueue_for_swarm(token: dict, *, reason: str = "new") -> bool:
    swarm_kinds = set(getattr(config, "MONITOR_SWARM_KINDS", None) or [])
    kind = token.get("kind") or ""
    if swarm_kinds and kind not in swarm_kinds:
        return False
    mint = token.get("mint") or ""
    if not monitor_state.should_swarm(mint, kind):
        return False
    token = {**token, "_alert_key": token.get("_alert_key") or _alert_key(token)}
    jid = monitor_state.enqueue_job(token, reason=reason)
    return bool(jid)


def process_swarm_job(job: dict) -> tuple[dict, object, str | None] | None:
    """Analyze one pending job; ack + mark seen/done only after success."""
    token = dict(job.get("token") or {})
    mint = token.get("mint") or job.get("mint") or ""
    kind = token.get("kind") or job.get("kind") or ""
    alert_key = job.get("alert_key") or token.get("_alert_key") or _alert_key(token)
    label = KIND_LABEL.get(kind, kind)
    console.print(f"  swarm analyzing {token.get('symbol')} ({label})…")

    try:
        verdict = analyze_token(token)
    except Exception:
        console.print(f"  [red]swarm failed for {token.get('symbol')}[/red]")
        traceback.print_exc()
        # Permanent fail: don't retry forever
        if mint:
            monitor_state.mark_swarm_done(mint, kind)
        if alert_key:
            monitor_state.mark_seen(alert_key, kind=kind)
        monitor_state.ack_job(job["id"])
        return None

    bought = None
    try:
        bought = maybe_buy(token, verdict)
        if bought:
            console.print(
                f"  [green]{config.MONITOR_PAPER_STRATEGY}: BUY {bought}[/green] "
                f"— {verdict.verdict} conf={verdict.confidence}"
            )
    except Exception:
        console.print(f"  [red]paper buy failed for {token.get('symbol')}[/red]")
        traceback.print_exc()

    _log_verdict(token, verdict, bought)
    if mint:
        monitor_state.mark_swarm_done(mint, kind)
    if alert_key and not str(alert_key).startswith("copy:"):
        # Copy fills use per-tx seen keys marked at enqueue time after logging
        monitor_state.mark_seen(alert_key, kind=kind)
    monitor_state.ack_job(job["id"])
    return (token, verdict, bought)


def worker_loop():
    """Background consumer for durable swarm queue."""
    _bootstrap_swarm_done_from_csv()
    idle = getattr(config, "MONITOR_WORKER_IDLE_SECONDS", 1.0)
    while not _worker_stop.is_set():
        try:
            touch_heartbeat("worker", pending=monitor_state.pending_count())
            if not config.MONITOR_SWARM_ENABLED:
                _worker_stop.wait(idle)
                continue
            jobs = monitor_state.pop_next_jobs(config.MONITOR_SWARM_MAX_PER_POLL)
            if not jobs:
                _worker_stop.wait(idle)
                continue
            results = []
            for job in jobs:
                if _worker_stop.is_set():
                    break
                # Re-check stage gate in case another worker path finished
                mint = job.get("mint") or ""
                kind = job.get("kind") or ""
                if mint and not monitor_state.should_swarm(mint, kind):
                    monitor_state.ack_job(job["id"])
                    continue
                row = process_swarm_job(job)
                if row:
                    results.append(row)
                touch_heartbeat("worker", pending=monitor_state.pending_count())
            if results:
                _print_verdicts(results)
        except Exception:
            console.print("[red]swarm worker error:[/red]")
            traceback.print_exc()
            _worker_stop.wait(idle)


def poll_copy_once(*, seed: bool = False, first_run: bool = False) -> list[dict]:
    if not config.MONITOR_COPY_ENABLED:
        return []

    trades = collect_copy_candidates()
    new_hits: list[dict] = []
    for t in trades:
        key = t.get("seen_key") or f"copy:{t.get('wallet')}:{t.get('tx_hash')}:{t.get('mint')}"
        if monitor_state.is_seen(key):
            continue
        t = {**t, "seen_key": key, "_alert_key": key}
        new_hits.append(t)

    if seed or first_run:
        for t in new_hits:
            monitor_state.mark_seen(t["seen_key"], kind="copy")
        console.print(
            f"[yellow]Seeded {len(new_hits)} copy-trade events as seen "
            f"(no alerts).[/yellow]"
        )
        return []

    if not new_hits:
        return []

    # Mark copy tx keys seen after we accept them into this poll (before investigate)
    # so a crash mid-investigate won't infinite-replay the same txs — but swarm
    # jobs for buys are still durable in pending.
    for t in new_hits:
        monitor_state.mark_seen(t["seen_key"], kind="copy")

    investigated = investigate_copy_trades(new_hits)
    _log_copy_trades(investigated)
    _print_copy_trades(investigated)

    # Mirror sells from the entry wallet
    for t in investigated:
        if t.get("side") != "sell":
            continue
        try:
            closed = maybe_sell_copy(t)
            if closed:
                console.print(
                    f"  [yellow]{config.MONITOR_PAPER_STRATEGY}: "
                    f"COPY-SELL {closed}[/yellow]"
                )
        except Exception:
            console.print(f"  [red]copy-sell failed for {t.get('symbol')}[/red]")
            traceback.print_exc()

    queued = 0
    swarm_mints: set[str] = set()
    for t in investigated:
        if t.get("side") != "buy":
            continue
        if t.get("investigation_quick") in ("skipped",):
            continue
        mint = t.get("mint") or ""
        if mint and mint in swarm_mints:
            continue
        if mint:
            swarm_mints.add(mint)
        if _enqueue_for_swarm(t, reason="copy"):
            queued += 1
    if queued:
        console.print(f"  [dim]queued {queued} copy mint(s) for swarm[/dim]")
    return investigated


def poll_once(*, seed: bool = False) -> list[dict]:
    """Fast producer: fetch, alert, enqueue — never blocks on LLM swarm."""
    if config.MONITOR_PAPER_TRADE and not seed:
        try:
            manage_open_positions(console)
        except Exception:
            console.print("[red]sniper position manage failed[/red]")
            traceback.print_exc()

    seen = monitor_state.load_seen()
    first_run = not seen
    candidates = collect_candidates()
    pending_jobs = monitor_state.load_pending()
    pending_mints = {j.get("mint") for j in pending_jobs.values() if j.get("mint")}

    new_hits: list[dict] = []
    upgrades: list[dict] = []
    for tok in candidates:
        key = _alert_key(tok)
        mint = tok.get("mint") or ""
        kind = tok.get("kind") or ""
        tok = {**tok, "_alert_key": key}

        already_seen = key in seen or monitor_state.is_seen(key)
        if not already_seen:
            # Skip re-alerting while a prior enqueue is still pending
            if mint and mint in pending_mints:
                continue
            new_hits.append(tok)
            continue

        if (
            getattr(config, "MONITOR_SWARM_RESCORE_ON_MIGRATE", True)
            and kind == "migrated"
            and mint
            and mint not in pending_mints
            and monitor_state.should_swarm(mint, kind)
        ):
            upgrades.append({**tok, "_stage_upgrade": True})

    if seed or first_run:
        for tok in new_hits:
            monitor_state.mark_seen(tok["_alert_key"], kind=tok.get("kind"))
        if first_run and not seed:
            console.print(
                f"[yellow]Seeded {len(new_hits)} current tokens as seen "
                f"(no alerts). Future Almost/Migrated hits will alert.[/yellow]"
            )
        else:
            console.print(f"[yellow]Seeded {len(new_hits)} tokens as seen.[/yellow]")
        poll_copy_once(seed=True, first_run=first_run)
        touch_heartbeat("producer", pending=monitor_state.pending_count(), seeded=True)
        return []

    if new_hits or upgrades:
        if new_hits:
            _log_hits(new_hits)
            _print_hits(new_hits, "Almost / Migrated alerts")
        if upgrades:
            _print_hits(upgrades, "Stage upgrades (re-swarm)")

        queued = 0
        for tok in new_hits + upgrades:
            reason = "stage_upgrade" if tok.get("_stage_upgrade") else "new"
            if _enqueue_for_swarm(tok, reason=reason):
                queued += 1
            elif not tok.get("_stage_upgrade"):
                # Nothing to analyze (swarm off / already done) — safe to mark seen now
                monitor_state.mark_seen(tok["_alert_key"], kind=tok.get("kind"))
        # New alerts stay unmarked until worker acks (pending prevents re-spam)
        if queued:
            console.print(
                f"  [dim]queued {queued} job(s) for swarm "
                f"(pending={monitor_state.pending_count()})[/dim]"
            )
    else:
        console.print(
            f"  no new tokens  "
            f"(watching {len(candidates)} that pass filters, "
            f"{len(monitor_state.load_seen())} seen total, "
            f"pending={monitor_state.pending_count()})"
        )

    poll_copy_once(seed=False, first_run=False)
    touch_heartbeat(
        "producer",
        pending=monitor_state.pending_count(),
        candidates=len(candidates),
    )
    return new_hits


def main():
    seed = "--seed" in sys.argv
    once = "--once" in sys.argv or seed
    sources = ["pump.fun"]
    if config.MONITOR_USE_GMGN:
        sources.append("gmgn" if gmgn_fetcher.available() else "gmgn(no key)")
    if config.MONITOR_USE_DEXSCREENER:
        sources.append("dexscreener")
    if config.MONITOR_COPY_ENABLED:
        n = len(gmgn_fetcher.copy_wallets_from_config())
        bit = f"copy({n} wallets)"
        if config.MONITOR_COPY_USE_SMARTMONEY:
            bit += "+sm"
        if config.MONITOR_COPY_USE_KOL:
            bit += "+kol"
        sources.append(bit)
    swarm_bit = ""
    if config.MONITOR_SWARM_ENABLED:
        swarm_bit = " + swarm/worker"
        if config.MONITOR_PAPER_TRADE:
            swarm_bit += "/sniper"
    console.print(
        "[bold]Graduation + copy-trade monitor[/bold] — Almost / Migrated via "
        + " + ".join(sources)
        + swarm_bit
        + f"  poll={config.MONITOR_POLL_SECONDS}s  "
        f"max_age={config.MONITOR_MAX_AGE_MINUTES}m"
    )

    worker = None
    if config.MONITOR_SWARM_ENABLED and not seed:
        _bootstrap_swarm_done_from_csv()
        worker = threading.Thread(target=worker_loop, name="swarm-worker", daemon=True)
        worker.start()
        console.print("  [dim]swarm worker thread started[/dim]")

    try:
        while True:
            try:
                console.rule(
                    f"[bold blue]poll {datetime.now(timezone.utc).strftime('%H:%M:%S')}Z"
                )
                poll_once(seed=seed)
                seed = False
            except KeyboardInterrupt:
                raise
            except Exception:
                console.print("[red]poll failed:[/red]")
                traceback.print_exc()
            if once:
                # Drain a bit of the queue in --once mode
                if config.MONITOR_SWARM_ENABLED and worker is None:
                    for job in monitor_state.pop_next_jobs(config.MONITOR_SWARM_MAX_PER_POLL):
                        process_swarm_job(job)
                break
            time.sleep(config.MONITOR_POLL_SECONDS)
    finally:
        _worker_stop.set()
        if worker is not None:
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
