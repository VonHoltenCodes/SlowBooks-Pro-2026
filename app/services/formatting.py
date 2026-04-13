from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def format_currency(value, settings: dict | None = None) -> str:
    settings = settings or {}
    currency = settings.get("currency", "USD")
    amount = _decimal(value)
    symbol = "$" if currency in {"NZD", "USD"} else f"{currency} "
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    return f"{sign}{symbol}{amount:,.2f}"


def _coerce_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def format_date(value, settings: dict | None = None) -> str:
    settings = settings or {}
    parsed = _coerce_date(value)
    if not parsed:
        return "" if not value else str(value)

    if settings.get("locale") == "en-NZ":
        return parsed.strftime("%d %b %Y").lstrip("0")
    return parsed.strftime("%b %d, %Y")
