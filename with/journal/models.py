"""Journal domain: accounts, trades and the P&L math derived from them."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse


class Tag(models.Model):
    name = models.CharField(max_length=32, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TradingAccount(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trading_accounts"
    )
    name = models.CharField(max_length=64)
    currency = models.CharField(max_length=3, default="USD")
    initial_balance = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="unique_account_name")]

    def __str__(self):
        return f"{self.name} ({self.currency})"


class Trade(models.Model):
    class AssetClass(models.TextChoices):
        STOCKS = "STOCKS", "Stocks"
        FOREX = "FOREX", "Forex"
        CRYPTO = "CRYPTO", "Crypto"
        FUTURES = "FUTURES", "Futures"
        OPTIONS = "OPTIONS", "Options"

    class Direction(models.TextChoices):
        LONG = "LONG", "Long"
        SHORT = "SHORT", "Short"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    account = models.ForeignKey(TradingAccount, on_delete=models.CASCADE, related_name="trades")
    symbol = models.CharField(max_length=32)
    asset_class = models.CharField(max_length=8, choices=AssetClass.choices, default=AssetClass.STOCKS)
    direction = models.CharField(max_length=5, choices=Direction.choices, default=Direction.LONG)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.OPEN)
    entry_date = models.DateTimeField()
    entry_price = models.DecimalField(
        max_digits=18, decimal_places=6, validators=[MinValueValidator(0)]
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=6, validators=[MinValueValidator(0)]
    )
    exit_date = models.DateTimeField(null=True, blank=True)
    exit_price = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    stop_loss = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    take_profit = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    fees = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)]
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="trades")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-entry_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(entry_price__gte=0), name="trade_entry_price_non_negative"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=0), name="trade_quantity_non_negative"
            ),
            models.CheckConstraint(condition=Q(fees__gte=0), name="trade_fees_non_negative"),
            models.CheckConstraint(
                condition=Q(exit_date__isnull=True) | Q(exit_date__gte=models.F("entry_date")),
                name="trade_exit_after_entry",
            ),
        ]

    def __str__(self):
        return f"{self.symbol} {self.direction} ({self.status})"

    def get_absolute_url(self):
        return reverse("trade_edit", args=[self.pk])

    # --- validation ---------------------------------------------------------
    def clean(self):
        errors = {}
        if self.exit_date and self.entry_date and self.exit_date < self.entry_date:
            errors["exit_date"] = "Exit date cannot be earlier than entry date."
        if self.status == self.Status.CLOSED:
            if self.exit_price is None:
                errors["exit_price"] = "Required when the trade is closed."
            if self.exit_date is None:
                errors["exit_date"] = "Required when the trade is closed."
        if errors:
            raise ValidationError(errors)

    # --- derived numbers ----------------------------------------------------
    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    @property
    def gross_pnl(self):
        """(exit - entry) * qty for longs, inverted for shorts. None while open."""
        if self.is_open or self.exit_price is None:
            return None
        move = self.exit_price - self.entry_price
        if self.direction == self.Direction.SHORT:
            move = -move
        return move * self.quantity

    @property
    def net_pnl(self):
        """Gross P&L minus fees."""
        gross = self.gross_pnl
        return None if gross is None else gross - self.fees

    @property
    def return_pct(self):
        """Net P&L as a percentage of the position's entry notional."""
        net = self.net_pnl
        if net is None:
            return None
        notional = self.entry_price * self.quantity
        if notional == 0:
            return None
        return net / notional * 100

    @property
    def risk_per_unit(self):
        """Distance from entry to stop, or None without a usable stop."""
        if self.stop_loss is None:
            return None
        risk = abs(self.entry_price - self.stop_loss)
        return None if risk == 0 else risk

    @property
    def r_multiple(self):
        """Realized R: profit in units of initial risk. Needs a stop and an exit."""
        risk = self.risk_per_unit
        if risk is None or self.exit_price is None:
            return None
        move = self.exit_price - self.entry_price
        if self.direction == self.Direction.SHORT:
            move = -move
        return move / risk

    @property
    def is_win(self):
        net = self.net_pnl
        return net is not None and net > 0

    @property
    def outcome(self):
        net = self.net_pnl
        if net is None:
            return "OPEN"
        if net > 0:
            return "WIN"
        return "LOSS" if net < 0 else "BE"