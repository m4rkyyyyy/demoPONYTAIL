"""Tests for the TradePulse journal.

Grouped by concern: P&L maths, dashboard aggregation, per-user isolation,
model validation, CSV round-trips and view behaviour.
"""

from datetime import timedelta
from decimal import Decimal

import csv

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from journal import csv_io
from journal.metrics import equity_curve, summarize
from journal.models import AssetClass, Direction, Tag, Trade, TradeStatus, TradingAccount

ONE = Decimal("1")
TWO = Decimal("2")


class JournalTestCase(TestCase):
    """Two users with one account each, plus a trade factory."""

    def setUp(self):
        self.alice = User.objects.create_user("alice", password="alice-password-123")
        self.bob = User.objects.create_user("bob", password="bob-password-456")

        self.alice_account = TradingAccount.objects.create(
            user=self.alice, name="Interactive Brokers", currency="USD",
            initial_balance=Decimal("10000.00"),
        )
        self.bob_account = TradingAccount.objects.create(
            user=self.bob, name="Binance Futures", currency="USD",
            initial_balance=Decimal("5000.00"),
        )

    def make_trade(self, **overrides):
        """Create a closed winning LONG by default; override what a test cares about."""
        now = timezone.now()
        fields = {
            "account": self.alice_account,
            "symbol": "AAPL",
            "asset_class": AssetClass.STOCKS,
            "direction": Direction.LONG,
            "status": TradeStatus.CLOSED,
            "entry_date": now - timedelta(days=10),
            "entry_price": Decimal("100"),
            "quantity": Decimal("10"),
            "exit_date": now - timedelta(days=5),
            "exit_price": Decimal("110"),
            "stop_loss": Decimal("95"),
            "take_profit": Decimal("115"),
            "fees": Decimal("0"),
            "notes": "",
        }
        fields.update(overrides)
        trade = Trade.objects.create(**fields)
        return trade

    def login_alice(self):
        self.assertTrue(self.client.login(username="alice", password="alice-password-123"))


class PnlCalculationTests(JournalTestCase):
    def test_long_gross_pnl(self):
        trade = self.make_trade(direction=Direction.LONG, entry_price=Decimal("100"),
                                exit_price=Decimal("110"), quantity=Decimal("10"))
        self.assertEqual(trade.gross_pnl, Decimal("100"))

    def test_long_net_pnl_subtracts_fees(self):
        trade = self.make_trade(direction=Direction.LONG, entry_price=Decimal("100"),
                                exit_price=Decimal("110"), quantity=Decimal("10"),
                                fees=Decimal("5"))
        self.assertEqual(trade.gross_pnl, Decimal("100"))
        self.assertEqual(trade.net_pnl, Decimal("95"))
        self.assertTrue(trade.is_win)
        self.assertEqual(trade.result, "WIN")

    def test_short_net_pnl_with_fees(self):
        trade = self.make_trade(direction=Direction.SHORT, entry_price=Decimal("100"),
                                exit_price=Decimal("90"), quantity=Decimal("10"),
                                fees=Decimal("3"))
        self.assertEqual(trade.gross_pnl, Decimal("100"))
        self.assertEqual(trade.net_pnl, Decimal("97"))

    def test_short_losing_trade_inverts_the_move(self):
        trade = self.make_trade(direction=Direction.SHORT, entry_price=Decimal("100"),
                                exit_price=Decimal("110"), quantity=Decimal("10"))
        self.assertEqual(trade.gross_pnl, Decimal("-100"))
        self.assertFalse(trade.is_win)
        self.assertEqual(trade.result, "LOSS")

    def test_forex_precision_is_preserved(self):
        trade = self.make_trade(asset_class=AssetClass.FOREX, symbol="EUR/USD",
                                entry_price=Decimal("1.08432"), exit_price=Decimal("1.09115"),
                                quantity=Decimal("10000"), fees=Decimal("0"))
        self.assertEqual(trade.gross_pnl, Decimal("68.30"))

    def test_open_trade_exposes_no_pnl(self):
        trade = self.make_trade(status=TradeStatus.OPEN, exit_price=None, exit_date=None)
        self.assertIsNone(trade.gross_pnl)
        self.assertIsNone(trade.net_pnl)
        self.assertIsNone(trade.return_pct)
        self.assertIsNone(trade.r_multiple)
        self.assertIsNone(trade.is_win)
        self.assertEqual(trade.result, "OPEN")

    def test_return_percentage_uses_net_pnl_over_entry_notional(self):
        trade = self.make_trade(entry_price=Decimal("100"), exit_price=Decimal("110"),
                                quantity=Decimal("10"), fees=Decimal("5"))
        # net 95 / notional 1000 = 9.5%
        self.assertEqual(trade.return_pct, Decimal("9.5"))

    def test_return_percentage_is_none_when_notional_is_zero(self):
        trade = self.make_trade(quantity=Decimal("0"))
        self.assertIsNone(trade.return_pct)

    def test_r_multiple_long(self):
        trade = self.make_trade(entry_price=Decimal("100"), exit_price=Decimal("110"),
                                stop_loss=Decimal("95"))
        self.assertEqual(trade.r_multiple, Decimal("2"))
        self.assertEqual(trade.risk_per_unit, Decimal("5"))
        self.assertEqual(trade.initial_risk, Decimal("50"))

    def test_r_multiple_short(self):
        trade = self.make_trade(direction=Direction.SHORT, entry_price=Decimal("100"),
                                exit_price=Decimal("95"), stop_loss=Decimal("105"))
        self.assertEqual(trade.r_multiple, ONE)

    def test_r_multiple_is_none_without_a_stop_loss(self):
        trade = self.make_trade(stop_loss=None)
        self.assertIsNone(trade.r_multiple)
        self.assertIsNone(trade.initial_risk)

    def test_r_multiple_is_none_when_stop_equals_entry(self):
        trade = self.make_trade(stop_loss=Decimal("100"))
        self.assertIsNone(trade.r_multiple)


class MetricsTests(JournalTestCase):
    def setUp(self):
        super().setUp()
        # +100, +200, -100 => 2 wins of 3, gross wins 300, gross losses 100
        self.make_trade(symbol="AAA", exit_price=Decimal("110"), fees=Decimal("0"))
        self.make_trade(symbol="BBB", entry_price=Decimal("100"), exit_price=Decimal("120"),
                        fees=Decimal("0"))
        self.make_trade(symbol="CCC", exit_price=Decimal("90"), fees=Decimal("0"))
        self.trades = Trade.objects.filter(account=self.alice_account)

    def test_win_rate(self):
        stats = summarize(self.trades)
        self.assertEqual(stats.closed_count, 3)
        self.assertEqual(stats.win_count, 2)
        self.assertEqual(stats.loss_count, 1)
        self.assertAlmostEqual(stats.win_rate, 2 / 3 * 100, places=6)

    def test_profit_factor(self):
        stats = summarize(self.trades)
        self.assertEqual(stats.gross_wins, Decimal("300"))
        self.assertEqual(stats.gross_losses, Decimal("100"))
        self.assertAlmostEqual(stats.profit_factor, 3.0, places=6)

    def test_net_pnl_and_fees_totals(self):
        self.make_trade(symbol="DDD", exit_price=Decimal("110"), fees=Decimal("5"))
        stats = summarize(Trade.objects.filter(account=self.alice_account))
        # +100 +200 -100 +95
        self.assertEqual(stats.net_pnl, Decimal("295"))
        self.assertEqual(stats.total_fees, Decimal("5"))

    def test_average_r_multiple(self):
        stats = summarize(self.trades)
        # Each trade risks 5 per unit on 10 units; moves are +10, +20, -10.
        self.assertAlmostEqual(stats.avg_r, (2 + 4 - 2) / 3, places=6)

    def test_stats_with_no_closed_trades_do_not_divide_by_zero(self):
        stats = summarize([])
        self.assertEqual(stats.closed_count, 0)
        self.assertEqual(stats.net_pnl, Decimal("0"))
        self.assertIsNone(stats.win_rate)
        self.assertIsNone(stats.profit_factor)
        self.assertIsNone(stats.avg_r)
        self.assertIsNone(stats.expectancy)

    def test_profit_factor_is_none_when_there_are_no_losses(self):
        self.make_trade(symbol="EEE", exit_price=Decimal("110"))
        stats = summarize(Trade.objects.filter(account=self.alice_account, symbol="EEE"))
        self.assertEqual(stats.profit_factor, None)
        self.assertEqual(stats.gross_losses, Decimal("0"))

    def test_open_trades_are_counted_separately(self):
        self.make_trade(symbol="FFF", status=TradeStatus.OPEN, exit_price=None, exit_date=None)
        open_trades = Trade.objects.filter(account=self.alice_account, status=TradeStatus.OPEN)
        stats = summarize(
            Trade.objects.filter(account=self.alice_account, status=TradeStatus.CLOSED),
            open_count=open_trades.count(),
        )
        self.assertEqual(stats.open_count, 1)
        self.assertEqual(stats.closed_count, 3)

    def test_equity_curve_starts_at_balance_and_accumulates(self):
        points = equity_curve(self.trades, starting_balance=Decimal("1000"))
        self.assertEqual(points[0]["equity"], 1000.0)
        self.assertEqual(len(points), 4)
        self.assertEqual(points[-1]["equity"], 1200.0)

    def test_equity_curve_with_no_trades_is_a_single_point(self):
        self.assertEqual(equity_curve([], Decimal("500")), [{"label": "Start", "equity": 500.0}])


class DashboardViewTests(JournalTestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_dashboard_renders_for_a_user_with_no_data(self):
        self.login_alice()
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "N/A")
        self.assertEqual(response.context["stats"].closed_count, 0)

    def test_dashboard_shows_metrics_and_charts(self):
        self.make_trade(exit_price=Decimal("120"))
        self.make_trade(symbol="LOSS", exit_price=Decimal("95"))
        self.login_alice()
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total net P&amp;L")
        self.assertContains(response, "equity-chart")
        self.assertContains(response, "winloss-chart")
        self.assertEqual(response.context["recent_trades"].__len__(), 2)

    def test_dashboard_recent_trades_are_capped_at_five(self):
        for index in range(8):
            self.make_trade(symbol=f"SYM{index}")
        self.login_alice()
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(len(response.context["recent_trades"]), 5)


class UserIsolationTests(JournalTestCase):
    def setUp(self):
        super().setUp()
        self.alice_trade = self.make_trade(symbol="AAPL")
        self.bob_trade = self.make_trade(account=self.bob_account, symbol="TSLA")
        self.login_alice()

    def test_trade_list_shows_only_own_trades(self):
        response = self.client.get(reverse("trade_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AAPL")
        self.assertNotContains(response, "TSLA")

    def test_cannot_open_another_users_trade_for_editing(self):
        response = self.client.get(reverse("trade_update", args=[self.bob_trade.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_post_an_edit_to_another_users_trade(self):
        response = self.client.post(
            reverse("trade_update", args=[self.bob_trade.pk]),
            {
                "account": self.bob_account.pk,
                "symbol": "HACKED",
                "asset_class": AssetClass.STOCKS,
                "direction": Direction.LONG,
                "status": TradeStatus.CLOSED,
                "entry_date": "2024-01-01T10:00",
                "entry_price": "1",
                "quantity": "1",
                "exit_date": "2024-01-02T10:00",
                "exit_price": "2",
                "fees": "0",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.bob_trade.refresh_from_db()
        self.assertEqual(self.bob_trade.symbol, "TSLA")

    def test_cannot_delete_another_users_trade(self):
        url = reverse("trade_delete", args=[self.bob_trade.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(Trade.objects.filter(pk=self.bob_trade.pk).exists())

    def test_export_contains_only_own_closed_trades(self):
        response = self.client.get(reverse("trade_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn("AAPL", body)
        self.assertNotIn("TSLA", body)

    def test_form_does_not_offer_another_users_accounts(self):
        response = self.client.get(reverse("trade_create"))
        accounts = response.context["form"].fields["account"].queryset
        self.assertIn(self.alice_account, accounts)
        self.assertNotIn(self.bob_account, accounts)

    def test_tags_are_scoped_per_user(self):
        tag = Tag.objects.create(user=self.bob, name="FOMO")
        response = self.client.get(reverse("trade_create"))
        self.assertNotIn(tag, response.context["form"].fields["tags"].queryset)

    def test_import_cannot_target_another_users_account(self):
        upload = SimpleUploadedFile(
            "trades.csv",
            b"symbol,asset_class,direction,entry_date,entry_price,quantity\n"
            b"XRP,CRYPTO,LONG,2024-01-01T00:00,0.5,100\n",
            content_type="text/csv",
        )
        response = self.client.post(
            reverse("trade_import"), {"account": self.bob_account.pk, "file": upload}
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with an error
        self.assertFalse(response.context["form"].is_valid())
        self.assertEqual(Trade.objects.filter(symbol="XRP").count(), 0)

    def test_account_names_are_unique_per_user_only(self):
        duplicate = TradingAccount.objects.create(
            user=self.bob, name="Interactive Brokers", initial_balance=Decimal("1")
        )
        self.assertEqual(duplicate.user, self.bob)
        form_post = self.client.post(
            reverse("account_create"),
            {"name": "Interactive Brokers", "currency": "USD", "initial_balance": "10"},
        )
        self.assertEqual(form_post.status_code, 200)
        self.assertFalse(form_post.context["form"].is_valid())

    def test_journal_views_require_login(self):
        self.client.logout()
        for name in ("dashboard", "trade_list", "trade_create", "trade_import", "trade_export"):
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])


class TradeValidationTests(JournalTestCase):
    def build(self, **overrides):
        now = timezone.now()
        fields = {
            "account": self.alice_account,
            "symbol": "AAPL",
            "asset_class": AssetClass.STOCKS,
            "direction": Direction.LONG,
            "status": TradeStatus.CLOSED,
            "entry_date": now - timedelta(days=5),
            "entry_price": Decimal("100"),
            "quantity": Decimal("10"),
            "exit_date": now - timedelta(days=1),
            "exit_price": Decimal("110"),
            "fees": Decimal("0"),
        }
        fields.update(overrides)
        return Trade(**fields)

    def test_a_valid_trade_passes_full_clean(self):
        self.build().full_clean()

    def test_negative_entry_price_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(entry_price=Decimal("-100")).full_clean()
        self.assertIn("entry_price", ctx.exception.message_dict)

    def test_negative_quantity_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(quantity=Decimal("-1")).full_clean()
        self.assertIn("quantity", ctx.exception.message_dict)

    def test_negative_fees_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(fees=Decimal("-2.50")).full_clean()
        self.assertIn("fees", ctx.exception.message_dict)

    def test_negative_exit_price_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(exit_price=Decimal("-1")).full_clean()
        self.assertIn("exit_price", ctx.exception.message_dict)

    def test_exit_before_entry_is_rejected(self):
        now = timezone.now()
        with self.assertRaises(ValidationError) as ctx:
            self.build(entry_date=now - timedelta(days=1), exit_date=now - timedelta(days=5)).full_clean()
        self.assertIn("exit_date", ctx.exception.message_dict)

    def test_closed_trade_requires_an_exit_price(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(exit_price=None).full_clean()
        self.assertIn("exit_price", ctx.exception.message_dict)

    def test_closed_trade_requires_an_exit_date(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(exit_date=None).full_clean()
        self.assertIn("exit_date", ctx.exception.message_dict)

    def test_open_trade_does_not_need_exit_details(self):
        self.build(status=TradeStatus.OPEN, exit_price=None, exit_date=None).full_clean()

    def test_invalid_asset_class_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(asset_class="BANANA").full_clean()
        self.assertIn("asset_class", ctx.exception.message_dict)

    def test_form_reports_negative_prices_as_field_errors(self):
        self.login_alice()
        response = self.client.post(
            reverse("trade_create"),
            {
                "account": self.alice_account.pk,
                "symbol": "AAPL",
                "asset_class": AssetClass.STOCKS,
                "direction": Direction.LONG,
                "status": TradeStatus.OPEN,
                "entry_date": "2024-01-01T10:00",
                "entry_price": "-5",
                "quantity": "10",
                "fees": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("entry_price", response.context["form"].errors)
        self.assertEqual(Trade.objects.count(), 0)


class TradeCrudViewTests(JournalTestCase):
    def setUp(self):
        super().setUp()
        self.login_alice()
        self.trade = self.make_trade()

    def test_create_trade(self):
        response = self.client.post(
            reverse("trade_create"),
            {
                "account": self.alice_account.pk,
                "symbol": "btc/usdt",
                "asset_class": AssetClass.CRYPTO,
                "direction": Direction.LONG,
                "status": TradeStatus.CLOSED,
                "entry_date": "2024-03-01T09:30",
                "entry_price": "61000.123456",
                "quantity": "0.35",
                "stop_loss": "58000",
                "take_profit": "70000",
                "exit_date": "2024-03-08T16:00",
                "exit_price": "68900",
                "fees": "18.00",
                "tags": [],
                "new_tags": "Breakout, FOMO, Breakout",
                "notes": "Range break.",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))
        trade = Trade.objects.get(symbol="BTC/USDT")
        self.assertEqual(trade.account, self.alice_account)
        self.assertEqual(trade.entry_price, Decimal("61000.123456"))
        self.assertEqual(sorted(t.name for t in trade.tags.all()), ["Breakout", "FOMO"])

    def test_edit_trade(self):
        response = self.client.post(
            reverse("trade_update", args=[self.trade.pk]),
            {
                "account": self.alice_account.pk,
                "symbol": "AAPL",
                "asset_class": AssetClass.STOCKS,
                "direction": Direction.SHORT,
                "status": TradeStatus.CLOSED,
                "entry_date": "2024-02-01T10:00",
                "entry_price": "100",
                "quantity": "10",
                "exit_date": "2024-02-05T10:00",
                "exit_price": "90",
                "fees": "0",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))
        self.trade.refresh_from_db()
        self.assertEqual(self.trade.direction, Direction.SHORT)
        self.assertEqual(self.trade.gross_pnl, Decimal("100"))

    def test_delete_trade(self):
        url = reverse("trade_delete", args=[self.trade.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url)
        self.assertRedirects(response, reverse("trade_list"))
        self.assertFalse(Trade.objects.filter(pk=self.trade.pk).exists())

    def test_trade_list_paginates_at_15_per_page(self):
        for index in range(17):
            self.make_trade(symbol=f"S{index:02d}")
        response = self.client.get(reverse("trade_list"))
        self.assertEqual(len(response.context["trades"]), 15)
        self.assertEqual(response.context["paginator"].num_pages, 2)

        page_two = self.client.get(reverse("trade_list"), {"page": 2})
        self.assertEqual(len(page_two.context["trades"]), 3)

    def test_filters_by_status_direction_asset_class_and_symbol(self):
        self.make_trade(symbol="ETHUSDT", asset_class=AssetClass.CRYPTO,
                        direction=Direction.SHORT)
        self.make_trade(symbol="OPENONE", status=TradeStatus.OPEN,
                        exit_price=None, exit_date=None)

        cases = [
            ({"status": TradeStatus.OPEN}, {"OPENONE"}),
            ({"direction": Direction.SHORT}, {"ETHUSDT"}),
            ({"asset_class": AssetClass.CRYPTO}, {"ETHUSDT"}),
            ({"symbol": "eth"}, {"ETHUSDT"}),
            ({"symbol": "AAPL"}, {"AAPL"}),
        ]
        for params, expected in cases:
            with self.subTest(filter=params):
                response = self.client.get(reverse("trade_list"), params)
                self.assertEqual({t.symbol for t in response.context["trades"]}, expected)
                self.assertTrue(response.context["is_filtered"])

    def test_junk_filter_values_are_ignored(self):
        response = self.client.get(
            reverse("trade_list"),
            {"status": "DROP TABLE", "direction": ";--", "asset_class": "nope"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["trades"]), 1)

    def test_create_account(self):
        response = self.client.post(
            reverse("account_create"),
            {"name": "Oanda", "currency": "usd", "initial_balance": "1000.00"},
        )
        self.assertRedirects(response, reverse("dashboard"))
        account = TradingAccount.objects.get(name="Oanda")
        self.assertEqual(account.user, self.alice)
        self.assertEqual(account.currency, "USD")


class CsvTests(JournalTestCase):
    HEADER = (
        "symbol,asset_class,direction,entry_date,entry_price,quantity,"
        "status,stop_loss,exit_date,exit_price,fees,tags,notes\n"
    )

    def upload(self, body):
        return SimpleUploadedFile("trades.csv", body.encode(), content_type="text/csv")

    def setUp(self):
        super().setUp()
        self.login_alice()

    def test_export_writes_a_header_and_computed_columns(self):
        self.make_trade(fees=Decimal("5"))
        response = self.client.get(reverse("trade_export"))
        rows = list(csv.DictReader(response.content.decode().splitlines()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(Decimal(rows[0]["net_pnl"]), Decimal("95"))
        self.assertEqual(Decimal(rows[0]["r_multiple"]), Decimal("2"))

    def test_export_skips_open_trades(self):
        self.make_trade(symbol="CLOSEDONE")
        self.make_trade(symbol="OPENTWO", status=TradeStatus.OPEN,
                        exit_price=None, exit_date=None)
        response = self.client.get(reverse("trade_export"))
        self.assertNotIn("OPENTWO", response.content.decode())

    def test_parse_reports_missing_columns(self):
        trades, errors = csv_io.parse_trades_csv("symbol,entry_price\nAAPL,100\n", self.alice_account)
        self.assertEqual(trades, [])
        self.assertIn("Missing required column", errors[0])

    def test_parse_returns_valid_trades_and_flags_bad_rows(self):
        body = self.HEADER + (
            "AAPL,STOCKS,LONG,2024-01-01T10:00,100,10,CLOSED,95,2024-01-05T10:00,110,1,Breakout|Earnings,ok\n"
            "MSFT,STOCKS,LONG,2024-01-01T10:00,100,10,CLOSED,95,2023-12-01T10:00,110,0,,bad date order\n"
            "TSLA,STOCKS,LONG,2024-01-01T10:00,-5,10,CLOSED,95,2024-01-05T10:00,110,0,,negative\n"
            "NVDA,BANANA,LONG,2024-01-01T10:00,100,10,CLOSED,95,2024-01-05T10:00,110,0,,bad class\n"
        )
        trades, errors = csv_io.parse_trades_csv(body, self.alice_account)
        self.assertEqual([t.symbol for t in trades], ["AAPL"])
        self.assertEqual([line for line, _ in errors], [3, 4, 5])
        self.assertTrue(any("exit_date" in message for _, messages in errors for message in messages))
        self.assertTrue(any("entry_price" in message for _, messages in errors for message in messages))
        self.assertTrue(any("asset_class" in message for _, messages in errors for message in messages))

    def test_parse_infers_status_from_exit_fields(self):
        body = self.HEADER + "AAPL,STOCKS,LONG,2024-01-01T10:00,100,10,,95,2024-01-05T10:00,110,0,,\n"
        trades, errors = csv_io.parse_trades_csv(body, self.alice_account)
        self.assertEqual(errors, [])
        self.assertEqual(trades[0].status, TradeStatus.CLOSED)

        body = self.HEADER + "AAPL,STOCKS,LONG,2024-01-01T10:00,100,10,,95,,,0,,\n"
        trades, errors = csv_io.parse_trades_csv(body, self.alice_account)
        self.assertEqual(errors, [])
        self.assertEqual(trades[0].status, TradeStatus.OPEN)

    def test_parse_rejects_a_closed_row_missing_its_exit_date(self):
        body = self.HEADER + "AAPL,STOCKS,LONG,2024-01-01T10:00,100,10,,95,,110,0,,\n"
        trades, errors = csv_io.parse_trades_csv(body, self.alice_account)
        self.assertEqual(trades, [])
        self.assertIn("Exit date is required", errors[0][1][0])

    def test_parse_flags_a_row_with_too_many_columns(self):
        body = self.HEADER + "AAPL,STOCKS,LONG,2024-01-01T10:00,100,10,CLOSED,95,2024-01-05T10:00,110,0,,,extra\n"
        trades, errors = csv_io.parse_trades_csv(body, self.alice_account)
        self.assertEqual(trades, [])
        self.assertEqual(errors[0][0], 2)
        self.assertIn("header columns", errors[0][1][0])

    def test_parse_accepts_plain_dates_and_lowercase_choices(self):
        body = self.HEADER + "eur/usd,forex,long,2024-01-01,1.0850,1000,,1.0750,2024-01-08,1.09,,,\n"
        trades, errors = csv_io.parse_trades_csv(body, self.alice_account)
        self.assertEqual(errors, [])
        trade = trades[0]
        self.assertEqual(trade.symbol, "EUR/USD")
        self.assertEqual(trade.asset_class, AssetClass.FOREX)
        self.assertEqual(trade.entry_date.year, 2024)
        self.assertEqual(trade.exit_date.year, 2024)
        self.assertIsNotNone(trade.entry_date.tzinfo)

    def test_import_view_creates_trades_with_tags(self):
        body = self.HEADER + (
            "AAPL,STOCKS,LONG,2024-01-01T10:00,100,10,CLOSED,95,2024-01-05T10:00,110,1,Breakout|FOMO,note\n"
            "MSFT,STOCKS,SHORT,2024-01-02T10:00,400,5,CLOSED,410,2024-01-08T10:00,390,1,,short\n"
        )
        response = self.client.post(
            reverse("trade_import"),
            {"account": self.alice_account.pk, "file": self.upload(body)},
        )
        self.assertRedirects(response, reverse("trade_list"))
        self.assertEqual(Trade.objects.filter(account=self.alice_account).count(), 2)
        aapl = Trade.objects.get(symbol="AAPL")
        self.assertEqual(sorted(t.name for t in aapl.tags.all()), ["Breakout", "FOMO"])
        self.assertEqual(aapl.net_pnl, Decimal("99"))

    def test_import_view_rejects_the_whole_file_if_one_row_is_bad(self):
        body = self.HEADER + (
            "AAPL,STOCKS,LONG,2024-01-01T10:00,100,10,CLOSED,95,2024-01-05T10:00,110,1,,good\n"
            "MSFT,STOCKS,LONG,2024-01-01T10:00,100,10,CLOSED,95,2023-12-01T10:00,110,0,,bad\n"
        )
        response = self.client.post(
            reverse("trade_import"),
            {"account": self.alice_account.pk, "file": self.upload(body)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Trade.objects.count(), 0)
        self.assertIn("file", response.context["form"].errors)

    def test_import_view_rejects_a_file_without_trade_rows(self):
        response = self.client.post(
            reverse("trade_import"),
            {"account": self.alice_account.pk, "file": self.upload(self.HEADER)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Trade.objects.count(), 0)

    def test_export_import_round_trip_preserves_pnl(self):
        original = self.make_trade(symbol="ROUND", fees=Decimal("2.50"))
        body = self.client.get(reverse("trade_export")).content.decode()
        self.assertIn("ROUND", body)

        reimported, errors = csv_io.parse_trades_csv(body, self.alice_account)
        self.assertEqual(errors, [])
        self.assertEqual(reimported[0].net_pnl, original.net_pnl)
        self.assertEqual(reimported[0].r_multiple, original.r_multiple)


class AuthenticationViewTests(JournalTestCase):
    def test_signup_creates_a_user_and_logs_them_in(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "newtrader",
                "email": "new@example.com",
                "password1": "sup3r-secret-pass",
                "password2": "sup3r-secret-pass",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))
        self.assertTrue(User.objects.filter(username="newtrader").exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]),
                         User.objects.get(username="newtrader").pk)

    def test_signup_rejects_weak_passwords(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "weakling",
                "password1": "12345",
                "password2": "12345",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="weakling").exists())

    def test_login_then_logout(self):
        self.login_alice()
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
