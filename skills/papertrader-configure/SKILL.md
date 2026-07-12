---
name: papertrader-configure
description: "Use when tuning PaperTrader's config.py (assets, position sizing, fees, stops, cadence) or swapping the AI provider between Claude, an OpenAI-compatible endpoint (DeepSeek/Groq/Ollama), or a Hermes Agent instance over SSH."
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
| `AI_PROVIDER` | `"hermes_ssh"` | `"claude"`, `"openai_compatible"`, or `"hermes_ssh"` |
| `AI_MODEL` | `"claude-opus-4-8"` | swap to `"claude-haiku-4-5"` for ~5x cheaper cycles |
| `CYCLE_MINUTES` | 60 | loop cadence; also used to annualize Sharpe |
| `MONITOR_COPY_WALLETS` | `[]` | Solana wallets to mirror via GMGN (or `GMGN_COPY_WALLETS` env) |
| `MONITOR_COPY_USE_SMARTMONEY` / `KOL` | `False` | optional public GMGN trade feeds |
| `MONITOR_COPY_INVESTIGATE` | `True` | run token info/security + swarm on new copy buys |
| `MONITOR_GMGN_TYPES` | Almost + Migrated | drop `new_creation` to skip New-tab rugs |
| `MONITOR_ALERT_KINDS` / `MONITOR_SWARM_KINDS` | `almost`, `migrated` | which stages alert + get graded |

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

### `hermes_ssh` — routing through a Hermes Agent instance

This is the current default. Instead of calling a model API directly, `llm._hermes_ssh` SSHes into a Hermes Agent host and runs `hermes chat -q "<prompt>" -Q --source papertrader --yolo`, the same pattern this homelab's AgentOS project already uses in production — not `hermes proxy` (which is OAuth-gated and only supports `nous`/`xai` upstreams, not whatever provider Hermes's own chat is actually configured to use).

```python
AI_PROVIDER = "hermes_ssh"
HERMES_SSH_HOST = "192.168.50.86"
HERMES_SSH_PORT = 22
HERMES_SSH_USER = "hermes"
HERMES_SSH_KEY_PATH = "/app/secrets/papertrader_hermes_ed25519"
HERMES_BIN_PATH = "/home/hermes/.hermes/hermes-agent/venv/bin/hermes"
HERMES_WORKDIR = "/home/hermes"
```

Requirements this depends on, all outside the git repo:
- A dedicated SSH keypair (not shared with AgentOS's own key) whose public half is appended to `authorized_keys` for the `hermes` user on the Hermes host — additive only, existing entries untouched.
- The private key runtime-mounted at `HERMES_SSH_KEY_PATH` via `docker-compose.yml`'s `./secrets:/app/secrets:ro` volume — `secrets/` is gitignored and dockerignored, never baked into the image or committed.
- `openssh-client` installed in the image (already in `Dockerfile`).

`--yolo` auto-approves any tool/shell call Hermes decides to make mid-response — required so the headless call can't hang waiting for an interactive approval that will never come, but it means there's no confirmation gate on whatever Hermes chooses to do. This was an explicit, separate decision from picking this provider — don't assume it's free to enable on a fresh box without re-confirming.

`hermes chat` is a general agent, not a raw completion API, so despite the JSON-only instruction in the prompt it may wrap the answer in extra text; `llm._extract_json` pulls the first top-level `{...}` block out of stdout rather than assuming the whole response is valid JSON.
