---
name: papertrader-extend
description: "Use when adding a new trading strategy (rule-based or AI persona) or a new market data fetcher/asset source to PaperTrader, or when explaining how the AI strategies make decisions."
---

# Extending PaperTrader

## File map

```
strategies/base.py     Strategy interface + Decision dataclass
strategies/rules.py    6 per-asset rule-based strategies (pure Python, no LLM calls)
strategies/factors.py  2 cross-sectional factor strategies (rank all assets against each other)
strategies/ai.py       3 Claude-persona strategies (one model call per cycle each)
strategies/__init__.py ALL_STRATEGIES = RULE_STRATEGIES + FACTOR_STRATEGIES + AI_STRATEGIES

fetchers/coingecko.py  majors (BTC/ETH/SOL/XRP/DOGE) → hourly OHLCV
fetchers/birdeye.py    Solana meme discovery + OHLCV, liquidity/volume filtered

pipeline/indicators.py pure-pandas TA: EMA12/26/50, RSI(14), MACD, ATR%, OBV+slope, vol z-score, 20-bar hi/lo, momentum
pipeline/snapshot.py   collapses an indicator-enriched OHLCV frame into the flat dict every strategy consumes

engine/metrics.py      Sharpe/return/max-drawdown + decay_status (rolling-vs-baseline Sharpe health check)

llm.py                 LLM client (Claude + OpenAI-compatible adapter), structured output schema
skills_loader.py        Reads skills/<name>/SKILL.md for injection into an AI persona's system prompt
skills/                 Reference docs, both self-authored (papertrader-*) and imported from HKUDS/Vibe-Trading
```

## Adding a rule-based strategy

Subclass `Strategy` in `strategies/rules.py` (or a new file), implement:
```python
def decide(self, snapshots: dict[str, dict], positions: dict[str, dict]) -> list[Decision]:
```
Add an instance to `RULE_STRATEGIES` (or directly to `ALL_STRATEGIES` in `strategies/__init__.py`). Always guard snapshot field access with `.get()` — indicator fields are `None` early in an asset's history. Look at the existing 6 classes for the pattern.

## Adding a cross-sectional factor strategy

`decide()` already receives the *entire* `snapshots` dict for the cycle, not just one asset — `strategies/factors.py` exploits that to rank all assets against each other instead of comparing one asset to a fixed threshold, mirroring how HKUDS/Vibe-Trading's Alpha Zoo factors work (compute a score per asset, cross-sectionally rank/z-score it, trade the extremes):

- **Cross-sectional momentum** — ranks every asset by `mom_24`, buys the top quartile, exits anything that falls out of the top half. Adapted from Carhart (1997) UMD momentum / George & Hwang (2004) 52-week-high momentum: relative strength, not an absolute threshold.
- **Liquidity-adjusted reversal** — ranks assets by `mom_6` ascending (biggest recent losers first), buys the top losers *if* they clear an extra liquidity/volume buffer for meme-kind assets, exits once `mom_6` recovers. Adapted from Jegadeesh (1990) short-term reversal, gated by an Amihud (2002)-style illiquidity check so a meme coin's dip isn't mistaken for a rug in progress.

To add another one: subclass `Strategy` in `strategies/factors.py`, sort/rank `snapshots.items()` by whatever score you want (a single field, or a combination), decide top/bottom cutoffs, and add the instance to `FACTOR_STRATEGIES`. The same `.get()`-guarding rule from rule-based strategies applies.

## Adding an AI persona

Subclass `AIStrategy` in `strategies/ai.py`, set `name` and a `persona` string (appended to the shared `BASE_RULES` system prompt), add an instance to `AI_STRATEGIES`. No other wiring needed.

### How the AI strategies actually work

Each AI strategy makes **one Claude API call per cycle** (`AIStrategy.decide` in `strategies/ai.py`):

1. **Payload** — that cycle's asset snapshots + this strategy's own open positions, bundled as JSON.
2. **Prompt** — shared `BASE_RULES` (return one decision per symbol; only sell what you hold; fees+slippage cost ~1% round trip so don't churn; weigh liquidity for memes) + that persona's specific style (momentum / contrarian / meme degen). Same market data, three prompts, three independently-tracked portfolios — that's the comparison the leaderboard is built on.
3. **Call** — `llm.py` uses `client.messages.parse(..., output_format=CycleDecisions)`, i.e. native structured output, not prompt-engineered JSON parsing:
   ```python
   class AssetDecision(BaseModel):
       symbol: str
       action: Literal["buy", "sell", "hold"]
       confidence: int
       reason: str
   class CycleDecisions(BaseModel):
       decisions: list[AssetDecision]
   ```
4. **Validation** — raw output is filtered to symbols that actually exist in this cycle's snapshots, and to `buy`/`sell` only (holds are implicit/dropped). Guards against a hallucinated symbol reaching the fill logic.
5. **Failure handling** — an API error (bad key, rate limit, network) is caught, logged, and treated as "hold everything" for that strategy that cycle. Never crashes the run.

### Skill-backed personas

An `AIStrategy` subclass can set a `skills: list[str]` class attribute naming one or more `skills/<name>/SKILL.md` docs (see `skills_loader.py`). Their content gets appended to that persona's system prompt as reference methodology, wrapped in an explicit instruction not to invent numbers for data the doc assumes but this system doesn't fetch (exchange reserves, sentiment scores, options data, fund filings, etc.) — these docs were written for HKUDS/Vibe-Trading's much larger toolset, not PaperTrader's.

Current wiring, chosen for both relevance and token cost (full docs run ~1-10k tokens each and get paid for every cycle):

| Persona | Skill | Why |
|---|---|---|
| AI momentum | `alpha-zoo` (~1k tokens) | Ties directly to the cross-sectional factor strategies already in `strategies/factors.py` |
| AI contrarian | `risk-analysis` (~2.8k tokens) | VaR/CVaR/drawdown framing fits a persona that's supposed to sit in cash most cycles |
| AI meme degen | `stablecoin-flow` (~2.5k tokens) | Crypto-native capital-flow signal, directly on-theme for meme-coin rotation |

`skills/` also has 8 more imported Vibe-Trading docs (`social-media-intelligence`, `geopolitical-risk`, `hedging-strategy`, `ml-strategy`, `performance-attribution`, `minute-analysis`, `fund-analysis`, `private-company-research`) that are **not** wired into any persona by default:
- `social-media-intelligence` (~10k tokens) and `geopolitical-risk` (~8k tokens) are relevant but expensive — attach them (add to a persona's `skills` list) only if you're OK with the added per-cycle cost.
- `hedging-strategy` assumes short/options capability `engine/portfolio.py` doesn't have (spot-only, no shorting). `ml-strategy` assumes a training pipeline that doesn't exist here. `minute-analysis` assumes minute bars; PaperTrader runs on `CANDLE_TIMEFRAME = "1H"`. `fund-analysis` and `private-company-research` are equity/VC research — no overlap with a 5-asset crypto paper trader. They're kept as reference docs for completeness, not wired in.

To add a skill to a persona: add its name to that class's `skills` list in `strategies/ai.py`. To add a brand-new skill doc: drop a `skills/<name>/SKILL.md` file (YAML frontmatter + markdown body, `skills_loader.load_skill` strips the frontmatter automatically).

## Adding a new asset source

Write a fetcher returning `{symbol: pd.DataFrame}` with columns `open, high, low, close, volume` and a `DatetimeIndex`, mirroring `fetchers/coingecko.py`. Set `df.attrs["kind"]` to `"major"` or `"meme"` — `pipeline/snapshot.py` branches on this (memes get extra `liquidity`/`volume24h`/`address` fields). Merge its output into `markets` in `main.run_cycle`, alongside the existing `markets.update(memes)` call. An asset needs ≥30 bars of history before `build_snapshot` will produce a usable snapshot for it.

For tuning position sizing, fees, stops, or swapping the AI provider, see the papertrader-configure skill.
