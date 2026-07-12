"""PaperTrader main loop.

Each cycle:
  1. Fetch majors (CoinGecko) + trending Solana memes (Birdeye)
  2. Clean into indicator snapshots
  3. Enforce SL/TP on every strategy's open positions
  4. Ask each strategy (rule-based and AI) for decisions, execute paper fills
  5. Record equity curves and print the leaderboard

Usage:
  python main.py            # run forever, one cycle per CYCLE_MINUTES
  python main.py --once     # single cycle (good for cron or testing)
"""

import sys
import time
import traceback

from rich.console import Console

import config
from engine.portfolio import apply_stops, close_position, equity, new_portfolio, open_position
from engine.store import load_state, log_equity, save_state
from fetchers.birdeye import fetch_meme_markets
from fetchers.coingecko import fetch_major_markets
from monitor_heartbeat import touch_heartbeat
from pipeline.snapshot import build_all
from report import print_leaderboard
from strategies import ALL_STRATEGIES

console = Console()


def run_cycle():
    console.rule("[bold blue]cycle start")

    console.print("Fetching majors from CoinGecko...")
    markets = fetch_major_markets()
    console.print(f"  {len(markets)} majors: {', '.join(markets)}")

    console.print("Fetching trending Solana memes from Birdeye...")
    memes = fetch_meme_markets()
    console.print(f"  {len(memes)} memes: {', '.join(memes) or '(none passed filters)'}")
    markets.update(memes)

    snapshots = build_all(markets)
    if not snapshots:
        console.print("[red]No usable market data this cycle; skipping.[/red]")
        touch_heartbeat("papertrader", status="no_data")
        return
    prices = {sym: s["price"] for sym, s in snapshots.items()}

    state = load_state()
    # Sniper lives in sniper_state.json — strip any legacy shared entry
    state.pop(config.MONITOR_PAPER_STRATEGY, None)
    equities = {}

    for strat in ALL_STRATEGIES:
        p = state.get(strat.name) or new_portfolio(strat.name)
        state[strat.name] = p

        closed = apply_stops(p, prices)
        if closed:
            console.print(f"  {strat.name}: stops closed {', '.join(closed)}")

        try:
            decisions = strat.decide(snapshots, p["positions"])
        except Exception:
            console.print(f"[red]  {strat.name} raised; holding[/red]")
            traceback.print_exc()
            decisions = []

        for d in decisions:
            px = prices.get(d.symbol)
            if px is None:
                continue
            if d.action == "buy":
                if open_position(p, d.symbol, px, d.reason):
                    console.print(f"  [green]{strat.name}: BUY {d.symbol}[/green] — {d.reason}")
            elif d.action == "sell":
                pnl = close_position(p, d.symbol, px, d.reason)
                if pnl is not None:
                    color = "green" if pnl >= 0 else "red"
                    console.print(f"  [{color}]{strat.name}: SELL {d.symbol} "
                                  f"(PnL ${pnl:+.2f})[/{color}] — {d.reason}")

        equities[strat.name] = equity(p, prices)

    save_state(state)
    log_equity(equities)
    print_leaderboard()
    touch_heartbeat("papertrader", strategies=len(ALL_STRATEGIES))


def main():
    once = "--once" in sys.argv
    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            raise
        except Exception:
            console.print("[red]cycle failed:[/red]")
            traceback.print_exc()
        if once:
            break
        console.print(f"😴 sleeping {config.CYCLE_MINUTES} min...\n")
        time.sleep(config.CYCLE_MINUTES * 60)


if __name__ == "__main__":
    main()
