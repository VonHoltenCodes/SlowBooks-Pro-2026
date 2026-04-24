# ============================================================================
# Check Printing — Generate check PDFs (standard 3-per-page format)
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payments import Payment
from app.models.bills import BillPayment, BillPaymentAllocation, Bill
from app.models.contacts import Customer, Vendor
from app.services.pdf_service import generate_check_pdf
from app.routes.settings import _get_all as get_settings

router = APIRouter(prefix="/api/checks", tags=["checks"])


@router.get("/print")
def print_check(
    payment_id: int = Query(default=None),
    bill_payment_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """Generate a check PDF for a payment or bill payment."""
    if payment_id:
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        customer = db.query(Customer).filter(Customer.id == payment.customer_id).first()
        check_data = {
            "payee": customer.name if customer else "Unknown",
            "date": payment.date,
            "amount": payment.amount,
            "check_number": payment.check_number or "",
            "memo": payment.notes or "",
            "details": [],
        }
        for alloc in payment.allocations:
            check_data["details"].append({
                "description": f"Invoice #{alloc.invoice_id}",
                "amount": alloc.amount,
            })
    elif bill_payment_id:
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
            check_data["details"].append({
                "description": f"Bill #{bill.bill_number}" if bill else f"Bill #{alloc.bill_id}",
                "amount": alloc.amount,
            })
    else:
        raise HTTPException(status_code=400, detail="Provide payment_id or bill_payment_id")

    company = get_settings(db)
    pdf_bytes = generate_check_pdf(check_data, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Check_{check_data['check_number']}.pdf"},
    )
