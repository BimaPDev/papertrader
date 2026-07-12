---
name: papertrader-configure
description: "Use when tuning PaperTrader's config.py (assets, position sizing, fees, stops, cadence) or swapping the AI provider between Claude and an OpenAI-compatible endpoint (DeepSeek/Groq/Ollama)."
---

# Configuring PaperTrader

Everything tunable lives in `config.py` — read live on every run, no reload/restart mechanics needed, just edit and rerun.

| Setting | Default | Meaning |
|---|---|---|
| `MAJORS` | BTC/ETH/SOL/XRP/DOGE | CoinGecko coin-id → display symbol map |
| `MEME_TOKEN_COUNT` | 5 | how many trending Solana tokens to track per cycle |
| `MIN_LIQUIDITY_USD` / `MIN_VOLUME_24H_USD` | 100k / 50k | meme-token filters |
| `EXCLUDED_TOKENS` | stables/wrapped majors | addresses never traded even if trending |
| `DAYS_BACK` | 7 | history window pulled for indicator warmup |
| `INITIAL_BALANCE` | 1000.0 | starting virtual USD per strategy |
| `POSITION_PCT` | 25.0 | % of cash deployed per new position |
| `MAX_OPEN_POSITIONS` | 3 | concurrent positions per strategy |
| `FEE_PCT` / `SLIPPAGE_PCT` | 0.30 / 0.20 | simulated cost per side, % |
| `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` | 12.0 / 25.0 | hard exit thresholds |
| `AI_PROVIDER` | `"claude"` | `"claude"` or `"openai_compatible"` |
| `AI_MODEL` | `"claude-opus-4-8"` | swap to `"claude-haiku-4-5"` for ~5x cheaper cycles |
| `CYCLE_MINUTES` | 60 | loop cadence; also used to annualize Sharpe |

Changing `INITIAL_BALANCE`, fees, or stops mid-run only affects *new* fills/positions going forward — existing open positions in `data/state.json` keep whatever they were opened under. For a clean comparison after a config change, reset with `rm -rf data/` (see papertrader-run skill).

## Swapping the AI provider

Set in `config.py`:
```python
AI_PROVIDER = "openai_compatible"
OPENAI_COMPAT_BASE_URL = "https://api.deepseek.com/v1"   # or Groq/Ollama endpoint
OPENAI_COMPAT_MODEL = "deepseek-chat"
OPENAI_COMPAT_KEY_ENV = "DEEPSEEK_KEY"                     # name of the .env var holding the key
```
Put the actual key in `.env` under whatever env var name `OPENAI_COMPAT_KEY_ENV` points to (`DEEPSEEK_KEY` and `GROQ_API_KEY` are already scaffolded in `.env.example`). Strategies never touch provider details directly — only `llm.py` changes behavior; it hits any OpenAI-compatible chat-completions endpoint with a JSON-mode prompt instead of Claude's native structured output (since not all such providers support structured output the same way).
