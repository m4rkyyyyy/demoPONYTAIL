"""Seed the database with a reviewable demo journal.

    python manage.py seed_trades

Creates user ``trader / password123``, two accounts and 20 trades (15 closed
with a realistic win/loss mix, 5 still open) so every screen has something to
show. Re-running is safe: existing data is left alone unless ``--force`` is
passed.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from journal.models import AssetClass, Direction, Tag, Trade, TradeStatus, TradingAccount

USERNAME = "trader"
PASSWORD = "password123"

ACCOUNTS = [
    {"name": "Interactive Brokers", "currency": "USD", "initial_balance": Decimal("50000.00")},
    {"name": "Binance Futures", "currency": "USD", "initial_balance": Decimal("25000.00")},
]

# entry/exit are "days ago" so the seed always looks recent.
# Omitting exit/exit_price leaves the trade open.
TRADES = [
    dict(
        symbol="AAPL", asset=AssetClass.STOCKS, direction=Direction.LONG, account=0,
        entry=60, entry_price="187.25", quantity=50, exit=52, exit_price="195.40",
        stop="182.00", target="196.00", fees="1.25", tags=["Breakout", "Earnings"],
        notes="Gapped down, reclaimed the VWAP, scaled out into strength at the target.",
    ),
    dict(
        symbol="MSFT", asset=AssetClass.STOCKS, direction=Direction.LONG, account=0,
        entry=55, entry_price="412.80", quantity=20, exit=49, exit_price="404.10",
        stop="405.00", target="425.00", fees="1.00", tags=["Reversal"],
        notes="Stopped out on a failed reclaim of the 50 EMA. Should have waited for a close.",
    ),
    dict(
        symbol="NVDA", asset=AssetClass.STOCKS, direction=Direction.LONG, account=0,
        entry=48, entry_price="118.40", quantity=60, exit=40, exit_price="127.90",
        stop="114.00", target="130.00", fees="1.50", tags=["Momentum"],
        notes="Held through the earnings gap with a 1R stop. Textbook trend continuation.",
    ),
    dict(
        symbol="EUR/USD", asset=AssetClass.FOREX, direction=Direction.LONG, account=0,
        entry=45, entry_price="1.07200", quantity=10000, exit=41, exit_price="1.08950",
        stop="1.06500", target="1.09500", fees="3.00", tags=["Trend"],
        notes="Backtested weekly demand zone. Let the winner run to the weekly high.",
    ),
    dict(
        symbol="GBP/USD", asset=AssetClass.FOREX, direction=Direction.SHORT, account=0,
        entry=44, entry_price="1.26800", quantity=8000, exit=38, exit_price="1.25900",
        stop="1.27500", target="1.25500", fees="2.50", tags=["Reversal"],
        notes="Daily supply rejection. Covered a third at the 1.26 handle.",
    ),
    dict(
        symbol="BTC/USDT", asset=AssetClass.CRYPTO, direction=Direction.LONG, account=1,
        entry=40, entry_price="61250.000000", quantity="0.350000", exit=33, exit_price="68900.000000",
        stop="58000.00", target="70000.00", fees="18.00", tags=["Breakout"],
        notes="Range break with volume expansion. 25x but sized down to 0.35 BTC.",
    ),
    dict(
        symbol="BTC/USDT", asset=AssetClass.CRYPTO, direction=Direction.SHORT, account=1,
        entry=36, entry_price="69500.000000", quantity="0.250000", exit=31, exit_price="71000.000000",
        stop="71000.00", target="66000.00", fees="15.00", tags=["FOMO"],
        notes="Shorted a blow-off top into resistance with no confirmation. Stopped out on the way back up.",
    ),
    dict(
        symbol="ETH/USDT", asset=AssetClass.CRYPTO, direction=Direction.LONG, account=1,
        entry=33, entry_price="3350.000000", quantity="2.500000", exit=27, exit_price="3480.000000",
        stop="3200.00", target="3600.00", fees="9.00", tags=["Momentum"],
        notes="Higher-low sequence on the 4H. Scaled out half at +2R.",
    ),
    dict(
        symbol="SOL/USDT", asset=AssetClass.CRYPTO, direction=Direction.LONG, account=1,
        entry=30, entry_price="142.300000", quantity="40.000000", exit=25, exit_price="128.600000",
        stop="136.00", target="155.00", fees="6.00", tags=["FOMO"],
        notes="Chased a 12% daily candle. Stop was too tight for the ATR.",
    ),
    dict(
        symbol="SPY", asset=AssetClass.STOCKS, direction=Direction.LONG, account=0,
        entry=28, entry_price="521.40", quantity=30, exit=22, exit_price="528.90",
        stop="514.00", target="532.00", fees="1.00", tags=["Trend"],
        notes="Core index position added to on the dip. Long-term hold.",
    ),
    dict(
        symbol="TSLA", asset=AssetClass.STOCKS, direction=Direction.SHORT, account=0,
        entry=26, entry_price="262.30", quantity=40, exit=19, exit_price="241.15",
        stop="272.00", target="240.00", fees="1.20", tags=["Reversal"],
        notes="Failed breakout at all-time highs. Best risk-adjusted trade this month.",
    ),
    dict(
        symbol="USD/JPY", asset=AssetClass.FOREX, direction=Direction.SHORT, account=0,
        entry=24, entry_price="156.2000", quantity=5000, exit=18, exit_price="157.1000",
        stop="157.5000", target="152.0000", fees="4.00", tags=["Trend"],
        notes="Wrong-footed on a dovish BoJ. Cut just short of the 157.50 stop when momentum flipped.",
    ),
    dict(
        symbol="AUD/USD", asset=AssetClass.FOREX, direction=Direction.LONG, account=0,
        entry=21, entry_price="0.65100", quantity=12000, exit=16, exit_price="0.64800",
        stop="0.64500", target="0.66000", fees="3.60", tags=["Reversal"],
        notes="Counter-trend long into weekly resistance. Small size, small loss.",
    ),
    dict(
        symbol="ES", asset=AssetClass.FUTURES, direction=Direction.LONG, account=0,
        entry=15, entry_price="5630.00", quantity=2, exit=11, exit_price="5705.00",
        stop="5595.00", target="5760.00", fees="6.50", tags=["Breakout"],
        notes="Overnight range breakout. Two contracts, 1.5R on the table.",
    ),
    dict(
        symbol="NQ", asset=AssetClass.FUTURES, direction=Direction.LONG, account=0,
        entry=12, entry_price="19780.00", quantity=1, exit=8, exit_price="19965.00",
        stop="19620.00", target="20100.00", fees="7.00", tags=["Momentum"],
        notes="Opening drive push. Managed to breakeven stop before the real move.",
    ),
    dict(
        symbol="AAPL", asset=AssetClass.STOCKS, direction=Direction.LONG, account=0,
        entry=6, entry_price="196.10", quantity=40,
        stop="190.00", target="205.00", fees="0",
        notes="Add-on to the core position after the earnings beat.",
    ),
    dict(
        symbol="BTC/USDT", asset=AssetClass.CRYPTO, direction=Direction.LONG, account=1,
        entry=5, entry_price="67200.000000", quantity="0.400000",
        stop="64000.00", target="72000.00", fees="0",
        notes="Spot accumulation. Wide stop, no leverage on this one.",
    ),
    dict(
        symbol="EUR/USD", asset=AssetClass.FOREX, direction=Direction.LONG, account=0,
        entry=3, entry_price="1.09250", quantity=9000,
        stop="1.08500", target="1.10500", fees="0",
        notes="Retest of the breakout level. Watching for a daily close above 1.0950.",
    ),
    dict(
        symbol="NVDA", asset=AssetClass.STOCKS, direction=Direction.SHORT, account=0,
        entry=2, entry_price="124.30", quantity=50,
        stop="130.00", target="112.00", fees="0",
        notes="Fade after a parabolic three-day run. Risky, size is halved.",
    ),
    dict(
        symbol="CL", asset=AssetClass.FUTURES, direction=Direction.SHORT, account=0,
        entry=1, entry_price="78.45", quantity=3,
        stop="79.80", target="75.00", fees="0",
        notes="Inventory build. Short the rally into the weekly supply shelf.",
    ),
]


class Command(BaseCommand):
    help = "Create a demo user, accounts and sample trades for reviewing the UI."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete the demo user's existing trades before seeding again.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            username=USERNAME,
            defaults={"email": "trader@example.com"},
        )
        user.set_password(PASSWORD)
        user.save()

        accounts = [
            TradingAccount.objects.get_or_create(
                user=user, name=spec["name"], defaults=spec
            )[0]
            for spec in ACCOUNTS
        ]

        if options["force"]:
            deleted, _ = Trade.objects.filter(account__user=user).delete()
            self.stdout.write(f"Removed {deleted} existing trade row(s).")
        elif Trade.objects.filter(account__user=user).exists():
            self.stdout.write(
                self.style.WARNING(
                    f"'{USERNAME}' already has trades. Nothing seeded. "
                    "Use --force to rebuild them."
                )
            )
            return

        now = timezone.now()
        created_trades = []

        for spec in TRADES:
            is_closed = "exit" in spec
            trade = Trade(
                account=accounts[spec["account"]],
                symbol=spec["symbol"],
                asset_class=spec["asset"],
                direction=spec["direction"],
                status=TradeStatus.CLOSED if is_closed else TradeStatus.OPEN,
                entry_date=now - timedelta(days=spec["entry"]),
                entry_price=Decimal(spec["entry_price"]),
                quantity=Decimal(spec["quantity"]),
                stop_loss=Decimal(spec["stop"]) if spec.get("stop") else None,
                take_profit=Decimal(spec["target"]) if spec.get("target") else None,
                fees=Decimal(spec.get("fees") or "0"),
                notes=spec.get("notes", ""),
            )
            if is_closed:
                trade.exit_date = now - timedelta(days=spec["exit"])
                trade.exit_price = Decimal(spec["exit_price"])
            trade.full_clean()
            trade.save()

            for name in spec.get("tags", []):
                tag, _ = Tag.objects.get_or_create(user=user, name=name)
                trade.tags.add(tag)

            created_trades.append(trade)

        closed = [t for t in created_trades if t.is_closed]
        net = sum((t.net_pnl for t in closed), Decimal("0"))
        wins = sum(1 for t in closed if t.is_win)

        self.stdout.write(self.style.SUCCESS(f"User '{USERNAME}' {'created' if created else 'updated'}."))
        self.stdout.write(f"  login: {USERNAME} / {PASSWORD}")
        self.stdout.write(f"  accounts: {', '.join(a.name for a in accounts)}")
        self.stdout.write(
            f"  trades: {len(created_trades)} "
            f"({len(closed)} closed, {len(created_trades) - len(closed)} open)"
        )
        if closed:
            self.stdout.write(
                f"  closed result: {wins}W / {len(closed) - wins}L, net P&L {net.quantize(Decimal('0.01'))}"
            )
