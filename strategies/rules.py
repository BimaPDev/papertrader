"""Rule-based strategies. Each is deliberately simple and distinct so the
leaderboard compares real styles rather than variations of one idea."""

from strategies.base import Decision, Strategy


def _has(snap, *keys) -> bool:
    return all(snap.get(k) is not None for k in keys)


class EmaCross(Strategy):
    """Buy 12/26 EMA cross up (price above EMA50); sell cross down."""
    name = "EMA oscillator crossing"

    def decide(self, snapshots, positions):
        out = []
        for sym, s in snapshots.items():
            if s.get("ema_cross_up") and s.get("above_ema50") and sym not in positions:
                out.append(Decision(sym, "buy", "EMA12 crossed above EMA26 in uptrend", 70))
            elif s.get("ema_cross_down") and sym in positions:
                out.append(Decision(sym, "sell", "EMA12 crossed below EMA26", 70))
        return out


class RsiReversion(Strategy):
    """Buy oversold (RSI<30) with positive OBV; exit overbought (RSI>70)."""
    name = "RSI mean reversion"

    def decide(self, snapshots, positions):
        out = []
        for sym, s in snapshots.items():
            if not _has(s, "rsi"):
                continue
            if s["rsi"] < 30 and s.get("obv_slope_pos") and sym not in positions:
                out.append(Decision(sym, "buy", f"RSI {s['rsi']} oversold, OBV rising", 65))
            elif s["rsi"] > 70 and sym in positions:
                out.append(Decision(sym, "sell", f"RSI {s['rsi']} overbought", 65))
        return out


class BurstPersistence(Strategy):
    """Buy 20-bar breakouts on a volume burst; exit on 20-bar breakdown."""
    name = "Burst persistence"

    def decide(self, snapshots, positions):
        out = []
        for sym, s in snapshots.items():
            if s.get("breakout_20") and (s.get("vol_z") or 0) > 1.5 and sym not in positions:
                out.append(Decision(sym, "buy", f"20-bar breakout, vol z {s['vol_z']}", 70))
            elif s.get("breakdown_20") and sym in positions:
                out.append(Decision(sym, "sell", "20-bar breakdown", 70))
        return out


class VolScaledMomentum(Strategy):
    """Momentum entries only when volatility is moderate; exit when momentum
    flips or volatility blows out."""
    name = "Vol-scaled cooldown"

    def decide(self, snapshots, positions):
        out = []
        for sym, s in snapshots.items():
            if not _has(s, "mom_6", "mom_24", "atr_pct"):
                continue
            if sym not in positions and s["mom_6"] > 2 and s["mom_24"] > 0 and s["atr_pct"] < 5:
                out.append(Decision(sym, "buy",
                                    f"mom6 {s['mom_6']}%, calm vol {s['atr_pct']}%", 60))
            elif sym in positions and (s["mom_6"] < -1 or s["atr_pct"] > 8):
                out.append(Decision(sym, "sell", "momentum flipped or vol spike", 60))
        return out


class MacdTrend(Strategy):
    """Buy MACD histogram flipping positive above EMA50; sell flip negative."""
    name = "MACD trend flip"

    def decide(self, snapshots, positions):
        out = []
        for sym, s in snapshots.items():
            if not _has(s, "macd_hist", "macd_hist_prev"):
                continue
            up = s["macd_hist_prev"] <= 0 < s["macd_hist"]
            down = s["macd_hist_prev"] >= 0 > s["macd_hist"]
            if up and s.get("above_ema50") and sym not in positions:
                out.append(Decision(sym, "buy", "MACD histogram flipped positive", 60))
            elif down and sym in positions:
                out.append(Decision(sym, "sell", "MACD histogram flipped negative", 60))
        return out


class VolumePressure(Strategy):
    """Buy when OBV rises with positive 24-bar momentum; sell when OBV turns."""
    name = "Vol-normalized pressure"

    def decide(self, snapshots, positions):
        out = []
        for sym, s in snapshots.items():
            if not _has(s, "mom_24"):
                continue
            if s.get("obv_slope_pos") and s["mom_24"] > 1 and (s.get("vol_z") or 0) > 0.5 \
                    and sym not in positions:
                out.append(Decision(sym, "buy", "OBV rising with momentum + volume", 60))
            elif s.get("obv_slope_pos") is False and sym in positions:
                out.append(Decision(sym, "sell", "OBV pressure faded", 60))
        return out


RULE_STRATEGIES: list[Strategy] = [
    EmaCross(), RsiReversion(), BurstPersistence(),
    VolScaledMomentum(), MacdTrend(), VolumePressure(),
]
