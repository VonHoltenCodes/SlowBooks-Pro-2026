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
CENT = Decimal("0.01")
# The request field that takes a discount the terms don't offer.
OVERRIDE = "allow_discount_override"


def discount_account(
    db: Session,
    account_id: Optional[int],
    discounts: list[Decimal],
    override: bool = False,
) -> Optional[Account]:
    """The account the payment's discounts post to: required when any
    allocation takes one, refused when none does, and an active income,
    expense or cost of goods account."""
    taken = any(d > 0 for d in discounts)
    if any(d < 0 for d in discounts):
        raise HTTPException(
            status_code=400, detail="A discount can't be less than zero."
        )
    # Money is kept in cents: a finer discount would be rounded away and
    # nothing posted, so it is refused, not dropped.
    fine = next((d for d in discounts if d != d.quantize(CENT)), None)
    if fine is not None:
        raise HTTPException(
            status_code=400,
            detail=f"A discount is in whole cents; {fine.normalize():f} isn't.",
        )
    if not taken:
        if override:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{OVERRIDE} takes a discount the terms don't offer, but no "
                    "invoice or bill on this payment takes one. Leave it out."
                ),
            )
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


def refuse_unpaid_discount(noun: str, number: str, amount, discount) -> None:
    """A discount is taken with a payment on the document: a row with a
    discount and nothing paid names the document and the discount."""
    if discount > 0 and amount <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{noun.capitalize()} {number} has a ${discount:,.2f} discount but "
                "nothing paid on it; a discount is taken with a payment. Pay some "
                "of it on this payment, or clear its discount."
            ),
        )


def check_against_terms(
    db: Session,
    doc,
    noun: str,
    number: str,
    discount,
    base,
    pay_date,
    override,
    may_override: bool = True,
) -> Optional[str]:
    """The discount the document's own terms offer, checked before it is
        taken: terms that offer one, a payment dated by its last day, no more
        than its percent of `base`, the document's total as QuickBooks takes it
    (#665: a customer paying in two instalments in the window got 2% of what
    was left for the second), to the cent as the forms suggest it,
        and none taken on the document before.

        A discount that fails is refused, the sentence naming
        allow_discount_override. With the override it is taken, by an
        administrator only (`may_override`; another role is refused in a
        sentence), and the reason is returned so the payment and the memo
        record it. None when the terms offer it."""
    from app.services.accounting import _q
    from app.services.payment_terms import document_rule

    if discount <= 0:
        return None
    what = f"{noun.capitalize()} {number}"
    # never before the document exists (#665: an August prepayment applied
    # to an October invoice posted its discount in August), override or not
    if doc.date is not None and pay_date < doc.date:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This payment is dated {_day(pay_date)}, before {noun} {number} "
                f"({_day(doc.date)}); an early-payment discount is taken with a "
                f"payment made on or after the {noun}'s date."
            ),
        )
    rule = document_rule(db, doc)
    by = rule.discount_date(doc.date) if rule is not None else None
    terms = (doc.terms or "").strip() or "no terms"
    reason = None
    if by is None:
        reason = f"{what}'s terms ({terms}) offer no early-payment discount."
    elif pay_date > by:
        reason = (
            f"{what}'s discount had to be taken by {_day(by)}; this payment is "
            f"dated {_day(pay_date)}."
        )
    else:
        most = _q(Decimal(str(base)) * rule.discount_percent / Decimal("100"))
        if discount > most:
            reason = (
                f"{what}'s terms offer {rule.discount_percent.normalize():f}% of its "
                f"${Decimal(str(base)):,.2f} total, ${most:,.2f}; "
                f"${discount:,.2f} is more."
            )
    if reason is None:
        earlier = _earlier_discount(db, doc, noun)
        if earlier is not None:
            reason = (
                f"An early-payment discount ({earlier}) was already taken on "
                f"{noun} {number}."
            )
    if reason is None:
        return None
    if not override:
        raise HTTPException(
            status_code=400, detail=f"{reason} Send {OVERRIDE} to take it anyway."
        )
    if not may_override:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{reason} Only an administrator can take a discount the terms "
                "don't offer; ask one to record it."
            ),
        )
    return reason


def refuse_doubled(allocations, key: str, noun: str, number_of) -> None:
    """One document twice on a payment with a discount: refused before any
    memo or credit is numbered, naming the document, never a number that
    was rolled back (#665)."""
    seen: dict = {}
    for a in allocations:
        doc_id = getattr(a, key)
        seen.setdefault(doc_id, []).append(a)
    for doc_id, rows in seen.items():
        if len(rows) > 1 and any(r.discount > 0 for r in rows):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{noun.capitalize()} {number_of(doc_id)} is on this payment "
                    "more than once with a "
                    "discount. Put it on one row, with its whole payment and its "
                    "discount."
                ),
            )


def record_override(payment, reasons: list[str], actor: str) -> None:
    """Note on the payment that a discount was taken against the terms,
    by whom and why (#665), as a COI override is noted on a bill payment."""
    if not reasons:
        return
    payment.discount_override = True
    said = " ".join(reasons)
    note = f"Discount taken against the terms by {actor}: {said}"
    payment.discount_override_note = (
        f"{payment.discount_override_note} {note}"
        if payment.discount_override_note
        else note
    )


def _day(d) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _earlier_discount(db: Session, doc, noun: str):
    """The number of a discount already standing on the document."""
    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.models.vendor_credits import VendorCredit, VendorCreditStatus

    if noun == "invoice":
        row = (
            db.query(CreditMemo.memo_number)
            .filter(
                CreditMemo.original_invoice_id == doc.id,
                CreditMemo.discount_payment_id.isnot(None),
                CreditMemo.status != CreditMemoStatus.VOID,
            )
            .first()
        )
    else:
        row = (
            db.query(VendorCredit.credit_number)
            .filter(
                VendorCredit.original_bill_id == doc.id,
                VendorCredit.discount_bill_payment_id.isnot(None),
                VendorCredit.status != VendorCreditStatus.VOID,
            )
            .first()
        )
    return row[0] if row else None


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


def post_customer_discount(
    db: Session, payment, invoice, amount: Decimal, account, override=None
):
    """The credit memo for a discount the customer took on `invoice` with
    `payment`, applied to the invoice. Flushed; the caller commits."""
    from app.routes.invoices.lifecycle import post_applied_credit

    memo = (
        f"Early-payment discount on invoice {invoice.invoice_number}"
        f"{_terms_words(invoice)}"
    )
    if override:
        memo = (memo + f" (taken against its terms: {override})")[:300]
    cm = post_applied_credit(
        db,
        invoice,
        payment.date,
        amount,
        # a journal line's memo is at most 300 characters
        memo[:300],
        account.id,
        "early-payment discount on",
        discount_payment_id=payment.id,
    )
    cm.discount_override = bool(override)
    return cm


def post_vendor_discount(
    db: Session, payment, bill, amount: Decimal, account, override=None
):
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
    from app.services import cash_basis
    from app.services.accounting import create_journal_entry, get_ap_account_id
    from app.services.numbering import next_vendor_credit_number

    memo = f"Early-payment discount on bill {bill.bill_number}{_terms_words(bill)}"
    if override:
        memo = (memo + f" (taken against its terms: {override})")[:300]
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
            discount_override=bool(override),
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
    ap_id = get_ap_account_id(db)
    txn = create_journal_entry(
        db,
        payment.date,
        f"Vendor Credit {vc.credit_number} - early-payment discount on bill "
        f"{bill.bill_number}",
        [
            {
                "account_id": ap_id,
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
        ]
        # read on the cash basis too: it settles that part of the bill (#664)
        + cash_basis.discount_cash_lines(
            db, ap_id, bill, amount, account.id, memo, invoice=False
        ),
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


def refuse_moving_its_terms(db: Session, doc, changes: dict, noun: str) -> None:
    """An invoice or bill with an early-payment discount taken on it keeps
    the terms and date the discount was taken under: a change would leave
    the discount's memo naming terms the document no longer has. 409 naming
    the discount; voiding its payment undoes it."""
    moving = ("terms" in changes and (changes["terms"] or "") != (doc.terms or "")) or (
        "date" in changes and changes["date"] != doc.date
    )
    if not moving:
        return
    taken = _earlier_discount(db, doc, noun)
    if taken is None:
        return
    number = doc.invoice_number if noun == "invoice" else doc.bill_number
    raise HTTPException(
        status_code=409,
        detail=(
            f"{noun.capitalize()} {number} has an early-payment discount "
            f"({taken}) taken under its terms and date, so they stay. Void "
            "the payment that took it to change them."
        ),
    )


def discounted_on(db: Session, doc, noun: str) -> str:
    """For an edit refused below what is paid: ", of which $6.66 is an
    early-payment discount" when part of the paid figure is one (the
    customer, or we, paid less in cash); empty when none is."""
    from sqlalchemy import func

    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.models.vendor_credits import VendorCredit, VendorCreditStatus

    if noun == "invoice":
        q = db.query(func.coalesce(func.sum(CreditMemo.total), 0)).filter(
            CreditMemo.original_invoice_id == doc.id,
            CreditMemo.discount_payment_id.isnot(None),
            CreditMemo.status != CreditMemoStatus.VOID,
        )
    else:
        q = db.query(func.coalesce(func.sum(VendorCredit.total), 0)).filter(
            VendorCredit.original_bill_id == doc.id,
            VendorCredit.discount_bill_payment_id.isnot(None),
            VendorCredit.status != VendorCreditStatus.VOID,
        )
    discounted = Decimal(str(q.scalar() or 0))
    if discounted <= 0:
        return ""
    return f", of which ${discounted:,.2f} is an early-payment discount"
