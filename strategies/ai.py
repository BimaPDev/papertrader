"""AI strategies: three personas, each making one model call per cycle over
the full set of cleaned snapshots. Failures degrade to no-op (hold all)."""

import json

from llm import get_decisions
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

    def decide(self, snapshots, positions):
        payload = {
            "snapshots": list(snapshots.values()),
            "open_positions": {
                sym: {"entry_price": pos["entry_price"], "opened_at": pos["opened_at"]}
                for sym, pos in positions.items()
            },
        }
        try:
            raw = get_decisions(BASE_RULES + self.persona, json.dumps(payload))
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


class AIContrarian(AIStrategy):
    name = "AI contrarian (Claude)"
    persona = """
Persona: patient contrarian. Buy fear (oversold RSI, capitulation candles with
volume spikes) and sell greed (overbought, euphoric breakouts late in a run).
Demand confluence before entering; sit in cash most cycles."""


class AIDegen(AIStrategy):
    name = "AI meme degen (Claude)"
    persona = """
Persona: meme-coin specialist. Focus on trending Solana memes with strong
liquidity and accelerating volume; majors only as fallback. Accept higher risk
for asymmetric upside, but never buy fading OBV or thin liquidity."""


AI_STRATEGIES: list[Strategy] = [AIMomentum(), AIContrarian(), AIDegen()]
