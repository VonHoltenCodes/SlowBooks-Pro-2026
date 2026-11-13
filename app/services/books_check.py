# ============================================================================
# Books Check (#893) — do these books agree with themselves?
#
# A company file can balance to the cent while a subledger quietly disagrees
# with its control account: the A/R Aging against Accounts Receivable, the
# payments waiting to be deposited against Undeposited Funds, the inventory
# valuation against Inventory. The trial balance cannot see that (the ledger
# is internally consistent, it is just missing or carrying something); only
# tying each control account to its subledger can. This is that tie, one row
# per pair, as of a date, each with the documents behind a difference
# wherever they can be named.
#
# The subledger figures are the existing reports' own (the agings, the
# customer and vendor balances, the pending-deposit list, the inventory
# valuation, the Sales Tax report, the Retainage report), called here rather
# than computed another way, so the check and the report a bookkeeper opens
# next cannot disagree. Control accounts are resolved through
# app/services/control_accounts.py. Nothing is stored; every read computes.
#
# Accrual basis only in this cut: the agings, the deposits and the payable
# figures are accrual figures. Cash-basis pairs are a follow-up.
# ============================================================================

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.bills import Bill, BillPayment, BillPaymentAllocation, BillStatus
from app.models.credit_memos import CreditApplication, CreditMemo, CreditMemoStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.payroll import PayRun, PayRunStatus
from app.models.transactions import Transaction, TransactionLine
from app.models.vendor_credits import (
    VendorCredit,
    VendorCreditApplication,
    VendorCreditStatus,
)
from app.services import control_accounts
from app.services.accounting import _q

ZERO = Decimal("0")
# Documents named per row, at most: a converted file can hold thousands of
# journal entries on Accounts Receivable, and the row says how many more.
MAX_DOCUMENTS = 25

PASS = "pass"
DIFFERENCE = "difference"
NOT_CHECKED = "not_checked"


@dataclass
class Check:
    """One row of the Books Check: a subledger beside its control account."""

    key: str
    name: str
    subledger_label: str
    control_label: str
    subledger: Optional[Decimal]
    control: Optional[Decimal]
    difference: Optional[Decimal]
    # True agrees, False differs, None was not checked (the row says why)
    ok: Optional[bool]
    sentence: str
    documents: list[dict] = field(default_factory=list)
    more_documents: int = 0

    @property
    def status(self) -> str:
        if self.ok is None:
            return NOT_CHECKED
        return PASS if self.ok else DIFFERENCE

    def as_dict(self) -> dict:
        out = asdict(self)
        for k in ("subledger", "control", "difference"):
            out[k] = None if out[k] is None else float(out[k])
        out["status"] = self.status
        return out


# ── helpers ──────────────────────────────────────────────────────────────


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _money(amount) -> str:
    amount = _q(_d(amount))
    return f"-${-amount:,.2f}" if amount < 0 else f"${amount:,.2f}"


def _doc(kind: str, rid, number, amount, why: str) -> dict:
    return {
        "type": kind,
        "id": rid,
        "number": "" if number is None else str(number),
        "amount": None if amount is None else float(_q(_d(amount))),
        "why": why,
    }


def _cap(docs: list[dict]) -> tuple[list[dict], int]:
    return docs[:MAX_DOCUMENTS], max(len(docs) - MAX_DOCUMENTS, 0)


def _balance(
    db: Session, account_ids: Iterable[int], as_of: date, debit_normal: bool
) -> Decimal:
    """The accounts' ledger balance to `as_of`, in their natural direction:
    the figure the Balance Sheet prints for them."""
    ids = [i for i in account_ids if i is not None]
    if not ids:
        return ZERO
    dr, cr = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id.in_(ids), Transaction.date <= as_of)
        .one()
    )
    return _q(_d(dr) - _d(cr) if debit_normal else _d(cr) - _d(dr))


def _account_words(db: Session, account_id: int) -> str:
    from app.services.account_label import label_for

    return label_for(db.get(Account, account_id), db)


def _txn_doc(txn: Transaction, amount, why: str) -> dict:
    """The document a journal transaction belongs to, as a link target: the
    invoice, bill or payment behind it where the transaction has one, else
    the entry itself."""
    st = txn.source_type or ""
    by_source = {
        "invoice": "invoice",
        "invoice_edit": "invoice",
        "bill": "bill",
        "bill_edit": "bill",
        "payment": "payment",
        "bill_payment": "bill_payment",
        "credit_memo": "credit_memo",
        "vendor_credit": "vendor_credit",
        "payroll": "pay_run",
        "job_cost": "job_cost",
        "job_cost_reversal": "job_cost",
    }
    by_txn = {"deposit": "deposit", "expense": "expense", "cc_charge": "cc_charge"}
    if st in by_source and txn.source_id:
        return _doc(by_source[st], txn.source_id, txn.reference or "", amount, why)
    if st in by_txn:
        return _doc(by_txn[st], txn.id, txn.reference or "", amount, why)
    return _doc("journal", txn.id, txn.reference or f"entry {txn.id}", amount, why)


def _row(
    key,
    name,
    sub_label,
    ctl_label,
    sub,
    ctl,
    pass_text,
    diff_text,
    docs_fn,
    t,
) -> Check:
    """A pair's row: PASS with `pass_text`, or the difference with
    `diff_text` (formatted with the figures) and the documents behind it."""
    sub, ctl = _q(sub), _q(ctl)
    diff = _q(sub - ctl)
    if diff == ZERO:
        return Check(
            key, name, sub_label, ctl_label, sub, ctl, diff, True, t.text(pass_text)
        )
    docs, more = _cap(docs_fn() if docs_fn else [])
    sentence = diff_text.format(sub=_money(sub), ctl=_money(ctl), diff=_money(diff))
    return Check(
        key,
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        diff,
        False,
        t.text(sentence),
        docs,
        more,
    )


def _not_checked(key, name, sub_label, ctl_label, sentence, t) -> Check:
    return Check(
        key, name, sub_label, ctl_label, None, None, None, None, t.text(sentence)
    )


def _today_only(key, name, sub_label, ctl_label, what, as_of, t) -> Check:
    return _not_checked(
        key,
        name,
        sub_label,
        ctl_label,
        f"{what} is kept for today only, so it cannot be set beside the "
        f"ledger as of {as_of.isoformat()}. Run Books Check as of today to "
        "compare them.",
        t,
    )


# ── receivables ──────────────────────────────────────────────────────────


def _invoice_drift(db: Session, as_of: date) -> list[dict]:
    """Invoices whose balance due is not their total less what was paid and
    credited on them: the aging reads the balance due, the ledger the
    postings, so a balance that drifted from its documents shows here."""
    paid = dict(
        db.query(PaymentAllocation.invoice_id, func.sum(PaymentAllocation.amount))
        .join(Payment, PaymentAllocation.payment_id == Payment.id)
        .filter(Payment.is_voided.isnot(True))
        .group_by(PaymentAllocation.invoice_id)
        .all()
    )
    credited = dict(
        db.query(CreditApplication.invoice_id, func.sum(CreditApplication.amount))
        .join(CreditMemo, CreditApplication.credit_memo_id == CreditMemo.id)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .group_by(CreditApplication.invoice_id)
        .all()
    )
    out = []
    for inv in (
        db.query(Invoice)
        .filter(
            Invoice.date <= as_of,
            Invoice.status.notin_([InvoiceStatus.VOID, InvoiceStatus.DRAFT]),
        )
        .order_by(Invoice.date, Invoice.id)
    ):
        expected = _q(
            _d(inv.total)
            - _d(inv.retainage_held)
            + _d(inv.retainage_released)
            - _d(paid.get(inv.id))
            - _d(credited.get(inv.id))
        )
        have = _q(_d(inv.balance_due))
        if expected != have:
            out.append(
                _doc(
                    "invoice",
                    inv.id,
                    inv.invoice_number,
                    have - expected,
                    f"Its balance due is {_money(have)}; its total less what "
                    f"was paid and credited on it is {_money(expected)}.",
                )
            )
    return out


def _bill_drift(db: Session, as_of: date) -> list[dict]:
    """Bills whose balance due is not their total less what was paid and
    credited on them (the payable side of _invoice_drift)."""
    paid = dict(
        db.query(BillPaymentAllocation.bill_id, func.sum(BillPaymentAllocation.amount))
        .join(BillPayment, BillPaymentAllocation.bill_payment_id == BillPayment.id)
        .filter(BillPayment.is_voided.isnot(True))
        .group_by(BillPaymentAllocation.bill_id)
        .all()
    )
    credited = dict(
        db.query(
            VendorCreditApplication.bill_id, func.sum(VendorCreditApplication.amount)
        )
        .join(VendorCredit, VendorCreditApplication.vendor_credit_id == VendorCredit.id)
        .filter(VendorCredit.status != VendorCreditStatus.VOID)
        .group_by(VendorCreditApplication.bill_id)
        .all()
    )
    out = []
    for bill in (
        db.query(Bill)
        .filter(
            Bill.date <= as_of,
            Bill.status.notin_([BillStatus.VOID, BillStatus.DRAFT]),
        )
        .order_by(Bill.date, Bill.id)
    ):
        expected = _q(
            _d(bill.total)
            - _d(bill.retainage_held)
            + _d(bill.retainage_released)
            - _d(paid.get(bill.id))
            - _d(credited.get(bill.id))
        )
        have = _q(_d(bill.balance_due))
        if expected != have:
            out.append(
                _doc(
                    "bill",
                    bill.id,
                    bill.bill_number,
                    have - expected,
                    f"Its balance due is {_money(have)}; its total less what "
                    f"was paid and credited on it is {_money(expected)}.",
                )
            )
    return out


def _strays(db: Session, account_id: int, as_of: date, words: str, receivable: bool):
    """The entries on a control account that no document of its subledger
    made and that name no customer (A/R) or vendor (A/P): a hand journal
    entry, a job cost entry, an import's nameless journal line. One that
    names its customer or vendor is that party's item, read by the aging and
    the balances (services/control_journal_lines.py, #897). Amounts are the
    debit less the credit."""
    from app.services.control_journal_lines import payable_lines, receivable_lines

    if receivable:
        lines, party = receivable_lines(db, as_of), "customer"
        fix = "move it to an invoice or a credit memo"
    else:
        lines, party = payable_lines(db, as_of), "vendor"
        fix = (
            f"move it to a bill or to {control_accounts.ACCRUED_JOB_COSTS} "
            "Accrued Job Costs"
        )
    out = []
    for c in lines:
        if c.party_id is not None:
            continue
        txn = c.transaction
        kind = (txn.source_type or "journal entry").replace("_", " ")
        out.append(
            _txn_doc(
                txn,
                c.amount if receivable else -c.amount,
                f"Posted straight to {words} by a {kind} that names no {party}, "
                f"so no {party}'s balance carries it; {fix}.",
            )
        )
    return out


def check_ar_aging(db: Session, as_of: date, t) -> Check:
    """A/R Aging total = Accounts Receivable."""
    from app.routes.reports.receivables import ar_aging_report

    name, sub_label, ctl_label = (
        "A/R Aging = Accounts Receivable",
        "A/R Aging total",
        "Accounts Receivable",
    )
    ar_id = control_accounts.find(db, "1100")
    if ar_id is None:
        return _not_checked(
            "ar_aging",
            name,
            sub_label,
            ctl_label,
            "This chart has no Accounts Receivable account (1100), so there "
            "is nothing to set the A/R Aging beside.",
            t,
        )
    sub = _d(ar_aging_report(db, as_of)["totals"]["total"])
    ctl = _balance(db, [ar_id], as_of, True)
    words = _account_words(db, ar_id)
    return _row(
        "ar_aging",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        f"The A/R Aging total equals {words} as of {as_of.isoformat()}.",
        "The A/R Aging total is {sub} and "
        + words
        + " is {ctl}, {diff} apart. Look at the documents below: an invoice "
        "whose balance due drifted from what was paid and credited on it, or "
        "an entry posted straight to the account (a journal entry, a job cost entry) "
        "that names no customer, which no customer's balance carries. "
        "The Journal checks further down name a voided document still posting "
        "and a posted invoice with no journal entry.",
        lambda: _invoice_drift(db, as_of) + _strays(db, ar_id, as_of, words, True),
        t,
    )


def _routed_customer_balances(db: Session) -> dict[int, Decimal]:
    """Each customer's balance on the A/R Aging row it lands on: a
    sub-customer billed with its parent (#638) adds to the parent's."""
    from app.services.contact_balances import customer_balances
    from app.services.sub_customers import billed_with_parent

    routed = billed_with_parent(db)
    out: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for cid, bal in customer_balances(db).items():
        out[routed.get(cid, cid)] += _d(bal)
    return dict(out)


def check_customer_balances(db: Session, as_of: date, today: date, t) -> Check:
    """The customers' balances add up to Accounts Receivable."""
    from app.routes.reports.receivables import ar_aging_report

    name, sub_label, ctl_label = (
        "Customer balances = Accounts Receivable",
        "Customer balances",
        "Accounts Receivable",
    )
    ar_id = control_accounts.find(db, "1100")
    if ar_id is None:
        return _not_checked(
            "customer_balances",
            name,
            sub_label,
            ctl_label,
            "This chart has no Accounts Receivable account (1100).",
            t,
        )
    if as_of != today:
        return _today_only(
            "customer_balances",
            name,
            sub_label,
            ctl_label,
            "A customer's balance",
            as_of,
            t,
        )
    balances = _routed_customer_balances(db)
    sub = sum(balances.values(), ZERO)
    ctl = _balance(db, [ar_id], as_of, True)
    words = _account_words(db, ar_id)

    def documents():
        rows = ar_aging_report(db, as_of)["items"]
        aged = {r["customer_id"]: _q(_d(r["total"])) for r in rows}
        names = {r["customer_id"]: r["customer_name"] for r in rows}
        out = []
        for cid in sorted(set(aged) | set(balances)):
            bal = _q(balances.get(cid, ZERO))
            if bal != aged.get(cid, ZERO):
                out.append(
                    _doc(
                        "customer",
                        cid,
                        names.get(cid, ""),
                        bal - aged.get(cid, ZERO),
                        f"The customer's balance is {_money(bal)}; their A/R "
                        f"Aging row is {_money(aged.get(cid, ZERO))}.",
                    )
                )
        return out + _invoice_drift(db, as_of) + _strays(db, ar_id, as_of, words, True)

    return _row(
        "customer_balances",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        f"The customers' balances add up to {words}.",
        "The customers' balances add up to {sub} and " + words + " is {ctl}, "
        "{diff} apart. Look at the documents below: a customer whose balance is "
        "not their A/R Aging row, an invoice whose balance due drifted, or an "
        "entry posted straight to the account.",
        documents,
        t,
    )


# ── payables ─────────────────────────────────────────────────────────────


def _credit_side(docs: list[dict]) -> list[dict]:
    """Documents on a credit-normal account: the amount as the account
    carries it (a credit is positive)."""
    return [
        {**d, "amount": None if d["amount"] is None else -d["amount"]} for d in docs
    ]


def check_ap_aging(db: Session, as_of: date, t) -> Check:
    """A/P Aging total = Accounts Payable."""
    from app.routes.reports.payables_tax import ap_aging_report

    name, sub_label, ctl_label = (
        "A/P Aging = Accounts Payable",
        "A/P Aging total",
        "Accounts Payable",
    )
    ap_id = control_accounts.find(db, "2000")
    if ap_id is None:
        return _not_checked(
            "ap_aging",
            name,
            sub_label,
            ctl_label,
            "This chart has no Accounts Payable account (2000), so there is "
            "nothing to set the A/P Aging beside.",
            t,
        )
    sub = _d(ap_aging_report(db, as_of)["totals"]["total"])
    ctl = _balance(db, [ap_id], as_of, False)
    words = _account_words(db, ap_id)
    return _row(
        "ap_aging",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        f"The A/P Aging total equals {words} as of {as_of.isoformat()}.",
        "The A/P Aging total is {sub} and "
        + words
        + " is {ctl}, {diff} apart. Look at the documents below: a bill whose "
        "balance due drifted from what was paid and credited on it, or a "
        "entry posted straight to the account (a journal entry, a job cost entry) "
        "that names no vendor, which no vendor's balance carries. The "
        "Journal checks further down name a voided document still posting and "
        "a posted bill with no journal entry.",
        lambda: _bill_drift(db, as_of)
        + _credit_side(_strays(db, ap_id, as_of, words, False)),
        t,
    )


def check_vendor_balances(db: Session, as_of: date, today: date, t) -> Check:
    """The vendors' balances add up to Accounts Payable."""
    from app.routes.reports.payables_tax import ap_aging_report
    from app.services.contact_balances import vendor_balances

    name, sub_label, ctl_label = (
        "Vendor balances = Accounts Payable",
        "Vendor balances",
        "Accounts Payable",
    )
    ap_id = control_accounts.find(db, "2000")
    if ap_id is None:
        return _not_checked(
            "vendor_balances",
            name,
            sub_label,
            ctl_label,
            "This chart has no Accounts Payable account (2000).",
            t,
        )
    if as_of != today:
        return _today_only(
            "vendor_balances",
            name,
            sub_label,
            ctl_label,
            "A vendor's balance",
            as_of,
            t,
        )
    balances = {vid: _d(b) for vid, b in vendor_balances(db).items()}
    sub = sum(balances.values(), ZERO)
    ctl = _balance(db, [ap_id], as_of, False)
    words = _account_words(db, ap_id)

    def documents():
        rows = ap_aging_report(db, as_of)["items"]
        aged = {r["vendor_id"]: _q(_d(r["total"])) for r in rows}
        names = {r["vendor_id"]: r["vendor_name"] for r in rows}
        out = []
        for vid in sorted(set(aged) | set(balances)):
            bal = _q(balances.get(vid, ZERO))
            if bal != aged.get(vid, ZERO):
                out.append(
                    _doc(
                        "vendor",
                        vid,
                        names.get(vid, ""),
                        bal - aged.get(vid, ZERO),
                        f"The vendor's balance is {_money(bal)}; their A/P "
                        f"Aging row is {_money(aged.get(vid, ZERO))}.",
                    )
                )
        return (
            out
            + _bill_drift(db, as_of)
            + _credit_side(_strays(db, ap_id, as_of, words, False))
        )

    return _row(
        "vendor_balances",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        f"The vendors' balances add up to {words}.",
        "The vendors' balances add up to {sub} and " + words + " is {ctl}, "
        "{diff} apart. Look at the documents below: a vendor whose balance is "
        "not their A/P Aging row, a bill whose balance due drifted, or an "
        "entry posted straight to the account.",
        documents,
        t,
    )


# ── accrued job costs ────────────────────────────────────────────────────

_ACCRUAL_SOURCES = (
    "job_cost",
    "job_cost_void",
    "job_cost_reversal",
    "job_cost_reversal_void",
)


def _accrual_strays(db: Session, acct_id: int, as_of: date) -> list[dict]:
    """Entries on 2010 that no job cost entry made (a hand journal entry, a
    bill line coded to it), credit-positive: what the open accruals cannot
    explain."""
    from app.services.voids import voided_transaction_ids

    rows = (
        db.query(
            Transaction,
            func.coalesce(func.sum(TransactionLine.credit), 0)
            - func.coalesce(func.sum(TransactionLine.debit), 0),
        )
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == acct_id, Transaction.date <= as_of)
        .group_by(Transaction.id)
        .order_by(Transaction.date, Transaction.id)
        .all()
    )
    rows = [
        (t, _q(_d(net)))
        for t, net in rows
        if (t.source_type or "") not in _ACCRUAL_SOURCES
        and not (t.source_type or "").endswith("_void")
        and _q(_d(net)) != ZERO
    ]
    voided = voided_transaction_ids(db, [t.id for t, _ in rows])
    return [
        _txn_doc(
            t,
            net,
            f"Posted to {control_accounts.ACCRUED_JOB_COSTS} Accrued Job Costs by "
            "something other than a job "
            "cost entry, so no accrual reverses it.",
        )
        for t, net in rows
        if t.id not in voided
    ]


def check_accrued_job_costs(db: Session, as_of: date, t) -> Optional[Check]:
    """Open accruals (job cost entries crediting 2010 not yet reversed) =
    2010 Accrued Job Costs (#897). No row for a chart without 2010: nothing
    was ever accrued there."""
    from app.services.accrued_job_costs import account_id, open_accruals

    acct_id = account_id(db)
    if acct_id is None:
        return None
    accruals = open_accruals(db, as_of)
    sub = sum((amount for _, amount in accruals), ZERO)
    ctl = _balance(db, [acct_id], as_of, False)
    words = _account_words(db, acct_id)

    def documents():
        out = []
        for jc, amount in accruals:
            when = (
                f"reverses on {jc.reverses_on.isoformat()}"
                if jc.reverses_on
                else "has no reversal date"
            )
            out.append(
                _doc(
                    "job_cost",
                    jc.id,
                    jc.number,
                    amount,
                    f"Accrued on {jc.date.isoformat()} and not reversed yet; it "
                    f"{when}.",
                )
            )
        return out + _accrual_strays(db, acct_id, as_of)

    return _row(
        "accrued_job_costs",
        "Open accruals = Accrued Job Costs",
        "Open accruals",
        "Accrued Job Costs",
        sub,
        ctl,
        f"The open accrued job costs add up to {words} as of {as_of.isoformat()}.",
        "The open accrued job costs add up to {sub} and " + words + " is {ctl}, "
        "{diff} apart. Look at the entries below: each accrual still open, and "
        "anything posted to the account that is not a job cost entry, which "
        "no accrual reverses.",
        documents,
        t,
    )


# ── Undeposited Funds ────────────────────────────────────────────────────


def check_undeposited_funds(db: Session, as_of: date, today: date, t) -> Check:
    """Payments waiting to be deposited (the Make Deposits list) =
    Undeposited Funds."""
    from app.services.undeposited_funds import (
        customer_money_sources,
        dead_transaction_ids,
        uf_account_id,
        waiting_balance,
    )

    name, sub_label, ctl_label = (
        "Payments to deposit = Undeposited Funds",
        "Waiting to be deposited",
        "Undeposited Funds",
    )
    uf_id = uf_account_id(db)
    if uf_id is None:
        return _not_checked(
            "undeposited_funds",
            name,
            sub_label,
            ctl_label,
            "This chart has no Undeposited Funds account (1200), so no "
            "payment waits for a deposit.",
            t,
        )
    if as_of != today:
        return _today_only(
            "undeposited_funds",
            name,
            sub_label,
            ctl_label,
            "The list of payments waiting to be deposited",
            as_of,
            t,
        )
    sub = waiting_balance(db, uf_id)
    ctl = _balance(db, [uf_id], as_of, True)
    words = _account_words(db, uf_id)

    def documents():
        # A line on 1200 that is not customer money and not a deposit: a
        # bill, expense or card charge saved on it before #785, which the
        # deposit list never offers (#821).
        money = customer_money_sources() | {"deposit"}
        rows = (
            db.query(TransactionLine, Transaction)
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(TransactionLine.account_id == uf_id)
            .order_by(Transaction.date, Transaction.id)
            .all()
        )
        dead = dead_transaction_ids(db, (txn for _, txn in rows))
        out = []
        for ln, txn in rows:
            st = txn.source_type or ""
            if txn.id in dead or st in money or st.endswith("_void"):
                continue
            out.append(
                _txn_doc(
                    txn,
                    _d(ln.debit) - _d(ln.credit),
                    f"Posted to {words} by a document that is not a customer's "
                    "payment, so it is never offered for deposit. Move it to "
                    "the account it belongs on.",
                )
            )
        return out

    return _row(
        "undeposited_funds",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        f"The payments waiting to be deposited add up to {words}.",
        "The payments waiting to be deposited add up to {sub} and "
        + words
        + " is {ctl}, {diff} apart. Look at the lines below, posted to the "
        "account by something other than a customer's payment, and at a "
        "deposit entered by hand that took more than was waiting.",
        documents,
        t,
    )


# ── Inventory ────────────────────────────────────────────────────────────


def check_inventory(db: Session, as_of: date, today: date, t) -> Check:
    """Inventory valuation (every tracked item's quantity × average cost) =
    the ledger's Inventory, the figures the valuation report shows."""
    from app.models.items import InventoryMovement, Item
    from app.services.inventory_service import (
        current_valuation,
        get_inventory_asset_account_id,
    )

    name, sub_label, ctl_label = (
        "Inventory valuation = Inventory",
        "Inventory valuation",
        "Inventory",
    )
    if as_of != today:
        return _today_only(
            "inventory",
            name,
            sub_label,
            ctl_label,
            "The inventory valuation (quantity on hand × average cost)",
            as_of,
            t,
        )
    val = current_valuation(db)
    if val["ledger_balance"] is None:
        return _not_checked(
            "inventory",
            name,
            sub_label,
            ctl_label,
            "This chart has no Inventory account (1300) and no item carries "
            "one of its own, so there is no inventory to tie.",
            t,
        )
    sub, ctl = _d(val["total_value"]), _d(val["ledger_balance"])

    def documents():
        out = []
        tracked = db.query(Item).filter(Item.track_inventory.is_(True)).all()
        for item in tracked:
            last = (
                db.query(InventoryMovement)
                .filter(InventoryMovement.item_id == item.id)
                .order_by(InventoryMovement.id.desc())
                .first()
            )
            qty, avg = _d(item.quantity_on_hand), _d(item.avg_cost)
            if item.is_active is False and qty * avg != 0:
                out.append(
                    _doc(
                        "item",
                        item.id,
                        item.name,
                        qty * avg,
                        f"An inactive item with {qty.normalize()} on hand: the "
                        "valuation leaves inactive items out, the ledger keeps "
                        "their value.",
                    )
                )
            if last is not None and (
                _d(last.balance_qty) != qty or _d(last.balance_avg_cost) != avg
            ):
                moved = _d(last.balance_qty) * _d(last.balance_avg_cost)
                out.append(
                    _doc(
                        "item",
                        item.id,
                        item.name,
                        qty * avg - moved,
                        f"Its quantity × average cost is {_money(qty * avg)}; "
                        f"its movements leave {_money(moved)}.",
                    )
                )
        # Postings on an inventory account that moved no item.
        accounts = {get_inventory_asset_account_id(db, it) for it in tracked}
        inv = control_accounts.find(db, "1300")
        accounts.add(inv)
        accounts.discard(None)
        moved_txns = {
            tid
            for (tid,) in db.query(InventoryMovement.transaction_id).filter(
                InventoryMovement.transaction_id.isnot(None)
            )
        }
        moved_sources = {
            (st, sid)
            for st, sid in db.query(
                InventoryMovement.source_type, InventoryMovement.source_id
            ).filter(InventoryMovement.source_id.isnot(None))
        }
        rows = (
            db.query(
                Transaction,
                func.sum(TransactionLine.debit) - func.sum(TransactionLine.credit),
            )
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .filter(TransactionLine.account_id.in_(accounts))
            .group_by(Transaction.id)
            .order_by(Transaction.date, Transaction.id)
            .all()
        )
        for txn, net in rows:
            st = txn.source_type or ""
            base = st[: -len("_void")] if st.endswith("_void") else st
            if txn.id in moved_txns or (base, txn.source_id) in moved_sources:
                continue
            if st.endswith("_void") or _q(_d(net)) == ZERO:
                continue
            out.append(
                _txn_doc(
                    txn,
                    net,
                    "Posted to an inventory account without moving any item, "
                    "so the ledger changed and the valuation did not.",
                )
            )
        return out

    return _row(
        "inventory",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        "The inventory valuation equals the ledger's Inventory.",
        "The inventory valuation is {sub} and the ledger's Inventory is {ctl}, "
        "{diff} apart. Look at the items and postings below: an item whose "
        "count or average cost drifted from its movements, an inactive item "
        "still holding stock, or a posting to an inventory account that moved "
        "no item.",
        documents,
        t,
    )


# ── Payroll liabilities ──────────────────────────────────────────────────

_PAYROLL_SOURCES = ("payroll", "payroll_void")
# What a pay stub owes someone other than the employee: the withholdings,
# the employer's shares and the deductions process_pay_run credits to the
# payables (2310–2370 and the benefit codes' own liability accounts).
_STUB_LIABILITY_FIELDS = (
    "federal_tax",
    "state_tax",
    "ss_tax",
    "employer_ss_tax",
    "medicare_tax",
    "employer_medicare_tax",
    "futa_tax",
    "suta_tax",
    "state_other_employee",
    "state_other_employer",
    "pretax_deductions",
    "posttax_deductions",
    "garnishments",
)


def run_liabilities(run: PayRun) -> Decimal:
    """What a processed run's stubs say it owes to the agencies and the
    benefit providers: every withholding and employer share (the payables
    process_pay_run posts, by the same stub fields), plus the employer's
    benefit contributions (benefits_engine.gl_groups, which the posting
    reads too). Net pay is not here; a run whose net pay is held on a
    liability adds it separately."""
    from app.services.benefits_engine import gl_groups

    total = sum(
        (_d(getattr(s, f)) for s in run.stubs for f in _STUB_LIABILITY_FIELDS), ZERO
    )
    return _q(total + gl_groups(run).employer_total)


def _payroll_liability_accounts(db: Session) -> list[int]:
    """The liability accounts payroll owes from: every liability a pay run's
    entry ever credited, the payables the Payroll Accounts settings post to
    (their seeded defaults when none is chosen) and 2300 Payroll
    Liabilities. Bank and card accounts never."""
    from app.services.payroll_accounts import PIECES, default_account, posting_accounts
    from app.services.payroll_accounts import PayrollAccountProblem

    ids: set[int] = set()
    ids |= {
        aid
        for (aid,) in db.query(TransactionLine.account_id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(Transaction.source_type == "payroll", TransactionLine.credit > 0)
        .distinct()
    }
    try:
        ids |= {v for v in posting_accounts(db).values() if v}
    except PayrollAccountProblem:
        for piece in PIECES:
            acct = default_account(db, piece)
            if acct is not None:
                ids.add(acct.id)
    # the umbrella the seeded liability pieces sit under (2300 Payroll
    # Liabilities), read from the pieces themselves rather than by number
    umbrellas = {
        n for piece in PIECES if "liability" in piece.kinds for n in piece.numbers[1:]
    }
    for umbrella in db.query(Account).filter(Account.account_number.in_(umbrellas)):
        ids.add(umbrella.id)
    if not ids:
        return []
    return [
        a.id
        for a in db.query(Account).filter(Account.id.in_(ids))
        if a.account_type == AccountType.LIABILITY and not a.bank_kind
    ]


def check_payroll_liabilities(db: Session, as_of: date, t) -> Check:
    """Unpaid payroll liabilities = Payroll Liabilities.

    The app keeps no list of tax deposits against pay runs, so "unpaid" is
    what processed runs accrued on their stubs (dated to `as_of`), plus an
    opening balance entered or imported for the accounts, less what was paid out of the
    accounts since (a check, expense, bill payment or journal entry
    debiting them). The difference is then a run whose entry does not post
    what its stubs say, or something else that credited the accounts."""
    from app.services.voids import voided_transaction_ids

    name, sub_label, ctl_label = (
        "Unpaid payroll liabilities = Payroll Liabilities",
        "Unpaid payroll liabilities",
        "Payroll liability accounts",
    )
    ids = _payroll_liability_accounts(db)
    if not ids:
        return _not_checked(
            "payroll_liabilities",
            name,
            sub_label,
            ctl_label,
            "This chart has no payroll liability account (2300 Payroll "
            "Liabilities or the payables under it), and no pay run has posted.",
            t,
        )
    ctl = _balance(db, ids, as_of, False)

    runs = (
        db.query(PayRun)
        .filter(PayRun.status == PayRunStatus.PROCESSED, PayRun.pay_date <= as_of)
        .order_by(PayRun.pay_date, PayRun.id)
        .all()
    )
    posted_by_txn = dict(
        db.query(
            TransactionLine.transaction_id,
            func.sum(TransactionLine.credit) - func.sum(TransactionLine.debit),
        )
        .filter(
            TransactionLine.account_id.in_(ids),
            TransactionLine.transaction_id.in_(
                [r.transaction_id for r in runs if r.transaction_id]
            ),
        )
        .group_by(TransactionLine.transaction_id)
        .all()
    )
    net_held = dict(
        db.query(TransactionLine.transaction_id, func.sum(TransactionLine.credit))
        .filter(
            TransactionLine.account_id.in_(ids),
            TransactionLine.description == "Net payroll",
            TransactionLine.transaction_id.in_(
                [r.transaction_id for r in runs if r.transaction_id]
            ),
        )
        .group_by(TransactionLine.transaction_id)
        .all()
    )
    accrued = ZERO
    run_docs = []
    for run in runs:
        owed = run_liabilities(run) + _q(_d(net_held.get(run.transaction_id)))
        accrued += owed
        posted = _q(_d(posted_by_txn.get(run.transaction_id)))
        label = f"Pay run {run.pay_date.isoformat()}"
        if not run.transaction_id:
            run_docs.append(
                _doc(
                    "pay_run",
                    run.id,
                    label,
                    owed,
                    "Processed with no journal entry: its stubs owe "
                    f"{_money(owed)} that the ledger never received.",
                )
            )
        elif posted != owed:
            run_docs.append(
                _doc(
                    "pay_run",
                    run.id,
                    label,
                    posted - owed,
                    f"Its stubs owe {_money(owed)} to the agencies and "
                    f"providers; its entry credited the payroll liability "
                    f"accounts {_money(posted)}.",
                )
            )

    # Everything else that moved the accounts, transaction by transaction:
    # a net debit is a payment (out of the subledger), a net credit is
    # something no pay run accrued (named), unless it is an opening balance.
    other = (
        db.query(
            Transaction,
            func.sum(TransactionLine.credit) - func.sum(TransactionLine.debit),
        )
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.account_id.in_(ids),
            Transaction.date <= as_of,
            or_(
                Transaction.source_type.is_(None),
                Transaction.source_type.notin_(_PAYROLL_SOURCES),
            ),
        )
        .group_by(Transaction.id)
        .order_by(Transaction.date, Transaction.id)
        .all()
    )
    voided = voided_transaction_ids(db, [txn.id for txn, _ in other])
    # A balance brought in rather than accrued here: the Opening Balances
    # page's entry, or an import's journal (QuickBooks, IIF, Migrate Data),
    # which carried a converted file's unpaid withholdings with no pay run.
    from app.routes.journal import _journal_sources

    brought_in = {"opening_balance", *_journal_sources()} - {"manual"}
    paid = ZERO
    opening = ZERO
    other_docs = []
    for txn, net in other:
        net = _q(_d(net))
        st = txn.source_type or ""
        if net == ZERO or txn.id in voided or st.endswith("_void"):
            continue
        if net < 0:
            paid += -net
        elif st in brought_in:
            opening += net
        else:
            other_docs.append(
                _txn_doc(
                    txn,
                    net,
                    "Credited a payroll liability account outside a pay run, "
                    "so no stub explains it.",
                )
            )
    sub = _q(accrued + opening - paid)
    return _row(
        "payroll_liabilities",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        "What the processed pay runs still owe the agencies and providers "
        "equals the payroll liability accounts.",
        "What the processed pay runs still owe is {sub} and the payroll "
        "liability accounts hold {ctl}, {diff} apart. Look at the pay runs and "
        "entries below: a run whose entry does not post what its stubs say, or "
        "a journal entry that credited the accounts outside a pay run.",
        lambda: run_docs + other_docs,
        t,
    )


# ── Sales tax ────────────────────────────────────────────────────────────

_SALES_TAX_DOCS = {
    "invoice": "invoice",
    "invoice_edit": "invoice",
    "credit_memo": "credit_memo",
}


def check_sales_tax(db: Session, as_of: date, t) -> Check:
    """Sales tax collected less sales tax paid = Sales Tax Payable, where
    the chart has the account: the Sales Tax report's total tax (every
    invoice and credit memo dated to `as_of`) less the payments its
    ledger check finds, beside the account's balance."""
    from app.routes.reports.payables_tax import sales_tax_report

    name, sub_label, ctl_label = (
        "Sales tax collected less paid = Sales Tax Payable",
        "Collected less paid",
        "Sales Tax Payable",
    )
    tax_id = control_accounts.find(db, "2200")
    if tax_id is None:
        return _not_checked(
            "sales_tax",
            name,
            sub_label,
            ctl_label,
            "This chart has no Sales Tax Payable account (2200), so there is "
            "nothing to tie the tax collected to.",
            t,
        )
    first = db.query(func.min(Transaction.date)).scalar()
    docs_first = min(
        [
            d
            for d in (
                first,
                db.query(func.min(Invoice.date)).scalar(),
                db.query(func.min(CreditMemo.date)).scalar(),
            )
            if d is not None
        ]
        or [as_of]
    )
    start = min(docs_first, as_of)
    data = sales_tax_report(start, as_of, "accrual", db)
    ledger = data["ledger"] or {}
    sub = _d(data["total_tax"]) - _d(ledger.get("payments"))
    ctl = _d(ledger.get("balance"))
    words = _account_words(db, tax_id)

    def documents():
        # each document's tax beside what its own entries put on 2200
        on_2200 = (
            db.query(
                Transaction,
                func.sum(TransactionLine.credit) - func.sum(TransactionLine.debit),
            )
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .filter(TransactionLine.account_id == tax_id, Transaction.date <= as_of)
            .group_by(Transaction.id)
            .all()
        )
        by_doc: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        others = []
        inv_txn = {
            tid: iid
            for iid, tid in db.query(Invoice.id, Invoice.transaction_id).filter(
                Invoice.transaction_id.isnot(None)
            )
        }
        cm_txn = {
            tid: cid
            for cid, tid in db.query(CreditMemo.id, CreditMemo.transaction_id).filter(
                CreditMemo.transaction_id.isnot(None)
            )
        }
        for txn, net in on_2200:
            st = txn.source_type or ""
            if txn.id in inv_txn:
                by_doc[("invoice", inv_txn[txn.id])] += _d(net)
            elif txn.id in cm_txn:
                by_doc[("credit_memo", cm_txn[txn.id])] -= _d(net)
            elif st in _SALES_TAX_DOCS and txn.source_id:
                kind = _SALES_TAX_DOCS[st]
                sign = 1 if kind == "invoice" else -1
                by_doc[(kind, txn.source_id)] += sign * _d(net)
            elif st.endswith("_void") or st == "sales_tax_payment":
                continue
            elif _q(_d(net)) != ZERO:
                others.append(
                    _txn_doc(
                        txn,
                        net,
                        f"Posted to {words} by something other than an invoice, "
                        "a credit memo or a sales tax payment, so the Sales Tax "
                        "report does not count it.",
                    )
                )
        out = []
        for item in data["items"]:
            kind = item["type"]
            model = Invoice if kind == "invoice" else CreditMemo
            number_col = (
                Invoice.invoice_number if kind == "invoice" else CreditMemo.memo_number
            )
            doc = db.query(model).filter(number_col == item["number"]).first()
            if doc is None:
                continue
            tax = abs(_q(_d(item["tax_amount"])))
            posted = _q(by_doc.get((kind, doc.id), ZERO))
            if tax != posted:
                out.append(
                    _doc(
                        kind,
                        doc.id,
                        item["number"],
                        tax - posted,
                        f"It charges {_money(tax)} of tax; its entries put "
                        f"{_money(posted)} on {words}.",
                    )
                )
        return out + others

    return _row(
        "sales_tax",
        name,
        sub_label,
        ctl_label,
        sub,
        ctl,
        f"The sales tax charged less the tax paid equals {words}.",
        "The sales tax charged less the tax paid is {sub} and "
        + words
        + " is {ctl}, {diff} apart. Look at the documents below: an invoice "
        "or credit memo whose tax is not what its entry posted, or something "
        "else posted to the account (tax on a purchase from before 2.18, a "
        "journal entry). The Sales Tax report shows the same check by period.",
        documents,
        t,
    )


# ── Retainage (construction) ─────────────────────────────────────────────


def _retainage_rows(db: Session, as_of: date, t) -> list[Check]:
    """Construction mode: retainage held on pay applications = Retainage
    Receivable; retainage held from subcontractors = Retainage Payable. The
    Retainage report's document rows are the subledger; the ledger amount
    no document explains is its "Journal entries and other" row, which
    names the job."""
    from app.services.retainage_report import (
        PAYABLE_ACCOUNT,
        RECEIVABLE_ACCOUNT,
        retainage_report,
    )

    data = retainage_report(db, as_of)
    out = []
    for side, account_name, key, name, sub_label, held_by in (
        (
            "receivable",
            RECEIVABLE_ACCOUNT,
            "retainage_receivable",
            "Retainage on pay applications = Retainage Receivable",
            "Held by owners",
            "pay applications",
        ),
        (
            "payable",
            PAYABLE_ACCOUNT,
            "retainage_payable",
            "Retainage held from subs = Retainage Payable",
            "Held from subs",
            "subcontractors' bills",
        ),
    ):
        acct = db.query(Account).filter(Account.name == account_name).first()
        if acct is None:
            out.append(
                _not_checked(
                    key,
                    name,
                    sub_label,
                    account_name,
                    f"This chart has no {account_name} account yet (Settings → "
                    "Company Type sets up the construction accounts).",
                    t,
                )
            )
            continue
        rows = data[side]["rows"]
        sub = sum(
            (_d(r["outstanding"]) for r in rows if r.get("kind") != "other"), ZERO
        )
        ctl = _d(data["books"][side])
        words = _account_words(db, acct.id)

        def documents(rows=rows, words=words):
            return [
                _doc(
                    "job",
                    r["job_id"],
                    r["job_name"],
                    r["outstanding"],
                    f"{words} holds this on the job with no pay application or "
                    "bill behind it: a journal entry posted straight to the "
                    "account, or a bill split across jobs.",
                )
                for r in rows
                if r.get("kind") == "other"
            ]

        out.append(
            _row(
                key,
                name,
                sub_label,
                account_name,
                sub,
                ctl,
                f"The retainage on the {held_by} equals {words}.",
                "The retainage on the "
                + held_by
                + " is {sub} and "
                + words
                + " is {ctl}, {diff} apart. Look at the jobs below, and at the "
                "Retainage report's Journal entries and other rows.",
                documents,
                t,
            )
        )
    return out


# ── Journal integrity ────────────────────────────────────────────────────


def check_unbalanced(db: Session, as_of: date, t) -> Check:
    """Every transaction balances: its debits equal its credits."""
    rows = (
        db.query(
            Transaction,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .filter(Transaction.date <= as_of)
        .group_by(Transaction.id)
        .having(
            func.coalesce(func.sum(TransactionLine.debit), 0)
            != func.coalesce(func.sum(TransactionLine.credit), 0)
        )
        .order_by(Transaction.date, Transaction.id)
        .all()
    )
    docs = []
    total = ZERO
    for txn, dr, cr in rows:
        off = _q(_d(dr) - _d(cr))
        if off == ZERO:
            continue
        total += abs(off)
        docs.append(
            _txn_doc(
                txn,
                off,
                f"Its debits are {_money(dr)} and its credits {_money(cr)}.",
            )
        )
    return _integrity(
        "journal_balanced",
        "Every transaction balances",
        docs,
        total,
        "Every transaction's debits equal its credits.",
        "{n} out of balance by {total} in all. Open each below: a transaction "
        "whose debits and credits differ was written outside the app's "
        "posting (an import, a repair by hand) and needs its missing line.",
        t,
    )


def check_missing_accounts(db: Session, as_of: date, t) -> Check:
    """No line sits on an account that no longer exists."""
    rows = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .outerjoin(Account, Account.id == TransactionLine.account_id)
        .filter(Account.id.is_(None), Transaction.date <= as_of)
        .order_by(Transaction.date, Transaction.id)
        .all()
    )
    docs = []
    total = ZERO
    for ln, txn in rows:
        amount = _d(ln.debit) - _d(ln.credit)
        total += abs(amount)
        docs.append(
            _txn_doc(
                txn,
                amount,
                f"A line of {_money(abs(amount))} sits on account id "
                f"{ln.account_id}, which is not in the chart: no report "
                "shows it.",
            )
        )
    return _integrity(
        "journal_accounts",
        "No line on a missing account",
        docs,
        total,
        "Every journal line is on an account in the chart.",
        "{n} on an account no longer in the chart, {total} in all. The "
        "statements leave those lines out; restore the account or move the "
        "lines to one that exists.",
        t,
    )


# kind: (model, is-void condition, number column, source types it posts
# under: its own posting, its edits, its void)
def _voidable():
    return (
        (
            "invoice",
            Invoice,
            Invoice.status == InvoiceStatus.VOID,
            "invoice_number",
        ),
        ("bill", Bill, Bill.status == BillStatus.VOID, "bill_number"),
        (
            "credit_memo",
            CreditMemo,
            CreditMemo.status == CreditMemoStatus.VOID,
            "memo_number",
        ),
        (
            "vendor_credit",
            VendorCredit,
            VendorCredit.status == VendorCreditStatus.VOID,
            "credit_number",
        ),
        ("payment", Payment, Payment.is_voided.is_(True), "reference"),
        ("bill_payment", BillPayment, BillPayment.is_voided.is_(True), "check_number"),
        ("payroll", PayRun, PayRun.status == PayRunStatus.VOID, None),
    )


_DOC_TYPE = {"payroll": "pay_run"}


def check_voided_live(db: Session, as_of: date, t) -> Check:
    """A voided document posts nothing live: its entry and its edits are
    cancelled, account by account, by its void."""
    from app.services.voids import TRANSACTION_KEYED_VOIDS

    docs = []
    total = ZERO
    for kind, model, is_void, number_attr in _voidable():
        voided = db.query(model).filter(is_void).all()
        if not voided:
            continue
        by_id = {d.id: d for d in voided}
        txns = (
            db.query(Transaction)
            .filter(
                or_(
                    Transaction.id.in_(
                        [d.transaction_id for d in voided if d.transaction_id]
                    ),
                    (
                        Transaction.source_type.in_(
                            (kind, f"{kind}_edit", f"{kind}_void")
                        )
                        & Transaction.source_id.in_(list(by_id))
                    ),
                )
            )
            .all()
        )
        owner: dict[int, int] = {}
        for d in voided:
            if d.transaction_id:
                owner[d.transaction_id] = d.id
        for txn in txns:
            if txn.id not in owner and txn.source_id in by_id:
                owner[txn.id] = txn.source_id
        # a reversal of one of those entries keyed by transaction id
        for rev in db.query(Transaction).filter(
            Transaction.source_type.in_(TRANSACTION_KEYED_VOIDS),
            Transaction.source_id.in_(list(owner)),
        ):
            owner.setdefault(rev.id, owner[rev.source_id])
        if not owner:
            continue
        net: dict[int, dict[int, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: ZERO)
        )
        for ln in (
            db.query(TransactionLine)
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(
                TransactionLine.transaction_id.in_(list(owner)),
                Transaction.date <= as_of,
            )
        ):
            net[owner[ln.transaction_id]][ln.account_id] += _d(ln.debit) - _d(ln.credit)
        for doc_id, accounts in net.items():
            live = {a: _q(v) for a, v in accounts.items() if _q(v) != ZERO}
            if not live:
                continue
            amount = sum((abs(v) for v in live.values()), ZERO) / 2
            total += amount
            d = by_id[doc_id]
            number = getattr(d, number_attr, None) if number_attr else None
            if kind == "payroll":
                number = f"Pay run {d.pay_date.isoformat()}"
            docs.append(
                _doc(
                    _DOC_TYPE.get(kind, kind),
                    doc_id,
                    number or doc_id,
                    amount,
                    "It is void, but its postings still leave "
                    + ", ".join(
                        f"{_money(v)} on {_account_words(db, a)}"
                        for a, v in sorted(live.items())
                        if db.get(Account, a) is not None
                    )
                    + ". The void did not reverse all of it.",
                )
            )
    return _integrity(
        "journal_voids",
        "A voided document posts nothing",
        docs,
        total,
        "Every voided document's postings are reversed in full.",
        "{n} voided but still posting, {total} in all. Open each below: its "
        "void left part of its entry standing, so the account still carries "
        "a document that no longer exists.",
        t,
    )


# The payables documents a QuickBooks Online import carries onto 2000 with no
# posting of their own: their balance is the imported history's (#713).
_CARRIED_KINDS = ("bill", "vendor_credit", "bill_payment")


def check_missing_entries(db: Session, as_of: date, t) -> Check:
    """No posted document lacks its journal entry: an invoice, bill,
    payment, bill payment, credit memo, vendor credit or pay run that was
    posted (not a draft, not void, not zero) with no entry, and no payment
    marked deposited by an entry that is not there."""
    from app.services.qbo_export_import import is_carried_payable

    existing = {tid for (tid,) in db.query(Transaction.id)}
    carried_ap = control_accounts.find(db, "2000")

    def lacks(tid):
        return tid is None or tid not in existing

    docs = []
    total = ZERO
    specs = (
        (
            "invoice",
            Invoice,
            "invoice_number",
            "total",
            (Invoice.status.notin_([InvoiceStatus.DRAFT, InvoiceStatus.VOID]),),
            Invoice.date,
        ),
        (
            "bill",
            Bill,
            "bill_number",
            "total",
            (Bill.status.notin_([BillStatus.DRAFT, BillStatus.VOID]),),
            Bill.date,
        ),
        (
            "credit_memo",
            CreditMemo,
            "memo_number",
            "total",
            (
                CreditMemo.status.notin_(
                    [CreditMemoStatus.DRAFT, CreditMemoStatus.VOID]
                ),
            ),
            CreditMemo.date,
        ),
        (
            "vendor_credit",
            VendorCredit,
            "credit_number",
            "total",
            (
                VendorCredit.status.notin_(
                    [VendorCreditStatus.DRAFT, VendorCreditStatus.VOID]
                ),
            ),
            VendorCredit.date,
        ),
        (
            "payment",
            Payment,
            "reference",
            "amount",
            (Payment.is_voided.isnot(True),),
            Payment.date,
        ),
        (
            "bill_payment",
            BillPayment,
            "check_number",
            "amount",
            (BillPayment.is_voided.isnot(True),),
            BillPayment.date,
        ),
    )
    for kind, model, number_attr, amount_attr, conds, date_col in specs:
        for d in db.query(model).filter(*conds, date_col <= as_of):
            amount = _d(getattr(d, amount_attr))
            if amount == ZERO or not lacks(d.transaction_id):
                continue
            if kind in _CARRIED_KINDS and is_carried_payable(db, d, carried_ap):
                continue  # on 2000 by the imported history, by design (#713)
            total += abs(amount)
            docs.append(
                _doc(
                    kind,
                    d.id,
                    getattr(d, number_attr, None) or d.id,
                    amount,
                    "Posted, but it has no journal entry: the ledger never "
                    f"received its {_money(amount)}.",
                )
            )
    for run in db.query(PayRun).filter(
        PayRun.status == PayRunStatus.PROCESSED, PayRun.pay_date <= as_of
    ):
        if lacks(run.transaction_id) and _d(run.total_gross) != ZERO:
            total += abs(_d(run.total_gross))
            docs.append(
                _doc(
                    "pay_run",
                    run.id,
                    f"Pay run {run.pay_date.isoformat()}",
                    run.total_gross,
                    "Processed, but it has no journal entry.",
                )
            )
    # a payment line marked deposited by an entry that is not there
    for ln, txn in (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.deposit_transaction_id.isnot(None),
            Transaction.date <= as_of,
        )
    ):
        if ln.deposit_transaction_id in existing:
            continue
        total += abs(_d(ln.debit))
        docs.append(
            _txn_doc(
                txn,
                ln.debit,
                f"Marked deposited by entry {ln.deposit_transaction_id}, which "
                "is not in the ledger, so it is neither waiting nor in the bank.",
            )
        )
    return _integrity(
        "journal_entries",
        "No posted document without its entry",
        docs,
        total,
        "Every posted document has its journal entry.",
        "{n} posted with no journal entry, {total} in all. The reports that "
        "read the documents count them and the ledger does not; open each "
        "below and save it again, or void it.",
        t,
    )


def _integrity(key, name, docs, total, pass_text, diff_text, t) -> Check:
    if not docs:
        return Check(key, name, "", "", None, None, ZERO, True, t.text(pass_text))
    shown, more = _cap(docs)
    n = len(docs)
    noun = "One document is" if n == 1 else f"{n} documents are"
    sentence = diff_text.format(n=noun, total=_money(total))
    return Check(
        key, name, "", "", None, None, _q(total), False, t.text(sentence), shown, more
    )


# ── the report ───────────────────────────────────────────────────────────


def books_check(db: Session, as_of: Optional[date] = None) -> dict:
    """Every pair, as of a date (today by default): the rows, how many
    differ, and the sentence the dashboard widget shows."""
    from app.services.terminology import terms_from_db

    today = date.today()
    as_of = as_of or today
    t = terms_from_db(db)
    rows = [
        check_ar_aging(db, as_of, t),
        check_customer_balances(db, as_of, today, t),
        check_ap_aging(db, as_of, t),
        check_vendor_balances(db, as_of, today, t),
        check_undeposited_funds(db, as_of, today, t),
        check_inventory(db, as_of, today, t),
        check_payroll_liabilities(db, as_of, t),
        check_sales_tax(db, as_of, t),
    ]
    accrued = check_accrued_job_costs(db, as_of, t)
    if accrued is not None:
        rows.append(accrued)
    if t.is_construction:
        rows += _retainage_rows(db, as_of, t)
    rows += [
        check_unbalanced(db, as_of, t),
        check_missing_accounts(db, as_of, t),
        check_voided_live(db, as_of, t),
        check_missing_entries(db, as_of, t),
    ]
    for r in rows:
        r.name = t.text(r.name)
        r.subledger_label = t.text(r.subledger_label)
        r.control_label = t.text(r.control_label)
    differences = sum(1 for r in rows if r.ok is False)
    return {
        "as_of": as_of.isoformat(),
        "basis": "accrual",
        "rows": [r.as_dict() for r in rows],
        "differences": differences,
        "checked": sum(1 for r in rows if r.ok is not None),
        "summary": summary_sentence(differences, as_of, today),
    }


def summary_sentence(differences: int, as_of: date, today: date) -> str:
    """The dashboard card's sentence: "Your books agree with themselves as
    of today", or "2 differences — open Books Check"."""
    when = "today" if as_of == today else as_of.isoformat()
    if not differences:
        return f"Your books agree with themselves as of {when}."
    noun = "difference" if differences == 1 else "differences"
    on = "" if as_of == today else f" as of {when}"
    return f"{differences} {noun}{on} — open Books Check."
