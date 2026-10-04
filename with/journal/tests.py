import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Trade, TradingAccount
from .views import metrics


def make_trade(user, **overrides):
    account, _ = TradingAccount.objects.get_or_create(user=user, name="Main")
    fields = {
        "symbol": "AAPL",
        "asset_class": Trade.AssetClass.STOCKS,
        "direction": Trade.Direction.LONG,
        "status": Trade.Status.CLOSED,
        "entry_date": timezone.now() - timedelta(days=2),
        "entry_price": Decimal("100"),
        "quantity": Decimal("10"),
        "exit_date": timezone.now() - timedelta(days=1),
        "exit_price": Decimal("110"),
        "stop_loss": Decimal("95"),
    }
    fields.update(overrides)
    return Trade.objects.create(account=account, **fields)


class PnlTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")

    def test_long_net_pnl_is_profit_minus_fees(self):
        trade = make_trade(self.alice, exit_price=Decimal("110"), fees=Decimal("25"))
        self.assertEqual(trade.gross_pnl, Decimal("100.00"))
        self.assertEqual(trade.net_pnl, Decimal("75.00"))
        self.assertEqual(trade.return_pct, Decimal("7.5"))

    def test_short_net_pnl_inverts_the_price_move(self):
        trade = make_trade(
            self.alice, direction=Trade.Direction.SHORT, entry_price=Decimal("100"),
            exit_price=Decimal("92"), fees=Decimal("8"),
        )
        self.assertEqual(trade.gross_pnl, Decimal("80.00"))
        self.assertEqual(trade.net_pnl, Decimal("72.00"))
        self.assertTrue(trade.is_win)

    def test_open_trade_has_no_realized_numbers(self):
        trade = make_trade(self.alice, status=Trade.Status.OPEN, exit_price=None, exit_date=None)
        self.assertIsNone(trade.gross_pnl)
        self.assertIsNone(trade.net_pnl)
        self.assertIsNone(trade.return_pct)
        self.assertFalse(trade.is_win)

    def test_r_multiple_uses_stop_distance_and_direction(self):
        long_trade = make_trade(self.alice, entry_price=Decimal("100"), exit_price=Decimal("110"), stop_loss=Decimal("95"))
        short_trade = make_trade(
            self.alice, direction=Trade.Direction.SHORT, entry_price=Decimal("100"),
            exit_price=Decimal("95"), stop_loss=Decimal("105"),
        )
        self.assertEqual(long_trade.r_multiple, Decimal("2.0"))
        self.assertEqual(short_trade.r_multiple, Decimal("1.0"))

    def test_r_multiple_is_none_without_a_usable_stop(self):
        self.assertIsNone(make_trade(self.alice, stop_loss=None).r_multiple)
        self.assertIsNone(make_trade(self.alice, stop_loss=Decimal("100")).r_multiple)

    def test_return_pct_is_none_when_position_notional_is_zero(self):
        self.assertIsNone(make_trade(self.alice, quantity=Decimal("0")).return_pct)


class MetricsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        make_trade(self.alice, exit_price=Decimal("110"), fees=Decimal("10"))   # +90
        make_trade(self.alice, exit_price=Decimal("110"), fees=Decimal("10"), symbol="NVDA")  # +90
        make_trade(self.alice, exit_price=Decimal("96"), fees=Decimal("5"))     # -45

    def test_win_rate_and_profit_factor(self):
        result = metrics(Trade.objects.filter(account__user=self.alice, status=Trade.Status.CLOSED))
        self.assertEqual(result["closed_count"], 3)
        self.assertAlmostEqual(result["win_rate"], 66.666, places=2)
        self.assertEqual(result["net_pnl"], Decimal("135.00"))
        self.assertEqual(result["profit_factor"], Decimal("4.00"))
        self.assertEqual(result["loss_count"], 1)

    def test_empty_journal_returns_none_not_zero_division(self):
        result = metrics(Trade.objects.none())
        self.assertIsNone(result["win_rate"])
        self.assertIsNone(result["profit_factor"])
        self.assertIsNone(result["avg_r"])
        self.assertEqual(result["net_pnl"], Decimal("0.00"))

    def test_dashboard_renders_for_new_user(self):
        User.objects.create_user("bob", password="pw")
        self.client.login(username="bob", password="pw")
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class ValidationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")

    def test_negative_price_is_rejected(self):
        trade = make_trade(self.alice, entry_price=Decimal("100"))
        trade.entry_price = Decimal("-1")
        with self.assertRaises(ValidationError):
            trade.full_clean()

    def test_exit_before_entry_is_rejected(self):
        trade = make_trade(self.alice)
        trade.exit_date = trade.entry_date - timedelta(days=1)
        with self.assertRaises(ValidationError) as ctx:
            trade.full_clean()
        self.assertIn("exit_date", ctx.exception.error_dict)

    def test_closed_trade_requires_exit_price_and_date(self):
        trade = make_trade(self.alice, status=Trade.Status.CLOSED, exit_price=None, exit_date=None)
        with self.assertRaises(ValidationError) as ctx:
            trade.full_clean()
        self.assertEqual(set(ctx.exception.error_dict), {"exit_price", "exit_date"})

    def test_form_rejects_negative_fees(self):
        account = make_trade(self.alice)
        self.client.login(username="alice", password="pw")
        response = self.client.post(
            reverse("trade_create"),
            {
                "account": account.pk,
                "symbol": "AAPL", "asset_class": "STOCKS", "direction": "LONG", "status": "CLOSED",
                "entry_date": "2026-01-01T10:00", "entry_price": "100", "quantity": "1",
                "exit_date": "2026-01-02T10:00", "exit_price": "101", "fees": "-5", "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "greater than or equal to 0")
        self.assertEqual(Trade.objects.count(), 1)


class IsolationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.bobs_trade = make_trade(self.bob, symbol="BTC/USDT")

    def test_list_only_shows_own_trades(self):
        make_trade(self.alice)
        self.client.login(username="alice", password="pw")
        response = self.client.get(reverse("trade_list"))
        self.assertEqual([t.pk for t in response.context["trades"]], [t.pk for t in Trade.objects.filter(account__user=self.alice)])

    def test_cannot_edit_or_delete_another_users_trade(self):
        self.client.login(username="alice", password="pw")
        self.assertEqual(self.client.get(reverse("trade_edit", args=[self.bobs_trade.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("trade_delete", args=[self.bobs_trade.pk])).status_code, 404)

    def test_export_is_scoped_to_owner(self):
        make_trade(self.alice, symbol="AAPL")
        self.client.login(username="alice", password="pw")
        rows = list(csv.DictReader(io.StringIO(self.client.get(reverse("trade_export")).content.decode())))
        self.assertEqual([row["symbol"] for row in rows], ["AAPL"])

    def test_anonymous_users_are_redirected(self):
        for name in ["dashboard", "trade_list", "trade_create", "trade_export", "trade_import"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)


class ImportExportTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.account = TradingAccount.objects.create(user=self.alice, name="Main")
        self.client.login(username="alice", password="pw")

    def upload(self, body):
        return self.client.post(
            reverse("trade_import"),
            {"account": self.account.pk, "file": io.BytesIO(body.encode())},
        )

    def test_import_creates_valid_rows_and_reports_bad_ones(self):
        header = "symbol,asset_class,direction,status,entry_date,entry_price,quantity,exit_date,exit_price,fees"
        good = "AAPL,STOCKS,LONG,CLOSED,2026-01-01 10:00,100,10,2026-01-02 10:00,110,5"
        bad = "AAPL,STOCKS,LONG,CLOSED,2026-01-01 10:00,-5,10,2026-01-02 10:00,110,5"
        response = self.upload(f"{header}\n{good}\n{bad}\n")

        self.assertEqual(Trade.objects.count(), 1)
        self.assertEqual(response.context["created"], 1)
        self.assertEqual(len(response.context["errors"]), 1)
        self.assertEqual(Trade.objects.get().net_pnl, Decimal("95.00"))

    def test_import_rejects_missing_header(self):
        response = self.upload("foo,bar\n1,2\n")
        self.assertEqual(Trade.objects.count(), 0)
        self.assertIn("Header row", response.context["errors"][0])