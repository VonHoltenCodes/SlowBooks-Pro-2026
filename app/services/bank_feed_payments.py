# ============================================================================
# A customer payment or a bill payment posted from a bank-feed line (#1056)
# ----------------------------------------------------------------------------
# A deposit in the feed that pays open invoices, or a payment out that pays
# open bills, used to be recorded in Receive Payments or Pay Bills first and
# then matched. Here the line's own amount, date and account make the
# payment, through the same create routes those pages post to (so every
# rule they keep, a closed period, a sub-customer's invoices, a currency, a
# Sub's insurance, a check number already used, is kept the same way), and
# the payment's bank line is linked to the statement line, which leaves To
# review. The register then shows the payment once. What the line pays
# beyond the amounts applied stays unapplied, as those pages leave it: a
# credit on the customer, a prepayment to the vendor.
#
# Both writes land together or not at all (replace_transaction._one_commit
# holds the create route's own commit back).
# ============================================================================

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.banking import BankTransaction
from app.models.transactions import TransactionLine
from app.services import bank_matching as m
from app.services.accounting import _q
from app.services.closing_date import check_closing_date


def _waiting(db: Session, bt: BankTransaction):
    """The line's ledger account, once the line is shown to be in To review."""
    from app.services.bank_dedupe import EXCLUDED

    if bt.transaction_line_id or bt.match_status in ("added", "auto", "manual"):
        raise HTTPException(
            status_code=400, detail="This statement line is already in the books."
        )
    if bt.match_status in EXCLUDED:
        raise HTTPException(
            status_code=400,
            detail="This statement line is excluded; restore it to To review first.",
        )
    return m._feed_account(db, bt)


def _applied(allocations, line_amount: Decimal, whose: str) -> Decimal:
    if not allocations:
        raise HTTPException(
            status_code=400,
            detail=f"Enter an amount to apply to at least one of the {whose}.",
        )
    if any(_q(a.amount) <= 0 for a in allocations):
        raise HTTPException(
            status_code=400,
            detail="Each amount applied must be more than zero; leave a row blank to skip it.",
        )
    total = _q(sum((Decimal(str(a.amount)) for a in allocations), Decimal("0")))
    if total > line_amount:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The amounts applied add up to ${total:,.2f}, more than this "
                f"line's ${line_amount:,.2f}. Lower one of them."
            ),
        )
    return total


def _link(db: Session, bt: BankTransaction, acct_id: int, txn_id: int) -> None:
    """Link the statement line to the payment's line on the feed's account,
    as Add links the entry it posts ("added": voiding the payment gives the
    line back to To review)."""
    amount = _q(bt.amount)
    side = TransactionLine.debit if amount > 0 else TransactionLine.credit
    line = (
        db.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == txn_id,
            TransactionLine.account_id == acct_id,
            side == abs(amount),
        )
        .first()
    )
    if line is None:  # never: the payment was made for this amount and account
        raise HTTPException(
            status_code=500,
            detail="The payment posted no line on this account to match; nothing was recorded.",
        )
    m.match(db, bt, line.id)
    bt.match_status = "added"
    m.clear_picks(bt)


def receive_payment(db: Session, bt: BankTransaction, data) -> BankTransaction:
    """One customer payment for the whole of a money-in line, deposited to
    the feed's account on the line's date, applied to the invoices given."""
    from app.routes.payments import create_payment
    from app.schemas.payments import PaymentAllocationCreate, PaymentCreate
    from app.services.replace_transaction import _one_commit

    acct = _waiting(db, bt)
    amount = _q(bt.amount)
    if amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Receive payment is for money in; this line is money out. Use Pay bills.",
        )
    _applied(data.allocations, amount, "customer's open invoices")
    check_closing_date(db, bt.date)
    with _one_commit(db):
        resp = create_payment(
            PaymentCreate(
                customer_id=data.customer_id,
                date=bt.date,
                amount=amount,
                reference=bt.check_number or None,
                deposit_to_account_id=acct.id,
                notes=(data.memo or "").strip() or None,
                class_id=data.class_id,
                allocations=[
                    PaymentAllocationCreate(invoice_id=a.invoice_id, amount=a.amount)
                    for a in data.allocations
                ],
            ),
            db,
            request=None,
            response=None,
        )
        from app.models.payments import Payment

        payment = db.get(Payment, resp.id)
        _link(db, bt, acct.id, payment.transaction_id)
        db.flush()
    return bt


def pay_bills(db: Session, bt: BankTransaction, data) -> BankTransaction:
    """One bill payment for the whole of a money-out line, paid from the
    feed's account on the line's date, applied to the bills given."""
    from app.models.bills import BillPayment
    from app.routes.bill_payments import create_bill_payment
    from app.schemas.bills import BillPaymentAllocationCreate, BillPaymentCreate
    from app.services.replace_transaction import _one_commit

    acct = _waiting(db, bt)
    amount = _q(bt.amount)
    if amount >= 0:
        raise HTTPException(
            status_code=400,
            detail="Pay bills is for money out; this line is money in. Use Receive payment.",
        )
    _applied(data.allocations, -amount, "vendor's open bills")
    check_closing_date(db, bt.date)
    with _one_commit(db):
        resp = create_bill_payment(
            BillPaymentCreate(
                vendor_id=data.vendor_id,
                date=bt.date,
                amount=-amount,
                method="check" if bt.check_number else None,
                check_number=bt.check_number or None,
                pay_from_account_id=acct.id,
                notes=(data.memo or "").strip() or None,
                class_id=data.class_id,
                allocations=[
                    BillPaymentAllocationCreate(bill_id=a.bill_id, amount=a.amount)
                    for a in data.allocations
                ],
            ),
            db,
            request=None,
            response=None,
            allow_duplicate_check_number=False,
        )
        payment = db.get(BillPayment, resp.id)
        _link(db, bt, acct.id, payment.transaction_id)
        db.flush()
    return bt
