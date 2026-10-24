# ============================================================================
# Check Printing — Generate check PDFs (standard 3-per-page format)
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payments import Payment
from app.models.bills import BillPayment, Bill
from app.models.contacts import Vendor
from app.models.transactions import Transaction
from app.services.bank_register import payees_for, voided_transaction_ids
from app.services.pdf_service import generate_check_pdf
from app.services.request_utils import content_disposition
from app.routes.settings import _get_all as get_settings

router = APIRouter(prefix="/api/checks", tags=["checks"])

# The postings a check is written in without a bill behind it: a bank
# register entry (+ Entry or the quick-entry row) and an Expense.
REGISTER_CHECK_SOURCES = ("bank_entry", "expense")


def register_check_data(db: Session, txn_id: int) -> dict:
    """The check behind a register entry or an expense (discussion #354):
    money out of a bank account, payee from the vendor or the entry, the
    reference as the check number, each split line on the stub. A deposit,
    a card charge, a transfer, a void and a bill payment are refused with
    the reason; a bill payment prints from its bill."""
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.source_type == "bill_payment":
        raise HTTPException(
            status_code=400,
            detail="A bill payment's check prints from the bill it paid.",
        )
    if txn.source_type not in REGISTER_CHECK_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a bank register entry or an expense prints as a check; "
                "this entry is neither."
            ),
        )
    bank_line = next(
        (ln for ln in txn.lines if ln.account and ln.account.bank_kind == "bank"),
        None,
    )
    if bank_line is None:
        paid_by_card = any(
            ln.account and ln.account.bank_kind == "credit_card" for ln in txn.lines
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "This was paid by card, so there is no check to print."
                if paid_by_card
                else "This entry does not touch a bank account, so there is no check to print."
            ),
        )
    if (bank_line.credit or Decimal("0")) <= 0:
        raise HTTPException(
            status_code=400,
            detail="This is money coming in, so there is no check to print.",
        )
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(
            status_code=400, detail="This entry is void, so there is no check to print."
        )
    payee = payees_for(db, [txn]).get(txn.id, "") or ""
    # the stub lists what the check paid: the category of each line, with
    # its memo when the line carries one of its own. A credit line is a
    # refund netted against the check (#387), shown as a negative amount so
    # the lines add up to the check (e.g. 620.00 expense less a 120.00
    # refund = a 500.00 check).
    details = []
    for ln in txn.lines:
        if ln is bank_line:
            continue
        debit = ln.debit or Decimal("0")
        credit = ln.credit or Decimal("0")
        if debit <= 0 and credit <= 0:
            continue
        name = ln.account.name if ln.account else ""
        memo = (ln.description or "").strip()
        if memo and memo != payee and memo != name:
            name = f"{name} — {memo}" if name else memo
        amount = debit if debit > 0 else -credit
        details.append({"description": name or "Payment", "amount": amount})
    memo = (bank_line.description or "").strip()
    return {
        "payee": payee,
        "date": txn.date,
        "amount": bank_line.credit,
        "check_number": txn.reference or "",
        "memo": "" if memo == payee else memo,
        "details": details,
    }


@router.get("/test-print")
def test_print_check(db: Session = Depends(get_db)):
    """A sample check with placeholder values (#440), printed through the
    same renderer and the same check-stock settings as a real check, so the
    alignment nudge can be set by holding the sheet against the stock."""
    check_data = {
        "payee": "Sample Payee",
        "date": date.today(),
        "amount": Decimal("1234.56"),
        "check_number": "0000",
        "memo": "Alignment test",
        "details": [{"description": "Alignment test", "amount": Decimal("1234.56")}],
    }
    company = get_settings(db)
    pdf_bytes = generate_check_pdf(check_data, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition("Check_test_print.pdf")},
    )


@router.get("/print")
def print_check(
    payment_id: int = Query(default=None),
    bill_payment_id: int = Query(default=None),
    transaction_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """Generate a check PDF for a bill payment, or for a check written
    straight into the bank register or as an expense (`transaction_id`,
    discussion #354) — money going out.

    A customer payment is money coming IN: the check was written by the
    customer, so there is nothing to print. Offering it printed a check
    payable to the customer for the amount they paid us, with the stub
    listing internal invoice ids (explore 2.17.3, W-M9 / F12)."""
    if payment_id and not bill_payment_id:
        if not db.query(Payment).filter(Payment.id == payment_id).first():
            raise HTTPException(status_code=404, detail="Payment not found")
        raise HTTPException(
            status_code=400,
            detail=(
                "This is a payment you received, so there is no check to print. "
                "Checks print for bills you pay."
            ),
        )
    if bill_payment_id:
        bp = db.query(BillPayment).filter(BillPayment.id == bill_payment_id).first()
        if not bp:
            raise HTTPException(status_code=404, detail="Bill payment not found")
        vendor = db.query(Vendor).filter(Vendor.id == bp.vendor_id).first()
        check_data = {
            "payee": vendor.name if vendor else "Unknown",
            "date": bp.date,
            "amount": bp.amount,
            "check_number": bp.check_number or "",
            "memo": "",
            "details": [],
        }
        for alloc in bp.allocations:
            bill = db.query(Bill).filter(Bill.id == alloc.bill_id).first()
            check_data["details"].append(
                {
                    "description": (
                        f"Bill #{bill.bill_number}"
                        if bill
                        else f"Bill #{alloc.bill_id}"
                    ),
                    "amount": alloc.amount,
                }
            )
        # The stub adds up to the check: a part paid ahead of any bill is
        # listed as such rather than left out.
        applied = sum((a.amount for a in bp.allocations), Decimal("0"))
        if bp.amount - applied > 0:
            check_data["details"].append(
                {
                    "description": "Paid ahead (not applied to a bill)",
                    "amount": bp.amount - applied,
                }
            )
    elif transaction_id:
        check_data = register_check_data(db, transaction_id)
    else:
        raise HTTPException(
            status_code=400, detail="Provide bill_payment_id or transaction_id"
        )

    company = get_settings(db)
    pdf_bytes = generate_check_pdf(check_data, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"Check_{check_data['check_number']}.pdf"
                if check_data["check_number"]
                else "Check.pdf"
            )
        },
    )
