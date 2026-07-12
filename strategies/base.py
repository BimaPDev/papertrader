"""Strategy interface. A strategy sees all asset snapshots plus its own open
positions and returns per-symbol decisions."""

from dataclasses import dataclass


@dataclass
class Decision:
    symbol: str
    action: str          # "buy" | "sell" | "hold"
    reason: str = ""
    confidence: int = 50  # 0-100


class Strategy:
    name: str = "base"

    def decide(self, snapshots: dict[str, dict], positions: dict[str, dict]) -> list[Decision]:
        """snapshots: {symbol: snapshot dict}; positions: this strategy's open
        positions {symbol: {...entry info}}. Return decisions (hold may be omitted)."""
        raise NotImplementedError
