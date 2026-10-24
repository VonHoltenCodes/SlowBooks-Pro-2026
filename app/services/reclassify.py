"""Reclassify (#337): move many lines to another account, or class, at once.

Catching up a client's books means parking the unfamiliar in a holding
account ("Other expenses") and moving each item once the client answers.
Each move was an edit of one transaction. This tool does them together:
pick an account and a period, see its lines, tick the ones to move, choose
the account they belong on (and a class), preview, then post the move as
one audited change, every moved line listed in the result.

What moves: a line the bookkeeper chose the account for. A journal entry's
lines, a register entry's, a check's, a card charge's, an expense's, a
bill's expense lines (the bill's own line follows, so the bill and its
posting agree), an import's. What stays: the bank side of an entry (the
register's), Accounts Receivable and Payable (the documents'), a line whose
account comes from the item on an invoice, a sales receipt or a credit
memo, payroll, depreciation and the system's own postings, and anything
voided. Each line says why when it will not move.

A move dated into a closed period takes the closing-date password, like
any other change there, and shows on the Closing Date Exceptions report.
The audit log carries one event for the move, naming every line, and the
change to each line.

The payee filter (#394): an unrecognized vendor is parked in Other
expenses until the client says what it was, so the list takes a vendor
(or a payee name) beside the account and period and shows that one's
lines. Each line's payee reads the same as the bank register's (reusing
``bank_register.payees_for``): a bill's or expense's vendor, else the
payee read off a bank entry, check or card charge; a journal entry has
none. A vendor chosen as the filter matches a line that names the vendor,
or one whose resolved payee contains the vendor's name (case aside), so
the bank-feed spelling (HOME DEPOT #4521 JOLIET IL) and the check memos
(Home Depot — lumber, Check 1042 - Home Depot) come along, not the exact
name alone.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.bills import Bill, BillLine
from app.models.classes import TxnClass
from app.models.contacts import Vendor
from app.models.transactions import Transaction, TransactionLine

# The documents and postings whose account was chosen by hand: these lines
# move. Journals of their own (hand-typed, imported, migrated) are listed by
# app.routes.journal._journal_sources at call time.
REGISTER_SOURCES = frozenset(
    {"bank_entry", "check", "cc_charge", "expense", "accountants_copy", "qbo_ledger"}
)
BILL_SOURCES = frozenset({"bill", "bill_edit"})

SOURCE_LABELS = {
    "manual": "Journal entry",
    "qbo_journal": "Journal entry",
    "iif_import": "Imported journal",
    "qb_ledger_import": "Imported journal",
    "qbo_ledger": "Imported entry",
    "opening_balance": "Opening balance",
    "item_opening": "Opening inventory",
    "accountants_copy": "Accountant's entry",
    "bank_entry": "Bank entry",
    "check": "Check",
    "cc_charge": "Card charge",
    "expense": "Expense",
    "bill": "Bill",
    "bill_edit": "Bill",
    "bill_payment": "Bill payment",
    "invoice": "Invoice",
    "invoice_edit": "Invoice",
    "payment": "Payment",
    "deposit": "Deposit",
    "credit_memo": "Credit memo",
    "vendor_credit": "Vendor credit",
    "transfer": "Transfer",
    "payroll": "Payroll",
    "depreciation": "Depreciation",
    "asset_acquisition": "Fixed asset",
    "sales_tax_payment": "Sales tax payment",
    "late_fee": "Late fee",
    "adjustment": "Inventory adjustment",
}


def source_label(source_type: str | None) -> str:
    source = source_type or ""
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    if source.endswith("_void"):
        return source_label(source[: -len("_void")]) + " (void)"
    if source.endswith("_import"):
        return "Migrated entry"
    return source.replace("_", " ").capitalize() or "Entry"


def _journal_sources() -> set[str]:
    from app.routes.journal import _journal_sources as sources

    return set(sources())


def _control_ids(db: Session) -> dict[int, str]:
    """Accounts Receivable and Payable, by id: the documents' side."""
    from app.services.accounting import get_ap_account_id, get_ar_account_id

    found = {}
    for finder, name in (
        (get_ar_account_id, "Accounts Receivable"),
        (get_ap_account_id, "Accounts Payable"),
    ):
        try:
            found[finder(db)] = name
        except Exception:
            continue
    return found


def account_words(acct: Account | None) -> str:
    if acct is None:
        return ""
    number = (acct.account_number or "").strip()
    return f"{number} {acct.name}".strip()


def _account_out(acct: Account | None) -> dict | None:
    if acct is None:
        return None
    return {
        "id": acct.id,
        "account_number": acct.account_number,
        "name": acct.name,
        "label": account_words(acct),
    }


def _class_out(cls: TxnClass | None) -> dict | None:
    if cls is None:
        return None
    return {"id": cls.id, "name": cls.name}


def _voided_ids(db: Session, txn_ids: list[int]) -> set[int]:
    """The transactions among these that a `<type>_void` reversal voided.
    Routed through app/services/voids.py so a document-keyed void (a payment,
    a bill) is told from a transaction-keyed one (#448); reversals themselves
    are caught separately by the endswith("_void") check in verdict()."""
    from app.services.voids import voided_transaction_ids

    return voided_transaction_ids(db, txn_ids)


def _bill_lines_by_bill(db: Session, bill_ids: set[int]) -> dict[int, list[BillLine]]:
    if not bill_ids:
        return {}
    out: dict[int, list[BillLine]] = {}
    for ln in (
        db.query(BillLine)
        .filter(BillLine.bill_id.in_(bill_ids))
        .order_by(BillLine.line_order, BillLine.id)
        .all()
    ):
        out.setdefault(ln.bill_id, []).append(ln)
    return out


def _paired_bill_line(
    txn: Transaction, line: TransactionLine, bill_lines
) -> BillLine | None:
    """The bill's own line behind a posted line: the k-th bill line on the
    account for the k-th posted line on it (a bill posts its lines in
    order, each to its account)."""
    on_account = [
        ln
        for ln in sorted(txn.lines, key=lambda x: x.id)
        if ln.account_id == line.account_id
    ]
    try:
        k = on_account.index(line)
    except ValueError:
        return None
    candidates = [bl for bl in bill_lines if bl.account_id == line.account_id]
    return candidates[k] if k < len(candidates) else None


class _Context:
    """What every line's verdict needs, loaded once."""

    def __init__(self, db: Session, lines: list[TransactionLine]):
        self.db = db
        self.journal_sources = _journal_sources()
        self.control = _control_ids(db)
        txn_ids = sorted({ln.transaction_id for ln in lines})
        self.voided = _voided_ids(db, txn_ids)
        bill_ids = {
            ln.transaction.source_id
            for ln in lines
            if (ln.transaction.source_type or "") in BILL_SOURCES
            and ln.transaction.source_id
        }
        self.bills = (
            {b.id: b for b in db.query(Bill).filter(Bill.id.in_(bill_ids)).all()}
            if bill_ids
            else {}
        )
        self.bill_lines = _bill_lines_by_bill(db, bill_ids)
        vendor_ids = {b.vendor_id for b in self.bills.values() if b.vendor_id} | {
            ln.transaction.source_id
            for ln in lines
            if (ln.transaction.source_type or "") == "expense"
            and ln.transaction.source_id
        }
        self.vendors = (
            {v.id: v for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids)).all()}
            if vendor_ids
            else {}
        )
        # The payee each line shows reads the same as the bank register's,
        # so the two never disagree (reusing payees_for; an IIF card charge
        # with no "CC Charge:" prefix keeps its payee instead of going blank).
        from app.services.bank_register import payees_for

        txns = list({ln.transaction.id: ln.transaction for ln in lines}.values())
        self.payee_names = payees_for(db, txns) if txns else {}

    def verdict(
        self, line: TransactionLine
    ) -> tuple[bool, str | None, BillLine | None]:
        """(movable, reason when not, the bill line that follows)."""
        txn = line.transaction
        source = txn.source_type or ""
        acct = line.account
        if acct is not None and acct.bank_kind:
            return False, "the bank side of the entry: reclassify its other side", None
        if line.account_id in self.control:
            return (
                False,
                f"{self.control[line.account_id]} is the document's side",
                None,
            )
        if source.endswith("_void") or txn.id in self.voided:
            return False, "voided", None
        if source in self.journal_sources or source in REGISTER_SOURCES:
            return True, None, None
        if source in BILL_SOURCES:
            bill_lines = self.bill_lines.get(txn.source_id or 0, [])
            paired = _paired_bill_line(txn, line, bill_lines)
            if paired is None:
                return (
                    False,
                    "this line's account comes from the bill's item; edit the bill",
                    None,
                )
            return True, None, paired
        label = source_label(source).lower()
        return (
            False,
            f"a {label}'s account comes from the document; edit the document",
            None,
        )

    def document(self, txn: Transaction) -> str | None:
        source = txn.source_type or ""
        if source in BILL_SOURCES:
            bill = self.bills.get(txn.source_id or 0)
            if bill is not None:
                return f"Bill {bill.bill_number}"
        return None

    def payee(self, txn: Transaction) -> tuple[int | None, str]:
        """(vendor id, payee name): the vendor behind a bill or expense,
        else the name the bank register reads for the entry (payees_for);
        (None, "") for a journal entry and the rest. A journal has no payee
        in the register's sense, so it stays blank here too."""
        source = txn.source_type or ""
        if source in BILL_SOURCES:
            bill = self.bills.get(txn.source_id or 0)
            vendor = self.vendors.get(bill.vendor_id) if bill is not None else None
            return (vendor.id, vendor.name) if vendor is not None else (None, "")
        if source == "expense":
            # the vendor is an expense's only linked record (its source_id);
            # the name (vendor, else the payee typed on it) comes from the
            # register's own lookup so the two columns match
            vendor = self.vendors.get(txn.source_id or 0)
            vendor_id = vendor.id if vendor is not None else None
            return vendor_id, self.payee_names.get(txn.id, "")
        if source in ("cc_charge", "bank_entry", "check"):
            return None, self.payee_names.get(txn.id, "")
        return None, ""


def _line_out(ctx: _Context, line: TransactionLine, classes: dict) -> dict:
    txn = line.transaction
    movable, reason, _ = ctx.verdict(line)
    cls_id = line.class_id if line.class_id is not None else txn.class_id
    debit = Decimal(line.debit or 0)
    credit = Decimal(line.credit or 0)
    vendor_id, payee = ctx.payee(txn)
    return {
        "id": line.id,
        "transaction_id": txn.id,
        "date": txn.date.isoformat(),
        "source_type": txn.source_type,
        "source_label": source_label(txn.source_type),
        "document": ctx.document(txn),
        "reference": txn.reference,
        "vendor_id": vendor_id,
        "payee": payee,
        "description": line.description or txn.description or "",
        "debit": str(debit),
        "credit": str(credit),
        "amount": str(debit - credit),
        "class_id": cls_id,
        "class_name": classes.get(cls_id).name if classes.get(cls_id) else None,
        "class_own": line.class_id is not None,
        "movable": movable,
        "reason": reason,
    }


def lines_on_account(
    db: Session,
    account_id: int,
    start_date: date,
    end_date: date,
    vendor_id: int | None = None,
    payee: str | None = None,
) -> dict:
    """The account's lines in the period, each with whether it can move;
    with ``vendor_id`` only that vendor's, with ``payee`` only those whose
    payee name contains it (case does not matter)."""
    acct = db.get(Account, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="From is after To.")
    vendor = db.get(Vendor, vendor_id) if vendor_id is not None else None
    if vendor_id is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    payee = (payee or "").strip()
    lines = (
        db.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.account_id == account_id,
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        )
        .order_by(Transaction.date, Transaction.id, TransactionLine.id)
        .all()
    )
    ctx = _Context(db, lines)
    classes = {c.id: c for c in db.query(TxnClass).all()}
    out = [_line_out(ctx, ln, classes) for ln in lines]
    if vendor is not None:
        # a contains match on the resolved payee, so the bank-feed spelling
        # (HOME DEPOT #4521 JOLIET IL), an IIF check memo (Home Depot —
        # lumber) and a QB report check (Check 1042 - Home Depot) all come
        # along with vendor "Home Depot", not the exact name alone. Whole
        # words only, so vendor "Ace" doesn't take "Space Coast Supply"'s.
        wanted = re.compile(
            r"(?<!\w)" + re.escape(vendor.name.strip()) + r"(?!\w)", re.IGNORECASE
        )
        out = [
            ln
            for ln in out
            if ln["vendor_id"] == vendor.id or wanted.search(ln["payee"] or "")
        ]
    if payee:
        out = [ln for ln in out if payee.casefold() in ln["payee"].casefold()]
    movable = [ln for ln in out if ln["movable"]]
    return {
        "account": _account_out(acct),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "vendor": (
            {"id": vendor.id, "name": vendor.name} if vendor is not None else None
        ),
        "payee": payee or None,
        "lines": out,
        "count": len(out),
        "movable": len(movable),
        "movable_total": str(
            sum((Decimal(ln["amount"]) for ln in movable), Decimal("0"))
        ),
    }


def _target_account(db: Session, account_id: int | None) -> Account | None:
    if account_id is None:
        return None
    acct = db.get(Account, account_id)
    if acct is None:
        raise HTTPException(
            status_code=404, detail="The account to move to was not found."
        )
    if acct.is_active is False:
        raise HTTPException(
            status_code=400,
            detail=f"{account_words(acct)} is inactive; pick an active account.",
        )
    if acct.bank_kind:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{account_words(acct)} is a bank or card account: money moves "
                "through the register, not by reclassifying."
            ),
        )
    control = _control_ids(db)
    if acct.id in control:
        raise HTTPException(
            status_code=400,
            detail=f"{control[acct.id]} is the documents' account; lines are not moved onto it.",
        )
    return acct


def _target_class(db: Session, class_id: int | None) -> TxnClass | None:
    if class_id is None:
        return None
    cls = db.get(TxnClass, class_id)
    if cls is None:
        raise HTTPException(
            status_code=404, detail="The class to move to was not found."
        )
    if cls.is_archived:
        raise HTTPException(
            status_code=400,
            detail=f"The class {cls.name} is archived; pick a live one.",
        )
    return cls


def reclassify(
    db: Session,
    line_ids: list[int],
    *,
    account_id: int | None,
    class_id: int | None,
    clear_class: bool = False,
    dry_run: bool = True,
) -> dict:
    """Move the lines: ``account_id`` is the account they go to (None
    leaves the account), ``class_id`` the class (None leaves it,
    ``clear_class`` removes the line's own). ``dry_run`` answers with the
    plan and changes nothing."""
    if account_id is None and class_id is None and not clear_class:
        raise HTTPException(
            status_code=400,
            detail="Choose an account to move the lines to, or a class.",
        )
    if class_id is not None and clear_class:
        raise HTTPException(
            status_code=400, detail="Choose a class, or no class, not both."
        )
    to_account = _target_account(db, account_id)
    to_class = _target_class(db, class_id)
    wanted = list(dict.fromkeys(int(i) for i in line_ids))
    lines = (
        db.query(TransactionLine).filter(TransactionLine.id.in_(wanted)).all()
        if wanted
        else []
    )
    by_id = {ln.id: ln for ln in lines}
    ctx = _Context(db, lines)
    classes = {c.id: c for c in db.query(TxnClass).all()}

    moved, skipped = [], []
    for line_id in wanted:
        line = by_id.get(line_id)
        if line is None:
            skipped.append({"line_id": line_id, "reason": "not found"})
            continue
        movable, reason, bill_line = ctx.verdict(line)
        if not movable:
            skipped.append({"line_id": line_id, "reason": reason})
            continue
        txn = line.transaction
        same_account = to_account is None or to_account.id == line.account_id
        cls_now = line.class_id if line.class_id is not None else txn.class_id
        if clear_class:
            same_class = line.class_id is None
        elif to_class is None:
            same_class = True
        else:
            same_class = line.class_id == to_class.id
        if same_account and same_class:
            skipped.append({"line_id": line_id, "reason": "already there"})
            continue
        from_account = line.account
        from_class = classes.get(cls_now)
        after_class = (
            None if clear_class else (to_class if to_class is not None else from_class)
        )
        moved.append(
            {
                "line_id": line.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "source_label": source_label(txn.source_type),
                "document": ctx.document(txn),
                "reference": txn.reference,
                "description": line.description or txn.description or "",
                "amount": str(Decimal(line.debit or 0) - Decimal(line.credit or 0)),
                "from_account": _account_out(from_account),
                "to_account": _account_out(
                    to_account if not same_account else from_account
                ),
                "from_class": _class_out(from_class),
                "to_class": _class_out(after_class),
                "document_line_follows": bill_line is not None,
                "_line": line,
                "_bill_line": bill_line,
            }
        )

    result = {
        "dry_run": dry_run,
        "to_account": _account_out(to_account),
        "to_class": _class_out(to_class),
        "clear_class": clear_class,
        "count": len(moved),
        "moved": [{k: v for k, v in m.items() if not k.startswith("_")} for m in moved],
        "skipped": skipped,
    }
    if dry_run or not moved:
        return result

    # Every date once: a move into a closed period takes the password, and
    # is refused whole without it, before any line changes.
    from app.services.closing_date import check_closing_date

    for day in sorted({m["_line"].transaction.date for m in moved}):
        check_closing_date(db, day)

    # The stored balances follow the lines (#490): each moved line comes
    # off the account it leaves and goes onto the one it lands on, in each
    # account's own direction (an expense line moved onto an income account
    # reads as negative income there), applied as one atomic update per
    # account after the lines are flushed. The ledger reports read the
    # lines; the Chart of Accounts, the accounts export and the AI tools
    # read this total, and used to keep showing the old account.
    from app.services.accounting import apply_balance_deltas, natural_delta

    balance_deltas: dict = {}
    for m in moved:
        line, bill_line = m["_line"], m["_bill_line"]
        if to_account is not None and line.account_id != to_account.id:
            from_account = line.account
            balance_deltas[from_account.id] = balance_deltas.get(
                from_account.id, Decimal("0")
            ) - natural_delta(from_account, line.debit, line.credit)
            balance_deltas[to_account.id] = balance_deltas.get(
                to_account.id, Decimal("0")
            ) + natural_delta(to_account, line.debit, line.credit)
            line.account_id = to_account.id
            if bill_line is not None:
                bill_line.account_id = to_account.id
        if clear_class:
            line.class_id = None
            if bill_line is not None:
                bill_line.class_id = None
        elif to_class is not None:
            line.class_id = to_class.id
            if bill_line is not None:
                bill_line.class_id = to_class.id
    db.flush()
    apply_balance_deltas(db, balance_deltas)

    # One event for the move, every line named, beside the change the
    # audit hook writes for each line.
    from app.services.audit import log_event

    from_accounts = sorted(
        {m["from_account"]["label"] for m in moved if m["from_account"]}
    )
    log_event(
        db,
        table_name="reclassify",
        record_id=moved[0]["line_id"],
        action="UPDATE",
        old_values={"accounts": from_accounts},
        new_values={
            "account": _account_out(to_account),
            "class": "cleared" if clear_class else _class_out(to_class),
            "lines": [m["line_id"] for m in moved],
            "transactions": sorted({m["transaction_id"] for m in moved}),
            "count": len(moved),
        },
        changed_fields=(["account_id"] if to_account is not None else [])
        + (["class_id"] if (to_class is not None or clear_class) else []),
        source="reclassify",
    )
    db.commit()
    return result
