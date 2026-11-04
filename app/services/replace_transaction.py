# ============================================================================
# Void-and-replace (#689): changing a posted transaction that has no edit
# of its own. An expense, a card charge, a transfer, a credit memo, a vendor
# credit, a customer payment and a bill payment are fixed by voiding them
# and entering them again; from a report's drill-down the two happen as
# one: the original is voided through its own void (the same refusals, in
# the same sentences: a reconciled line, a closed period, a payment whose
# discount or application would be left behind), the new one is posted
# through its own create with the edited fields, and each names the other
# (replaces_id / replaced_by_id, and an audit-log entry naming both). One
# database transaction: a refusal on either side leaves the books as they
# were.
# ============================================================================

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.audit import log_event


@dataclass(frozen=True)
class Kind:
    """One replaceable kind: its words, the table its record is in, the
    void and create it goes through, and how a create's answer names the
    new record."""

    word: str
    table: str
    model: Callable[[], Any]
    void: Callable[[Session, int], Any]
    create: Callable[..., Any]
    new_id: Callable[[Any], int]


def _txn_model():
    from app.models.transactions import Transaction

    return Transaction


def _void_expense(db, rid):
    from app.routes.expenses import void_expense

    return void_expense(rid, db)


def _create_expense(db, data, allow_duplicate_check_number=False):
    from app.routes.expenses import create_expense

    return create_expense(
        data, db, allow_duplicate_check_number=allow_duplicate_check_number
    )


def _void_cc(db, rid):
    from app.routes.cc_charges import void_cc_charge

    return void_cc_charge(rid, db)


def _create_cc(db, data, allow_duplicate_check_number=False):
    from app.routes.cc_charges import create_cc_charge

    return create_cc_charge(data, db)


def _void_transfer(db, rid):
    from app.routes.transfers import void_transfer

    return void_transfer(rid, db)


def _create_transfer(db, data, allow_duplicate_check_number=False):
    from app.routes.transfers import create_transfer

    return create_transfer(data, db)


def _void_cm(db, rid):
    from app.routes.credit_memos import void_credit_memo

    return void_credit_memo(rid, db)


def _create_cm(db, data, allow_duplicate_check_number=False):
    from app.routes.credit_memos import create_credit_memo

    return create_credit_memo(data, db)


def _void_vc(db, rid):
    from app.routes.vendor_credits import void_vendor_credit

    return void_vendor_credit(rid, db)


def _create_vc(db, data, allow_duplicate_check_number=False):
    from app.routes.vendor_credits import create_vendor_credit

    return create_vendor_credit(data, db)


def _void_payment(db, rid):
    from app.routes.payments import void_payment

    # The new payment posts its own processing fee, so the original's is
    # taken back with it: the fee is booked once, as before.
    return void_payment(rid, fee_refunded=True, db=db)


def _create_payment(db, data, allow_duplicate_check_number=False):
    from app.routes.payments import create_payment

    return create_payment(data, db, request=None, response=None)


def _void_bill_payment(db, rid):
    from app.routes.bill_payments import void_bill_payment

    return void_bill_payment(rid, db)


def _create_bill_payment(db, data, allow_duplicate_check_number=False):
    from app.routes.bill_payments import create_bill_payment

    return create_bill_payment(
        data,
        db,
        request=None,
        response=None,
        allow_duplicate_check_number=allow_duplicate_check_number,
    )


def _model(path, name):
    def load():
        import importlib

        return getattr(importlib.import_module(path), name)

    return load


def _attr_id(resp) -> int:
    return int(resp["id"] if isinstance(resp, dict) else resp.id)


KINDS: dict[str, Kind] = {
    "expense": Kind(
        "expense", "transactions", _txn_model, _void_expense, _create_expense, _attr_id
    ),
    "cc_charge": Kind(
        "card charge",
        "transactions",
        _txn_model,
        _void_cc,
        _create_cc,
        lambda r: int(r["transaction_id"]),
    ),
    "transfer": Kind(
        "transfer",
        "transactions",
        _txn_model,
        _void_transfer,
        _create_transfer,
        _attr_id,
    ),
    "credit_memo": Kind(
        "credit memo",
        "credit_memos",
        _model("app.models.credit_memos", "CreditMemo"),
        _void_cm,
        _create_cm,
        _attr_id,
    ),
    "vendor_credit": Kind(
        "vendor credit",
        "vendor_credits",
        _model("app.models.vendor_credits", "VendorCredit"),
        _void_vc,
        _create_vc,
        _attr_id,
    ),
    "payment": Kind(
        "payment",
        "payments",
        _model("app.models.payments", "Payment"),
        _void_payment,
        _create_payment,
        _attr_id,
    ),
    "bill_payment": Kind(
        "bill payment",
        "bill_payments",
        _model("app.models.bills", "BillPayment"),
        _void_bill_payment,
        _create_bill_payment,
        _attr_id,
    ),
}


@contextmanager
def _one_commit(db: Session):
    """The void's and the create's own commits held back as flushes, so
    the two land together or not at all. The routes commit as they always
    do; here a commit only writes ahead, and the caller commits once."""
    db.commit = db.flush  # shadows Session.commit on this session only
    try:
        yield
    except BaseException:
        del db.commit
        db.rollback()
        raise
    del db.commit


def replace(
    db: Session,
    kind: str,
    original_id: int,
    data,
    allow_duplicate_check_number: bool = False,
):
    """Void `original_id` of `kind` and post `data` (that kind's create
    payload) in its place. Returns the create's own answer, with
    `replaces_id` set on it."""
    spec = KINDS[kind]
    model = spec.model()
    original = db.get(model, original_id)
    if original is not None and getattr(original, "replaced_by_id", None):
        raise HTTPException(
            status_code=400,
            detail=(
                f"This {spec.word} was already replaced by "
                f"{spec.word} #{original.replaced_by_id}; edit that one."
            ),
        )
    with _one_commit(db):
        spec.void(db, original_id)
        resp = spec.create(
            db, data, allow_duplicate_check_number=allow_duplicate_check_number
        )
        new_id = spec.new_id(resp)
        original = db.get(model, original_id)
        new = db.get(model, new_id)
        original.replaced_by_id = new_id
        new.replaces_id = original_id
        log_event(
            db,
            spec.table,
            original_id,
            "REPLACE",
            old_values={"id": original_id},
            new_values={
                "kind": kind,
                "replaces_id": original_id,
                "replaced_by_id": new_id,
                "note": (
                    f"{spec.word.capitalize()} #{original_id} voided and "
                    f"replaced by {spec.word} #{new_id}"
                ),
            },
            changed_fields=["replaced_by_id"],
        )
        db.flush()
    db.commit()
    if isinstance(resp, dict):
        resp["replaces_id"] = original_id
    elif hasattr(resp, "replaces_id"):
        resp.replaces_id = original_id
    return resp
