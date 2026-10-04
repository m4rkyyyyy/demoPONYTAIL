"""Populate a demo journal: `python manage.py seed_trades`."""

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from journal.models import Tag, Trade, TradingAccount

SIX_DP = Decimal("0.000001")
SYMBOLS = [
    ("AAPL", Trade.AssetClass.STOCKS),
    ("NVDA", Trade.AssetClass.STOCKS),
    ("EUR/USD", Trade.AssetClass.FOREX),
    ("GBP/JPY", Trade.AssetClass.FOREX),
    ("BTC/USDT", Trade.AssetClass.CRYPTO),
    ("ETH/USDT", Trade.AssetClass.CRYPTO),
    ("ES", Trade.AssetClass.FUTURES),
    ("AAPL 150C", Trade.AssetClass.OPTIONS),
]
TAGS = ["Breakout", "Reversal", "FOMO", "Earnings", "Scalp", "News"]
TAG_POOL = [Tag.objects.get_or_create(name=name)[0] for name in TAGS]


class Command(BaseCommand):
    help = "Create the demo trader (trader / password123) with 20 sample trades."

    def handle(self, *args, **options):
        random.seed(7)
        user, created = User.objects.get_or_create(
            username="trader", defaults={"email": "trader@example.com"}
        )
        if created:
            user.set_password("password123")
            user.save()
            self.stdout.write(self.style.SUCCESS("Created user trader / password123"))
        else:
            user.set_password("password123")
            user.save()

        account, _ = TradingAccount.objects.get_or_create(
            user=user, name="Interactive Brokers", defaults={"initial_balance": Decimal("25000.00")}
        )
        now = timezone.now()
        made = 0

        for index in range(20):
            symbol, asset_class = random.choice(SYMBOLS)
            direction = random.choice([Trade.Direction.LONG, Trade.Direction.SHORT])
            entry_price = Decimal(str(round(random.uniform(5, 900), 4)))
            quantity = Decimal(str(round(random.uniform(0.1, 25), 4)))
            stop_loss = (entry_price * Decimal("0.97")).quantize(SIX_DP)
            take_profit = (entry_price * Decimal("1.05")).quantize(SIX_DP)
            entry_date = now - timedelta(days=random.randint(1, 120), hours=random.randint(0, 23))
            status = Trade.Status.OPEN if index % 5 == 0 else Trade.Status.CLOSED
            sign = Decimal(1) if direction == Trade.Direction.LONG else Decimal(-1)

            trade = Trade(
                account=account,
                symbol=symbol,
                asset_class=asset_class,
                direction=direction,
                status=status,
                entry_date=entry_date,
                entry_price=entry_price,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                fees=Decimal(str(round(random.uniform(1, 25), 2))),
                notes="Seeded sample trade.",
            )
            if status == Trade.Status.CLOSED:
                # Winners ~60% of the time so the dashboard charts look alive.
                move = Decimal(str(round(random.uniform(0.01, 0.09), 4))) * entry_price
                if random.random() > 0.6:
                    move = -move * Decimal("0.8")
                trade.exit_date = entry_date + timedelta(hours=random.randint(1, 72))
                trade.exit_price = (entry_price + sign * move).quantize(SIX_DP)

            trade.full_clean(exclude=["tags"])
            trade.save()
            trade.tags.set(random.sample(TAG_POOL, 2))
            made += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {made} trades for {account}."))
        self.stdout.write("Log in at /login/ with trader / password123")