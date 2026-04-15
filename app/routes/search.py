from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer, Vendor
from app.models.estimates import Estimate
from app.models.invoices import Invoice
from app.models.items import Item
from app.models.payments import Payment
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/search", tags=["search"])

LIMIT_PER = 5


@router.get("")
def unified_search(
    q: str = Query(min_length=2),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions()),
):
    pattern = f"%{q}%"
    results = {}

    customers = db.query(Customer).filter(Customer.is_active == True, (Customer.name.ilike(pattern) | Customer.company.ilike(pattern) | Customer.email.ilike(pattern))).limit(LIMIT_PER).all()
    if customers:
        results['customers'] = [{"id": c.id, "name": c.name, "company": c.company, "email": c.email} for c in customers]

    vendors = db.query(Vendor).filter(Vendor.is_active == True, (Vendor.name.ilike(pattern) | Vendor.company.ilike(pattern))).limit(LIMIT_PER).all()
    if vendors:
        results['vendors'] = [{"id": v.id, "name": v.name, "company": v.company} for v in vendors]

    items = db.query(Item).filter(Item.is_active == True, (Item.name.ilike(pattern) | Item.description.ilike(pattern))).limit(LIMIT_PER).all()
    if items:
        results['items'] = [{"id": i.id, "name": i.name, "item_type": i.item_type.value} for i in items]

    invoices = db.query(Invoice).filter(Invoice.invoice_number.ilike(pattern)).order_by(Invoice.date.desc()).limit(LIMIT_PER).all()
    if invoices:
        results['invoices'] = [{"id": inv.id, "invoice_number": inv.invoice_number, "customer_name": inv.customer.name if inv.customer else "", "total": float(inv.total), "status": inv.status.value} for inv in invoices]

    estimates = db.query(Estimate).filter(Estimate.estimate_number.ilike(pattern)).order_by(Estimate.date.desc()).limit(LIMIT_PER).all()
    if estimates:
        results['estimates'] = [{"id": e.id, "estimate_number": e.estimate_number, "customer_name": e.customer.name if e.customer else "", "total": float(e.total), "status": e.status.value} for e in estimates]

    payments = db.query(Payment).filter((Payment.reference.ilike(pattern) | Payment.check_number.ilike(pattern))).order_by(Payment.date.desc()).limit(LIMIT_PER).all()
    if payments:
        results['payments'] = [{"id": p.id, "amount": float(p.amount), "date": p.date.isoformat(), "customer_name": p.customer.name if p.customer else "", "method": p.method} for p in payments]

    return results
