"""Graduation + copy-trade monitor — trenches, wallet mirrors, rug/legit swarm.

Separate from the hourly paper-trading loop: polls every few seconds/minutes,
dedupes by mint / trade id, enriches with DexScreener / GMGN token security,
runs a specialist swarm on new hits, logs verdicts, and optionally paper-buys
`legit` tokens.

Usage:
  python monitor.py            # poll forever
  python monitor.py --once     # single poll (seeds seen set on first run)
  python monitor.py --seed     # mark current tokens/trades as seen without alerting
"""

from __future__ import annotations

import csv
import json
import sys
import time
import traceback
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

import config
from fetchers import gmgn as gmgn_fetcher
from fetchers.dexscreener import enrich_many, fetch_solana_markets
from fetchers.gmgn import KIND_LABEL
from fetchers.pumpfun import fetch_graduated, fetch_near_graduation
from swarm.orchestra import analyze_token
from swarm.paper import manage_open_positions, maybe_buy

console = Console()

SEEN_FILE = config.DATA_DIR / "monitor_seen.json"
SWARM_DONE_FILE = config.DATA_DIR / "swarm_analyzed.json"
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


def _normalize_seen_keys(raw: set[str]) -> set[str]:
    """Collapse legacy kind:mint keys to mint:… when MONITOR_SEEN_BY_MINT."""
    if not config.MONITOR_SEEN_BY_MINT:
        return raw
    out: set[str] = set()
    for k in raw:
        if not k:
            continue
        if k.startswith("copy:") or k.startswith("mint:"):
            out.add(k)
        elif ":" in k:
            mint = k.split(":", 1)[1]
            if mint:
                out.add(f"mint:{mint}")
        else:
            out.add(f"mint:{k}")
    return out


def _load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        data = json.loads(SEEN_FILE.read_text())
        return _normalize_seen_keys(set(data.get("mints") or []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_seen(seen: set[str]):
    mints = sorted(_normalize_seen_keys(seen))[-config.MONITOR_SEEN_CAP :]
    SEEN_FILE.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mints": mints,
    }, indent=2))


def _bootstrap_swarm_done_from_csv() -> set[str]:
    """One-time: treat prior verdict CSV rows as already analyzed."""
    if not VERDICT_FILE.exists():
        return set()
    done: set[str] = set()
    try:
        with VERDICT_FILE.open(newline="") as f:
            for row in csv.DictReader(f):
                mint = (row.get("mint") or "").strip()
                if mint:
                    done.add(mint)
    except OSError:
        return set()
    return done


def _load_swarm_done() -> set[str]:
    if SWARM_DONE_FILE.exists():
        try:
            data = json.loads(SWARM_DONE_FILE.read_text())
            return set(data.get("mints") or [])
        except (json.JSONDecodeError, OSError):
            pass
    boot = _bootstrap_swarm_done_from_csv()
    if boot:
        _save_swarm_done(boot)
        console.print(
            f"  [dim]swarm: loaded {len(boot)} already-analyzed mint(s) "
            f"from {VERDICT_FILE.name}[/dim]"
        )
    return boot


def _save_swarm_done(done: set[str]):
    mints = sorted(done)[-config.MONITOR_SEEN_CAP :]
    SWARM_DONE_FILE.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mints": mints,
    }, indent=2))


def _alert_key(tok: dict) -> str:
    """Dedupe key for alerts. Default: once per mint across stages/sources."""
    mint = tok.get("mint") or ""
    if config.MONITOR_SEEN_BY_MINT and mint:
        return f"mint:{mint}"
    return f"{tok.get('kind', 'migrated')}:{mint}"


_SWARM_PRIORITY = {
    "migrated": 0,
    "almost": 1,
    "dex_boost": 2,
    "dex_profile": 3,
    "dex": 3,
    "copy": 4,
    "new": 5,
}


def _swarm_sort_key(tok: dict):
    return (
        _SWARM_PRIORITY.get(tok.get("kind"), 9),
        -(tok.get("liquidity_usd") or 0),
        -(tok.get("market_cap") or 0),
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
        console.print(
            f"  [green]ALERT[/green] {h['symbol']} ({label}) "
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
    """Prefer GMGN when the same stage+mint appears from multiple sources."""
    by_key: dict[str, dict] = {}
    for t in tokens:
        key = f"{t.get('kind', 'migrated')}:{t['mint']}"
        prev = by_key.get(key)
        if prev is None or str(t.get("source", "")).startswith("gmgn"):
            by_key[key] = t
    return list(by_key.values())


def collect_copy_candidates() -> list[dict]:
    """Fetch recent trades from configured copy wallets / optional public feeds."""
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
        try:
            rows = gmgn_fetcher.fetch_wallet_activity(wallet)
            console.print(
                f"  gmgn copy wallet {wallet[:4]}…{wallet[-4:]}: "
                f"{len(rows)} recent trades"
            )
            trades.extend(rows)
        except Exception:
            console.print(f"[red]GMGN wallet activity failed for {wallet[:8]}…[/red]")
            traceback.print_exc()

    if config.MONITOR_COPY_USE_SMARTMONEY:
        try:
            rows = gmgn_fetcher.fetch_track_trades("smartmoney")
            console.print(f"  gmgn smartmoney: {len(rows)} trades")
            trades.extend(rows)
        except Exception:
            console.print("[red]GMGN smartmoney feed failed[/red]")
            traceback.print_exc()

    if config.MONITOR_COPY_USE_KOL:
        try:
            rows = gmgn_fetcher.fetch_track_trades("kol")
            console.print(f"  gmgn kol: {len(rows)} trades")
            trades.extend(rows)
        except Exception:
            console.print("[red]GMGN kol feed failed[/red]")
            traceback.print_exc()

    return trades


def investigate_copy_trades(trades: list[dict]) -> list[dict]:
    """Attach GMGN token info/security to each trade (buys first, capped)."""
    if not config.MONITOR_COPY_INVESTIGATE or not trades:
        return trades

    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") != "buy"]
    batch = buys[: config.MONITOR_COPY_MAX_INVESTIGATE]
    skipped = len(buys) - len(batch)
    if skipped > 0:
        console.print(
            f"  [yellow]copy investigate capped at {len(batch)}/{len(buys)} "
            f"buys this poll[/yellow]"
        )

    out: list[dict] = []
    for t in batch:
        console.print(
            f"  investigating {t.get('symbol')} ({t.get('mint', '')[:8]}…)…"
        )
        try:
            out.append(gmgn_fetcher.investigate_token(t))
        except Exception:
            console.print(f"  [red]investigate failed for {t.get('symbol')}[/red]")
            traceback.print_exc()
            t = {**t, "investigation_quick": "unknown", "investigation_flags": ["investigate_failed"]}
            out.append(t)
    # Sells / overflow buys: log without full investigation
    for t in sells + buys[len(batch):]:
        out.append({**t, "investigation_quick": t.get("investigation_quick") or "skipped"})
    return out


def collect_candidates() -> list[dict]:
    tokens: list[dict] = []

    try:
        tokens.extend(fetch_graduated())
    except Exception:
        console.print("[red]pump.fun Migrated fetch failed[/red]")
        traceback.print_exc()

    if config.MONITOR_WATCH_NEAR_GRADUATION:
        try:
            tokens.extend(fetch_near_graduation())
        except Exception:
            console.print("[red]pump.fun Almost fetch failed[/red]")
            traceback.print_exc()

    if config.MONITOR_USE_GMGN:
        if not gmgn_fetcher.available():
            console.print(
                "[yellow]GMGN skipped — set GMGN_API_KEY in .env "
                "(https://gmgn.ai/ai)[/yellow]"
            )
        else:
            try:
                gmgn_tokens = gmgn_fetcher.fetch_trenches()
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
            except Exception:
                console.print("[red]GMGN trenches fetch failed[/red]")
                traceback.print_exc()

    if config.MONITOR_USE_DEXSCREENER:
        try:
            dex = fetch_solana_markets()
            by_kind: dict[str, int] = {}
            for t in dex:
                by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
            console.print(
                f"  dexscreener solana: {len(dex)} after filters "
                f"(profiles={by_kind.get('dex_profile', 0)}, "
                f"boosts={by_kind.get('dex_boost', 0)})"
            )
            tokens.extend(dex)
        except Exception:
            console.print("[red]DexScreener Solana fetch failed[/red]")
            traceback.print_exc()

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


def run_swarm(hits: list[dict]) -> list[tuple[dict, object, str | None]]:
    """Analyze up to MONITOR_SWARM_MAX_PER_POLL hits; log + optional paper buy.

    Skips mints already swarm-analyzed (once per mint). Prefers Migrated/Almost
    over brand-new bonding-curve noise when capping.
    """
    if not config.MONITOR_SWARM_ENABLED or not hits:
        return []

    swarm_kinds = set(getattr(config, "MONITOR_SWARM_KINDS", None) or [])
    done = _load_swarm_done()
    queue: list[dict] = []
    skipped = 0
    skipped_kind = 0
    for tok in hits:
        if swarm_kinds and tok.get("kind") not in swarm_kinds:
            skipped_kind += 1
            continue
        mint = tok.get("mint") or ""
        if config.MONITOR_SWARM_ONCE_PER_MINT and mint and mint in done:
            skipped += 1
            continue
        queue.append(tok)

    queue.sort(key=_swarm_sort_key)
    batch = queue[: config.MONITOR_SWARM_MAX_PER_POLL]
    if skipped_kind:
        console.print(
            f"  [dim]swarm skip {skipped_kind} hit(s) outside "
            f"{', '.join(sorted(swarm_kinds))}[/dim]"
        )
    if skipped:
        console.print(f"  [dim]swarm skip {skipped} already-analyzed mint(s)[/dim]")
    if len(queue) > len(batch):
        console.print(
            f"  [yellow]swarm capped at {len(batch)}/{len(queue)} candidates "
            f"this poll (priority: Migrated → Almost)[/yellow]"
        )

    results = []
    for tok in batch:
        mint = tok.get("mint") or ""
        label = KIND_LABEL.get(tok.get("kind"), tok.get("kind"))
        console.print(f"  swarm analyzing {tok.get('symbol')} ({label})…")
        try:
            verdict = analyze_token(tok)
        except Exception:
            console.print(f"  [red]swarm failed for {tok.get('symbol')}[/red]")
            traceback.print_exc()
            if mint:
                done.add(mint)  # don't retry forever on hard failures
            continue
        bought = None
        try:
            bought = maybe_buy(tok, verdict)
            if bought:
                console.print(
                    f"  [green]{config.MONITOR_PAPER_STRATEGY}: BUY {bought}[/green] "
                    f"— {verdict.verdict} conf={verdict.confidence}"
                )
        except Exception:
            console.print(f"  [red]paper buy failed for {tok.get('symbol')}[/red]")
            traceback.print_exc()
        _log_verdict(tok, verdict, bought)
        results.append((tok, verdict, bought))
        if mint:
            done.add(mint)

    _save_swarm_done(done)
    _print_verdicts(results)
    return results


def poll_copy_once(*, seed: bool = False, first_run: bool = False) -> list[dict]:
    """Poll copy wallets; investigate + swarm new buy trades."""
    if not config.MONITOR_COPY_ENABLED:
        return []

    seen = _load_seen()
    trades = collect_copy_candidates()
    new_hits: list[dict] = []
    for t in trades:
        key = t.get("seen_key") or f"copy:{t.get('wallet')}:{t.get('tx_hash')}:{t.get('mint')}"
        if key in seen:
            continue
        seen.add(key)
        new_hits.append(t)

    _save_seen(seen)

    if seed or first_run:
        console.print(
            f"[yellow]Seeded {len(new_hits)} copy-trade events as seen "
            f"(no alerts).[/yellow]"
        )
        return []

    if not new_hits:
        return []

    investigated = investigate_copy_trades(new_hits)
    _log_copy_trades(investigated)
    _print_copy_trades(investigated)

    # Swarm only buys that look at least tentatively real / caution
    to_swarm = [
        t for t in investigated
        if t.get("side") == "buy"
        and t.get("investigation_quick") not in ("skipped",)
    ]
    if to_swarm:
        run_swarm(to_swarm)
    return investigated


def poll_once(*, seed: bool = False) -> list[dict]:
    """Fetch + filter. Returns newly seen hits (empty when seeding)."""
    # Manage open sniper positions every poll (even with no new hits)
    if config.MONITOR_PAPER_TRADE and not seed:
        try:
            manage_open_positions(console)
        except Exception:
            console.print("[red]sniper position manage failed[/red]")
            traceback.print_exc()

    seen = _load_seen()
    first_run = not seen
    candidates = collect_candidates()

    new_hits = []
    for tok in candidates:
        key = _alert_key(tok)
        if key in seen:
            continue
        seen.add(key)
        new_hits.append(tok)

    _save_seen(seen)

    if seed or first_run:
        if first_run and not seed:
            console.print(
                f"[yellow]Seeded {len(new_hits)} current tokens as seen "
                f"(no alerts). Future New/Almost/Migrated hits will alert.[/yellow]"
            )
        else:
            console.print(f"[yellow]Seeded {len(new_hits)} tokens as seen.[/yellow]")
        # Still seed copy wallets on the same first/seed run
        poll_copy_once(seed=True, first_run=first_run)
        return []

    if new_hits:
        _log_hits(new_hits)
        _print_hits(new_hits, "Almost / Migrated alerts")
        run_swarm(new_hits)
    else:
        console.print(
            f"  no new tokens  "
            f"(watching {len(candidates)} that pass filters, "
            f"{len(seen)} seen total)"
        )

    poll_copy_once(seed=False, first_run=False)
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
        swarm_bit = " + swarm"
        if config.MONITOR_PAPER_TRADE:
            swarm_bit += "/sniper"
    console.print(
        "[bold]Graduation + copy-trade monitor[/bold] — Almost / Migrated via "
        + " + ".join(sources)
        + swarm_bit
        + f"  poll={config.MONITOR_POLL_SECONDS}s  "
        f"max_age={config.MONITOR_MAX_AGE_MINUTES}m"
    )

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
            break
        time.sleep(config.MONITOR_POLL_SECONDS)


if __name__ == "__main__":
    main()
