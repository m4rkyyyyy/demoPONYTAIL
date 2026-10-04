"""Forms: trade CRUD, signup, CSV import."""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Tag, Trade, TradingAccount

INPUT = (
    "block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
)
SELECT = INPUT + " pr-8"

# datetime-local submits "YYYY-MM-DDTHH:MM"; tell the field to read it back.
DATETIME_INPUT = {"type": "datetime-local"}
DATETIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]


class StyledForm(forms.Form):
    """One place to keep the Tailwind classes on every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                SELECT if isinstance(field.widget, forms.Select) else INPUT
            )


class TradeForm(StyledForm, forms.ModelForm):
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
            "exit_date",
            "exit_price",
            "stop_loss",
            "take_profit",
            "fees",
            "tags",
            "notes",
        ]
        widgets = {
            "entry_date": forms.DateTimeInput(attrs=DATETIME_INPUT),
            "exit_date": forms.DateTimeInput(attrs=DATETIME_INPUT),
            "notes": forms.Textarea(attrs={"rows": 3, "class": INPUT}),
            "tags": forms.SelectMultiple(attrs={"size": 4}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = TradingAccount.objects.filter(user=user)
        self.fields["tags"].queryset = Tag.objects.all()
        for name in ("entry_date", "exit_date"):
            self.fields[name].widget.format = DATETIME_FORMATS[0]
            self.fields[name].input_formats = DATETIME_FORMATS

    def clean_symbol(self):
        return self.cleaned_data["symbol"].strip().upper()


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("username", "email", "password1", "password2"):
            self.fields[name].widget.attrs["class"] = INPUT


class TradeImportForm(StyledForm, forms.Form):
    file = forms.FileField(
        label="CSV file",
        help_text=(
            "Header row required: symbol, asset_class, direction, status, entry_date, "
            "entry_price, quantity, exit_date, exit_price, stop_loss, take_profit, fees, notes. "
            "All trades are imported into the selected account."
        ),
    )
    account = forms.ModelChoiceField(queryset=TradingAccount.objects.none())

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = TradingAccount.objects.filter(user=user)