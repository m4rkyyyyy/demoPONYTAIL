"""Journal views. Every queryset is scoped through ``account__user``."""

import csv
import io
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from .forms import SignupForm, TradeForm, TradeImportForm
from .models import Trade, TradingAccount

EXPORT_COLUMNS = [
    "id", "account", "symbol", "asset_class", "direction", "status", "entry_date", "entry_price",
    "quantity", "exit_date", "exit_price", "stop_loss", "take_profit", "fees", "gross_pnl",
    "net_pnl", "return_pct", "r_multiple", "notes",
]

IMPORTABLE = {
    "symbol", "asset_class", "direction", "status", "entry_date", "entry_price", "quantity",
    "exit_date", "exit_price", "stop_loss", "take_profit", "fees", "notes",
}


def user_trades(user):
    return Trade.objects.filter(account__user=user)


def default_account(user):
    """Signup hands every user one account so trade entry never dead-ends."""
    account, _ = TradingAccount.objects.get_or_create(user=user, name="Main Account")
    return account


def metrics(closed):
    """Aggregate a closed-trade queryset. None means 'nothing to show yet'."""
    trades = list(closed)
    total = len(trades)
    wins = [t.net_pnl for t in trades if t.is_win]
    losses = [t.net_pnl for t in trades if t.net_pnl is not None and t.net_pnl < 0]
    realized_r = [t.r_multiple for t in trades if t.r_multiple is not None]
    gross_wins = sum(wins, Decimal("0"))
    gross_losses = abs(sum(losses, Decimal("0")))
    return {
        "closed_count": total,
        "win_rate": (len(wins) / total * 100) if total else None,
        "net_pnl": sum((t.net_pnl or Decimal("0") for t in trades), Decimal("0")),
        "profit_factor": (gross_wins / gross_losses) if gross_losses else None,
        "avg_r": (sum(realized_r) / len(realized_r)) if realized_r else None,
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven": total - len(wins) - len(losses),
    }


class SignUpView(CreateView):
    form_class = SignupForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        default_account(self.object)
        login(self.request, self.object)
        return response


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "journal/dashboard.html"

    def get_context_data(self, **kwargs):
        closed = user_trades(self.request.user).filter(status=Trade.Status.CLOSED)
        curve, running = [], Decimal("0")
        # ponytail: cumulative curve rebuilt per request; cache it if the log grows past thousands of rows
        for trade in closed.order_by("exit_date", "pk"):
            running += trade.net_pnl or Decimal("0")
            curve.append({"date": trade.exit_date.date().isoformat(), "equity": float(running)})
        return {
            **super().get_context_data(**kwargs),
            **metrics(closed),
            "equity_curve": curve,
            "recent_trades": user_trades(self.request.user)[:5],
        }


class TradeListView(LoginRequiredMixin, ListView):
    model = Trade
    template_name = "journal/trade_list.html"
    context_object_name = "trades"
    paginate_by = 15

    def get_queryset(self):
        queryset = user_trades(self.request.user)
        params = self.request.GET
        for param, field in (("status", "status"), ("direction", "direction"), ("asset_class", "asset_class")):
            if params.get(param):
                queryset = queryset.filter(**{field: params[param]})
        if params.get("q"):
            queryset = queryset.filter(symbol__icontains=params["q"].strip())
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            selected={key: self.request.GET.get(key, "") for key in ("status", "direction", "asset_class", "q")},
            status_choices=Trade.Status.choices,
            direction_choices=Trade.Direction.choices,
            asset_class_choices=Trade.AssetClass.choices,
        )
        return context


class TradeCreateView(LoginRequiredMixin, CreateView):
    model = Trade
    form_class = TradeForm
    template_name = "journal/trade_form.html"

    def get_form_kwargs(self):
        default_account(self.request.user)
        return {**super().get_form_kwargs(), "user": self.request.user}


class TradeUpdateView(LoginRequiredMixin, UpdateView):
    model = Trade
    form_class = TradeForm
    template_name = "journal/trade_form.html"

    def get_queryset(self):
        return user_trades(self.request.user)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}


class TradeDeleteView(LoginRequiredMixin, DeleteView):
    model = Trade
    template_name = "journal/trade_confirm_delete.html"
    success_url = reverse_lazy("trade_list")

    def get_queryset(self):
        return user_trades(self.request.user)


@login_required
def trade_export(request):
    trades = user_trades(request.user).filter(status=Trade.Status.CLOSED).order_by("exit_date", "pk")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="tradepulse-trades.csv"'
    writer = csv.writer(response)
    writer.writerow(EXPORT_COLUMNS)
    # ponytail: loads the whole closed history per export; paginate if a journal reaches 100k rows
    for trade in trades:
        writer.writerow([
            trade.pk,
            trade.account.name,
            trade.symbol,
            trade.asset_class,
            trade.direction,
            trade.status,
            trade.entry_date.isoformat(),
            trade.entry_price,
            trade.quantity,
            trade.exit_date.isoformat() if trade.exit_date else "",
            trade.exit_price if trade.exit_price is not None else "",
            trade.stop_loss if trade.stop_loss is not None else "",
            trade.take_profit if trade.take_profit is not None else "",
            trade.fees,
            trade.gross_pnl,
            trade.net_pnl,
            round(trade.return_pct, 4) if trade.return_pct is not None else "",
            round(trade.r_multiple, 4) if trade.r_multiple is not None else "",
            trade.notes,
        ])
    return response


@login_required
def trade_import(request):
    form = TradeImportForm(request.POST or None, request.FILES or None, user=request.user)
    result = None
    if request.method == "POST" and form.is_valid():
        result = import_trades(request.FILES["file"], form.cleaned_data["account"])
        if result["errors"]:
            form.add_error("file", "Nothing was imported — fix the rows below.")
        else:
            messages.success(request, f"Imported {result['created']} trade(s).")

    return render(
        request,
        "journal/trade_import.html",
        {"form": form, "created": result["created"] if result else None, "errors": result["errors"] if result else []},
    )


def import_trades(upload, account):
    """Row-by-row import; every row runs through the model's own validation."""
    try:
        text = io.StringIO(upload.read().decode("utf-8-sig"))
    except UnicodeDecodeError:
        return {"created": 0, "errors": ["File must be a UTF-8 encoded CSV."]}

    reader = csv.DictReader(text)
    if not {"symbol", "entry_price", "quantity", "entry_date"} <= set(reader.fieldnames or []):
        return {"created": 0, "errors": ["Header row must contain at least: symbol, entry_date, entry_price, quantity."]}

    created, errors = 0, []
    for line, row in enumerate(reader, start=2):
        data = {key: value for key, value in row.items() if key in IMPORTABLE and value not in (None, "")}
        trade = Trade(account=account, **data)
        try:
            trade.full_clean(exclude=["tags"])
            trade.save()
            created += 1
        except ValidationError as exc:
            errors.append(f"Row {line}: {'; '.join(exc.messages)}")
    return {"created": created, "errors": errors}