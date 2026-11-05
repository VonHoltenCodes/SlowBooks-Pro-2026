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
import re
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


# The columns each importer reads, in the order the import page and the
# templates (app/static/downloads/*-template.csv) list them, each with the
# other headings a plain spreadsheet uses for it (#710). A heading is matched
# without regard to case, spaces, underscores or hyphens, so "Item Name",
# "item_name" and "ITEMNAME" all read as Name.
ITEM_COLUMNS: dict[str, tuple[str, ...]] = {
    "Name": ("Item Name", "Item"),
    "Code": ("Item Code", "SKU"),
    "Type": ("Item Type",),
    "Description": ("Sales Description",),
    "Rate": ("Price", "Sales Price", "Unit Price"),
    "Cost": ("Unit Cost", "Purchase Cost"),
    "Income Account": ("Income Account Number", "Account"),
    "Negative Price": (),
}
CUSTOMER_COLUMNS: dict[str, tuple[str, ...]] = {
    # Donor: a nonprofit's list, as its screens name customers
    "Name": ("Customer Name", "Customer", "Donor Name", "Donor", "Display Name"),
    "Company": ("Company Name",),
    "Email": ("E-mail", "Email Address"),
    "Phone": ("Phone Number", "Telephone"),
    "Address": (
        "Street",
        "Street Address",
        "Address 1",
        "Bill Address",
        "Billing Address",
    ),
    "City": (),
    "State": ("Province",),
    "ZIP": ("Zip Code", "Postal Code"),
    "Terms": (),
}
VENDOR_COLUMNS: dict[str, tuple[str, ...]] = {
    **CUSTOMER_COLUMNS,
    "Name": ("Vendor Name", "Vendor", "Payee", "Display Name"),
    "Address": ("Street", "Street Address", "Address 1"),
}


def _header_key(text: str | None) -> str:
    return re.sub(r"[\s_\-]+", "", (text or "").lower())


def _match_headers(fieldnames, columns: dict) -> dict[str, str]:
    """{our column: the file's own heading} for each column the file has.
    The column's own name wins over an alias when a file has both."""
    by_key: dict[str, str] = {}
    for heading in fieldnames or []:
        by_key.setdefault(_header_key(heading), heading)
    found: dict[str, str] = {}
    for column, aliases in columns.items():
        for name in (column, *aliases):
            heading = by_key.get(_header_key(name))
            if heading is not None and heading not in found.values():
                found[column] = heading
                break
    return found


def _no_name_column(fieldnames, columns: dict) -> str:
    """The one error a file with no name column gets, rather than "Missing
    name" on every row (#710)."""
    headed = ", ".join(f'"{h}"' for h in (fieldnames or []) if h) or "none"
    wanted = ", ".join(("Name", *columns["Name"]))
    return (
        f"No Name column: the file's headings are {headed}. Head the name "
        f"column one of {wanted}."
    )


def _read_rows(reader, headers: dict[str, str]):
    """Each row keyed by our column names, whatever the file headed them."""
    for row in reader:
        yield {column: row.get(heading) for column, heading in headers.items()}


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
    # Matched to the active list the same way the parser matches a document's
    # terms (#664/#665: NFKC, confusables and whitespace aside), so a
    # fullwidth "Ｎｅｔ ３０" reads as Net 30 just as the list and /dates do.
    from app.services.payment_terms import _key

    raw_key = _key(raw)
    for option in choices:
        if raw_key == _key(option):
            return option, None
    # text the terms parser reads ("Net 99", "COD") keeps its meaning and is
    # kept as typed; the forms offer a record's own terms beside the list
    # (#665), so only text nothing reads is refused
    from app.services.payment_terms import read_text

    read = read_text(raw)
    if read is not None and read.net_days < 0:
        # due before it is dated: every invoice on them would be refused
        return None, (
            f'Terms "{raw[:50]}" would make an invoice due before its date. '
            "Use a number of days of 0 or more."
        )
    if read is not None:
        return raw, None
    # the list named, but not without end: a long list ends "and N more"
    shown = ", ".join(choices[:TERMS_SHOWN])
    if len(choices) > TERMS_SHOWN:
        shown += f" and {len(choices) - TERMS_SHOWN} more"
    return None, (
        f"Terms \"{raw[:50]}\" isn't on the terms list ({shown}) and isn't "
        '"Net" and a number of days. Use one of those, add it under Settings > '
        "Payment Terms, or leave Terms blank for the default."
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


def _contact_import(
    db: Session, csv_text: str, model, schema, fields, default_terms, columns
):
    reader = UnguardedDictReader(io.StringIO(csv_text))
    headers = _match_headers(reader.fieldnames, columns)
    if "Name" not in headers:
        return {
            "created": 0,
            "skipped": 0,
            "errors": [_no_name_column(reader.fieldnames, columns)],
        }
    choices = form_terms(db)
    created = 0
    skipped = 0
    errors = []

    for i, row in enumerate(_read_rows(reader, headers), start=2):
        try:
            name = (row.get("Name") or "").strip()[:200]
            if not name:
                errors.append(
                    f'Row {i}: Missing name: the "{headers["Name"]}" column is blank.'
                )
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
        db, csv_text, Customer, CustomerCreate, fields, default_terms, CUSTOMER_COLUMNS
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
    return _contact_import(
        db, csv_text, Vendor, VendorCreate, fields, "Net 30", VENDOR_COLUMNS
    )


def _amount(row: dict, key: str) -> Decimal | None:
    """The cell as an amount (blank is 0), or None when it is not one."""
    raw = (row.get(key) or "").strip().replace(",", "").lstrip("$")
    # "varies": a price list's word for a price set on each sale, as an
    # item at 0 is (#710's sample list); any other word is refused.
    if not raw or raw.lower() in ("varies", "variable"):
        return Decimal("0")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


_ACCOUNT_KINDS = {
    "asset": "an asset",
    "liability": "a liability",
    "equity": "an equity",
    "expense": "an expense",
    "cogs": "a cost of goods sold",
}


class _IncomeAccounts:
    """An item row's Income Account cell (#710) -> an active income account,
    by number, by name, or by its QuickBooks path ("Landscaping
    Services:Lawn Maintenance", the parent's name before the colon)."""

    def __init__(self, db: Session):
        from app.models.accounts import Account

        accounts = db.query(Account).filter(Account.is_active.is_(True)).all()
        by_id = {a.id: a for a in accounts}

        def path(a, depth=0):
            parent = by_id.get(a.parent_id)
            if parent is None or depth > 10:
                return a.name
            return f"{path(parent, depth + 1)}:{a.name}"

        self.by_number: dict[str, object] = {}
        self.by_name: dict[str, list] = {}
        for a in accounts:
            if a.account_number:
                self.by_number[a.account_number.strip().lower()] = a
            for key in {a.name, path(a)}:
                self.by_name.setdefault(self._key(key), []).append(a)

    @staticmethod
    def _key(text: str) -> str:
        return ":".join(
            " ".join(part.split()).lower() for part in (text or "").split(":")
        )

    def find(self, raw: str, heading: str = "Income Account"):
        """(account, None) or (None, why not), the column named as the
        file heads it."""
        from app.models.accounts import AccountType

        key = self._key(raw)
        hits = [self.by_number[key]] if key in self.by_number else []
        if not hits:
            hits = self.by_name.get(key, [])
        if not hits and ":" in key:
            # the chart may hold the sub-account under its own name only
            hits = self.by_name.get(key.rsplit(":", 1)[1], [])
        income = list(
            {a.id: a for a in hits if a.account_type == AccountType.INCOME}.values()
        )
        if len(income) == 1:
            return income[0], None
        if len(income) > 1:
            return None, (
                f'{heading} "{raw[:80]}" names more than one income '
                "account; use its account number."
            )
        if hits:
            kind = _ACCOUNT_KINDS.get(hits[0].account_type.value, "non-income")
            return None, (
                f'{heading} "{raw[:80]}" is {hits[0].name}, {kind} account; '
                "an item's income account must be an income account."
            )
        return None, (
            f'{heading} "{raw[:80]}" is not in the chart of accounts. Use an '
            "income account's number or name, add the account first, or leave "
            "the column blank for the default income account."
        )


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
    headers = _match_headers(reader.fieldnames, ITEM_COLUMNS)
    if "Name" not in headers:
        return {
            "created": 0,
            "skipped": 0,
            "errors": [_no_name_column(reader.fieldnames, ITEM_COLUMNS)],
        }
    income_accounts = _IncomeAccounts(db) if "Income Account" in headers else None
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

    for i, row in enumerate(_read_rows(reader, headers), start=2):
        try:
            name = (row.get("Name") or "").strip()[:200]
            if not name:
                errors.append(
                    f'Row {i}: Missing name: the "{headers["Name"]}" column is blank.'
                )
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
                # named as the file heads it ("Price", "Unit Cost")
                errors.append(
                    f'Row {i}: {headers[bad]} "{row.get(bad)}" is not a number.'
                )
                continue

            # An optional income account (#710): the item posts its sales
            # there, not to the default income account.
            income_account_id = None
            raw_account = _cell(row, "Income Account")
            if raw_account and income_accounts is not None:
                account, problem = income_accounts.find(
                    raw_account, headers["Income Account"]
                )
                if problem:
                    errors.append(f"Row {i}: {problem} {name} was not imported.")
                    continue
                income_account_id = account.id

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
                    description=row.get("Description") or "",
                    income_account_id=income_account_id,
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
