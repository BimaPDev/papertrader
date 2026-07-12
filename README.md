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

## Notes

- Sharpe/drawdown need a few dozen cycles of equity history to be meaningful —
  let it run for a day or two before reading much into the leaderboard.
- Meme tokens rotate with Birdeye trending; a held token that drops out of
  trending is still marked at its last known price until it reappears.
- To reset all portfolios: delete the `data/` directory.
