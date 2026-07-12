"""AI strategies: three personas, each making one model call per cycle over
the full set of cleaned snapshots. Failures degrade to no-op (hold all)."""

import json

from llm import get_decisions
from skills_loader import load_skills
from strategies.base import Decision, Strategy

BASE_RULES = """You are a cryptocurrency paper-trading strategy. You receive
cleaned market snapshots (price, EMAs, RSI, MACD, ATR%, volume z-score, OBV
direction, breakouts, momentum) for major coins and trending Solana meme
coins, plus your currently open positions.

Rules:
- Return one decision per symbol: buy, sell, or hold.
- Only sell symbols you actually hold; only buy symbols you don't hold.
- Fees + slippage cost ~1% round trip — do not churn. Trade only on real edge.
- Meme coins carry rug/liquidity risk; weigh the liquidity figure.
"""


class AIStrategy(Strategy):
    persona = ""
    # Names of skills/<name>/SKILL.md reference docs to inject into this
    # persona's system prompt. Keep this list short: full skill docs run
    # ~1-10k tokens each and this strategy pays that cost every cycle.
    skills: list[str] = []

    def decide(self, snapshots, positions):
        payload = {
            "snapshots": list(snapshots.values()),
            "open_positions": {
                sym: {"entry_price": pos["entry_price"], "opened_at": pos["opened_at"]}
                for sym, pos in positions.items()
            },
        }
        system_prompt = BASE_RULES + self.persona
        if self.skills:
            system_prompt += (
                "\n\nReference methodology below. Apply it only where the snapshot "
                "data actually supports it — most of these docs assume data feeds "
                "(exchange reserves, sentiment scores, options data, fund filings) "
                "that this system does not fetch. Never invent a number to fill a "
                "gap; reason qualitatively instead or skip that part of the framework.\n\n"
                + load_skills(self.skills)
            )
        try:
            raw = get_decisions(system_prompt, json.dumps(payload))
        except Exception as e:
            print(f"  ⚠️  {self.name}: AI call failed ({e}); holding")
            return []
        valid = set(snapshots)
        return [
            Decision(d.symbol, d.action, d.reason, d.confidence)
            for d in raw
            if d.symbol in valid and d.action in ("buy", "sell")
        ]


class AIMomentum(AIStrategy):
    name = "AI momentum (Claude)"
    persona = """
Persona: disciplined momentum trader. Buy strength with volume confirmation
(rising OBV, positive momentum, breakouts). Cut losers fast, let winners run.
Prefer majors; touch memes only on overwhelming confluence."""
    skills = ["alpha-zoo"]


class AIContrarian(AIStrategy):
    name = "AI contrarian (Claude)"
    persona = """
Persona: patient contrarian. Buy fear (oversold RSI, capitulation candles with
volume spikes) and sell greed (overbought, euphoric breakouts late in a run).
Demand confluence before entering; sit in cash most cycles."""
    skills = ["risk-analysis"]


class AIDegen(AIStrategy):
    name = "AI meme degen (Claude)"
    persona = """
Persona: meme-coin specialist. Focus on trending Solana memes with strong
liquidity and accelerating volume; majors only as fallback. Accept higher risk
for asymmetric upside, but never buy fading OBV or thin liquidity."""
    skills = ["stablecoin-flow"]


AI_STRATEGIES: list[Strategy] = [AIMomentum(), AIContrarian(), AIDegen()]
