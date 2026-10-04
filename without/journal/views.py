"""Views for TradePulse.

Two rules hold everywhere in this module:

1. Every journal view requires a login (``LoginRequiredMixin``).
2. Every journal queryset is filtered through ``account__user=request.user``.

Because both are applied at the queryset level rather than in templates or
``if`` statements, a user cannot reach another user's trade by guessing an ID:
``SingleObjectMixin.get_object`` runs the scoped queryset and 404s on a miss.
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, FormView, ListView, TemplateView, UpdateView
from django.views.generic.base import View

from journal import csv_io
from journal.forms import SignUpForm, TradeForm, TradeImportForm, TradingAccountForm
from journal.metrics import equity_curve, summarize
from journal.models import ZERO, AssetClass, Direction, Trade, TradeStatus, TradingAccount


def user_trades(user):
    """The caller's trades, prefetched for list rendering."""
    return (
        Trade.objects.filter(account__user=user)
        .select_related("account")
        .prefetch_related("tags")
    )


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "journal/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        accounts = list(TradingAccount.objects.filter(user=user))
        trades = list(user_trades(user))
        closed = [t for t in trades if t.is_closed]
        open_trades = [t for t in trades if t.is_open]

        starting_balance = sum((a.initial_balance for a in accounts), ZERO)
        stats = summarize(closed, open_count=len(open_trades))

        context.update(
            {
                "stats": stats,
                "accounts": accounts,
                "starting_balance": starting_balance,
                "equity_points": equity_curve(closed, starting_balance),
                "win_loss": {
                    "wins": stats.win_count,
                    "losses": stats.loss_count,
                    "breakeven": stats.breakeven_count,
                },
                "recent_trades": trades[:5],
                "open_trades": open_trades[:5],
                "has_accounts": bool(accounts),
            }
        )
        return context


class TradeListView(LoginRequiredMixin, ListView):
    model = Trade
    template_name = "journal/trade_list.html"
    context_object_name = "trades"
    paginate_by = 15

    def get_selected_filters(self):
        """Only accept values that are real choices, so bad query strings can't 500."""
        query = self.request.GET
        return {
            "status": query.get("status", ""),
            "direction": query.get("direction", ""),
            "asset_class": query.get("asset_class", ""),
            "symbol": query.get("symbol", "").strip(),
        }

    def get_queryset(self):
        queryset = user_trades(self.request.user)
        filters = self.get_selected_filters()

        if filters["status"] in TradeStatus.values:
            queryset = queryset.filter(status=filters["status"])
        if filters["direction"] in Direction.values:
            queryset = queryset.filter(direction=filters["direction"])
        if filters["asset_class"] in AssetClass.values:
            queryset = queryset.filter(asset_class=filters["asset_class"])
        if filters["symbol"]:
            queryset = queryset.filter(symbol__icontains=filters["symbol"].upper())

        self.active_filters = filters
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_trades = context.get("page_obj").object_list if context.get("page_obj") else []
        stats = summarize(t for t in page_trades if t.is_closed)
        context.update(
            {
                "active_filters": getattr(self, "active_filters", {}),
                "status_choices": TradeStatus.choices,
                "direction_choices": Direction.choices,
                "asset_class_choices": AssetClass.choices,
                "page_stats": stats,
                "is_filtered": any(getattr(self, "active_filters", {}).values()),
                "symbols": (
                    user_trades(self.request.user)
                    .values_list("symbol", flat=True)
                    .distinct()
                    .order_by("symbol")
                ),
            }
        )
        return context


class OwnedTradeMixin(LoginRequiredMixin):
    """Scope object lookups to the caller's journal.

    Reusing :func:`user_trades` keeps the ownership rule in exactly one place.
    """

    def get_queryset(self):
        return user_trades(self.request.user)


class TradeCreateView(OwnedTradeMixin, CreateView):
    model = Trade
    form_class = TradeForm
    template_name = "journal/trade_form.html"
    success_url = reverse_lazy("dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.symbol} trade saved.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "New trade"
        context["submit_label"] = "Create trade"
        return context


class TradeUpdateView(OwnedTradeMixin, UpdateView):
    model = Trade
    form_class = TradeForm
    template_name = "journal/trade_form.html"
    success_url = reverse_lazy("dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.symbol} trade updated.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Edit {self.object.symbol}"
        context["submit_label"] = "Save changes"
        return context


class TradeDeleteView(OwnedTradeMixin, DeleteView):
    model = Trade
    template_name = "journal/trade_confirm_delete.html"
    success_url = reverse_lazy("trade_list")
    context_object_name = "trade"

    def form_valid(self, form):
        symbol = self.object.symbol
        response = super().form_valid(form)
        messages.success(self.request, f"{symbol} trade deleted.")
        return response


class AccountCreateView(LoginRequiredMixin, CreateView):
    model = TradingAccount
    form_class = TradingAccountForm
    template_name = "journal/account_form.html"
    success_url = reverse_lazy("dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Account '{self.object.name}' created.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "New trading account"
        context["submit_label"] = "Create account"
        return context


class TradeExportView(LoginRequiredMixin, View):
    """Stream every closed trade in the caller's journal as CSV."""

    def get(self, request):
        trades = (
            Trade.objects.filter(account__user=request.user, status=TradeStatus.CLOSED)
            .select_related("account")
            .prefetch_related("tags")
            .order_by("exit_date", "pk")
        )
        response = HttpResponse(csv_io.export_to_csv(trades), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="tradepulse-trades.csv"'
        return response


class TradeImportView(LoginRequiredMixin, FormView):
    template_name = "journal/trade_import.html"
    form_class = TradeImportForm
    success_url = reverse_lazy("trade_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        raw = form.cleaned_data["file"].read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        trades, errors = csv_io.parse_trades_csv(text, form.cleaned_data["account"])

        if errors:
            summary = "; ".join(
                f"row {line}: {problems[0]}" for line, problems in errors[:3]
            )
            if len(errors) > 3:
                summary += f" (+{len(errors) - 3} more)"
            form.add_error("file", ValidationError(f"{len(errors)} row(s) rejected. {summary}"))
            return self.form_invalid(form)

        if not trades:
            form.add_error("file", ValidationError("No trade rows found in that file."))
            return self.form_invalid(form)

        with transaction.atomic():
            created = csv_io.save_trades(trades, self.request.user)

        messages.success(self.request, f"Imported {created} trade(s).")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["required_columns"] = csv_io.REQUIRED_COLUMNS
        context["optional_columns"] = csv_io.OPTIONAL_COLUMNS
        return context
