# ============================================================================
# Bill Payments — pay bills (AP), DR AP (2000), CR Bank
# Feature 1 continued: Pay Bills workflow
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bills import Bill, BillStatus, BillPayment, BillPaymentAllocation
from app.models.contacts import Vendor
from app.models.accounts import Account
from app.schemas.bills import BillPaymentCreate, BillPaymentResponse
from app.services.accounting import create_journal_entry
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/bill-payments", tags=["bill_payments"])


def _get_ap_account_id(db):
    acct = db.query(Account).filter(Account.account_number == "2000").first()
    return acct.id if acct else None


@router.get("", response_model=list[BillPaymentResponse])
def list_bill_payments(vendor_id: int = None, db: Session = Depends(get_db)):
    q = db.query(BillPayment)
    if vendor_id:
        q = q.filter(BillPayment.vendor_id == vendor_id)
    payments = q.order_by(BillPayment.date.desc()).all()
    results = []
    for p in payments:
        resp = BillPaymentResponse.model_validate(p)
        if p.vendor:
            resp.vendor_name = p.vendor.name
        results.append(resp)
    return results


@router.post("", response_model=BillPaymentResponse, status_code=201)
def create_bill_payment(data: BillPaymentCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    alloc_total = sum(a.amount for a in data.allocations)
    if alloc_total > data.amount:
        raise HTTPException(status_code=400, detail="Allocations exceed payment amount")

    payment = BillPayment(
        vendor_id=data.vendor_id,
        date=data.date,
        amount=data.amount,
        method=data.method,
        check_number=data.check_number,
        pay_from_account_id=data.pay_from_account_id,
        notes=data.notes,
    )
    db.add(payment)
    db.flush()

    for alloc_data in data.allocations:
        bill = db.query(Bill).filter(Bill.id == alloc_data.bill_id).first()
        if not bill:
            raise HTTPException(
                status_code=404, detail=f"Bill {alloc_data.bill_id} not found"
            )
        if alloc_data.amount > float(bill.balance_due):
            raise HTTPException(
                status_code=400, detail=f"Allocation exceeds bill balance"
            )

        db.add(
            BillPaymentAllocation(
                bill_payment_id=payment.id,
                bill_id=alloc_data.bill_id,
                amount=alloc_data.amount,
            )
        )

        bill.amount_paid += Decimal(str(alloc_data.amount))
        bill.balance_due -= Decimal(str(alloc_data.amount))
        if bill.balance_due <= 0:
            bill.status = BillStatus.PAID
        else:
            bill.status = BillStatus.PARTIAL

    # Journal: DR AP, CR Bank
    ap_id = _get_ap_account_id(db)
    bank_id = data.pay_from_account_id
    if not bank_id:
        # Default to first checking account
        checking = db.query(Account).filter(Account.account_number == "1000").first()
        bank_id = checking.id if checking else None

    if ap_id and bank_id:
        journal_lines = [
            {
                "account_id": ap_id,
                "debit": Decimal(str(data.amount)),
                "credit": Decimal("0"),
                "description": f"Bill payment to {vendor.name}",
            },
            {
                "account_id": bank_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(data.amount)),
                "description": f"Bill payment to {vendor.name}",
            },
        ]
        txn = create_journal_entry(
            db,
            data.date,
            f"Bill payment to {vendor.name}",
            journal_lines,
            source_type="bill_payment",
            source_id=payment.id,
        )
        payment.transaction_id = txn.id

    db.commit()
    db.refresh(payment)
    resp = BillPaymentResponse.model_validate(payment)
    resp.vendor_name = vendor.name
    return resp
