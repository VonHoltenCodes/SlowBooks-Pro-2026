# ============================================================================
# CSV Import Service — import entities from CSV files
# Feature 14: Resilient import with error collection (like iif_import.py)
#
# Imported text is stored verbatim — deliberately — with one exception: the
# apostrophe our own export puts in front of a formula-shaped value
# (csv_export._csv_safe) is taken off again, or re-importing our own file
# created a second customer named "'=HYPERLINK(...)" (2.17.3 exploratory
# test, W-M15). A value like "-Dash Co" is otherwise kept as written.
# XSS is handled by escapeHtml() in the frontend.
#
# A row is checked the way the customer / vendor form checks it: an email
# must look like one, terms must be one the form offers, lengths fit the
# columns. A row that fails is reported and skipped; the rest import.
# ============================================================================

import csv
import io
import logging
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType
from app.schemas.contacts import CustomerCreate, VendorCreate
from app.services.csv_export import strip_formula_guard

logger = logging.getLogger(__name__)

# How many terms a row's refusal names.
TERMS_SHOWN = 10


def form_terms(db: Session) -> tuple[str, ...]:
    """The terms the customer and vendor forms offer: the active entries
    of the terms list (#664). A value outside it has no option on the form,
    so the next save of that customer silently changed it to the first
    option."""
    from app.models.payment_terms import PaymentTerm
    from app.services.payment_terms import ensure_seeded

    ensure_seeded(db)
    return tuple(
        name
        for (name,) in db.query(PaymentTerm.name)
        .filter(PaymentTerm.is_active.is_(True))
        .order_by(PaymentTerm.name)
    )


class UnguardedDictReader(csv.DictReader):
    """csv.DictReader that takes the export's formula guard off every value
    (csv_export.strip_formula_guard). For every importer that reads names
    or text from a CSV."""

    def __next__(self):
        row = super().__next__()
        return {k: strip_formula_guard(v) for k, v in row.items()}


def _cell(row: dict, key: str) -> str | None:
    """A text cell, trimmed; blank is absent."""
    value = (row.get(key) or "").strip()
    return value or None


def _terms(row: dict, default: str, choices) -> tuple[str | None, str | None]:
    """(terms in the form's spelling, None) — `default` when the cell is
    blank — or (None, the sentence to show) when the form does not offer
    them. Row messages are built here, never taken from an exception's
    text (CodeQL py/stack-trace-exposure)."""
    raw = _cell(row, "Terms")
    if raw is None:
        return default, None
    for option in choices:
        if " ".join(raw.split()).lower() == " ".join(option.split()).lower():
            return option, None
    # the list named, but not without end: a long list ends "and N more"
    shown = ", ".join(choices[:TERMS_SHOWN])
    if len(choices) > TERMS_SHOWN:
        shown += f" and {len(choices) - TERMS_SHOWN} more"
    return None, (
        f'Terms "{raw[:50]}" isn\'t on the terms list ({shown}). '
        "Use one of those, add it under Settings > Payment Terms, or leave "
        "Terms blank for the default."
    )


_LABELS = {
    "bill_address1": "Address",
    "address1": "Address",
    "bill_city": "City",
    "bill_state": "State",
    "bill_zip": "ZIP",
    "zip": "ZIP",
}


def _validation_message(exc: ValidationError, values: dict) -> str:
    """The first problem pydantic found, as a sentence a person can act on."""
    err = exc.errors()[0]
    field = str(err.get("loc", ["value"])[0])
    label = _LABELS.get(field, field.replace("_", " ").capitalize())
    if field == "email":
        return (
            f"\"{values.get('email')}\" is not an email address. Fix it, or "
            "leave Email blank."
        )
    if err.get("type") == "string_too_long":
        limit = (err.get("ctx") or {}).get("max_length")
        return f"{label} is longer than {limit} characters."
    return f"{label} is not valid."


def _contact_import(db: Session, csv_text: str, model, schema, fields, default_terms):
    reader = UnguardedDictReader(io.StringIO(csv_text))
    choices = form_terms(db)
    created = 0
    skipped = 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            name = (row.get("Name") or "").strip()[:200]
            if not name:
                errors.append(f"Row {i}: Missing name")
                continue

            existing = db.query(model).filter(model.name == name).first()
            if existing:
                skipped += 1
                continue

            values = {"name": name}
            for column, attr in fields:
                value = _cell(row, column)
                if value is not None:
                    values[attr] = value
            values["terms"], problem = _terms(row, default_terms, choices)
            if problem:
                errors.append(f"Row {i}: {problem}")
                continue
            try:
                data = schema.model_validate(values)
            except ValidationError as exc:
                errors.append(f"Row {i}: {_validation_message(exc, values)}")
                continue

            db.add(model(**data.model_dump()))
            created += 1
        except Exception:
            logger.exception("Failed to import %s row %d", model.__name__, i)
            errors.append(f"Row {i}: import failed")

    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


def import_customers(db: Session, csv_text: str) -> dict:
    from app.services.settings_service import get_all_settings

    # A row without terms gets the company's default (Settings -> Default
    # Terms), not a blank that no screen can show.
    default_terms = get_all_settings(db).get("default_terms") or "Net 30"
    if default_terms not in form_terms(db):
        default_terms = "Net 30"
    fields = (
        ("Company", "company"),
        ("Email", "email"),
        ("Phone", "phone"),
        ("Address", "bill_address1"),
        ("City", "bill_city"),
        ("State", "bill_state"),
        ("ZIP", "bill_zip"),
    )
    return _contact_import(
        db, csv_text, Customer, CustomerCreate, fields, default_terms
    )


def import_vendors(db: Session, csv_text: str) -> dict:
    fields = (
        ("Company", "company"),
        ("Email", "email"),
        ("Phone", "phone"),
        ("Address", "address1"),
        ("City", "city"),
        ("State", "state"),
        ("ZIP", "zip"),
    )
    # The vendor form's default: Settings' Default Terms are invoice terms.
    return _contact_import(db, csv_text, Vendor, VendorCreate, fields, "Net 30")


def _amount(row: dict, key: str) -> Decimal | None:
    """The cell as an amount (blank is 0), or None when it is not one."""
    raw = (row.get(key) or "").strip().replace(",", "").lstrip("$")
    if not raw:
        return Decimal("0")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def import_items(db: Session, csv_text: str) -> dict:
    from app.routes.items import item_code_key, item_name_key

    # Names already taken by an active item — the same name in other
    # capitals included, as the item form refuses it (W-M16) — plus the
    # rows this file adds, so a name twice in the file imports once.
    taken = {item_name_key(n) for (n,) in db.query(Item.name).filter(Item.is_active)}
    # Codes already used, by any item (the unique index covers inactive ones
    # too), compared as the item form compares them (#445).
    codes = {
        item_code_key(c): n
        for (c, n) in db.query(Item.code, Item.name).filter(Item.code.isnot(None))
    }
    reader = UnguardedDictReader(io.StringIO(csv_text))
    created = 0
    skipped = 0
    errors = []

    type_map = {
        "product": ItemType.PRODUCT,
        "service": ItemType.SERVICE,
        "material": ItemType.MATERIAL,
        "labor": ItemType.LABOR,
    }
    # An optional Negative Price column (#639), as the export writes it
    # (True/False) or as a spreadsheet says yes: the item is a discount or
    # concession, and a line on it may carry a negative price.
    yes = {"true", "yes", "y", "1", "x"}

    for i, row in enumerate(reader, start=2):
        try:
            name = (row.get("Name") or "").strip()[:200]
            if not name:
                errors.append(f"Row {i}: Missing name")
                continue

            existing = db.query(Item).filter(Item.name == name).first()
            if existing or item_name_key(name) in taken:
                skipped += 1
                continue

            rate, cost = _amount(row, "Rate"), _amount(row, "Cost")
            bad = next(
                (k for k, v in (("Rate", rate), ("Cost", cost)) if v is None), None
            )
            if bad:
                errors.append(f'Row {i}: {bad} "{row.get(bad)}" is not a number.')
                continue

            item_type = type_map.get(
                (row.get("Type") or "service").strip().lower(), ItemType.SERVICE
            )
            # An optional Code column (#445). A code another item already
            # has is left off, and the row says so: the item still comes in.
            code = (row.get("Code") or "").strip()[:50] or None
            if code and item_code_key(code) in codes:
                errors.append(
                    f"Row {i}: Item code {code} is already used by "
                    f"{codes[item_code_key(code)]}; {name} was imported "
                    "without a code."
                )
                code = None
            db.add(
                Item(
                    name=name,
                    code=code,
                    item_type=item_type,
                    description=row.get("Description", ""),
                    rate=rate,
                    cost=cost,
                    allows_negative_price=(row.get("Negative Price") or "")
                    .strip()
                    .lower()
                    in yes,
                )
            )
            taken.add(item_name_key(name))
            if code:
                codes[item_code_key(code)] = name
            created += 1
        except Exception:
            logger.exception("Failed to import item row %d", i)
            errors.append(f"Row {i}: import failed")

    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}
