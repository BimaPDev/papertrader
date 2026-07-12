"""
PaperTrader configuration.

Everything tunable lives here: assets, data windows, portfolio settings,
fees, AI provider, and loop cadence.
"""

# ── Assets ──────────────────────────────────────────────────────────────────
# Majors fetched from CoinGecko (id → display symbol)
MAJORS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "ripple": "XRP",
    "dogecoin": "DOGE",
}

# Solana meme coins: discovered dynamically from Birdeye trending each cycle
MEME_TOKEN_COUNT = 5          # how many trending meme tokens to track
MIN_LIQUIDITY_USD = 100_000   # skip tokens thinner than this
MIN_VOLUME_24H_USD = 50_000   # skip tokens with less 24h volume

# Tokens never traded (stables / wrapped majors returned by trending)
EXCLUDED_TOKENS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "So11111111111111111111111111111111111111112",   # wSOL
    "So11111111111111111111111111111111111111111",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",  # WBTC
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # WETH
}

# ── Market data ─────────────────────────────────────────────────────────────
CANDLE_TIMEFRAME = "1H"   # Birdeye candle type; CoinGecko data resampled to match
DAYS_BACK = 7             # history window for indicators

# ── Paper portfolios ────────────────────────────────────────────────────────
INITIAL_BALANCE = 1_000.0   # virtual USD each strategy starts with
POSITION_PCT = 25.0         # % of cash deployed per new position
MAX_OPEN_POSITIONS = 3      # per strategy
FEE_PCT = 0.30              # taker fee per side, %
SLIPPAGE_PCT = 0.20         # simulated slippage per side, %
STOP_LOSS_PCT = 12.0        # hard stop below entry
TAKE_PROFIT_PCT = 25.0      # hard take-profit above entry

# ── AI decision layer ───────────────────────────────────────────────────────
# Provider: "claude", "openai_compatible" (DeepSeek/Groq/Ollama), or
# "hermes_ssh" (route through a Hermes Agent instance over SSH)
AI_PROVIDER = "hermes_ssh"
AI_MODEL = "claude-opus-4-8"
# Cheaper option for frequent cycles: AI_MODEL = "claude-haiku-4-5"

# Used only when AI_PROVIDER = "openai_compatible":
OPENAI_COMPAT_BASE_URL = "https://api.deepseek.com/v1"
OPENAI_COMPAT_MODEL = "deepseek-chat"
OPENAI_COMPAT_KEY_ENV = "DEEPSEEK_KEY"

# Used only when AI_PROVIDER = "hermes_ssh": mirrors the same SSH pattern
# this homelab's AgentOS project already uses to talk to Hermes.
HERMES_SSH_HOST = "192.168.50.86"
HERMES_SSH_PORT = 22
HERMES_SSH_USER = "hermes"
HERMES_SSH_KEY_PATH = "/app/secrets/papertrader_hermes_ed25519"
HERMES_BIN_PATH = "/home/hermes/.hermes/hermes-agent/venv/bin/hermes"
HERMES_WORKDIR = "/home/hermes"

AI_MAX_TOKENS = 2048

# ── Loop ────────────────────────────────────────────────────────────────────
CYCLE_MINUTES = 60          # matches 1H candles; also used to annualize Sharpe

# ── Graduation monitor (monitor.py) ─────────────────────────────────────────
# Watches pump.fun tokens that just completed the bonding curve ("graduated").
# Separate from the hourly paper loop — these tokens rarely have ≥30 hourly
# bars yet, so they won't feed strategies until you wire a short-history path.
MONITOR_POLL_SECONDS = 15           # how often to poll pump.fun / GMGN
MONITOR_FETCH_LIMIT = 40            # candidates pulled per source per poll
MONITOR_MAX_AGE_MINUTES = 120       # ignore graduates older than this
MONITOR_MIN_MARKET_CAP_USD = 5_000  # drop dust / dead post-grad prints
MONITOR_MIN_HOLDERS = 10
MONITOR_MAX_SNIPER_PCT = 40.0       # skip if snipers own more than this %
MONITOR_MAX_TOP_HOLDERS_PCT = 60.0  # skip extreme concentration
MONITOR_MAX_DEV_HOLDINGS_PCT = 15.0
MONITOR_ENRICH_DEXSCREENER = True   # attach liquidity / pair URL
MONITOR_MIN_LIQUIDITY_USD = 3_000   # post-enrich floor for graduated only
MONITOR_WATCH_NEAR_GRADUATION = True  # also alert bonding tokens near migrate
MONITOR_NEAR_MIN_MARKET_CAP_USD = 40_000
MONITOR_NEAR_MAX_MARKET_CAP_USD = 75_000  # pump.fun grad ~$69k; keep a band
MONITOR_SEEN_CAP = 5_000            # trim persisted mint set
MONITOR_SEEN_BY_MINT = True         # one alert per mint (ignore New→Almost→Migrated repeats)
MONITOR_SWARM_ONCE_PER_MINT = True  # never re-run expensive swarm on the same mint

# DexScreener Solana (https://dexscreener.com/solana) — profiles + boosts
# Off by default while focusing Almost/Migrated; add dex_* to MONITOR_ALERT_KINDS to use.
MONITOR_USE_DEXSCREENER = False
MONITOR_DEX_USE_PROFILES = True     # /token-profiles/latest/v1
MONITOR_DEX_USE_BOOSTS = True       # /token-boosts/latest + top
MONITOR_DEX_MAX_AGE_MINUTES = 24 * 60  # skip older pairs when pairCreatedAt known

# GMGN.ai Solana trenches (https://gmgn.ai/?chain=sol) — New / Almost / Migrated
# Needs GMGN_API_KEY in .env (https://gmgn.ai/ai). Soft-skips if missing.
# Default: skip New (bonding-curve rugs); focus Almost + Migrated.
MONITOR_USE_GMGN = True
MONITOR_GMGN_TYPES = [
    # "new_creation",    # New — usually rugs; enable only if you want early noise
    "near_completion",   # Almost — bonding nearly full
    "completed",         # Migrated — graduated to DEX
]
MONITOR_NEW_MAX_AGE_MINUTES = 30    # New tab: ignore creations older than this
MONITOR_GMGN_FILTER_PRESET = "safe"  # None | "safe" | "strict" (server-side)
MONITOR_GMGN_MAX_RUG_RATIO = 0.3

# Which kinds alert / swarm. Kinds: new, almost, migrated, dex_profile, dex_boost, copy
MONITOR_ALERT_KINDS = ["almost", "migrated"]
MONITOR_SWARM_KINDS = ["almost", "migrated"]

# Copy-trade wallet monitor (GMGN wallet activity + token info/security)
# Put Solana addresses you want to mirror here, and/or set GMGN_COPY_WALLETS
# in .env as a comma-separated list. Soft-skips when empty / no API key.
MONITOR_COPY_ENABLED = True
MONITOR_COPY_WALLETS: list[str] = [
    # "YourCopyTargetWallet1111111111111111111111111",
]
MONITOR_COPY_SIDES = ["buy"]          # investigate buys; sells are logged only
MONITOR_COPY_MIN_USD = 10.0           # ignore dust fills
MONITOR_COPY_FETCH_LIMIT = 20         # recent txs per wallet / feed
MONITOR_COPY_MAX_AGE_MINUTES = 60     # ignore older fills when a wallet is first added
MONITOR_COPY_USE_SMARTMONEY = False   # also poll GMGN public smart-money feed
MONITOR_COPY_USE_KOL = False          # also poll GMGN public KOL feed
MONITOR_COPY_INVESTIGATE = True       # token info+security + swarm on new buys
MONITOR_COPY_MAX_INVESTIGATE = 5      # cap investigations per poll (rate limits)

# Rug/legit swarm + paper sniper (monitor-owned; not in hourly main.py)
MONITOR_SWARM_ENABLED = True
MONITOR_SWARM_MAX_PER_POLL = 5      # max new hits to analyze per poll
MONITOR_SWARM_WORKERS = 5           # parallel specialist LLM calls (5 agents)
MONITOR_PAPER_TRADE = True          # buy paper positions on legit verdicts
MONITOR_PAPER_MIN_CONFIDENCE = 70
MONITOR_PAPER_STRATEGY = "Swarm sniper"

# ── Paths ───────────────────────────────────────────────────────────────────
from pathlib import Path
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
