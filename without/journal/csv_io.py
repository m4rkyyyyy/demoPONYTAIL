"""CSV import/export for trades.

Both directions are pure functions over strings/lists so they can be unit
tested without touching the request layer. Import is deliberately
all-or-nothing: a file with a single bad row is rejected wholesale rather than
half-imported, because a partially imported journal is harder to reason about
than a rejected one.
"""

import csv
import io
from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from journal.models import Tag, Trade, TradeStatus

REQUIRED_COLUMNS = ("symbol", "asset_class", "direction", "entry_date", "entry_price", "quantity")
OPTIONAL_COLUMNS = (
    "status",
    "stop_loss",
    "take_profit",
    "exit_date",
    "exit_price",
    "fees",
    "tags",
    "notes",
)

EXPORT_COLUMNS = (
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
    "net_pnl",
    "return_pct",
    "r_multiple",
    "tags",
    "notes",
)

ZERO = Decimal("0")


def _fmt(value):
    """Render a Decimal without exponent notation (``1E+2`` is not CSV-friendly)."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def _fmt_dt(value):
    return value.isoformat() if value else ""


def export_rows(trades):
    """Yield dict rows for ``csv.DictWriter``, one per closed trade."""
    for trade in trades:
        yield {
            "account": trade.account.name,
            "symbol": trade.symbol,
            "asset_class": trade.asset_class,
            "direction": trade.direction,
            "status": trade.status,
            "entry_date": _fmt_dt(trade.entry_date),
            "entry_price": _fmt(trade.entry_price),
            "quantity": _fmt(trade.quantity),
            "stop_loss": _fmt(trade.stop_loss),
            "take_profit": _fmt(trade.take_profit),
            "exit_date": _fmt_dt(trade.exit_date),
            "exit_price": _fmt(trade.exit_price),
            "fees": _fmt(trade.fees),
            "net_pnl": _fmt(trade.net_pnl),
            "return_pct": _fmt(trade.return_pct),
            "r_multiple": _fmt(trade.r_multiple),
            "tags": "|".join(tag.name for tag in trade.tags.all()),
            "notes": trade.notes,
        }


def export_to_csv(trades):
    """Render trades to a CSV string with a header row."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    for row in export_rows(trades):
        writer.writerow(row)
    return buffer.getvalue()


def parse_datetime_field(raw):
    """Parse a date or datetime cell into an aware datetime, or ``None``."""
    raw = (raw or "").strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        day = parse_date(raw)
        if day is None:
            raise ValueError(f"'{raw}' is not a valid date")
        parsed = datetime.combine(day, time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def parse_decimal_field(raw, allow_blank=True):
    raw = (raw or "").strip()
    if not raw:
        if allow_blank:
            return None
        raise ValueError("this column is required")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"'{raw}' is not a number") from None


def _clean_row(row):
    """Map one normalised dict onto model kwargs. Raises ``ValueError`` on junk."""
    data = {
        "symbol": row.get("symbol", "").upper(),
        "asset_class": row.get("asset_class", "").upper(),
        "direction": row.get("direction", "").upper(),
        "entry_date": parse_datetime_field(row.get("entry_date")),
        "entry_price": parse_decimal_field(row.get("entry_price"), allow_blank=False),
        "quantity": parse_decimal_field(row.get("quantity"), allow_blank=False),
        "stop_loss": parse_decimal_field(row.get("stop_loss")),
        "take_profit": parse_decimal_field(row.get("take_profit")),
        "exit_date": parse_datetime_field(row.get("exit_date")),
        "exit_price": parse_decimal_field(row.get("exit_price")),
        "fees": parse_decimal_field(row.get("fees")) or ZERO,
        "notes": row.get("notes", ""),
    }

    status = (row.get("status") or "").upper()
    if not status:
        # Infer when the caller left it blank: an exit price means it closed.
        has_exit = data["exit_price"] is not None or data["exit_date"] is not None
        status = TradeStatus.CLOSED if has_exit else TradeStatus.OPEN
    data["status"] = status

    if not data["entry_date"]:
        raise ValueError("entry_date is required")
    return data


def parse_trades_csv(text, account):
    """Validate a CSV payload.

    Returns ``(trades, errors)``. ``errors`` is a list of
    ``(line_number, [messages])`` tuples; ``trades`` only contains rows that
    validated cleanly, so the caller can show a preview and let the user fix
    the file.
    """
    reader = csv.DictReader(text.splitlines())

    if not reader.fieldnames:
        return [], ["The file is empty."]

    headers = {(name or "").strip().lower() for name in reader.fieldnames}
    missing = [name for name in REQUIRED_COLUMNS if name not in headers]
    if missing:
        return [], ["Missing required column(s): " + ", ".join(missing)]

    trades = []
    errors = []

    for line_number, raw_row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in raw_row.values()):
            continue

        # DictReader files surplus values under a None key; treat that as a
        # malformed row rather than tripping over a list where a str is expected.
        if None in raw_row:
            errors.append(
                (line_number, ["More values in this row than there are header columns."])
            )
            continue

        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw_row.items()}
        try:
            trade = Trade(account=account, **_clean_row(row))
        except ValueError as exc:
            errors.append((line_number, [str(exc)]))
            continue

        try:
            trade.full_clean()
        except ValidationError as exc:
            errors.append((line_number, _flatten(exc)))
            continue

        trade._pending_tags = [name for name in row.get("tags", "").split("|") if name.strip()]
        trades.append(trade)

    return trades, errors


def _flatten(exc):
    if hasattr(exc, "message_dict"):
        return [f"{field}: {msg}" for field, msgs in exc.message_dict.items() for msg in msgs]
    return list(exc.messages)


def save_trades(trades, user):
    """Persist validated trades plus any inline tags. Caller supplies the atomic block."""
    created = 0
    for trade in trades:
        trade.save()
        for name in getattr(trade, "_pending_tags", []):
            name = name.strip()
            if not name:
                continue
            tag, _ = Tag.objects.get_or_create(user=user, name=name[:32])
            trade.tags.add(tag)
        created += 1
    return created
