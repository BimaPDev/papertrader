---
name: papertrader-run
description: "Use when running PaperTrader cycles, checking the leaderboard, interpreting data/state.json, data/trades.csv, data/equity.csv, or resetting portfolio state."
---

# Running PaperTrader & reading its data

## Commands

```bash
.venv/bin/python main.py --once   # run exactly one cycle, then exit (cron/debugging)
.venv/bin/python main.py          # run forever, one cycle every config.CYCLE_MINUTES (default 60)
.venv/bin/python report.py        # print the current leaderboard without running a cycle
.venv/bin/python monitor.py --seed  # mark current pump.fun graduates as seen (no alerts)
.venv/bin/python monitor.py         # poll for new graduates / near-grads (config.MONITOR_POLL_SECONDS)
.venv/bin/python monitor.py --once  # single monitor poll
```

Graduation monitor state: `data/monitor_seen.json` (alert dedupe, once per mint), `data/swarm_analyzed.json` (swarm once per mint), `data/graduates.csv` (alert log), `data/copy_trades.csv` (copy-wallet trades + investigation), `data/swarm_verdicts.csv` (rug/legit swarm). Default focus: **Almost + Migrated** only (`MONITOR_ALERT_KINDS` / `MONITOR_SWARM_KINDS`; GMGN skips `new_creation`). Add `"new"` or `dex_*` to those lists to widen. Tunables: `MONITOR_*` in `config.py`.

**Copy wallets:** add Solana addresses to `MONITOR_COPY_WALLETS` (or env), then `monitor.py --seed` once. Optional `MONITOR_COPY_USE_SMARTMONEY` / `MONITOR_COPY_USE_KOL` poll public GMGN feeds without a wallet list. Investigation quick labels: `looks_real` / `caution` / `high_risk` / `fake_or_unknown`.

**Swarm + sniper:** on each new alert, five specialist agents (Security / Holders / Traders / Liquidity / Social) + an orchestrator (via `config.AI_PROVIDER`) verdict `rug` / `suspicious` / `legit`. Deep GMGN enrich adds Analysis %, phishing check, creator 7d realized PnL / win rate, avg buy MC, holder distribution. Hard short-circuit rugs phishing-fail / honeypot / extreme concentration / very low analysis without an LLM call. `MONITOR_PAPER_TRADE` paper-buys `legit` into strategy `"Swarm sniper"` in `data/state.json` (position key `SYMBOL:mintprefix`); SL/TP run every poll. Not part of hourly `main.py` strategies.

Sharpe/drawdown are noisy until there are a few dozen equity samples (a day or two of hourly cycles) — don't over-read the leaderboard after only 1–2 cycles.

To wipe all portfolios and start clean: `rm -rf data/`. It's regenerated on the next run. `data/` and `.env` are gitignored.

## What one cycle does (`main.py:run_cycle`)

1. **Fetch** — CoinGecko for majors, Birdeye for trending Solana memes (liquidity/volume filtered).
2. **Clean** — indicators computed, then collapsed into one flat snapshot dict per asset.
3. **Risk first** — every strategy's stop-loss (-12%) / take-profit (+25%) is enforced before new decisions.
4. **Decide** — each of the 11 strategies (6 per-asset rule-based, 2 cross-sectional factor, 3 AI) returns buy/sell/hold per symbol. A strategy that raises an exception is caught and treated as "hold everything" that cycle — one broken strategy never kills the run.
5. **Fill** — simulated with `FEE_PCT` + `SLIPPAGE_PCT` per side, sized at `POSITION_PCT` of cash, capped at `MAX_OPEN_POSITIONS`.
6. **Record** — state → `data/state.json`, fills → `data/trades.csv`, equity → `data/equity.csv`, then the leaderboard prints.

## Data shapes

**Portfolio** (`data/state.json`, keyed by strategy name):
```python
{
  "strategy": "EMA oscillator crossing",
  "cash": 750.0,
  "positions": {"ETH": {"qty": 0.1378, "entry_price": 1814.10, "entry_usd": 250.0, "opened_at": "2026-07-12T13:59:41Z"}},
  "total_trades": 1, "winning_trades": 0,
  "created_at": "2026-07-12T13:59:41Z"
}
```

**trades.csv columns**: `timestamp, strategy, symbol, action, qty, price, usd, pnl, pnl_pct, cash_after, reason`

**equity.csv columns**: `timestamp, strategy, equity`

**Snapshot** (internal — what each strategy sees per asset per cycle, not persisted):
```python
{
  "symbol": "ETH", "kind": "major",              # "major" | "meme"
  "price": 1814.10, "bars": 168,
  "ema12": ..., "ema26": ..., "above_ema50": True,
  "ema_cross_up": False, "ema_cross_down": False,
  "rsi": 42.3, "macd_hist": ..., "macd_hist_prev": ...,
  "atr_pct": 2.1, "vol_z": 0.8, "obv_slope_pos": True,
  "breakout_20": False, "breakdown_20": False,
  "mom_6": 1.2, "mom_24": -0.4, "change_24h": 0.9,
  # meme-only: "liquidity": ..., "volume24h": ..., "address": ...
}
```
Numeric fields are `None` (not NaN) when undefined, so it always serializes cleanly — this is exactly what gets fed into the AI prompt payload for the AI strategies.

## Strategies on the leaderboard

| Rule-based (per-asset) | Cross-sectional factor | AI personas (Claude) |
|---|---|---|
| EMA oscillator crossing | Cross-sectional momentum | AI momentum |
| RSI mean reversion | Liquidity-adjusted reversal | AI contrarian |
| Burst persistence | | AI meme degen |
| Vol-scaled cooldown | | |
| MACD trend flip | | |
| Vol-normalized pressure | | |

The leaderboard table (`report.print_leaderboard`) has one column beyond return/Sharpe/drawdown/win-rate/trades/open-positions worth knowing about:

- **Health** — `healthy` / `warning` / `decayed` / `new`, from `engine/metrics.decay_status`. Splits a strategy's equity curve in half and compares second-half Sharpe to first-half Sharpe; `new` until there are ≥20 equity samples. This flags a strategy whose edge is fading even while its all-time Sharpe still looks fine — check it before trusting a strategy's rank blindly.

For how the AI strategies actually make decisions (prompting, structured output, validation), how the cross-sectional factor strategies rank assets, or how to add a new strategy/fetcher, see the papertrader-extend skill.
