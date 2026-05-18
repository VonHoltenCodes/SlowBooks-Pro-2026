from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer
from app.schemas.contacts import CustomerCreate, CustomerUpdate, CustomerResponse
from app.routes._helpers import get_or_404
from app.services.duplicate_detection import find_duplicates

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.get("", response_model=list[CustomerResponse])
def list_customers(active_only: bool = False, search: str = None, db: Session = Depends(get_db)):
    q = db.query(Customer)
    if active_only:
        q = q.filter(Customer.is_active == True)
    if search:
        q = q.filter(Customer.name.ilike(f"%{search}%"))
    return q.order_by(Customer.name).all()


@router.get("/check-duplicate")
def check_duplicate(name: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """Phase 11: standalone duplicate-check endpoint the UI can call BEFORE
    submitting a create form to warn the user proactively."""
    existing = db.query(Customer).filter(Customer.is_active == True).all()  # noqa
    return {"duplicates": find_duplicates(name, existing)}


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, Customer, customer_id)


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
    customer = Customer(**data.model_dump())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.put("/{customer_id}", response_model=CustomerResponse)
def update_customer(customer_id: int, data: CustomerUpdate, db: Session = Depends(get_db)):
    customer = get_or_404(db, Customer, customer_id)
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(customer, key, val)
    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = get_or_404(db, Customer, customer_id)
    customer.is_active = False
    db.commit()
    return {"message": "Customer deactivated"}
