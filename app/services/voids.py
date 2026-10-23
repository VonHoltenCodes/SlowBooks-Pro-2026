"""What a `<type>_void` reversal actually voided (issue #448).

A void posts a reversing journal entry whose ``source_type`` is
``<type>_void`` and whose ``source_id`` points back at what it reversed.
The codebase keeps two conventions for that pointer, and telling a voided
posting from a live one depends on knowing which:

- **Transaction-keyed** — ``source_id`` is the reversed TRANSACTION's id.
  These are postings that are their own document (an expense, a transfer,
  a cc charge, a journal entry) or reversals of a bare posting row (a
  payment's applied-FX adjustment, a QBO import's cost of goods). Matching
  ``source_id`` against ledger transaction ids is correct for them.
- **Document-keyed** — ``source_id`` is a DOCUMENT's id (a payment, a bill,
  an invoice), and that document's posting is a DIFFERENT transaction id
  (``document.transaction_id``). Matching ``source_id`` against transaction
  ids both misses the real voided posting and can falsely flag an unrelated
  posting whose transaction id happens to equal the document id (#448). We
  must read each document's ``transaction_id``.

``voided_transaction_ids`` is the one place that knows this; callers (the
bank register, reclassify) route through it so a new void type is classified
once, here. ``tests/test_register_void_keys.py`` asserts every ``_void``
source type in ``app/`` is in one of the two sets below, so none can fall
through silently.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.bills import Bill, BillPayment
from app.models.credit_memos import CreditMemo
from app.models.in_kind import InKindGift
from app.models.invoices import Invoice
from app.models.job_costing import JobCost
from app.models.nonprofit import FunctionalAllocation, RestrictionRelease
from app.models.payments import Payment
from app.models.transactions import Transaction
from app.models.vendor_credits import VendorCredit

# source_id already IS the reversed transaction's id — match it directly.
# cc_charge/transfer go through bank_posting.void_document (source_id=txn.id);
# payment_apply_void and qbo_cogs_void reverse a bare posting row whose id is
# a transaction id. (Read at their creation sites, not just the prose in #448.)
TRANSACTION_KEYED_VOIDS = frozenset(
    {
        "expense_void",  # an expense is its own transaction
        "manual_void",
        "bank_entry_void",
        "deposit_void",
        "cc_charge_void",
        "transfer_void",
        "qbo_ledger_void",
        "qbo_journal_void",
        "payment_apply_void",  # reverses a payment's applied-FX posting row
        "qbo_cogs_void",  # reverses a local cost-of-goods posting row
    }
)

# source_id is the DOCUMENT's id; the posting it voided is the document's
# transaction_id. Each entry: (model, id column, transaction-id column).
# All of these models carry a nullable transaction_id (read 2026-10 at #448).
DOCUMENT_KEYED_VOIDS = {
    "payment_void": (Payment, Payment.id, Payment.transaction_id),
    "bill_payment_void": (BillPayment, BillPayment.id, BillPayment.transaction_id),
    "invoice_void": (Invoice, Invoice.id, Invoice.transaction_id),
    "bill_void": (Bill, Bill.id, Bill.transaction_id),
    "credit_memo_void": (CreditMemo, CreditMemo.id, CreditMemo.transaction_id),
    "vendor_credit_void": (VendorCredit, VendorCredit.id, VendorCredit.transaction_id),
    "job_cost_void": (JobCost, JobCost.id, JobCost.transaction_id),
    "in_kind_gift_void": (InKindGift, InKindGift.id, InKindGift.transaction_id),
    "restriction_release_void": (
        RestrictionRelease,
        RestrictionRelease.id,
        RestrictionRelease.transaction_id,
    ),
    "functional_allocation_void": (
        FunctionalAllocation,
        FunctionalAllocation.id,
        FunctionalAllocation.transaction_id,
    ),
}


def voided_transaction_ids(db: Session, txn_ids) -> set[int]:
    """The ids among ``txn_ids`` whose posting a ``<type>_void`` reversal
    voided. Voided-ness is derived from the ledger, never a flag (the
    expenses convention, #448)."""
    ids = [int(i) for i in txn_ids]
    if not ids:
        return set()
    voided: set[int] = set()

    # Transaction-keyed: source_id is a transaction id, match it directly.
    for (sid,) in (
        db.query(Transaction.source_id)
        .filter(
            Transaction.source_type.in_(TRANSACTION_KEYED_VOIDS),
            Transaction.source_id.in_(ids),
        )
        .all()
    ):
        if sid is not None:
            voided.add(sid)

    # Document-keyed: gather each type's voided document ids (one scan), then
    # map those documents to their posting's transaction id — one query per
    # type that actually has a reversal row, so no N+1.
    void_docs: dict[str, set[int]] = defaultdict(set)
    for source_type, sid in (
        db.query(Transaction.source_type, Transaction.source_id)
        .filter(
            Transaction.source_type.in_(DOCUMENT_KEYED_VOIDS.keys()),
            Transaction.source_id.isnot(None),
        )
        .all()
    ):
        void_docs[source_type].add(sid)
    for source_type, doc_ids in void_docs.items():
        _model, id_col, txn_col = DOCUMENT_KEYED_VOIDS[source_type]
        for (tid,) in (
            db.query(txn_col).filter(id_col.in_(doc_ids), txn_col.in_(ids)).all()
        ):
            if tid is not None:
                voided.add(tid)
    return voided
