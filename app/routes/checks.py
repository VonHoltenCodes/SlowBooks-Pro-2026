# ============================================================================
# Check Printing — Generate check PDFs (standard 3-per-page format)
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payments import Payment
from app.models.bills import BillPayment, Bill
from app.models.contacts import Vendor
from app.services.pdf_service import generate_check_pdf
from app.services.request_utils import content_disposition
from app.routes.settings import _get_all as get_settings

router = APIRouter(prefix="/api/checks", tags=["checks"])


@router.get("/print")
def print_check(
    payment_id: int = Query(default=None),
    bill_payment_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """Generate a check PDF for a bill payment — money going out.

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
    else:
        raise HTTPException(status_code=400, detail="Provide bill_payment_id")

    company = get_settings(db)
    pdf_bytes = generate_check_pdf(check_data, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"Check_{check_data['check_number']}.pdf"
            )
        },
    )
