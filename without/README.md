# TradePulse

A self-contained Django trading journal for equities, forex, crypto, futures
and options. Log every trade, see what actually made money, and stop guessing.

No build step: Tailwind, Chart.js and Lucide come from CDNs, the database is
SQLite, and `seed_trades` fills it with a realistic demo journal.

---

## Setup

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate          # create db.sqlite3
python manage.py seed_trades      # demo user + 2 accounts + 20 trades
python manage.py runserver        # http://127.0.0.1:8000
```

Log in at <http://127.0.0.1:8000/> with **`trader` / `password123`**.

Re-running the seeder is safe. Pass `--force` to rebuild the trades:

```powershell
python manage.py seed_trades --force
```

Run the tests:

```powershell
python manage.py test              # 68 tests
python manage.py test journal.tests.PnlCalculationTests   # one group
```

## Layout

```
manage.py
requirements.txt
tradepulse/            settings, root urls, wsgi/asgi
journal/
  models.py            TradingAccount, Trade, Tag + the P&L properties
  metrics.py           summarize() / equity_curve() -- all dashboard maths
  csv_io.py            CSV import (all-or-nothing) and export
  forms.py             account, trade, import and signup forms
  views.py             one place where the per-user queryset rule lives
  urls.py  auth_urls.py
  admin.py
  tests.py
  management/commands/seed_trades.py
  templates/
    base.html
    registration/{login,signup}.html
    journal/{dashboard,trade_list,trade_form,trade_confirm_delete,
             trade_import,account_form,_form_fields}.html
```

## How it works

### Data model

`Trade -> TradingAccount -> User`. That chain is what makes isolation cheap:
one filter (`Trade.objects.filter(account__user=request.user)`) scopes every
query, and `OwnedTradeMixin` applies it to all object lookups, so a guessed ID
from another user 404s instead of leaking. `Tag` is user-scoped too, so two
traders can both have a "FOMO" tag.

Prices and quantities are `DecimalField(max_digits=20, decimal_places=6)`, so a
forex entry of `0.08432` or a crypto size of `0.00321456` round-trips exactly
instead of being silently rounded.

### P&L

All of it lives on the model, and everything returns `None` rather than raising
when it is undefined:

| Property | Definition |
| --- | --- |
| `gross_pnl` | `(exit - entry) * qty`, sign flipped for `SHORT`; `None` while open |
| `net_pnl` | `gross_pnl - fees` |
| `return_pct` | `net_pnl / (entry * qty) * 100` |
| `risk_per_unit` | `abs(entry - stop_loss)` |
| `initial_risk` | `risk_per_unit * qty` |
| `r_multiple` | realised move / `risk_per_unit` |

`r_multiple` is `None` for an open trade, for a trade with no stop, and for a
trade whose stop sits exactly on the entry price -- that last case has zero
risk, so the ratio is mathematically undefined and the UI shows `N/A`.

### Dashboard maths

`journal/metrics.py:summarize()` returns a `JournalStats` dataclass.
`win_rate`, `profit_factor` and `avg_r` are `None` when undefined (no closed
trades; or no losing trade, which makes profit factor infinite rather than
zero). The `journal_extras` template filters turn any `None` into `N/A`, so a
brand-new account renders cleanly instead of raising `ZeroDivisionError`.

The equity curve starts from the combined opening balance of the user's
accounts and accumulates net P&L in exit-date order.

### Validation

Per-field `MinValueValidator(0)` blocks negative prices, quantities and fees.
`Trade.clean()` handles the cross-field rules: exit date may not precede entry
date, and `CLOSED` trades require both `exit_price` and `exit_date`.

### CSV

Export writes every closed trade in the journal, including computed `net_pnl`,
`return_pct` and `r_multiple`, so a round-trip is lossless for P&L.

Import is **all-or-nothing**. Every row is validated first; if one row fails,
the file is rejected and the offending line numbers are reported. A partially
imported journal is harder to reason about than a rejected file.

Required: `symbol, asset_class, direction, entry_date, entry_price, quantity`.
Optional: `status, stop_loss, take_profit, exit_date, exit_price, fees, tags, notes`.

`status` may be blank -- it is inferred from the exit fields. Dates accept
`YYYY-MM-DD` or full ISO timestamps. Multiple tags are separated by `|`.
`asset_class`, `direction` and `status` are case-insensitive.

## Routes

| Path | Name | Notes |
| --- | --- | --- |
| `/` | `dashboard` | metrics, equity curve, win/loss, last 5 trades |
| `/trades/` | `trade_list` | 15 per page, filter by status/direction/class/symbol |
| `/trades/new/` | `trade_create` | |
| `/trades/<id>/edit/` | `trade_update` | 404s on another user's trade |
| `/trades/<id>/delete/` | `trade_delete` | 404s on another user's trade |
| `/trades/import/` | `trade_import` | |
| `/trades/export/` | `trade_export` | closed trades, CSV attachment |
| `/accounts/new/` | `account_create` | |
| `/accounts/login/` `/accounts/logout/` `/accounts/signup/` | | logout is POST-only (Django 5) |
| `/admin/` | | staff only |

## Production notes

Defaults are tuned for local development. Before deploying:

```powershell
$env:DJANGO_DEBUG = "0"
$env:DJANGO_SECRET_KEY = "<generate a real one>"
$env:DJANGO_ALLOWED_HOSTS = "yourdomain.com"
```

Then `python manage.py collectstatic` and serve `staticfiles/` over HTTPS.
Swap `SECRET_KEY`, `ALLOWED_HOSTS` and the CSRF trusted origins via the
environment rather than editing `settings.py`. Tailwind and Chart.js are loaded
from public CDNs; vendor them yourself if you need a hard content-security-policy.

The equity curve sums balances across accounts, which assumes they share a
currency. Group by account if you trade multiple currencies at once.
