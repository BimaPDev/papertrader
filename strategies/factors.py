"""Cross-sectional factor strategies, adapted from the classic academic
factors in HKUDS/Vibe-Trading's Alpha Zoo (George & Hwang 2004, Carhart 1997,
Jegadeesh 1990, Amihud 2002). Unlike strategies/rules.py, these rank *all*
assets against each other every cycle instead of judging one asset against a
fixed threshold — exploiting the fact that main.py already hands every
strategy the full snapshot set at once."""

import config
from strategies.base import Decision, Strategy


class CrossSectionalMomentum(Strategy):
    """Buy the top momentum quartile across all assets, exit anything that
    falls out of the top half. Relative strength, not an absolute mom_24
    threshold — inspired by Carhart (1997) UMD and George & Hwang (2004)
    52-week-high momentum."""
    name = "Cross-sectional momentum"

    def decide(self, snapshots, positions):
        scored = [(sym, s["mom_24"]) for sym, s in snapshots.items() if s.get("mom_24") is not None]
        if not scored:
            return []
        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = [sym for sym, _ in scored]
        n = len(ranked)
        top_quartile = set(ranked[: max(1, n // 4)])
        top_half = set(ranked[: max(1, n // 2)])

        out = []
        for sym in top_quartile:
            if sym not in positions:
                out.append(Decision(sym, "buy",
                                     f"top momentum quartile (mom24 {snapshots[sym]['mom_24']}%)", 65))
        for sym in positions:
            if sym not in top_half:
                out.append(Decision(sym, "sell", "dropped out of top momentum half", 60))
        return out


class LiquidityAdjustedReversal(Strategy):
    """Buy the sharpest short-term losers that still clear a liquidity bar —
    Jegadeesh (1990) short-term reversal, gated by an Amihud (2002)-style
    illiquidity check so a meme coin's dip isn't mistaken for the start of a
    rug. Exit once the bounce plays out."""
    name = "Liquidity-adjusted reversal"

    def decide(self, snapshots, positions):
        candidates = []
        for sym, s in snapshots.items():
            mom6 = s.get("mom_6")
            if mom6 is None:
                continue
            if s.get("kind") == "meme":
                liq, vol = s.get("liquidity") or 0, s.get("volume24h") or 0
                # extra buffer above the fetcher's own trending-list floor —
                # a thin, crashing meme coin is a rug risk, not a reversal
                if liq < config.MIN_LIQUIDITY_USD * 1.5 or vol < config.MIN_VOLUME_24H_USD * 1.5:
                    continue
            candidates.append((sym, mom6))
        candidates.sort(key=lambda x: x[1])  # biggest losers first

        out = []
        for sym, mom6 in candidates[:2]:
            if mom6 < -3 and sym not in positions:
                out.append(Decision(sym, "buy", f"short-term reversal candidate (mom6 {mom6}%)", 55))
        for sym in positions:
            s = snapshots.get(sym)
            if s and (s.get("mom_6") or 0) > 2:
                out.append(Decision(sym, "sell", "reversal played out", 55))
        return out


FACTOR_STRATEGIES: list[Strategy] = [CrossSectionalMomentum(), LiquidityAdjustedReversal()]
