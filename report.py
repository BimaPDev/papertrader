"""Leaderboard: rank all strategies by Sharpe, styled like a backtest table.

Run directly (`python report.py`) or called at the end of each cycle.
"""

from rich.console import Console
from rich.table import Table

from engine.metrics import summarize
from engine.store import load_equity_curves, load_state


def leaderboard() -> list[dict]:
    state = load_state()
    curves = load_equity_curves()
    rows = [
        summarize(name, curves.get(name, []), portfolio)
        for name, portfolio in state.items()
    ]
    rows.sort(key=lambda r: r["sharpe"], reverse=True)
    return rows


def print_leaderboard():
    rows = leaderboard()
    console = Console()
    if not rows:
        console.print("[yellow]No data yet — run main.py first.[/yellow]")
        return

    table = Table(title="📊 Paper Trading Leaderboard (ranked by Sharpe)")
    table.add_column("Rank", justify="right")
    table.add_column("Strategy")
    table.add_column("Equity", justify="right")
    table.add_column("Return", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Win rate", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Open", justify="right")
    table.add_column("Health", justify="right")

    health_style = {"healthy": "green", "warning": "yellow", "decayed": "red", "new": "dim"}
    for i, r in enumerate(rows, 1):
        ret_style = "green" if r["return_pct"] >= 0 else "red"
        health = r["health"]
        table.add_row(
            str(i),
            r["strategy"],
            f"${r['equity']:,.2f}",
            f"[{ret_style}]{r['return_pct']:+.2f}%[/{ret_style}]",
            f"{r['sharpe']:.2f}",
            f"[red]{r['max_dd_pct']:.2f}%[/red]",
            f"{r['win_rate']:.0f}%",
            str(r["trades"]),
            str(r["open_positions"]),
            f"[{health_style[health]}]{health}[/{health_style[health]}]",
        )
    console.print(table)


if __name__ == "__main__":
    print_leaderboard()
