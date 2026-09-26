from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer
from app.schemas.contacts import CustomerCreate, CustomerUpdate, CustomerResponse
from app.routes._helpers import get_or_404
from app.services.contact_balances import ZERO, customer_balances
from app.services.duplicate_detection import find_duplicates

router = APIRouter(prefix="/api/customers", tags=["customers"])


def _responses(db: Session, customers: list[Customer]) -> list[CustomerResponse]:
    """The customers with what each one owes, summed from the open documents
    (the stored Customer.balance column is never written; see
    services/contact_balances). One set of grouped queries for the page."""
    balances = customer_balances(db, [c.id for c in customers])
    out = []
    for c in customers:
        resp = CustomerResponse.model_validate(c)
        resp.balance = balances.get(c.id, ZERO)
        out.append(resp)
    return out


def _response(db: Session, customer: Customer) -> CustomerResponse:
    return _responses(db, [customer])[0]


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    active_only: bool = False, search: str = None, db: Session = Depends(get_db)
):
    q = db.query(Customer)
    if active_only:
        q = q.filter(Customer.is_active)
    if search:
        q = q.filter(Customer.name.ilike(f"%{search}%"))
    return _responses(db, q.order_by(Customer.name).all())


@router.get("/check-duplicate")
def check_duplicate(
    name: str = Query(..., min_length=1), db: Session = Depends(get_db)
):
    """Phase 11: standalone duplicate-check endpoint the UI can call BEFORE
    submitting a create form to warn the user proactively."""
    existing = db.query(Customer).filter(Customer.is_active == True).all()  # noqa
    return {"duplicates": find_duplicates(name, existing)}


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    return _response(db, get_or_404(db, Customer, customer_id))


@router.get("/{customer_id}/credits")
def customer_credits(customer_id: int, db: Session = Depends(get_db)):
    """Money the customer has with us that is not on an invoice yet — the
    unapplied part of their payments and credit memos not yet applied —
    each with what can still be applied, so Receive Payment and the
    customer page can offer to apply it (explore 2.17.3, F12 / W-H9: an
    overpayment or an unapplied payment could never be used afterwards).
    `total` is in home currency; each credit is in its own currency."""
    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.models.payments import Payment
    from app.services.contact_balances import unapplied_payments
    from app.services.currency import home_currency

    customer = get_or_404(db, Customer, customer_id)
    home = home_currency(db)
    rows = unapplied_payments(db, [customer_id])
    payments = (
        {
            p.id: p
            for p in db.query(Payment).filter(
                Payment.id.in_([r.payment_id for r in rows])
            )
        }
        if rows
        else {}
    )
    credits = []
    total = ZERO
    for r in rows:
        p = payments[r.payment_id]
        credits.append(
            {
                "kind": "payment",
                "id": r.payment_id,
                "date": r.date.isoformat(),
                "number": p.check_number or p.reference or "",
                "method": p.method or "",
                "currency": (r.currency or home).upper(),
                "amount": float(r.amount),
                "available": float(r.unapplied),
            }
        )
        total += r.unapplied_home
    memos = (
        db.query(CreditMemo)
        .filter(
            CreditMemo.customer_id == customer_id,
            CreditMemo.status != CreditMemoStatus.VOID,
            CreditMemo.balance_remaining > 0,
        )
        .all()
    )
    for m in memos:
        credits.append(
            {
                "kind": "credit_memo",
                "id": m.id,
                "date": m.date.isoformat(),
                "number": m.memo_number,
                "method": "",
                "currency": home,
                "amount": float(m.total),
                "available": float(m.balance_remaining),
            }
        )
        total += m.balance_remaining
    credits.sort(key=lambda c: (c["date"], c["kind"], c["id"]))
    return {
        "customer_id": customer.id,
        "customer_name": customer.name,
        "home_currency": home,
        "total": float(total),
        "credits": credits,
    }


@router.post("", response_model=CustomerResponse, status_code=201)
def create_customer(
    data: CustomerCreate,
    force: bool = Query(False, description="Bypass duplicate-name warning"),
    db: Session = Depends(get_db),
):
    # Phase 11: warn on likely duplicate names unless the caller explicitly
    # passes ?force=true to confirm.
    if not force:
        existing = db.query(Customer).filter(Customer.is_active == True).all()  # noqa
        dupes = find_duplicates(data.name, existing)
        if dupes:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "possible_duplicate",
                    "message": "A similarly-named customer already exists. Pass ?force=true to create anyway.",
                    "duplicates": dupes,
                },
            )
    values = data.model_dump()
    if not (values.get("terms") or "").strip():
        # A new customer gets the company's default terms (Settings), not a
        # hard-coded Net 30 (explore 2.17.3, macbase1 F5).
        from app.services.settings_service import get_setting_raw

        values["terms"] = get_setting_raw(db, "default_terms") or "Net 30"
    customer = Customer(**values)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return _response(db, customer)


@router.put("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: int, data: CustomerUpdate, db: Session = Depends(get_db)
):
    customer = get_or_404(db, Customer, customer_id)
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(customer, key, val)
    db.commit()
    db.refresh(customer)
    return _response(db, customer)


@router.delete("/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = get_or_404(db, Customer, customer_id)
    customer.is_active = False
    db.commit()
    return {"message": "Customer deactivated"}
