# ============================================================================
# CSV invoice import (#882) — invoices from any program, in SlowBooks' own
# column layout. SlowBooks publishes the layout (the template below); the
# person moves their export's columns to match it. No per-program importer.
#
# One row is one invoice line; rows that share an invoice_number are one
# invoice, their lines in row order. Every invoice is made by the same
# function the New Invoice form saves through (routes/invoices/crud.py,
# post_invoice), so the journal (A/R, income, sales tax), the aging and the
# customer balance agree with a typed invoice. The number the invoice had
# in the other program is kept, and one already in the books is refused by
# number, never given another.
#
# A dry run makes every invoice exactly as the import would, then rolls the
# lot back: the totals it reports are the ones that would post. Nothing
# posts while any row is refused, an unknown customer included; the person
# fixes those rows (or adds the customers) and imports the file again.
# ============================================================================

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.csv_import import (
    UnguardedDictReader,
    _cell,
    _header_key,
    _match_headers,
    _read_rows,
)

logger = logging.getLogger(__name__)

# The columns, in the template's order: (heading, required).
INVOICE_COLUMNS: tuple[tuple[str, bool], ...] = (
    ("invoice_number", True),
    ("date", True),
    ("due_date", False),
    ("customer", True),
    ("item", False),
    ("description", False),
    ("quantity", True),
    ("rate", True),
    ("taxable", False),
    ("memo", False),
    ("class", False),
    ("job", False),
)
COLUMN_NAMES = tuple(name for name, _ in INVOICE_COLUMNS)
REQUIRED = tuple(name for name, required in INVOICE_COLUMNS if required)

# The template's example: two invoices, the first of two lines, on the
# customers and items of the other templates (app/static/downloads).
TEMPLATE_ROWS = (
    (
        "R-1001",
        "2026-11-02",
        "",
        "Ines Halvorsen",
        "DES-HR",
        "Design work by the hour",
        "2",
        "85.00",
        "",
        "Lobby sign",
        "",
        "",
    ),
    (
        "R-1001",
        "2026-11-02",
        "",
        "Ines Halvorsen",
        "",
        "Trip charge",
        "1",
        "25.00",
        "no",
        "",
        "",
        "",
    ),
    (
        "R-1002",
        "2026-11-03",
        "2026-11-18",
        "Orchard Lane Apartments",
        "Installation Labor",
        "Installation labor per hour",
        "3",
        "65.00",
        "yes",
        "",
        "",
        "",
    ),
)

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
_YES = {"yes", "y", "true", "1", "x"}
_NO = {"no", "n", "false", "0"}
# How many unknown customers the refusal summary names.
NAMES_SHOWN = 20


def invoice_template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMN_NAMES)
    writer.writerows(TEMPLATE_ROWS)
    return out.getvalue()


def _key(text) -> str:
    """A name as the lookups compare it: runs of spaces as one, capitals
    ignored."""
    return " ".join(str(text or "").split()).casefold()


def _date(raw: str | None) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _number(raw: str | None) -> Decimal | None:
    text = (raw or "").strip().replace(",", "").replace("$", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


class _Lookups:
    """The books' customers, items, classes and jobs, by the names a file
    gives them."""

    def __init__(self, db: Session):
        from app.models.classes import TxnClass
        from app.models.contacts import Customer
        from app.models.items import Item
        from app.models.jobs import Job
        from app.routes.items import item_code_key, item_name_key

        self.customers: dict[str, list] = {}
        for c in db.query(Customer).filter(Customer.is_active.is_(True)):
            # its own name, and "Parent: Sub" / "Parent:Sub" for a sub-customer
            for name in {c.name, c.full_name, c.qb_name}:
                hits = self.customers.setdefault(_key(name), [])
                if c not in hits:
                    hits.append(c)
        items = db.query(Item).filter(Item.is_active.is_(True)).all()
        self.item_codes = {item_code_key(i.code): i for i in items if i.code}
        self.item_names = {item_name_key(i.name): i for i in items}
        self._code_key, self._name_key = item_code_key, item_name_key
        self.classes = {
            _key(c.name): c
            for c in db.query(TxnClass).filter(TxnClass.is_archived.is_(False))
        }
        self.jobs: dict[tuple[int, str], list] = {}
        for j in db.query(Job).filter(Job.is_active.is_(True)):
            self.jobs.setdefault((j.customer_id, _key(j.name)), []).append(j)

    def item(self, raw: str):
        """By code first, then by name."""
        return self.item_codes.get(self._code_key(raw)) or self.item_names.get(
            self._name_key(raw)
        )


def _taxable(raw: str | None) -> tuple[bool | None, bool]:
    """(the line's tax flag or None for the rule, whether the cell reads)."""
    if raw is None:
        return None, True
    word = raw.strip().lower()
    if word in _YES:
        return True, True
    if word in _NO:
        return False, True
    return None, False


def _default_tax_rate(db: Session) -> Decimal:
    """The company's default tax rate as a fraction, as the New Invoice
    form starts with it (a percent in Settings: 8.25 is 8.25%)."""
    from app.services.settings_service import get_all_settings

    pct = get_all_settings(db).get("default_tax_rate") or "0"
    try:
        rate = Decimal(str(pct).strip() or "0") / Decimal("100")
    except ArithmeticError:
        return Decimal("0")
    return rate if 0 <= rate < 1 else Decimal("0")


def _default_terms(db: Session) -> str:
    from app.services.settings_service import get_all_settings

    return (get_all_settings(db).get("default_terms") or "").strip() or "Net 30"


def _read_line(row: dict, n: int, look: _Lookups, problems: list[str]):
    """The row's line as {item, description, quantity, rate, taxable,
    class, job_name}, or None with its problems added."""
    before = len(problems)
    raw_item = _cell(row, "item")
    item = look.item(raw_item) if raw_item else None
    if raw_item and item is None:
        problems.append(
            f'Row {n}: item "{raw_item[:80]}" is not on the Items list (by code '
            "or name). Add it, fix the cell, or leave item blank for the default "
            "income account."
        )
    description = _cell(row, "description")
    if description is None and item is not None:
        description = item.description or item.name
    if raw_item is None and description is None:
        problems.append(f"Row {n}: the line has no item and no description.")

    raw_qty = _cell(row, "quantity")
    quantity = Decimal("1") if raw_qty is None else _number(raw_qty)
    if quantity is None:
        problems.append(f'Row {n}: quantity "{raw_qty[:40]}" is not a number.')
    elif quantity < 0:
        problems.append(
            f"Row {n}: quantity {raw_qty} is negative. A return or a refund "
            "belongs on a credit memo."
        )

    raw_rate = _cell(row, "rate")
    if raw_rate is None:
        rate = Decimal(str(item.rate or 0)) if item is not None else None
        if item is None:
            problems.append(
                f"Row {n}: rate is blank, and there is no item to take it from."
            )
    else:
        rate = _number(raw_rate)
        if rate is None:
            problems.append(f'Row {n}: rate "{raw_rate[:40]}" is not a number.')

    taxable, reads = _taxable(_cell(row, "taxable"))
    if not reads:
        problems.append(
            f'Row {n}: taxable "{_cell(row, "taxable")[:20]}" is not yes or no. '
            "Leave it blank for the item's or the customer's rule."
        )

    raw_class = _cell(row, "class")
    txn_class = look.classes.get(_key(raw_class)) if raw_class else None
    if raw_class and txn_class is None:
        problems.append(
            f'Row {n}: class "{raw_class[:80]}" is not on the class list. Add it, '
            "or leave class blank."
        )
    if len(problems) > before:
        return None
    return {
        "row": n,
        "item": item,
        "description": description,
        "quantity": quantity,
        "rate": rate,
        "taxable": taxable,
        "class": txn_class,
        "job_name": _cell(row, "job"),
    }


def _row_of(problem: str) -> int:
    """A refusal's row number, so the list reads top to bottom."""
    head = problem.split(":", 1)[0]
    digits = head.removeprefix("Row ").strip()
    return int(digits) if digits.isdigit() else 0


def _refusal_text(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("detail") or ""
    return str(detail or "it was refused").strip()


def import_invoices(db: Session, csv_text: str, dry_run: bool = True) -> dict:
    """Read `csv_text` in the invoice template's layout and make its
    invoices. Returns the report the page shows: invoices, lines, customers
    and items matched, the amounts, every refusal with its row, and whether
    anything posted (never on a dry run, never with a refusal)."""
    from app.models.invoices import Invoice
    from app.routes.invoices.crud import post_invoice
    from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate
    from app.services.replace_transaction import _one_commit

    report = {
        "dry_run": dry_run,
        "posted": False,
        "invoices": 0,
        "lines": 0,
        "customers_matched": 0,
        "customers_unknown": [],
        "items_matched": 0,
        "subtotal": "0.00",
        "tax": "0.00",
        "total": "0.00",
        "documents": [],
        "errors": [],
    }
    reader = UnguardedDictReader(io.StringIO(csv_text))
    headers = _match_headers(reader.fieldnames, {c: () for c in COLUMN_NAMES})
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        headed = ", ".join(f'"{h}"' for h in (reader.fieldnames or []) if h)
        report["errors"].append(
            f"The file has no {', '.join(missing)} column. Its headings are "
            f"{headed or 'none'}; the template's are {', '.join(COLUMN_NAMES)}."
        )
        return report
    # every heading the file has that the import does not read, said once
    ignored = [
        h
        for h in (reader.fieldnames or [])
        if h and _header_key(h) not in {_header_key(c) for c in COLUMN_NAMES}
    ]

    from app.services.terminology import terms_from_db

    look = _Lookups(db)
    customers_list = terms_from_db(db)("Customers")
    in_books = {n for (n,) in db.query(Invoice.invoice_number)}
    problems: list[str] = []
    unknown: dict[str, None] = {}
    invoices: dict[str, dict] = {}

    for n, row in enumerate(_read_rows(reader, headers), start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # a blank line between invoices
        number = _cell(row, "invoice_number")
        if number is None:
            problems.append(f"Row {n}: invoice_number is blank.")
            continue
        if len(number) > 50:
            problems.append(f"Row {n}: invoice_number is longer than 50 characters.")
            continue
        raw_date = _cell(row, "date")
        when = _date(raw_date)
        if when is None:
            problems.append(
                f'Row {n}: date "{(raw_date or "")[:40]}" is not a date. Write it '
                "2026-11-02 or 11/02/2026."
            )
        raw_due = _cell(row, "due_date")
        due = _date(raw_due) if raw_due else None
        if raw_due and due is None:
            problems.append(
                f'Row {n}: due_date "{raw_due[:40]}" is not a date. Write it '
                "2026-11-30 or 11/30/2026, or leave it blank for the customer's "
                "terms."
            )
        raw_customer = _cell(row, "customer")
        customer = None
        if raw_customer is None:
            problems.append(f"Row {n}: customer is blank.")
        else:
            hits = look.customers.get(_key(raw_customer), [])
            if len(hits) == 1:
                customer = hits[0]
            elif hits:
                problems.append(
                    f'Row {n}: customer "{raw_customer[:80]}" names more than one '
                    'customer; write a sub-customer as "Parent: Sub".'
                )
            else:
                unknown.setdefault(raw_customer, None)
                problems.append(
                    f'Row {n}: customer "{raw_customer[:80]}" is not on the '
                    f"{customers_list} list. Add it, or fix the name; nothing "
                    "posts until every customer is found."
                )
        line = _read_line(row, n, look, problems)

        inv = invoices.get(number)
        if inv is None:
            inv = invoices[number] = {
                "number": number,
                "row": n,
                "date": when,
                "due_date": due,
                "customer": customer,
                "customer_text": raw_customer,
                "memo": _cell(row, "memo"),
                "lines": [],
                "refused": False,
            }
            if number in in_books:
                problems.append(
                    f"Row {n}: invoice {number} is already in the books. It is "
                    "not imported again; take its rows out of the file."
                )
                inv["refused"] = True
        else:
            # the invoice's own fields come from its first row; a later row
            # that says otherwise is a different invoice under one number
            for field, value, text in (
                ("date", when, raw_date),
                ("due_date", due, raw_due),
                ("customer_text", raw_customer, raw_customer),
            ):
                if text is None:
                    continue
                first = inv[field]
                same = (
                    _key(first) == _key(value)
                    if field == "customer_text"
                    else first == value
                )
                if not same:
                    problems.append(
                        f"Row {n}: {field.replace('_text', '')} differs from row "
                        f"{inv['row']}, the first row of invoice {number}."
                    )
                    inv["refused"] = True
            memo = _cell(row, "memo")
            if memo and inv["memo"] and memo != inv["memo"]:
                problems.append(
                    f"Row {n}: memo differs from row {inv['row']}, the first row "
                    f"of invoice {number}. An invoice has one memo."
                )
                inv["refused"] = True
            elif memo and not inv["memo"]:
                inv["memo"] = memo
        if when is None or customer is None or line is None:
            inv["refused"] = True
            continue
        if raw_due and due is None:
            inv["refused"] = True
            continue
        if line["job_name"]:
            jobs = look.jobs.get((customer.id, _key(line["job_name"])), [])
            if len(jobs) != 1:
                problems.append(
                    f'Row {n}: job "{line["job_name"][:80]}" is not one of '
                    f"{customer.full_name}'s jobs."
                    if not jobs
                    else f'Row {n}: {customer.full_name} has more than one job named "{line["job_name"][:80]}".'
                )
                inv["refused"] = True
                continue
            line["job"] = jobs[0]
        else:
            line["job"] = None
        inv["lines"].append(line)

    report["invoices"] = len(invoices)
    report["lines"] = sum(len(i["lines"]) for i in invoices.values())
    report["customers_matched"] = len(
        {i["customer"].id for i in invoices.values() if i["customer"] is not None}
    )
    report["items_matched"] = len(
        {
            ln["item"].id
            for i in invoices.values()
            for ln in i["lines"]
            if ln["item"] is not None
        }
    )
    report["customers_unknown"] = list(unknown)[:NAMES_SHOWN]
    if ignored:
        report["ignored_columns"] = ignored

    # Make each invoice that read cleanly, exactly as the form would, in
    # one transaction held back (each invoice in a savepoint, so one the
    # invoice path refuses is reported and the rest still add up). Kept
    # only when it is not a dry run and nothing was refused.
    #
    # On SQLite the transaction must be begun by hand first (W-148): the
    # pysqlite driver emits no BEGIN before a SAVEPOINT, so with nothing
    # written yet the savepoint opened a transaction of its own and its
    # RELEASE committed the invoice, leaving the final rollback nothing to
    # undo. begin_write() issues BEGIN IMMEDIATE on this session's
    # connection when none is open (a no-op on PostgreSQL, and when one
    # is), so every savepoint nests inside it. It is asked again before
    # each invoice: a refusal that rolls the whole session back ends the
    # transaction, and the next savepoint must not open one of its own.
    from app.database import begin_write

    tax_rate = _default_tax_rate(db)
    default_terms = _default_terms(db)
    subtotal = tax = total = Decimal("0")
    with _one_commit(db):
        for inv in invoices.values():
            if inv["refused"] or not inv["lines"]:
                continue
            lines = inv["lines"]
            customer = inv["customer"]
            jobs = {ln["job"].id if ln["job"] else None for ln in lines}
            classes = {ln["class"].id if ln["class"] else None for ln in lines}
            data = InvoiceCreate(
                customer_id=customer.id,
                date=inv["date"],
                due_date=inv["due_date"],
                terms=customer.terms or default_terms,
                tax_rate=tax_rate,
                notes=inv["memo"],
                # the header carries a job or a class every line shares
                job_id=next(iter(jobs)) if len(jobs) == 1 else None,
                class_id=next(iter(classes)) if len(classes) == 1 else None,
                # invoices already issued in the other program: their
                # amounts are what they are
                allow_zero_total=True,
                allow_over_credit_limit=True,
                lines=[
                    InvoiceLineCreate(
                        item_id=ln["item"].id if ln["item"] else None,
                        description=ln["description"],
                        quantity=ln["quantity"],
                        rate=ln["rate"],
                        is_taxable=ln["taxable"],
                        class_id=ln["class"].id if ln["class"] else None,
                        job_id=ln["job"].id if ln["job"] else None,
                        line_order=k,
                    )
                    for k, ln in enumerate(lines)
                ],
            )
            begin_write(db)
            savepoint = db.begin_nested()
            try:
                resp = post_invoice(data, db, number=inv["number"])
                savepoint.commit()
            except HTTPException as exc:
                savepoint.rollback()
                problems.append(
                    f"Row {inv['row']}: invoice {inv['number']} was refused: "
                    f"{_refusal_text(exc)}"
                )
                continue
            except Exception:
                savepoint.rollback()
                logger.exception("CSV invoice import: invoice %s failed", inv["number"])
                problems.append(
                    f"Row {inv['row']}: invoice {inv['number']} could not be "
                    "made; the server log has the reason."
                )
                continue
            subtotal += Decimal(str(resp.subtotal))
            tax += Decimal(str(resp.tax_amount))
            total += Decimal(str(resp.total))
            report["documents"].append(
                {
                    "row": inv["row"],
                    "invoice_number": inv["number"],
                    "customer": customer.full_name,
                    "date": inv["date"].isoformat(),
                    "due_date": resp.due_date.isoformat() if resp.due_date else None,
                    "lines": len(lines),
                    "subtotal": f"{Decimal(str(resp.subtotal)):.2f}",
                    "tax": f"{Decimal(str(resp.tax_amount)):.2f}",
                    "total": f"{Decimal(str(resp.total)):.2f}",
                }
            )
    report["subtotal"] = f"{subtotal:.2f}"
    report["tax"] = f"{tax:.2f}"
    report["total"] = f"{total:.2f}"
    report["errors"] = sorted(problems, key=_row_of)
    if dry_run or problems:
        db.rollback()
    else:
        db.commit()
        report["posted"] = True
    return report
