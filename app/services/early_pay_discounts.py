# ============================================================================
# Early-payment discounts (#664), taken where the money is: Receive Payments
# (the customer took the discount its terms offered) and Pay Bills (we took
# the vendor's).
#
# A discount is a credit document applied to the invoice or bill at once,
# the way a write-off is: a credit memo posting DR <discount account> / CR
# A/R, or a vendor credit posting DR A/P / CR <discount account>. So the
# A/R and A/P sub-ledgers, statements and agings already read it, and the
# discount names the payment it was taken with: voiding the payment voids
# its discounts. The account is the one the person picks on the payment.
# ============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType

# Where a discount can post: income (a sales discount, or purchase
# discounts earned), expense or cost of goods. Never a balance-sheet
# account, which would hide the discount from the P&L.
DISCOUNT_ACCOUNT_TYPES = (AccountType.INCOME, AccountType.EXPENSE, AccountType.COGS)


def discount_account(
    db: Session, account_id: Optional[int], discounts: list[Decimal]
) -> Optional[Account]:
    """The account the payment's discounts post to: required when any
    allocation takes one, refused when none does, and an active income,
    expense or cost of goods account."""
    taken = any(d > 0 for d in discounts)
    if any(d < 0 for d in discounts):
        raise HTTPException(
            status_code=400, detail="A discount can't be less than zero."
        )
    if not taken:
        if account_id is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "discount_account_id names where a discount posts, but no "
                    "invoice or bill on this payment takes one. Leave it out, "
                    "or enter the discount."
                ),
            )
        return None
    if account_id is None:
        raise HTTPException(
            status_code=400,
            detail="Pick the account the discount posts to (discount_account_id).",
        )
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Discount account not found")
    if account.is_active is False:
        raise HTTPException(
            status_code=400,
            detail=f"{account.name} is inactive; pick an active account for the discount.",
        )
    if account.account_type not in DISCOUNT_ACCOUNT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A discount posts to an income, expense or cost of goods "
                f"account; {account.name} is {account.account_type.value}."
            ),
        )
    return account


def refuse_foreign(doc, currency: str, home: str, number: str, noun: str) -> None:
    """A discount is posted in the home currency; a foreign-currency
    document's would book at the wrong value, so it is refused."""
    if currency != home:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{noun.capitalize()} {number} is in {currency}; an early-payment "
                "discount is taken in the home currency only. Record this one "
                f"with a {'credit memo' if noun == 'invoice' else 'vendor credit'}."
            ),
        )


def _terms_words(doc) -> str:
    return f" ({doc.terms.strip()})" if (doc.terms or "").strip() else ""


def post_customer_discount(db: Session, payment, invoice, amount: Decimal, account):
    """The credit memo for a discount the customer took on `invoice` with
    `payment`, applied to the invoice. Flushed; the caller commits."""
    from app.routes.invoices.lifecycle import post_applied_credit

    memo = (
        f"Early-payment discount on invoice {invoice.invoice_number}"
        f"{_terms_words(invoice)}"
    )
    return post_applied_credit(
        db,
        invoice,
        payment.date,
        amount,
        memo,
        account.id,
        "early-payment discount on",
        discount_payment_id=payment.id,
    )


def post_vendor_discount(db: Session, payment, bill, amount: Decimal, account):
    """The vendor credit for a discount we took on `bill` with `payment`,
    applied to the bill: DR A/P / CR the discount account. Flushed; the
    caller commits."""
    from app.models.bills import BillStatus
    from app.models.vendor_credits import (
        VendorCredit,
        VendorCreditApplication,
        VendorCreditLine,
        VendorCreditStatus,
    )
    from app.services.accounting import create_journal_entry, get_ap_account_id
    from app.services.numbering import next_vendor_credit_number

    memo = f"Early-payment discount on bill {bill.bill_number}{_terms_words(bill)}"
    vc = None
    for _ in range(10):
        vc = VendorCredit(
            credit_number=next_vendor_credit_number(db),
            vendor_id=bill.vendor_id,
            date=payment.date,
            original_bill_id=bill.id,
            subtotal=amount,
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=amount,
            amount_applied=amount,
            balance_remaining=Decimal("0"),
            notes=memo,
            class_id=bill.class_id,
            job_id=bill.job_id,
            status=VendorCreditStatus.APPLIED,
            discount_bill_payment_id=payment.id,
        )
        try:
            # a savepoint: a taken number rolls back this credit only
            with db.begin_nested():
                db.add(vc)
                db.flush()
            break
        except IntegrityError as e:
            if "credit_number" not in str(e.orig).lower():
                raise
            vc = None
    if vc is None:
        raise HTTPException(
            status_code=503, detail="Could not assign a vendor credit number"
        )
    db.add(
        VendorCreditLine(
            vendor_credit_id=vc.id,
            account_id=account.id,
            description=memo,
            quantity=1,
            rate=amount,
            amount=amount,
            line_order=0,
        )
    )
    txn = create_journal_entry(
        db,
        payment.date,
        f"Vendor Credit {vc.credit_number} - early-payment discount on bill "
        f"{bill.bill_number}",
        [
            {
                "account_id": get_ap_account_id(db),
                "debit": amount,
                "credit": Decimal("0"),
                "description": memo,
            },
            {
                "account_id": account.id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": memo,
            },
        ],
        source_type="vendor_credit",
        source_id=vc.id,
        class_id=bill.class_id,
        job_id=bill.job_id,
    )
    vc.transaction_id = txn.id
    db.add(
        VendorCreditApplication(vendor_credit_id=vc.id, bill_id=bill.id, amount=amount)
    )
    bill.amount_paid = Decimal(str(bill.amount_paid or 0)) + amount
    bill.balance_due = Decimal(str(bill.balance_due or 0)) - amount
    bill.status = BillStatus.PAID if bill.balance_due <= 0 else BillStatus.PARTIAL
    return vc


def void_customer_discounts(db: Session, payment) -> None:
    """Void the discounts taken with `payment`, before its own void puts
    the payment's amounts back on the invoices."""
    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.routes.credit_memos import void_credit_memo_unchecked

    for cm in (
        db.query(CreditMemo)
        .filter(
            CreditMemo.discount_payment_id == payment.id,
            CreditMemo.status != CreditMemoStatus.VOID,
        )
        .all()
    ):
        void_credit_memo_unchecked(db, cm)


def void_vendor_discounts(db: Session, payment) -> None:
    """Void the discounts taken with a bill payment, before its own void."""
    from app.models.vendor_credits import VendorCredit, VendorCreditStatus
    from app.routes.vendor_credits import void_vendor_credit_unchecked

    for vc in (
        db.query(VendorCredit)
        .filter(
            VendorCredit.discount_bill_payment_id == payment.id,
            VendorCredit.status != VendorCreditStatus.VOID,
        )
        .all()
    ):
        void_vendor_credit_unchecked(db, vc)
