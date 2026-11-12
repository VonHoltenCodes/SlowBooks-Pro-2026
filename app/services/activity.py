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
    ),
    "pay_application": DocType(
        "Pay Application",
        "pay_applications",
        (("pay_application_lines", "pay_application_id"),),
        number_fields=("application_no", "number"),
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
    "billed_amount",
    "received_qty",
    "last_emailed_at",
    "last_emailed_to",
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
}
# The vocabulary keys among those words (terms.text swaps them in prose too)
_VOCAB_FIELDS = {"customer_id": "Customer", "job_id": "Job", "class_id": "Class"}
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
}
_LINE_LABEL_FIELDS = ("description", "memo", "item_no")
_VOID_STATUSES = {"void", "voided"}


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
                mine = mine or _same(old.get(fk), doc_id)
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


def _changes(words: _Words, row: AuditLog) -> list[str]:
    """One line per field an UPDATE moved, in words: old → new."""
    out = []
    old = row.old_values or {}
    new = row.new_values or {}
    for f in row.changed_fields or []:
        if f in _QUIET_FIELDS or f == "status":
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
        if b == a:
            continue
        if not b:
            out.append(f"{label} set to {a}")
        elif not a:
            out.append(f"{label} cleared (was {b})")
        else:
            out.append(f"{label} {b} → {a}")
    return out


def _line_words(words: _Words, values: dict) -> str:
    """A line as a sentence names it: its description (or account, item)
    and its amount."""
    label = ""
    for f in _LINE_LABEL_FIELDS:
        if values.get(f):
            label = str(values[f])
            break
    if not label:
        for f in ("item_id", "account_id"):
            if values.get(f):
                label = words.value(f, values[f])
                break
    if len(label) > 80:
        label = label[:77] + "..."
    money = ""
    for f in ("amount", "sell_amount", "scheduled_value"):
        if values.get(f) not in (None, ""):
            money = money_text(values[f])
            break
    if not money:
        debit, credit = values.get("debit"), values.get("credit")
        if debit not in (None, "", "0", "0.00", 0):
            money = f"debit {money_text(debit)}"
        elif credit not in (None, "", "0", "0.00", 0):
            money = f"credit {money_text(credit)}"
    if label and money:
        return f"{label}, {money}"
    return label or money or "a line"


def _number(dt: DocType, values: dict) -> str:
    for f in dt.number_fields:
        if values.get(f) not in (None, ""):
            return str(values[f])
    return ""


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


def _related_sentence(words: _Words, kind: str, row: AuditLog) -> str | None:
    """A related record's row as this document's event."""
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
    for group in _group(own + lines + attachments + [r for _, r in related]):
        first = group[0]
        when = _utc(first.timestamp)
        who = words.who(first.username, first.source)
        heads: list[tuple[str, str]] = []  # (kind, sentence)
        details: list[str] = []
        created = False
        for r in group:
            if r.table_name == dt.table and r.record_id == doc_id:
                if r.action == "INSERT":
                    created = True
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
                    if "status" in changed:
                        after = _status_words(new.get("status"))
                        if after in _VOID_STATUSES:
                            heads.append(("voided", f"Voided by {who}"))
                        elif after and after != _status_words(old.get("status")):
                            heads.append(("status", f"Marked {after} by {who}"))
                    if "is_voided" in changed and new.get("is_voided") is True:
                        heads.append(("voided", f"Voided by {who}"))
                    if "replaced_by_id" in changed and new.get("replaced_by_id"):
                        heads.append(
                            ("replaced", f"Replaced by a corrected copy by {who}")
                        )
                    details.extend(
                        c for c in _changes(words, r) if not c.startswith("Void ")
                    )
            elif r.table_name in line_tables:
                if created and r.action == "INSERT":
                    details.append(f"Line: {_line_words(words, r.new_values or {})}")
                elif r.action == "INSERT":
                    details.append(
                        f"Line added: {_line_words(words, r.new_values or {})}"
                    )
                elif r.action == "DELETE":
                    details.append(
                        f"Line removed: {_line_words(words, r.old_values or {})}"
                    )
                else:
                    moved = _changes(words, r)
                    if moved:
                        line = _line_words(
                            words, {**(r.old_values or {}), **(r.new_values or {})}
                        )
                        details.append(f"Line {line}: " + "; ".join(moved))
            elif r.table_name == "attachments":
                vals = (r.new_values if r.action == "INSERT" else r.old_values) or {}
                name = vals.get("filename") or "a file"
                if r.action == "INSERT":
                    heads.append(("attachment", f"{name} attached by {who}"))
                elif r.action == "DELETE":
                    heads.append(("attachment", f"Attachment {name} removed by {who}"))
            elif r.id in kinds:
                sentence = _related_sentence(words, kinds[r.id], r)
                if sentence:
                    heads.append((kinds[r.id], f"{sentence} by {who}"))
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
        for i, (kind, sentence) in enumerate(heads):
            events.append(
                _Event(
                    when,
                    first.id * 10 + i,
                    who,
                    kind,
                    words.text(sentence) + ".",
                    details if i == primary else [],
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
    from app.services.email_errors import failure_sentence

    if not dt.email_types:
        return []
    rows = (
        words.db.query(EmailLog)
        .filter(EmailLog.entity_type.in_(dt.email_types), EmailLog.entity_id == doc_id)
        .order_by(EmailLog.id)
        .all()
    )
    out = []
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
            details = [failure_sentence(r.error_message or "")]
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
