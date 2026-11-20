"""Activity on every document (#894): what happened to it, who did it, when.

A document's view shows its history in sentences, newest first: made, each
edit field by field, voided, emailed, files attached or removed, payments
and credits applied, a deposit taking a payment, status changes. Nothing
here writes; it reads what the app already keeps:

- the audit log (app.services.audit writes a row for every INSERT, UPDATE
  and DELETE on every table, with the actor): the document's own rows, its
  lines' rows, its attachments' rows and the rows of the records that touch
  it (a payment's application to an invoice, a credit applied to a bill);
- the email log, for each send and whether it went (its sender since #894);
- the document audits, for a document rendered with a recorded hash.

Lines are tied to their document by the line's foreign key as each audit row
carries it. SQLite reuses a deleted row's id, so a line's rows are walked in
order and each is kept only while the line belonged to this document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from itertools import count

from sqlalchemy import Text, cast, or_, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.audit import AuditLog
from app.models.document_audit import DocumentAudit
from app.models.email_log import EmailLog
from app.services.audit_redaction import REDACTED
from app.services.request_context import IMPORT_SOURCE_PREFIX
from app.services.terminology import terms_from_db


class ActivityNotFound(Exception):
    """An unknown record type, or no such record: the route answers 404."""


@dataclass(frozen=True)
class Related:
    """Rows of another table that point at this document: (table, the
    column holding this document's id, what the row is called)."""

    table: str
    fk: str
    kind: str


@dataclass(frozen=True)
class DocType:
    word: str  # the record's name in a sentence, a vocabulary key when it is one
    table: str
    lines: tuple[tuple[str, str], ...] = ()  # (line table, its fk to the document)
    related: tuple[Related, ...] = ()
    attachment_types: tuple[str, ...] = ()
    email_types: tuple[str, ...] = ()
    source_types: tuple[str, ...] | None = None  # transactions: the kinds it may be
    number_fields: tuple[str, ...] = ()
    extra: tuple[str, ...] = field(default=())
    # its form keeps each line's id (a purchase order sends them; a pay
    # application's lines are its schedule's), so a line with no id is new
    # and a line whose id is gone was removed, even when no old id is left:
    # pass 0 of _compare_lines
    line_ids: bool = False


_TXN_LINES = (("transaction_lines", "transaction_id"),)
_TXN_NUMBER = ("reference", "source_ref")

DOC_TYPES: dict[str, DocType] = {
    "invoice": DocType(
        "Invoice",
        "invoices",
        (("invoice_lines", "invoice_id"),),
        (
            Related("payment_allocations", "invoice_id", "payment_applied"),
            Related("credit_applications", "invoice_id", "credit_applied"),
            # a purchase order linked to it or unlinked (#986)
            Related("purchase_orders", "invoice_id", "po_linked"),
        ),
        ("invoice",),
        ("invoice", "reminder"),
        number_fields=("invoice_number",),
    ),
    "estimate": DocType(
        "Estimate",
        "estimates",
        (("estimate_lines", "estimate_id"),),
        attachment_types=("estimate",),
        email_types=("estimate",),
        number_fields=("estimate_number",),
    ),
    "credit_memo": DocType(
        "Credit Memo",
        "credit_memos",
        (("credit_memo_lines", "credit_memo_id"),),
        (Related("credit_applications", "credit_memo_id", "credit_used"),),
        number_fields=("memo_number",),
    ),
    "bill": DocType(
        "Bill",
        "bills",
        (("bill_lines", "bill_id"),),
        (
            Related("bill_payment_allocations", "bill_id", "bill_paid"),
            Related("vendor_credit_applications", "bill_id", "vendor_credit_applied"),
        ),
        ("bill",),
        number_fields=("bill_number",),
    ),
    "vendor_credit": DocType(
        "Vendor Credit",
        "vendor_credits",
        (("vendor_credit_lines", "vendor_credit_id"),),
        (
            Related(
                "vendor_credit_applications", "vendor_credit_id", "vendor_credit_used"
            ),
        ),
        ("vendor_credit",),
        number_fields=("credit_number",),
    ),
    "bill_payment": DocType(
        "Bill Payment",
        "bill_payments",
        (),
        (Related("bill_payment_allocations", "bill_payment_id", "bill_payment_paid"),),
        number_fields=("check_number",),
    ),
    "payment": DocType(
        "Payment",
        "payments",
        (),
        (Related("payment_allocations", "payment_id", "payment_used"),),
        email_types=("acknowledgment_payment",),
        number_fields=("check_number", "reference"),
        extra=("deposited",),
    ),
    "purchase_order": DocType(
        "Purchase Order",
        "purchase_orders",
        (("purchase_order_lines", "purchase_order_id"),),
        attachment_types=("purchase_order",),
        number_fields=("po_number",),
        line_ids=True,
    ),
    "pay_application": DocType(
        "Pay Application",
        "pay_applications",
        (("pay_application_lines", "pay_application_id"),),
        number_fields=("application_no", "number"),
        line_ids=True,
    ),
    "change_order": DocType(
        "Change Order",
        "change_orders",
        (("change_order_lines", "change_order_id"),),
        number_fields=("number",),
    ),
    "expense": DocType(
        "Expense",
        "transactions",
        _TXN_LINES,
        attachment_types=("expense",),
        source_types=("expense",),
        number_fields=_TXN_NUMBER,
    ),
    "cc_charge": DocType(
        "Card Charge",
        "transactions",
        _TXN_LINES,
        attachment_types=("cc_charge",),
        source_types=("cc_charge",),
        number_fields=_TXN_NUMBER,
    ),
    "check": DocType(
        "Check",
        "transactions",
        _TXN_LINES,
        attachment_types=("bank_entry", "expense"),
        source_types=("bank_entry", "expense"),
        number_fields=_TXN_NUMBER,
    ),
    "bank_entry": DocType(
        "Register Entry",
        "transactions",
        _TXN_LINES,
        attachment_types=("bank_entry",),
        source_types=("bank_entry",),
        number_fields=_TXN_NUMBER,
    ),
    "deposit": DocType(
        "Deposit",
        "transactions",
        _TXN_LINES + (("deposit_other_lines", "deposit_transaction_id"),),
        attachment_types=("bank_entry",),
        source_types=("deposit",),
        number_fields=_TXN_NUMBER,
        extra=("took_payments",),
    ),
    "journal_entry": DocType(
        "Journal Entry",
        "transactions",
        _TXN_LINES,
        attachment_types=("journal_entry",),
        number_fields=_TXN_NUMBER,
    ),
    # a job cost entry (#897): its lines, its Reverses on date, and the
    # reversal of an accrual the start-up pass posts
    "job_cost": DocType(
        "Job Cost Entry",
        "job_costs",
        (("job_cost_lines", "job_cost_id"),),
        number_fields=("number",),
    ),
    "job": DocType(
        "Job",
        "jobs",
        number_fields=("job_number", "name"),
    ),
    "customer": DocType(
        "Customer",
        "customers",
        attachment_types=("customer",),
        email_types=("statement", "collection", "giving_statement"),
        number_fields=("name",),
    ),
    "vendor": DocType(
        "Vendor",
        "vendors",
        attachment_types=("vendor",),
        number_fields=("name",),
    ),
}
# A sales receipt is an invoice row; its page may call it by its own name.
DOC_TYPES["sales_receipt"] = DOC_TYPES["invoice"]

# Fields that move on their own as other things happen (a payment moves the
# balance; the app stamps the time): never an "edit" a person made.
_QUIET_FIELDS = {
    "id",
    "updated_at",
    "created_at",
    "balance",
    "balance_due",
    "amount_paid",
    "amount_applied",
    "balance_remaining",
    "line_order",
    "position",
    "transaction_id",
    "payment_token",
    "stripe_checkout_session_id",
    "checkout_external_id",
    "posted_by",
    "replaced_by_id",
    "replaces_id",
    "billed_amount",
    "received_qty",
    "last_emailed_at",
    "last_emailed_to",
    "reversal_transaction_id",
}
# Shown as "<field> changed", never with the values: tax ids and bank numbers
# a read-only sign-in may not see in their history
_PRIVATE_FIELDS = {"tax_id", "ssn", "ein", "account_number", "routing_number"}
# Text long enough to say only that it changed
_PROSE_FIELDS = {"notes", "memo", "description", "scope_notes", "terms_text"}
_MONEY_FIELDS = {
    "amount",
    "total",
    "subtotal",
    "tax_amount",
    "debit",
    "credit",
    "rate",
    "unit_cost",
    "credit_limit",
    "contract_amount",
    "original_contract_amount",
    "scheduled_value",
    "this_period",
    "stored",
    "sell_amount",
    "retainage_held",
    "retainage_released",
    "deposit_applied",
    "fee_amount",
    "cap_amount",
    "contingency_amount",
    "fair_value_amount",
}
# Field names in words; a vocabulary key where the company may say it its way
_FIELD_WORDS = {
    "date": "Date",
    "due_date": "Due date",
    "expiration_date": "Expiration date",
    "expected_date": "Expected date",
    "po_number": "P.O. number",
    "ref_number": "Ref. number",
    "check_number": "Check number",
    "reference": "Reference",
    "terms": "Terms",
    "payment_term_id": "Terms",
    "tax_rate": "Tax rate",
    "tax_amount": "Tax",
    "subtotal": "Subtotal",
    "total": "Total",
    "amount": "Amount",
    "quantity": "Quantity",
    "rate": "Rate",
    "debit": "Debit",
    "credit": "Credit",
    "status": "Status",
    "notes": "Notes",
    "memo": "Memo",
    "description": "Description",
    "account_id": "Account",
    "item_id": "Item",
    "vendor_id": "Vendor",
    "customer_id": "Customer",
    "job_id": "Job",
    "class_id": "Class",
    "cost_code_id": "Cost code",
    "is_taxable": "Taxable",
    "tax_exempt": "Tax exempt",
    "method": "Method",
    "pay_from_account_id": "Paid from",
    "deposit_to_account_id": "Deposit to",
    "is_voided": "Void",
    "is_active": "Active",
    "reverses_on": "Reverses on",
    "debit_account_id": "Cost account",
    "credit_account_id": "Offset account",
}
# The vocabulary keys among those words (terms.text swaps them in prose too)
_VOCAB_FIELDS = {
    "customer_id": "Customer",
    "job_id": "Job",
    "class_id": "Class",
    # the invoice a purchase order fills (#986)
    "invoice_id": "Invoice",
}
# An id-field's table, for its value by name
_NAMED_TABLES = {
    "account_id": ("accounts", ("name",)),
    "pay_from_account_id": ("accounts", ("name",)),
    "deposit_to_account_id": ("accounts", ("name",)),
    "default_expense_account_id": ("accounts", ("name",)),
    "customer_id": ("customers", ("name",)),
    "vendor_id": ("vendors", ("name",)),
    "job_id": ("jobs", ("name",)),
    "class_id": ("classes", ("name",)),
    "item_id": ("items", ("name",)),
    "cost_code_id": ("cost_codes", ("code", "name")),
    "payment_term_id": ("payment_terms", ("name",)),
    "invoice_id": ("invoices", ("invoice_number",)),
    "debit_account_id": ("accounts", ("account_number", "name")),
    "credit_account_id": ("accounts", ("account_number", "name")),
}
_LINE_LABEL_FIELDS = ("description", "memo", "item_no")
_VOID_STATUSES = {"void", "voided"}
# What a void itself writes beside the status (#1128 and the construction
# documents): who and when are the event's own, the reason its sentence's
# (W-203: they read as a second "Edited by" entry with raw fields)
_VOID_FIELDS = {"voided_at", "voided_by", "void_reason"}
# What an approve, convert or promote writes beside the status: the action's
# own, said in its sentence ("Approved by … on <date>"), never a second
# "Edited by" entry of raw fields (W-210)
_ACTION_FIELDS = {
    "approved": {
        "approved_on",
        "approved_by",
        "contract_sum_original",
        "contract_sum_to_date",
        "invoice_id",
    },
    "converted": {"converted_invoice_id"},
    "promoted": {"promoted_to_id"},
}


# ---------------------------------------------------------------------------
# small words
# ---------------------------------------------------------------------------


def money_text(value) -> str:
    """$1,234.56 / -$1,234.56, the way the app's sentences show money."""
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError):
        return str(value)
    return f"{'-' if amount < 0 else ''}${abs(amount):,.2f}"


def _utc(stamp) -> datetime | None:
    if stamp is None:
        return None
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp)
        except ValueError:
            return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def local_words(stamp: datetime | None, tz_offset: int | None) -> str:
    """The time as the company's clock reads it: 'YYYY-MM-DD HH:MM'. The page
    sends its clock's offset (JavaScript's getTimezoneOffset, minutes west of
    UTC), as the Closing Date Exceptions report does; without one, the
    machine's own zone, which is the company's on a desktop install."""
    if stamp is None:
        return ""
    if tz_offset is None:
        return stamp.astimezone().strftime("%Y-%m-%d %H:%M")
    shifted = stamp.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=int(tz_offset)
    )
    return shifted.strftime("%Y-%m-%d %H:%M")


class _Words:
    """The sentence pieces for one read: the company's vocabulary, names of
    the records an id points at, and each actor by the name people know."""

    def __init__(self, db: Session):
        self.db = db
        self.terms = terms_from_db(db)
        self._names: dict[tuple[str, int], str] = {}
        self._people: dict[str, str] | None = None

    def t(self, key: str) -> str:
        return self.terms(key)

    def text(self, s: str) -> str:
        return self.terms.text(s)

    def field(self, name: str) -> str:
        if name in _VOCAB_FIELDS:
            return self.t(_VOCAB_FIELDS[name])
        word = _FIELD_WORDS.get(name)
        if word is None:
            word = name[:-3] if name.endswith("_id") else name
            # a percentage reads as its label does on the form: Markup %
            if word.endswith("_pct"):
                word = word[:-4] + " %"
            word = word.replace("_", " ").strip().capitalize()
        return word

    def name_of(self, table: str, row_id, fields=("name",)) -> str:
        try:
            key = (table, int(row_id))
        except (TypeError, ValueError):
            return ""
        if key not in self._names:
            tbl = Base.metadata.tables.get(table)
            text = ""
            if tbl is not None:
                cols = [tbl.c[f] for f in fields if f in tbl.c]
                if cols:
                    row = self.db.execute(
                        select(*cols).where(tbl.c.id == key[1])
                    ).first()
                    if row is not None:
                        text = " ".join(str(v) for v in row if v not in (None, ""))
            self._names[key] = text
        return self._names[key]

    def value(self, name: str, value) -> str:
        if value is None or value == "":
            return ""
        if name in _NAMED_TABLES:
            table, fields = _NAMED_TABLES[name]
            return self.name_of(table, value, fields) or f"#{value}"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if name in _MONEY_FIELDS:
            return money_text(value)
        if name == "status" and isinstance(value, str):
            return value.replace("_", " ").capitalize()
        if name.endswith("_pct") or name == "tax_rate":
            try:
                pct = Decimal(str(value)) * (100 if name == "tax_rate" else 1)
                return f"{pct.normalize():f}%"
            except (InvalidOperation, ValueError):
                return str(value)
        return str(value)

    def who(self, username: str | None, source: str | None = None) -> str:
        """An actor by the name people know: a sign-in's display name, a
        token by its name, the machine as "the system"; an import's rows
        say which import made them."""
        name = self._person(username)
        if source and source.startswith(IMPORT_SOURCE_PREFIX):
            import_name = source[len(IMPORT_SOURCE_PREFIX) :]
            ran = "" if name in ("the system", "") else f", run by {name}"
            return f"the system, in the {import_name} import{ran}"
        return name

    def _person(self, username: str | None) -> str:
        if not username:
            return "someone (not recorded)"
        if username == "system":
            return "the system"
        if username == "scheduled run":
            return "a scheduled run"
        if username == "operator":
            return "the operator"
        if username.startswith("token:"):
            return f"an API token ({username[len('token:'):]})"
        if self._people is None:
            from app.models.users import User

            self._people = {
                u.username: (u.display_name or u.username)
                for u in self.db.query(User.username, User.display_name).all()
            }
        return self._people.get(username, username)


# ---------------------------------------------------------------------------
# reading the audit log
# ---------------------------------------------------------------------------


def _mentions(column, key: str, value: int):
    """A cheap text filter for an audit snapshot holding key = value; the
    rows it passes are checked exactly in Python."""
    text = cast(column, Text)
    return or_(
        text.like(f'%"{key}": {value},%'),
        text.like(f'%"{key}": {value}}}%'),
        text.like(f'%"{key}":{value},%'),
        text.like(f'%"{key}":{value}}}%'),
    )


def _same(a, b) -> bool:
    try:
        return a is not None and int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _own_rows(db: Session, table: str, record_id: int) -> list[AuditLog]:
    """The document's own rows, since it was last made under this id."""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.table_name == table, AuditLog.record_id == record_id)
        .order_by(AuditLog.id)
        .all()
    )
    last_delete = max(
        (i for i, r in enumerate(rows) if r.action == "DELETE" and i < len(rows) - 1),
        default=None,
    )
    return rows if last_delete is None else rows[last_delete + 1 :]


def _child_rows(
    db: Session, table: str, fk: str, doc_id: int, where=None
) -> list[AuditLog]:
    """Each audit row of `table` made while its row pointed at the document
    through `fk` (a line, an application, an attachment). `where`, when
    given, is a further test of the row's snapshot (an attachment's type)."""
    tbl = Base.metadata.tables.get(table)
    candidate = {
        r.record_id
        for r in db.query(AuditLog.record_id).filter(
            AuditLog.table_name == table,
            or_(
                _mentions(AuditLog.new_values, fk, doc_id),
                _mentions(AuditLog.old_values, fk, doc_id),
            ),
        )
    }
    current: set[int] = set()
    if tbl is not None and fk in tbl.c:
        current = {
            r[0] for r in db.execute(select(tbl.c.id).where(tbl.c[fk] == doc_id))
        }
    ids = candidate | current
    if not ids:
        return []
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.table_name == table, AuditLog.record_id.in_(list(ids)))
        .order_by(AuditLog.id)
        .all()
    )
    owner: dict[int, bool] = {}
    kept = []
    for r in rows:
        new = r.new_values or {}
        old = r.old_values or {}
        if r.action == "INSERT":
            mine = _same(new.get(fk), doc_id) and (where is None or where(new))
            owner[r.record_id] = mine
        elif r.action == "UPDATE":
            if r.record_id not in owner:
                owner[r.record_id] = r.record_id in current
            mine = owner[r.record_id]
            if fk in (r.changed_fields or []):
                # moved off this document, or onto it (a purchase order
                # linked to an invoice, #986)
                mine = mine or _same(old.get(fk), doc_id) or _same(new.get(fk), doc_id)
                owner[r.record_id] = _same(new.get(fk), doc_id)
        else:  # DELETE
            mine = _same(old.get(fk), doc_id) and (where is None or where(old))
            owner.pop(r.record_id, None)
        if mine:
            kept.append(r)
    return kept


# ---------------------------------------------------------------------------
# turning rows into sentences
# ---------------------------------------------------------------------------


def _changes(words: _Words, row: AuditLog, skip=frozenset()) -> list[str]:
    """One line per field an UPDATE moved, in words: old → new."""
    out = []
    old = row.old_values or {}
    new = row.new_values or {}
    for f in row.changed_fields or []:
        if f in _QUIET_FIELDS or f == "status" or f in skip:
            continue
        if f.endswith("_at"):
            # a stamp (approved_at, sent_at, …) is the moment of the save
            # the entry already carries, and read as a raw ISO time (W-203)
            continue
        label = words.field(f)
        before, after = old.get(f), new.get(f)
        if (
            f in _PRIVATE_FIELDS
            or REDACTED in (before, after)
            or f in _PROSE_FIELDS
            and len(str(before or "") + str(after or "")) > 60
        ):
            out.append(f"{label} changed")
            continue
        b, a = words.value(f, before), words.value(f, after)
        if b == a or _empty_or_zero(before) and _empty_or_zero(after):
            # an empty number saved as zero (a markup filled in on approval,
            # W-210) is no change a person made
            continue
        if not b:
            out.append(f"{label} set to {a}")
        elif not a:
            out.append(f"{label} cleared (was {b})")
        else:
            out.append(f"{label} {b} → {a}")
    return out


def _empty_or_zero(value) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def _line_title(words: _Words, values: dict) -> str:
    """A line's name: its description, else its item's name, else its
    account's name; "" for a line with none. Never an id."""
    label = _line_label(values)
    if not label:
        for f, table in (
            ("item_id", "items"),
            ("account_id", "accounts"),
            ("debit_account_id", "accounts"),
        ):
            label = words.name_of(table, values.get(f)).strip()
            if label:
                break
    return label[:77] + "..." if len(label) > 80 else label


def _line_money(values: dict) -> str:
    """A line's amount as its sentence says it: "$60.00", or a journal
    line's "debit $60.00"."""
    amount = _line_amount(values)
    if amount is None:
        return ""
    side, number = amount
    return money_text(number) if side == "amount" else f"{side} {money_text(number)}"


def _line_words(words: _Words, values: dict) -> str:
    """A line as the document's creation lists it: its name and its
    amount; a line with no name, its amount."""
    label, money = _line_title(words, values), _line_money(values)
    if label and money:
        return f"{label}, {money}"
    return label or money or "a line"


def _line_name(words: _Words, values: dict, place: int, alone: bool = False) -> str:
    """A line as a change names it, the same on every kind: by its
    description, else its item's name, else its account's name, each with
    its amount ("Patch, $55.00"); a line with none of those by its place
    among the document's lines ("2 ($200.00)" after the word Line, "Line 2
    ($200.00)" when it stands `alone`). Never an id."""
    label, money = _line_title(words, values), _line_money(values)
    if label:
        return f"{label}, {money}" if money else label
    text = f"{place} ({money})" if money else str(place)
    return f"Line {text}" if alone else text


def _action_sentence(words: _Words, after: str, who: str, changed, new) -> str:
    """The status change in words, with what the action itself wrote."""
    if after == "approved":
        on = new.get("approved_on") if "approved_on" in changed else None
        text = f"Approved by {who}" + (f" on {on}" if on else "")
        if "invoice_id" in changed and new.get("invoice_id"):
            num = words.value("invoice_id", new["invoice_id"])
            text += f"; {words.t('Invoice').lower()} {num} made"
        return text
    if after == "converted" and new.get("converted_invoice_id"):
        num = words.name_of(
            "invoices", new["converted_invoice_id"], ("invoice_number",)
        )
        return f"Converted to {words.t('Invoice').lower()} {num} by {who}".replace(
            "  ", " "
        )
    if after == "promoted" and new.get("promoted_to_id"):
        num = words.name_of("change_orders", new["promoted_to_id"], ("number",))
        return f"Promoted to change order {num} by {who}".replace("  ", " ")
    return f"Marked {after} by {who}"


def _number(dt: DocType, values: dict) -> str:
    for f in dt.number_fields:
        if values.get(f) not in (None, ""):
            return str(values[f])
    return ""


def _other_number(words: _Words, dt: DocType, doc_id: int, other_id) -> str:
    """The number of the document this one replaced or was replaced by,
    when it tells the two apart: a copy that kept the original's number (a
    check number, a reference) is not named by it."""
    number = _number(dt, _row(words.db, dt.table, other_id))
    return "" if number == _number(dt, _row(words.db, dt.table, doc_id)) else number


def _status_words(value) -> str:
    return str(value or "").replace("_", " ").lower()


@dataclass
class _Event:
    when: datetime | None
    order: int  # a tie-breaker: the audit row id, or a large one for logs
    who: str
    kind: str
    text: str
    details: list[str] = field(default_factory=list)

    def out(self, tz_offset):
        return {
            "when": self.when.isoformat() if self.when else "",
            "when_local": local_words(self.when, tz_offset),
            "who": self.who,
            "kind": self.kind,
            "text": self.text,
            "details": self.details,
        }


def _group(rows: list[AuditLog]) -> list[list[AuditLog]]:
    """Rows written by one save: the same actor within a few seconds, in
    the order they were written."""
    groups: list[list[AuditLog]] = []
    for r in sorted(rows, key=lambda r: r.id):
        if groups:
            last = groups[-1][-1]
            a, b = _utc(last.timestamp), _utc(r.timestamp)
            if (
                last.username == r.username
                and (last.source or "") == (r.source or "")
                and a is not None
                and b is not None
                and abs((b - a).total_seconds()) <= 2
                and r.id - last.id < 400
            ):
                groups[-1].append(r)
                continue
        groups.append([r])
    return groups


def _po_link_sentence(words: _Words, row: AuditLog, doc_id: int) -> str | None:
    """A purchase order's row as its invoice's event (#986): linked when it
    came to name the invoice, unlinked when it stopped; any other edit of
    the order is the order's own history, not the invoice's."""
    old, new = row.old_values or {}, row.new_values or {}
    values = new if row.action != "DELETE" else old
    # an edit's snapshot carries only what moved: the number from the row
    num = values.get("po_number") or old.get("po_number")
    if not num:
        num = _row(words.db, "purchase_orders", row.record_id).get("po_number")
    po = f"Purchase order{' ' + str(num) if num else ''}"
    if row.action == "INSERT":
        return f"{po} linked"
    if row.action == "DELETE":
        return f"{po} deleted, so unlinked"
    if "invoice_id" not in (row.changed_fields or []):
        return None
    return f"{po} linked" if _same(new.get("invoice_id"), doc_id) else f"{po} unlinked"


def _related_sentence(
    words: _Words, kind: str, row: AuditLog, doc_id: int | None = None
) -> str | None:
    """A related record's row as this document's event."""
    if kind == "po_linked":
        return _po_link_sentence(words, row, doc_id)
    values = (row.new_values if row.action != "DELETE" else row.old_values) or {}
    amount = money_text(values.get("amount"))
    adding = row.action == "INSERT"
    if row.action == "UPDATE":
        if "amount" not in (row.changed_fields or []):
            return None
        before = money_text((row.old_values or {}).get("amount"))
        return f"An amount applied changed from {before} to {amount}"
    invoice_word = words.t("Invoice")
    if kind == "payment_applied":
        pay = _row(words.db, "payments", values.get("payment_id"))
        frm = (
            f" from {words.name_of('customers', pay.get('customer_id'))}" if pay else ""
        )
        ref = f" (check {pay['check_number']})" if pay.get("check_number") else ""
        return (
            f"Payment of {amount}{frm}{ref} applied"
            if adding
            else f"Payment of {amount}{frm}{ref} unapplied"
        )
    if kind == "credit_applied":
        memo = _row(words.db, "credit_memos", values.get("credit_memo_id"))
        num = f" {memo['memo_number']}" if memo.get("memo_number") else ""
        return (
            f"Credit memo{num} applied: {amount}"
            if adding
            else (f"Credit memo{num} unapplied: {amount}")
        )
    if kind == "credit_used":
        inv = _row(words.db, "invoices", values.get("invoice_id"))
        num = f" {inv['invoice_number']}" if inv.get("invoice_number") else ""
        return (
            f"{amount} applied to {invoice_word.lower()}{num}"
            if adding
            else f"{amount} unapplied from {invoice_word.lower()}{num}"
        )
    if kind == "payment_used":
        inv = _row(words.db, "invoices", values.get("invoice_id"))
        num = f" {inv['invoice_number']}" if inv.get("invoice_number") else ""
        return (
            f"{amount} applied to {invoice_word.lower()}{num}"
            if adding
            else f"{amount} unapplied from {invoice_word.lower()}{num}"
        )
    if kind == "bill_paid":
        bp = _row(words.db, "bill_payments", values.get("bill_payment_id"))
        ref = f" (check {bp['check_number']})" if bp.get("check_number") else ""
        return (
            f"Bill payment of {amount}{ref} applied"
            if adding
            else f"Bill payment of {amount}{ref} unapplied"
        )
    if kind == "bill_payment_paid":
        bill = _row(words.db, "bills", values.get("bill_id"))
        num = f" {bill['bill_number']}" if bill.get("bill_number") else ""
        return (
            f"{amount} applied to bill{num}"
            if adding
            else f"{amount} unapplied from bill{num}"
        )
    if kind == "vendor_credit_applied":
        vc = _row(words.db, "vendor_credits", values.get("vendor_credit_id"))
        num = f" {vc['credit_number']}" if vc.get("credit_number") else ""
        return (
            f"Vendor credit{num} applied: {amount}"
            if adding
            else (f"Vendor credit{num} unapplied: {amount}")
        )
    if kind == "vendor_credit_used":
        bill = _row(words.db, "bills", values.get("bill_id"))
        num = f" {bill['bill_number']}" if bill.get("bill_number") else ""
        return (
            f"{amount} applied to bill{num}"
            if adding
            else f"{amount} unapplied from bill{num}"
        )
    return None


def _row(db: Session, table: str, row_id) -> dict:
    tbl = Base.metadata.tables.get(table)
    if tbl is None or row_id in (None, ""):
        return {}
    try:
        row = db.execute(select(tbl).where(tbl.c.id == int(row_id))).mappings().first()
    except (TypeError, ValueError):
        return {}
    return dict(row) if row else {}


# a line's own bookkeeping, never what a person changed on it
_LINE_ORDER_FIELDS = ("line_order", "line_no", "sort_order", "position")
_LINE_SKIP = set(_LINE_ORDER_FIELDS)
# the fields that hold a line's amount (its extended total), first found
_LINE_AMOUNT_FIELDS = ("amount", "sell_amount", "scheduled_value")
# what differs between a document and its corrected copy without anyone
# having changed it: where a line was cleared, deposited or billed on
_COPY_LINE_SKIP = frozenset(
    {
        "cleared",
        "reconciliation_id",
        "deposit_transaction_id",
        "billed_invoice_line_id",
        "purchase_order_line_id",
        "recognizes_invoice_id",
        "recognizes_bill_id",
    }
)
_COPY_SKIP = frozenset(
    {
        "is_voided",
        "voided_by",
        "void_reason",
        "replaces_id",
        "source_id",
        "source_type",
        "exchange_rate",
    }
)
# each snapshot of a line carries the row it is a snapshot of ("_row"), so
# a line kept by id is known from one deleted and made again under an id
# SQLite gave out twice
_ROW_SERIAL = count(1)


def _line_key(values: dict) -> tuple:
    order = next((values[f] for f in _LINE_ORDER_FIELDS if values.get(f)), 0)
    try:
        return (int(order), int(values.get("id") or 0))
    except (TypeError, ValueError):
        return (0, int(values.get("id") or 0))


def _line_diff_fields(before: dict, after: dict, fk: str, skip=frozenset()) -> list:
    """The fields a person could have changed that differ between two
    snapshots of a line."""
    out = []
    for f in dict.fromkeys([*before, *after]):
        if (
            f in _QUIET_FIELDS
            or f in _LINE_SKIP
            or f in skip
            or f == fk
            or f.startswith("_")
            or f.endswith("_at")
        ):
            continue
        if _same_value(before.get(f), after.get(f)):
            continue
        out.append(f)
    return out


def _tidy(value):
    """A number as a person reads it: 2.0000 and 2.0 are both 2."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if not number.is_finite():
        return value
    text = format(number.normalize(), "f")
    return text


def _same_value(a, b) -> bool:
    """Equal as stored: a number sent as 1.0 is the 1.0000 the column kept."""
    if a == b:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if a in (None, "") or b in (None, ""):
        return a in (None, "") and b in (None, "")
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


_TOTAL_FIELDS = {
    "subtotal",
    "total",
    "tax_amount",
    "balance_due",
    "amount_due",
    "total_amount",
    "amount",
}


def _is_total_field(name: str) -> bool:
    return name in _TOTAL_FIELDS or name.endswith("_total")


def _decimal(value) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _line_label(values: dict) -> str:
    """The line's description as typed (a memo, an item number), or ""."""
    for f in _LINE_LABEL_FIELDS:
        text = str(values.get(f) or "").strip()
        if text:
            return text
    return ""


def _line_amount(values: dict) -> tuple | None:
    """The line's amount, its extended total, and the side it is on: a
    journal line's is its debit or its credit, and a debit and a credit of
    one figure are not the same amount."""
    for f in _LINE_AMOUNT_FIELDS:
        number = _decimal(values.get(f))
        if number is not None:
            return ("amount", number)
    for side in ("debit", "credit"):
        number = _decimal(values.get(side))
        if number:
            return (side, number)
    return None


def _line_subject(values: dict) -> tuple:
    """What stands for a line that has no description, the way its name
    does: its item, else its account (a journal line's with the side it is
    on), else nothing at all."""
    try:
        if values.get("item_id") not in (None, ""):
            return ("item", int(values["item_id"]))
        if values.get("account_id") not in (None, ""):
            side = ""
            if "debit" in values or "credit" in values:
                amount = _line_amount(values)
                side = amount[0] if amount else ""
            return ("account", int(values["account_id"]), side)
    except (TypeError, ValueError):
        pass
    return ("nothing",)


def _compare_lines(
    before: list[dict],
    after: list[dict],
    fk: str,
    keeps_ids: bool = False,
    skip=frozenset(),
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """THE RULE for how Activity compares a document's lines before and
    after a save, the same for every kind of document. Returns (the pairs,
    each one line before and after; the lines removed; the lines added).

    The lines before and the lines after are two lists. They are paired in
    passes. A line paired in one pass is out of the later ones, and within
    a pass the lines pair in their original order, so the same save always
    reads the same way.

    0. Identity. A line the save kept by its id (the same row, edited in
       place or left alone) pairs with itself. Where the kind's form keeps
       line ids (`keeps_ids`: a purchase order, a pay application), that is
       the whole of who is who: an old id that is gone is a line removed,
       and a line with no id is a line added, even when no old id survives.
    1. Identical. Lines equal in every compared field pair, wherever they
       sit. So a reorder alone, on any kind, names no line.
    2. The same description. Lines whose description (not an empty one) is
       the same pair as one edited line.
    3. The same subject, for lines with no description. Such a line is
       known by what its name falls back on: its item, else its account
       (a journal entry line's account and the side it is on), else nothing
       at all. Two lines with no description and the same subject pair as
       one edited line. This is what keeps a journal entry with no
       descriptions from pairing across accounts. A line that has a
       description is not in this pass: two described lines on one item or
       one account are two lines, and only pass 4 can make them one.
    4. The same amount. Lines whose amount (the extended total; a debit is
       not a credit) is the same pair as one edited line, whatever else
       changed: description, quantity, rate.
    5. What is left was removed, and added.

    Passes 2 to 4 are for the kinds that rewrite their lines with no ids.
    The stated consequence: on those kinds, a described line that changes
    both its description and its amount reads as one removed and one
    added.

    `skip` names fields left out of every comparison (a corrected copy
    against the document it replaces)."""
    left, new = list(before), list(after)
    pairs: list[tuple[dict, dict]] = []

    def take(same) -> None:
        for b in list(left):
            i = next((i for i, a in enumerate(new) if same(b, a)), None)
            if i is not None:
                pairs.append((b, new.pop(i)))
                del left[next(j for j, x in enumerate(left) if x is b)]

    def identity(b, a):
        return b.get("_row") is not None and b.get("_row") == a.get("_row")

    def identical(b, a):
        return not _line_diff_fields(b, a, fk, skip)

    def description(b, a):
        return bool(_line_label(b)) and _line_label(b) == _line_label(a)

    def subject(b, a):
        return (
            not _line_label(b)
            and not _line_label(a)
            and _line_subject(b) == _line_subject(a)
        )

    def amount(b, a):
        return _line_amount(b) is not None and _line_amount(b) == _line_amount(a)

    take(identity)
    take(identical)
    if not keeps_ids:
        take(description)
        take(subject)
        take(amount)
    return pairs, left, new


def _field_changes(words: _Words, table, before: dict, after: dict, fields) -> list:
    """Each of `fields` old → new, numbers as a person reads them (W-223:
    "Quantity 1 → 3", not "1.0000 → 3.0")."""
    row = AuditLog(
        table_name=table,
        record_id=after.get("id") or 0,
        action="UPDATE",
        changed_fields=list(fields),
        old_values={f: _tidy(before.get(f)) for f in fields},
        new_values={f: _tidy(after.get(f)) for f in fields},
    )
    return _changes(words, row)


def _line_changes(
    words: _Words,
    table: str,
    fk: str,
    before: list[dict],
    after: list[dict],
    keeps_ids: bool = False,
    skip=frozenset(),
) -> list[str]:
    """One save's change of a document's lines in sentences: each edited
    line field by field, then each line added, then each removed, once,
    with its real amount. The pairing is `_compare_lines`, the one rule."""
    pairs, removed, added = _compare_lines(before, after, fk, keeps_ids, skip)
    place_before = {id(b): i + 1 for i, b in enumerate(before)}
    place_after = {id(a): i + 1 for i, a in enumerate(after)}
    said = []
    for b, a in sorted(pairs, key=lambda pair: place_after[id(pair[1])]):
        moved = _field_changes(words, table, b, a, _line_diff_fields(b, a, fk, skip))
        if moved:
            name = _line_name(words, a, place_after[id(a)])
            said.append(f"Line {name}: " + "; ".join(moved))
    said.extend(
        f"Line added: {_line_name(words, a, place_after[id(a)], alone=True)}"
        for a in added
    )
    said.extend(
        f"Line removed: {_line_name(words, b, place_before[id(b)], alone=True)}"
        for b in removed
    )
    return said


class _LineReplay:
    """One table of a document's lines, walked save by save through the
    audit log's rows: `save` takes one save's rows and gives the lines as
    they were before it and as it left them, each in the document's order.

    A save that rewrites the lines (an invoice, estimate or bill deletes
    and inserts them; a change order bulk-deletes them, which writes no
    audit row, and inserts them at 0 before its recompute) leaves deleted
    and inserted rows; a save that keeps a line by its id (a purchase
    order, a pay application) leaves an edit of that row, or nothing."""

    def __init__(self, words: _Words, table: str, fk: str, doc_id: int, last_seen):
        self.words, self.table, self.fk, self.doc_id = words, table, fk, doc_id
        self.last_seen = last_seen  # (table, row id) → its last save's index
        self.alive: dict[int, dict] = {}
        tbl = Base.metadata.tables.get(table)
        self.current = (
            {
                x
                for (x,) in words.db.execute(
                    select(tbl.c.id).where(tbl.c[fk] == doc_id)
                )
            }
            if tbl is not None and fk in tbl.c
            else set()
        )

    def _known(self, rid: int, values: dict) -> dict:
        return {**values, "id": rid, "_row": next(_ROW_SERIAL)}

    def _stored(self, rid: int) -> dict:
        """The line as the table has it now, when it is still this
        document's (for a line made before the audit log kept its rows)."""
        row = _row(self.words.db, self.table, rid)
        return row if _same(row.get(self.fk), self.doc_id) else {}

    def save(self, gi: int, rows: list[AuditLog]) -> tuple[list[dict], list[dict]]:
        fk, doc_id = self.fk, self.doc_id
        before = dict(self.alive)
        after = dict(self.alive)
        made: set[int] = set()
        for r in rows:
            rid = r.record_id
            old, new = r.old_values or {}, r.new_values or {}
            if r.action == "INSERT":
                # over a living line: SQLite gave a new line the id of one
                # deleted with no row of its own, so it replaced that one
                after[rid] = self._known(rid, new)
                made.add(rid)
            elif r.action == "DELETE":
                if rid not in made:
                    before[rid] = (
                        {**before[rid], **old}
                        if rid in before
                        else self._known(rid, old)
                    )
                after.pop(rid, None)
            else:
                moved = fk in (r.changed_fields or [])
                if rid not in after:
                    if moved and not _same(old.get(fk), doc_id):
                        # a line that came to this document from another
                        after[rid] = self._known(rid, {**self._stored(rid), **new})
                        made.add(rid)
                        continue
                    before[rid] = after[rid] = self._known(
                        rid, {**self._stored(rid), **old}
                    )
                if moved and not _same(new.get(fk), doc_id):
                    after.pop(rid)  # it left for another document
                    continue
                after[rid] = {**after[rid], **new}
        if made:
            # lines that left the document with no row of their own (a bulk
            # delete) went with the save that wrote their successors
            for rid in list(after):
                if (
                    rid not in made
                    and rid not in self.current
                    and self.last_seen.get((self.table, rid), -1) < gi
                ):
                    del after[rid]
        self.alive = after
        return (
            sorted(before.values(), key=_line_key),
            sorted(after.values(), key=_line_key),
        )


def _last_seen(tables, groups: list[list[AuditLog]]) -> dict[tuple, int]:
    seen: dict[tuple, int] = {}
    for gi, group in enumerate(groups):
        for r in group:
            if r.table_name in tables:
                seen[(r.table_name, r.record_id)] = gi
    return seen


def _line_details(
    words: _Words, dt: DocType, doc_id: int, groups: list[list[AuditLog]]
) -> tuple[dict[int, list[str]], dict[int, dict[str, list[dict]]]]:
    """What each save did to the document's lines, in words, by the index
    of its group (W-215), and the lines the document was made with, by the
    index of the group that made it.

    Each save is read as one change of the line set: the lines before it
    against the lines after it, paired by `_compare_lines` (the rule is in
    its docstring). A line found unchanged says nothing; an edited one
    names each field old → new; a line added or removed says so, once,
    with its real amount."""
    tables = dict(dt.lines)
    out: dict[int, list[str]] = {}
    made: dict[int, dict[str, list[dict]]] = {}
    if not tables:
        return out, made
    last_seen = _last_seen(tables, groups)
    replay = {
        table: _LineReplay(words, table, fk, doc_id, last_seen)
        for table, fk in tables.items()
    }
    for gi, group in enumerate(groups):
        created = any(
            r.table_name == dt.table and r.record_id == doc_id and r.action == "INSERT"
            for r in group
        )
        said: list[str] = []
        for table, fk in tables.items():
            rows = [r for r in group if r.table_name == table]
            if not rows:
                continue
            before, after = replay[table].save(gi, rows)
            if created and not before:
                said.extend(f"Line: {_line_words(words, v)}" for v in after)
                made.setdefault(gi, {})[table] = after
            else:
                said.extend(_line_changes(words, table, fk, before, after, dt.line_ids))
        out[gi] = said
    return out, made


def _lines_left(words: _Words, table: str, fk: str, doc_id: int) -> list[dict]:
    """A document's lines of one table as its last save left them, read
    from the audit log (the document a corrected copy replaced)."""
    groups = _group(_child_rows(words.db, table, fk, doc_id))
    replay = _LineReplay(words, table, fk, doc_id, _last_seen({table}, groups))
    lines: list[dict] = []
    for gi, group in enumerate(groups):
        _before, lines = replay.save(gi, group)
    return lines


def _values_left(db: Session, table: str, doc_id: int) -> dict:
    """A document's own fields as its last save left them, from the audit
    log; nothing when the log does not hold its making."""
    values: dict = {}
    seen = False
    for r in _own_rows(db, table, doc_id):
        if r.action == "INSERT":
            values, seen = dict(r.new_values or {}), True
        elif r.action == "UPDATE":
            values.update(r.new_values or {})
    return values if seen else {}


def _copy_details(
    words: _Words,
    dt: DocType,
    old_id: int,
    new_values: dict,
    new_lines: dict[str, list[dict]],
) -> list[str]:
    """What a corrected copy changed from the document it replaced: its own
    fields old → new, its lines by the one rule, then its totals."""
    old_values = _values_left(words.db, dt.table, old_id)
    fields = [
        f
        for f in (new_values if old_values else {})
        if f not in _COPY_SKIP
        and f not in dt.number_fields
        and not _same_value(old_values.get(f), new_values.get(f))
    ]
    totals = [f for f in fields if _is_total_field(f)]
    fields = [f for f in fields if f not in totals]
    said = _field_changes(words, dt.table, old_values, new_values, fields)
    for table, fk in dt.lines:
        if table in new_lines:
            said.extend(
                _line_changes(
                    words,
                    table,
                    fk,
                    _lines_left(words, table, fk, old_id),
                    new_lines[table],
                    skip=_COPY_LINE_SKIP,
                )
            )
    said.extend(_field_changes(words, dt.table, old_values, new_values, totals))
    return said


def _audit_events(
    words: _Words,
    dt: DocType,
    doc_id: int,
    own: list[AuditLog],
    lines: list[AuditLog],
    attachments: list[AuditLog],
    related: list[tuple[str, AuditLog]],
) -> list[_Event]:
    noun = words.t(dt.word)
    kinds = {r.id: k for k, r in related}
    line_tables = {t for t, _ in dt.lines}
    events: list[_Event] = []
    groups = _group(own + lines + attachments + [r for _, r in related])
    line_said, made_lines = _line_details(words, dt, doc_id, groups)
    replaced_said: set = set()  # the copies this document's replacement named
    copies_said: set = set()  # the documents this one said it corrects
    for gi, group in enumerate(groups):
        first = group[0]
        lines_said = False
        when = _utc(first.timestamp)
        who = words.who(first.username, first.source)
        heads: list[tuple[str, str]] = []  # (kind, sentence)
        details: list[str] = []
        tail: list[str] = []  # the document's totals, said after its lines
        # a void's reason, said after its sentence as typed (no vocabulary
        # swap in the person's own words)
        reasons: dict[int, str] = {}  # id() of the head tuple → reason
        # details that belong to one sentence of the save, not to its edit
        own_details: dict[int, list[str]] = {}
        made: dict = {}  # the document's own fields as this save made it
        copy_of = None  # the document this one is a corrected copy of
        # what an approve/convert/promote in this save wrote, on any of the
        # document's rows of it (the status and the stamps may flush apart)
        folded: set = set()
        for r in group:
            if (
                r.table_name == dt.table
                and r.record_id == doc_id
                and r.action == "UPDATE"
                and "status" in (r.changed_fields or [])
            ):
                folded |= _ACTION_FIELDS.get(
                    _status_words((r.new_values or {}).get("status")), set()
                )
        for r in group:
            if r.table_name == dt.table and r.record_id == doc_id:
                if r.action == "INSERT":
                    made = dict(r.new_values or {})
                    copy_of = made.get("replaces_id") or copy_of
                    num = _number(dt, r.new_values or {})
                    heads.append(
                        (
                            "created",
                            f"{noun}{' ' + num if num else ''} created by {who}",
                        )
                    )
                elif r.action == "DELETE":
                    heads.append(("deleted", f"{noun} deleted by {who}"))
                else:
                    changed = r.changed_fields or []
                    old, new = r.old_values or {}, r.new_values or {}
                    voided = False
                    if made and r.action == "UPDATE":
                        made.update(new)
                    if "replaces_id" in changed and new.get("replaces_id"):
                        copy_of = new["replaces_id"]
                    if "status" in changed:
                        after = _status_words(new.get("status"))
                        if after in _VOID_STATUSES:
                            voided = True
                        elif after and after != _status_words(old.get("status")):
                            action = _action_sentence(words, after, who, changed, new)
                            heads.append(("status", action))
                    if "is_voided" in changed and new.get("is_voided") is True:
                        voided = True
                    if voided:
                        reason = str(new.get("void_reason") or "").strip()
                        head = ("voided", f"Voided by {who}")
                        if reason and reason != REDACTED:
                            reasons[id(head)] = reason
                        heads.append(head)
                    if "reversal_transaction_id" in changed and new.get(
                        "reversal_transaction_id"
                    ):
                        # an accrued job cost's reversal (#897)
                        on = new.get("reversed_on")
                        heads.append(
                            (
                                "reversed",
                                "Accrual reversed"
                                + (f" on {on}" if on else "")
                                + f" by {who} (journal entry "
                                f"{new['reversal_transaction_id']})",
                            )
                        )
                    copy = new.get("replaced_by_id")
                    if (
                        "replaced_by_id" in changed
                        and copy
                        and copy not in replaced_said
                    ):
                        # the replacement writes the field and an entry of
                        # its own: said once (W-237), with the copy's number
                        replaced_said.add(copy)
                        num = _other_number(words, dt, doc_id, copy)
                        heads.append(
                            (
                                "replaced",
                                "Replaced by a corrected copy"
                                + (f" ({num})" if num else "")
                                + f" by {who}",
                            )
                        )
                    # the document's totals after the lines that moved them
                    # (W-223): a line's own change is said first
                    totals = {f for f in changed if _is_total_field(f)}
                    details.extend(
                        c
                        for c in _changes(words, r, skip=_VOID_FIELDS | folded | totals)
                        if not c.startswith("Void ")
                    )
                    tail.extend(
                        _changes(
                            words,
                            r,
                            skip=_VOID_FIELDS | folded | (set(changed) - totals),
                        )
                    )
            elif r.table_name in line_tables:
                # the save's lines, said once as one change of the set (W-215)
                if not lines_said:
                    details.extend(line_said.get(gi, []))
                    lines_said = True
            elif r.table_name == "attachments":
                vals = (r.new_values if r.action == "INSERT" else r.old_values) or {}
                name = vals.get("filename") or "a file"
                if r.action == "INSERT":
                    heads.append(("attachment", f"{name} attached by {who}"))
                elif r.action == "DELETE":
                    heads.append(("attachment", f"Attachment {name} removed by {who}"))
            elif r.id in kinds:
                sentence = _related_sentence(words, kinds[r.id], r, doc_id)
                if sentence:
                    heads.append((kinds[r.id], f"{sentence} by {who}"))
        details.extend(tail)
        if copy_of and copy_of not in copies_said:
            copies_said.add(copy_of)
            # a corrected copy says whose, and what it changed (W-237: it
            # read "Replaces set to 1")
            num = _other_number(words, dt, doc_id, copy_of)
            head = (
                "replaces",
                "Corrected copy of "
                + (num or f"an earlier {noun.lower()}")
                + f" by {who}",
            )
            own_details[id(head)] = (
                _copy_details(words, dt, copy_of, made, made_lines.get(gi, {}))
                if made
                else []
            )
            heads.append(head)
        if not heads and details:
            heads.append(("edited", f"Edited by {who}"))
        if not heads:
            continue
        # the sentences of one save, each its own event; the field changes
        # go with the first (the edit, the creation)
        primary = next(
            (i for i, (k, _) in enumerate(heads) if k in ("created", "edited")), 0
        )
        if details and heads[primary][0] not in ("created", "edited"):
            heads.insert(0, ("edited", f"Edited by {who}"))
            primary = 0
        for i, head in enumerate(heads):
            kind, sentence = head
            text = words.text(sentence)
            if id(head) in reasons:
                text += ": " + reasons[id(head)]
            events.append(
                _Event(
                    when,
                    first.id * 10 + i,
                    who,
                    kind,
                    text + ("" if text.endswith((".", "!", "?")) else "."),
                    (details if i == primary else []) + own_details.get(id(head), []),
                )
            )
    return events


def _transaction_void_events(words: _Words, doc_id: int) -> list[_Event]:
    """A register entry, an expense, a card charge, a deposit or a journal
    entry is voided by a reversing entry that points back at it."""
    from app.models.transactions import Transaction
    from app.services.voids import TRANSACTION_KEYED_VOIDS

    out = []
    voids = (
        words.db.query(Transaction)
        .filter(
            Transaction.source_id == doc_id,
            Transaction.source_type.in_(list(TRANSACTION_KEYED_VOIDS)),
        )
        .all()
    )
    for v in voids:
        row = (
            words.db.query(AuditLog)
            .filter(
                AuditLog.table_name == "transactions",
                AuditLog.record_id == v.id,
                AuditLog.action == "INSERT",
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        who = words.who(
            row.username if row else v.posted_by, row.source if row else None
        )
        reason = (v.memo or "").strip()
        text = f"Voided by {who}" + (f": {reason}" if reason else "")
        out.append(
            _Event(
                _utc(row.timestamp if row else v.created_at),
                (row.id * 10) if row else 0,
                who,
                "voided",
                text + ".",
            )
        )
    return out


def _deposit_events(words: _Words, dt: DocType, doc_id: int) -> list[_Event]:
    """A deposit taking a payment, and a payment taken into a deposit: the
    payment's undeposited-funds line moving to the deposit."""
    from app.models.transactions import Transaction, TransactionLine

    events = []
    if "took_payments" in dt.extra:
        rows = (
            words.db.query(AuditLog)
            .filter(
                AuditLog.table_name == "transaction_lines",
                AuditLog.action == "UPDATE",
                or_(
                    _mentions(AuditLog.new_values, "deposit_transaction_id", doc_id),
                    _mentions(AuditLog.old_values, "deposit_transaction_id", doc_id),
                ),
            )
            .order_by(AuditLog.id)
            .all()
        )
        for r in rows:
            if "deposit_transaction_id" not in (r.changed_fields or []):
                continue
            took = _same((r.new_values or {}).get("deposit_transaction_id"), doc_id)
            gave = _same((r.old_values or {}).get("deposit_transaction_id"), doc_id)
            if not (took or gave):
                continue
            line = words.db.get(TransactionLine, r.record_id)
            what = "a payment"
            if line is not None:
                txn = words.db.get(Transaction, line.transaction_id)
                amount = money_text(line.debit or line.credit)
                if txn is not None and txn.source_type == "payment":
                    pay = _row(words.db, "payments", txn.source_id)
                    cust = words.name_of("customers", pay.get("customer_id"))
                    what = f"payment of {amount}" + (f" from {cust}" if cust else "")
                else:
                    what = f"{amount} from {txn.description if txn else 'an entry'}"
            who = words.who(r.username, r.source)
            text = (
                f"Took {what}"
                if took
                else f"{what[0].upper()}{what[1:]} put back on the payments to deposit"
            )
            events.append(
                _Event(
                    _utc(r.timestamp),
                    r.id * 10,
                    who,
                    "deposit",
                    words.text(f"{text} by {who}."),
                )
            )
    if "deposited" in dt.extra:
        pay = _row(words.db, "payments", doc_id)
        txn_id = pay.get("transaction_id")
        if txn_id:
            line_ids = [
                i
                for (i,) in words.db.query(TransactionLine.id).filter(
                    TransactionLine.transaction_id == txn_id
                )
            ]
            if line_ids:
                rows = (
                    words.db.query(AuditLog)
                    .filter(
                        AuditLog.table_name == "transaction_lines",
                        AuditLog.action == "UPDATE",
                        AuditLog.record_id.in_(line_ids),
                    )
                    .order_by(AuditLog.id)
                    .all()
                )
                for r in rows:
                    if "deposit_transaction_id" not in (r.changed_fields or []):
                        continue
                    new = (r.new_values or {}).get("deposit_transaction_id")
                    old = (r.old_values or {}).get("deposit_transaction_id")
                    who = words.who(r.username, r.source)
                    dep = _row(words.db, "transactions", new or old)
                    on = f" of {dep['date']}" if dep.get("date") else ""
                    text = (
                        f"Deposited in the deposit{on} by {who}"
                        if new
                        else f"Taken out of the deposit{on} by {who}"
                    )
                    events.append(
                        _Event(_utc(r.timestamp), r.id * 10, who, "deposit", text + ".")
                    )
    return events


def _email_events(words: _Words, dt: DocType, doc_id: int) -> list[_Event]:
    from app.services.email_errors import FailureReader

    if not dt.email_types:
        return []
    rows = (
        words.db.query(EmailLog)
        .filter(EmailLog.entity_type.in_(dt.email_types), EmailLog.entity_id == doc_id)
        .order_by(EmailLog.id)
        .all()
    )
    out = []
    reader = FailureReader(words.db) if rows else None
    for r in rows:
        who = words.who(r.username) if r.username else "someone (not recorded)"
        what = {
            "reminder": "Payment reminder emailed",
            "statement": "Statement emailed",
            "collection": "Collection letter emailed",
            "giving_statement": "Giving statement emailed",
        }.get(r.entity_type, "Emailed")
        if r.status == "sent":
            text = f"{what} to {r.recipient} by {who}."
            details = [f"Subject: {r.subject}"] if r.subject else []
            if r.cc:
                details.append(f"Cc: {r.cc}")
        else:
            text = f"An email to {r.recipient} by {who} could not be sent."
            # the one reader of the stored text, scrubbed (W-228)
            details = [reader.read(r, admin=False).sentence]
        out.append(
            _Event(
                _utc(r.sent_at),
                10**12 + r.id,
                who,
                "emailed",
                words.text(text),
                details,
            )
        )
    return out


def _document_audit_events(words: _Words, type_: str, doc_id: int) -> list[_Event]:
    """A document rendered with a recorded hash (record_doc_audit), when its
    row is keyed by this record type and id."""
    rows = (
        words.db.query(DocumentAudit)
        .filter(DocumentAudit.doc_type == type_, DocumentAudit.doc_key == str(doc_id))
        .order_by(DocumentAudit.id)
        .all()
    )
    return [
        _Event(
            _utc(r.created_at),
            10**12 + r.id,
            words.who(r.username),
            "printed",
            f"Printed to PDF by {words.who(r.username)} (document check {r.id}).",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# the one entry point
# ---------------------------------------------------------------------------


def _check_record(db: Session, type_: str, doc_id: int) -> tuple[DocType, dict]:
    dt = DOC_TYPES.get(type_)
    if dt is None:
        raise ActivityNotFound(
            f"SlowBooks Pro keeps no activity for a record of type '{type_}'."
        )
    record = _row(db, dt.table, doc_id)
    if dt.table == "transactions" and record:
        source = record.get("source_type") or ""
        if dt.source_types is not None and source not in dt.source_types:
            record = {}
    if not record:
        words = terms_from_db(db)
        raise ActivityNotFound(words.text(f"There is no {dt.word.lower()} {doc_id}."))
    return dt, record


def document_activity(
    db: Session, type_: str, doc_id: int, tz_offset: int | None = None
) -> dict:
    """Every event on one document, newest first, each a sentence with who
    and when. Raises ActivityNotFound for an unknown type or record."""
    dt, _record = _check_record(db, type_, doc_id)
    words = _Words(db)
    own = _own_rows(db, dt.table, doc_id)
    lines: list[AuditLog] = []
    for table, fk in dt.lines:
        lines.extend(_child_rows(db, table, fk, doc_id))
    attachments: list[AuditLog] = []
    for att_type in dt.attachment_types:
        attachments.extend(
            _child_rows(
                db,
                "attachments",
                "entity_id",
                doc_id,
                where=lambda v, t=att_type: v.get("entity_type") == t
                and v.get("employee_id") in (None, ""),
            )
        )
    related: list[tuple[str, AuditLog]] = []
    for rel in dt.related:
        related.extend(
            (rel.kind, r) for r in _child_rows(db, rel.table, rel.fk, doc_id)
        )
    events = _audit_events(words, dt, doc_id, own, lines, attachments, related)
    if dt.table == "transactions":
        events.extend(_transaction_void_events(words, doc_id))
    events.extend(_deposit_events(words, dt, doc_id))
    events.extend(_email_events(words, dt, doc_id))
    events.extend(_document_audit_events(words, type_, doc_id))
    floor = datetime.min.replace(tzinfo=timezone.utc)
    events.sort(key=lambda e: (e.when or floor, e.order), reverse=True)
    return {
        "type": type_,
        "id": doc_id,
        "count": len(events),
        "events": [e.out(tz_offset) for e in events],
    }
