---
name: papertrader-setup
description: "Use when setting up, installing, or first running the PaperTrader project — Python venv, dependencies, .env API keys (CoinGecko, Birdeye, Anthropic), and the first --once run."
---

# PaperTrader setup

PaperTrader is a multi-strategy crypto paper trader: real market data in, simulated fills out, no real money or exchange accounts involved. Requires Python 3.11+.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## Configure `.env`

| Key | Required | Notes |
|---|---|---|
| `COINGECKO_API_KEY` | Recommended | Demo/free-tier key works — get one at coingecko.com/en/api. Runs without it but hits stricter anonymous rate limits. |
| `BIRDEYE_API_KEY` | Yes | Needed for Solana meme-coin discovery/OHLCV. Without it, meme fetches silently return nothing (majors still work). Get one at birdeye.so. |
| `GMGN_API_KEY` | Optional | Powers GMGN Solana trenches + copy-wallet monitoring in `monitor.py` (https://gmgn.ai/ai). Soft-skips if missing; pump.fun grads still work. |
| `GMGN_COPY_WALLETS` | Optional | Comma-separated Solana wallets to mirror (same as `MONITOR_COPY_WALLETS` in `config.py`). |
| `ANTHROPIC_API_KEY` | Yes (for AI strategies) | Powers the 3 AI personas. Rule-based strategies work without it; AI strategies error per-cycle and hold if missing. |
| `DEEPSEEK_KEY` / `GROQ_API_KEY` | Only if using an OpenAI-compatible provider | See the papertrader-configure skill. |

No database, no Docker. State is plain files under `data/` (gitignored, created on first run).

## First run

Always do a single-cycle run first to confirm keys and network access work, before leaving it looping:

```bash
.venv/bin/python main.py --once
```

If it prints a leaderboard table at the end, setup is working. For ongoing running and interpreting output, see the papertrader-run skill.
