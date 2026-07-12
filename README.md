# PaperTrader

Multi-strategy crypto paper trader. Fetches real market data for major coins
(BTC/ETH/SOL/XRP/DOGE via CoinGecko) and trending Solana meme coins (via
Birdeye), cleans it into indicator snapshots, and runs 11 strategies — 6
per-asset rule-based, 2 cross-sectional factor strategies, and 3 AI personas
(Claude) — each against its own virtual $1,000 portfolio. A leaderboard ranks
them by Sharpe, like a live out-of-sample backtest.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in BIRDEYE_API_KEY, COINGECKO_API_KEY, ANTHROPIC_API_KEY

.venv/bin/python main.py --once   # single cycle
.venv/bin/python main.py          # run forever (one cycle per hour)
.venv/bin/python report.py        # print the leaderboard anytime

.venv/bin/python monitor.py --seed  # seed current pump.fun grads (no alerts)
.venv/bin/python monitor.py         # watch new graduates / near-grads forever
.venv/bin/python monitor.py --once  # single monitor poll
```

## How a cycle works

1. **Fetch** — CoinGecko hourly data for majors; Birdeye trending discovery +
   OHLCV for Solana memes (filtered by liquidity/volume floors).
2. **Clean** — `pipeline/` computes EMA/RSI/MACD/ATR/OBV/volume z-score and
   distills each asset into a compact snapshot dict.
3. **Risk first** — every portfolio's stop-loss (-12%) / take-profit (+25%)
   levels are enforced before new decisions.
4. **Decide** — each strategy sees all snapshots + its own positions and
   returns buy/sell/hold. AI strategies make one structured-output model call
   per cycle. Fills simulate 0.3% fee + 0.2% slippage per side.
5. **Record** — state to `data/state.json`, trades to `data/trades.csv`,
   equity curves to `data/equity.csv`, then the leaderboard prints.

## Strategies

| Rule-based (per-asset) | Cross-sectional factor | AI (Claude personas) |
|---|---|---|
| EMA oscillator crossing | Cross-sectional momentum | AI momentum |
| RSI mean reversion | Liquidity-adjusted reversal | AI contrarian |
| Burst persistence (breakout + volume) | | AI meme degen |
| Vol-scaled cooldown (momentum, calm vol) | | |
| MACD trend flip | | |
| Vol-normalized pressure (OBV) | | |

The two factor strategies (`strategies/factors.py`) rank *all* assets against
each other every cycle instead of judging one asset against a fixed
threshold — adapted from classic academic factors (Carhart 1997, George &
Hwang 2004, Jegadeesh 1990, Amihud 2002) in HKUDS's
[Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) Alpha Zoo. The
leaderboard's **Health** column is the same idea applied to a strategy's own
track record: it compares each strategy's second-half Sharpe to its
first-half Sharpe (`engine/metrics.decay_status`) and flags `healthy` /
`warning` / `decayed` — a lightweight version of Vibe-Trading's
factor/strategy decay monitor.

## Configuration

Everything lives in `config.py`: assets, position sizing, fees, stops, cycle
cadence, and the AI provider. Default model is `claude-opus-4-8`; switch to
`claude-haiku-4-5` for ~5x cheaper cycles, or set
`AI_PROVIDER = "openai_compatible"` to use DeepSeek/Groq/Ollama.

## Graduation + copy-trade monitor

`monitor.py` is a separate fast loop for tokens that **just left** launchpad
bonding curves (and optionally ones near graduation), plus wallets you want to
**copy-trade**:

- **pump.fun** graduated feed (no key)
- **GMGN.ai** Solana trenches — **New / Almost / Migrated**
  (`new_creation` / `near_completion` / `completed`) — needs `GMGN_API_KEY`
  from https://gmgn.ai/ai
- **GMGN copy wallets** — poll `wallet_activity` for addresses in
  `MONITOR_COPY_WALLETS` / `GMGN_COPY_WALLETS`; each new **buy** is checked via
  GMGN `token/info` + `token/security` + holders/traders/creator 7d stats
  (phishing, Analysis %, win rate, avg buy MC, distribution) then scored by
  the rug/legit swarm
- **DexScreener** enrichment for liquidity / pair URL when missing

Filters concentration/sniper/rug risk, logs new mints to `data/graduates.csv`
and copy events to `data/copy_trades.csv` (seen set in `data/monitor_seen.json`).
On each **new** alert a specialist swarm (Security / Holders / Traders /
Liquidity / Social + Orchestrator) scores rug vs suspicious vs legit using those
GMGN analytics → `data/swarm_verdicts.csv`. With `MONITOR_PAPER_TRADE`, `legit`
tokens (≥ `MONITOR_PAPER_MIN_CONFIDENCE`) get a paper buy in the **Swarm sniper**
portfolio inside `data/state.json` (SL/TP enforced each poll). Tunables live
under `MONITOR_*` in `config.py`. The swarm does **not** feed the hourly
indicator strategies — fresh grads usually lack the ≥30 hourly bars those need.

After adding copy wallets, run `python monitor.py --seed` once so historical
fills are marked seen (or rely on `MONITOR_COPY_MAX_AGE_MINUTES`).

## Notes

- Sharpe/drawdown need a few dozen cycles of equity history to be meaningful —
  let it run for a day or two before reading much into the leaderboard.
- Meme tokens rotate with Birdeye trending; a held token that drops out of
  trending is still marked at its last known price until it reappears.
- To reset all portfolios: delete the `data/` directory.
