"""Forms for accounts, trades and CSV import.

Every queryset that feeds a choice field is narrowed to the requesting user,
so a form can never offer another user's accounts or tags -- which is also
what makes the ModelForm's own uniqueness validation behave per user.
"""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from journal.models import Tag, Trade, TradingAccount

INPUT_CLASSES = (
    "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 "
    "text-sm text-slate-900 shadow-sm outline-none focus:border-indigo-500 "
    "focus:ring-2 focus:ring-indigo-200"
)
TEXTAREA_CLASSES = INPUT_CLASSES + " min-h-28"
SELECT_MULTIPLE_CLASSES = INPUT_CLASSES + " min-h-32"


def _split_tags(raw):
    """Turn ``"Breakout, fomo ,, Earnings"`` into ``["Breakout", "fomo", "Earnings"]``."""
    seen = set()
    names = []
    for chunk in (raw or "").split(","):
        name = chunk.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


class TagWidgetMixin:
    """Apply consistent Tailwind styling to every widget on a form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            if isinstance(widget, forms.Textarea):
                extra = TEXTAREA_CLASSES
            elif isinstance(widget, forms.SelectMultiple):
                extra = SELECT_MULTIPLE_CLASSES
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                extra = INPUT_CLASSES
            else:
                extra = INPUT_CLASSES
            widget.attrs.setdefault("class", extra)


class TradingAccountForm(TagWidgetMixin, forms.ModelForm):
    class Meta:
        model = TradingAccount
        fields = ["name", "currency", "initial_balance"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Interactive Brokers"}),
            "currency": forms.TextInput(attrs={"placeholder": "USD", "maxlength": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.user is not None:
            # Assigned before validation, not in the view, so the instance is
            # complete if anything reads it during form validation.
            self.instance.user = self.user

    def clean_name(self):
        """Enforce one account name per user.

        Done here rather than relying on the Meta constraint because ``user``
        is not a form field, which puts it in the form's validation
        exclusion list and makes Django skip that constraint check -- leaving
        the database to raise IntegrityError instead of showing an error.
        """
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            return name

        duplicates = TradingAccount.objects.filter(name=name, user=self.user)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError("You already have an account called '%s'." % name)
        return name

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "USD").strip().upper()


class TradeForm(TagWidgetMixin, forms.ModelForm):
    new_tags = forms.CharField(
        required=False,
        label="Add tags",
        help_text="Comma separated. Anything new is created for you.",
        widget=forms.TextInput(attrs={"placeholder": "Breakout, FOMO"}),
    )

    class Meta:
        model = Trade
        fields = [
            "account",
            "symbol",
            "asset_class",
            "direction",
            "status",
            "entry_date",
            "entry_price",
            "quantity",
            "stop_loss",
            "take_profit",
            "exit_date",
            "exit_price",
            "fees",
            "tags",
            "notes",
        ]
        widgets = {
            "symbol": forms.TextInput(attrs={"placeholder": "AAPL, EUR/USD, BTC/USDT"}),
            "notes": forms.Textarea(attrs={"placeholder": "Why did you take this trade?"}),
            "entry_date": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
            ),
            "exit_date": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
            ),
            "entry_price": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "quantity": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "exit_price": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "stop_loss": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "take_profit": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "fees": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "tags": forms.SelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.user is not None:
            self.fields["account"].queryset = TradingAccount.objects.filter(user=self.user)
            self.fields["tags"].queryset = Tag.objects.filter(user=self.user)
        self.fields["account"].empty_label = "Select an account"
        self.fields["tags"].required = False
        self.fields["entry_price"].required = True
        self.fields["quantity"].required = True
        self.fields["entry_date"].required = True
        if not self.instance.pk:
            self.fields["status"].initial = Trade._meta.get_field("status").default

    def clean_symbol(self):
        return (self.cleaned_data.get("symbol") or "").strip().upper()

    def save(self, commit=True):
        trade = super().save(commit=commit)
        if not commit:
            return trade
        for name in _split_tags(self.cleaned_data.get("new_tags")):
            tag, _ = Tag.objects.get_or_create(user=self.user, name=name)
            trade.tags.add(tag)
        return trade


class TradeImportForm(forms.Form):
    """Bulk upload: one account for the whole file keeps the import simple."""

    account = forms.ModelChoiceField(
        queryset=TradingAccount.objects.none(),
        label="Import into account",
        help_text="Every row in the file is attached to this account.",
    )
    file = forms.FileField(
        label="CSV file",
        help_text=(
            "Required columns: symbol, asset_class, direction, entry_date, "
            "entry_price, quantity. Optional: status, stop_loss, take_profit, "
            "exit_date, exit_price, fees, tags, notes."
        ),
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["account"].queryset = TradingAccount.objects.filter(user=user)
        self.fields["account"].empty_label = "Select an account"
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", INPUT_CLASSES)


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ["username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", INPUT_CLASSES)
