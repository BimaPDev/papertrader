---
name: papertrader-troubleshoot
description: "Use when PaperTrader cycles fail, produce no data, AI strategies keep holding, or fetchers return empty results — diagnostic steps for common failure modes."
---

# Troubleshooting PaperTrader

- **"No usable market data this cycle; skipping."** — both fetchers returned nothing. Check `COINGECKO_API_KEY`/`BIRDEYE_API_KEY` in `.env`, check network access, and check CoinGecko/Birdeye status. Each asset needs ≥30 bars of history before `pipeline/snapshot.py:build_snapshot` will produce a snapshot for it — a brand-new/thin meme token may simply not have enough candle history yet.

- **AI strategy always holds / prints `⚠️ ... AI call failed`** — usually a missing or invalid `ANTHROPIC_API_KEY`, or (for `openai_compatible` mode) a missing key under whatever env var `OPENAI_COMPAT_KEY_ENV` points to. This fails soft by design — `strategies/ai.py` catches the exception and treats it as "hold everything" for that strategy that cycle, so it never crashes the run. Check the printed error text for the specific cause (auth, rate limit, timeout). A suspiciously short key value (real Anthropic keys are ~100+ chars) usually means `.env` still has the `.env.example` placeholder, never actually replaced.

- **`hermes_ssh` provider fails** — check in order: (1) `ssh -i <HERMES_SSH_KEY_PATH> hermes@<HERMES_SSH_HOST>` connects at all — key not mounted, wrong permissions (must be `600`), or the public half was never added to that host's `authorized_keys`; (2) `hermes chat` itself works when run manually over that same SSH session — if the underlying Hermes provider (e.g. its `omniroute` config) is down or logged out, the SSH layer succeeds but the chat call fails; (3) `llm._extract_json` raising "no JSON object found" — the model answered conversationally despite the schema instruction; check what it actually said, since `hermes chat` is a full agent, not a raw completion API, and can ignore formatting instructions more than a dedicated structured-output call would.

- **Rate limiting from CoinGecko** — the demo tier is ~30 req/min; `fetchers/coingecko.py:fetch_major_markets` already sleeps 1.5s between coins. If you add more majors (see papertrader-extend skill), expect fetch time to grow proportionally, and consider a paid key.

- **Leaderboard says "No data yet — run main.py first."** — `report.py` reads `data/state.json` + `data/equity.csv`; run `.venv/bin/python main.py --once` at least once before calling `report.py` on its own.

- **Sharpe/leaderboard numbers look meaningless** — Sharpe needs at least a few dozen equity samples (`engine/metrics.py:sharpe` returns 0 below 3 samples and is noisy well beyond that). Let it run for a day or two of hourly cycles before drawing conclusions.

- **Want a clean slate** — `rm -rf data/`; nothing else holds state. Regenerated automatically on the next run.

- **One strategy's logic is broken but others should keep working** — this is already handled: `main.py:run_cycle` wraps each strategy's `decide()` call in a try/except and treats a raised exception as "hold" for that strategy only, printing the traceback. Check the printed traceback to find which strategy and why.
