"""Display filters.

The stats layer returns ``None`` for anything mathematically undefined (no
trades, no losing trades, no stop loss). These filters turn those ``None``s
into ``N/A`` so no template ever prints a bare "None" or divides by zero.
"""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter
def money(value, places=2):
    """Format as currency; ``N/A`` when undefined."""
    number = _decimal(value)
    if number is None:
        return "N/A"
    return f"{number:,.{int(places)}f}"


@register.filter
def signed_money(value, places=2):
    """Like :func:`money` but always carries an explicit ``+``/``-`` sign."""
    number = _decimal(value)
    if number is None:
        return "N/A"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.{int(places)}f}"


@register.filter
def percent(value, places=1):
    number = _decimal(value)
    if number is None:
        return "N/A"
    return f"{number:,.{int(places)}f}%"


@register.filter
def r_multiple(value, places=2):
    """Format an R-multiple, e.g. ``+1.85R``. ``N/A`` without a stop loss."""
    number = _decimal(value)
    if number is None:
        return "N/A"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.{int(places)}f}R"


@register.filter
def pnl_class(value):
    """CSS suffix for win/loss colouring."""
    number = _decimal(value)
    if number is None:
        return "text-slate-500"
    if number > 0:
        return "text-emerald-600"
    if number < 0:
        return "text-rose-600"
    return "text-slate-500"


@register.filter
def styled_field(field):
    """Render a field with the shared Tailwind input classes.

    Used for Django's built-in auth forms, whose widgets this project does
    not otherwise get to configure.
    """
    from journal.forms import INPUT_CLASSES

    return field.as_widget(attrs={"class": INPUT_CLASSES})
