"""Data models for the TradePulse trading journal.

Every model that stores user data hangs off ``django.contrib.auth``'s user
model, so ownership can always be resolved by walking a foreign key. That is
what makes the strict per-user isolation in :mod:`journal.views` a one-liner
(``Trade.objects.filter(account__user=request.user)``) instead of a rule that
has to be remembered in a dozen places.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

ZERO = Decimal("0")


class AssetClass(models.TextChoices):
    STOCKS = "STOCKS", "Stocks"
    FOREX = "FOREX", "Forex"
    CRYPTO = "CRYPTO", "Crypto"
    FUTURES = "FUTURES", "Futures"
    OPTIONS = "OPTIONS", "Options"


class Direction(models.TextChoices):
    LONG = "LONG", "Long"
    SHORT = "SHORT", "Short"


class TradeStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    CLOSED = "CLOSED", "Closed"


class Tag(models.Model):
    """A free-form label such as "Breakout", "FOMO" or "Earnings".

    Scoped per user so two traders can use the same word independently.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
    )
    name = models.CharField(max_length=32)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_tag_name_per_user"),
        ]

    def __str__(self):
        return self.name


class TradingAccount(models.Model):
    """A broker/exchange the user trades through."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trading_accounts",
    )
    name = models.CharField(max_length=80)
    currency = models.CharField(max_length=3, default="USD")
    initial_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_account_name_per_user"),
        ]

    def __str__(self):
        return f"{self.name} ({self.currency})"


class Trade(models.Model):
    """A single round-trip trade, open or closed.

    Prices and quantities use six decimal places so crypto and forex entries
    such as ``0.08432`` or ``0.00321456`` are stored exactly rather than being
    rounded into an unrecoverable value.
    """

    account = models.ForeignKey(
        TradingAccount,
        on_delete=models.CASCADE,
        related_name="trades",
    )
    symbol = models.CharField(max_length=32)
    asset_class = models.CharField(max_length=10, choices=AssetClass.choices)
    direction = models.CharField(max_length=5, choices=Direction.choices)
    status = models.CharField(
        max_length=6,
        choices=TradeStatus.choices,
        default=TradeStatus.OPEN,
    )

    entry_date = models.DateTimeField()
    entry_price = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        validators=[MinValueValidator(ZERO)],
    )
    quantity = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        validators=[MinValueValidator(ZERO)],
    )

    exit_date = models.DateTimeField(null=True, blank=True)
    exit_price = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
    )
    stop_loss = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
    )
    take_profit = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
    )

    fees = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )

    tags = models.ManyToManyField(Tag, blank=True, related_name="trades")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["asset_class"]),
            models.Index(fields=["symbol"]),
        ]

    def __str__(self):
        return f"{self.symbol} {self.direction} ({self.status})"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def clean(self):
        """Cross-field rules that a per-field validator cannot express."""
        super().clean()
        errors = {}

        if self.entry_date and self.exit_date and self.exit_date < self.entry_date:
            errors["exit_date"] = "Exit date cannot be earlier than the entry date."

        if self.status == TradeStatus.CLOSED:
            if self.exit_price is None:
                errors["exit_price"] = "Exit price is required for closed trades."
            if self.exit_date is None:
                errors["exit_date"] = "Exit date is required for closed trades."

        if errors:
            raise ValidationError(errors)

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    @property
    def is_closed(self):
        return self.status == TradeStatus.CLOSED

    @property
    def is_open(self):
        return self.status == TradeStatus.OPEN

    @property
    def notional(self):
        """Cash value at risk going in: entry price x quantity."""
        return self.entry_price * self.quantity

    @property
    def gross_pnl(self):
        """Price move x quantity, before fees. ``None`` while the trade is open."""
        if not self.is_closed or self.exit_price is None:
            return None
        move = self.exit_price - self.entry_price
        if self.direction == Direction.SHORT:
            move = -move
        return move * self.quantity

    @property
    def net_pnl(self):
        """Gross P&L minus fees. ``None`` while the trade is open."""
        gross = self.gross_pnl
        if gross is None:
            return None
        return gross - (self.fees or ZERO)

    @property
    def return_pct(self):
        """Net P&L as a percentage of the position's entry notional."""
        net = self.net_pnl
        if net is None:
            return None
        basis = self.notional
        if not basis:
            return None
        return (net / basis) * Decimal(100)

    @property
    def risk_per_unit(self):
        """Absolute distance from entry to stop. ``None`` without a stop."""
        if self.stop_loss is None:
            return None
        return abs(self.entry_price - self.stop_loss)

    @property
    def initial_risk(self):
        """Total cash risked at entry, or ``None`` when no stop is set."""
        risk = self.risk_per_unit
        if risk is None:
            return None
        return risk * self.quantity

    @property
    def r_multiple(self):
        """Realised R for closed trades, else ``None``.

        Returns ``None`` (rather than raising) when the trade is open, has no
        stop loss, or when the stop sits exactly on the entry price -- in that
        last case the risk is zero and the ratio is undefined.
        """
        if not self.is_closed or self.exit_price is None:
            return None
        risk = self.risk_per_unit
        if not risk:
            return None
        move = self.exit_price - self.entry_price
        if self.direction == Direction.SHORT:
            move = -move
        return move / risk

    @property
    def is_win(self):
        """``True``/``False`` for closed trades, ``None`` while open."""
        net = self.net_pnl
        if net is None:
            return None
        return net > ZERO

    @property
    def result(self):
        """One of ``WIN`` / ``LOSS`` / ``BREAKEVEN`` / ``OPEN`` for UI badges."""
        if self.is_open:
            return "OPEN"
        if self.net_pnl > ZERO:
            return "WIN"
        if self.net_pnl < ZERO:
            return "LOSS"
        return "BREAKEVEN"
