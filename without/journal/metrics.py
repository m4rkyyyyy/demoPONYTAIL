"""Aggregation helpers for the dashboard.

Everything here is pure: it takes an iterable of closed ``Trade`` objects and
returns a plain dataclass. Keeping the maths out of the views and out of the
model's ``aggregate()`` calls means the zero-trade edge cases live in exactly
one place.
"""

from dataclasses import dataclass
from decimal import Decimal

from journal.models import ZERO


@dataclass(frozen=True)
class JournalStats:
    """Summary of a set of closed trades.

    ``win_rate``, ``profit_factor`` and ``avg_r`` are ``None`` when they are
    mathematically undefined (no trades, or no losing trades to divide by).
    Templates render those as ``N/A`` instead of guessing a number.
    """

    closed_count: int = 0
    open_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0
    gross_wins: Decimal = ZERO
    gross_losses: Decimal = ZERO
    total_fees: Decimal = ZERO
    net_pnl: Decimal = ZERO
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_r: float | None = None
    avg_win: Decimal | None = None
    avg_loss: Decimal | None = None
    expectancy: Decimal | None = None
    best_trade: Decimal | None = None
    worst_trade: Decimal | None = None


def summarize(closed_trades, open_count: int = 0) -> JournalStats:
    """Build :class:`JournalStats` from an iterable of closed trades.

    ``open_count`` is passed separately so the dashboard can show open
    positions without them polluting the win/loss maths.
    """
    trades = list(closed_trades)
    nets = [t.net_pnl for t in trades if t.net_pnl is not None]

    wins = [n for n in nets if n > ZERO]
    losses = [n for n in nets if n < ZERO]
    breakeven = [n for n in nets if n == ZERO]

    gross_wins = sum(wins, ZERO)
    gross_losses = abs(sum(losses, ZERO))

    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]

    stats = JournalStats(
        closed_count=len(nets),
        open_count=open_count,
        win_count=len(wins),
        loss_count=len(losses),
        breakeven_count=len(breakeven),
        gross_wins=gross_wins,
        gross_losses=gross_losses,
        total_fees=sum((t.fees or ZERO for t in trades), ZERO),
        net_pnl=sum(nets, ZERO),
        win_rate=(len(wins) / len(nets) * 100) if nets else None,
        # Undefined without a losing trade to divide by: infinite, not zero.
        profit_factor=float(gross_wins / gross_losses) if gross_losses else None,
        avg_r=float(sum(r_values, ZERO) / len(r_values)) if r_values else None,
        avg_win=(gross_wins / len(wins)) if wins else None,
        avg_loss=(gross_losses / len(losses)) if losses else None,
        expectancy=(sum(nets, ZERO) / len(nets)) if nets else None,
        best_trade=max(nets) if nets else None,
        worst_trade=min(nets) if nets else None,
    )
    return stats


def equity_curve(closed_trades, starting_balance: Decimal = ZERO) -> list[dict]:
    """Cumulative net P&L over time, ordered by exit date.

    Returns Chart.js-ready points: a short axis label and a float equity
    value. ``starting_balance`` seeds the curve with the accounts' combined
    opening balance so the first point is not pinned to zero.
    """
    trades = sorted(
        (t for t in closed_trades if t.exit_date and t.net_pnl is not None),
        key=lambda t: (t.exit_date, t.pk or 0),
    )

    running = Decimal(starting_balance)
    points = [{"label": "Start", "equity": float(running)}]
    for trade in trades:
        running += trade.net_pnl
        points.append(
            {
                "label": trade.exit_date.strftime("%d %b %H:%M"),
                "equity": float(running),
                "symbol": trade.symbol,
            }
        )
    return points
