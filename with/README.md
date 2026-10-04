# TradePulse

Personal trading journal for equities, forex, crypto, futures and options.
Django 5 + SQLite, no build step (Tailwind and Chart.js come from CDNs).

## Setup

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_trades        # demo user: trader / password123, 20 trades
python manage.py test                # 19 tests
python manage.py runserver
```

Open http://127.0.0.1:8000/ and log in as `trader` / `password123`, or sign up for a
fresh empty journal.

## Layout

| Path | What |
| --- | --- |
| `journal/models.py` | `TradingAccount`, `Trade`, `Tag` + P&L math as properties |
| `journal/views.py` | Dashboard, list/CRUD, CSV import/export, `metrics()` |
| `journal/forms.py` | Trade/signup/import forms, Tailwind widget styling |
| `journal/tests.py` | P&L, metrics, validation, user isolation, import/export |
| `templates/` | base layout, dashboard, trade log, forms, auth |

P&L math is `Decimal` throughout: `gross_pnl`, `net_pnl` (gross − fees),
`return_pct` (% of entry notional) and `r_multiple` (realized profit ÷ entry-to-stop
risk). Anything a closed trade cannot answer returns `None` instead of raising, and the
templates print `N/A`.

Validation lives in `Trade.clean()` (exit not before entry, closed trades need an exit)
plus `MinValueValidator` and DB check constraints on price/quantity/fees, so forms,
the admin-less ORM and CSV import all reject the same bad rows.

## Deploying

Set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0` and `DJANGO_ALLOWED_HOSTS` in the
environment; everything else is environment-overridable from `tradepulse/settings.py`.