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
# Provider: "claude" (default) or "openai_compatible" (DeepSeek/Groq/Ollama)
AI_PROVIDER = "claude"
AI_MODEL = "claude-opus-4-8"
# Cheaper option for frequent cycles: AI_MODEL = "claude-haiku-4-5"

# Used only when AI_PROVIDER = "openai_compatible":
OPENAI_COMPAT_BASE_URL = "https://api.deepseek.com/v1"
OPENAI_COMPAT_MODEL = "deepseek-chat"
OPENAI_COMPAT_KEY_ENV = "DEEPSEEK_KEY"

AI_MAX_TOKENS = 2048

# ── Loop ────────────────────────────────────────────────────────────────────
CYCLE_MINUTES = 60          # matches 1H candles; also used to annualize Sharpe

# ── Paths ───────────────────────────────────────────────────────────────────
from pathlib import Path
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
