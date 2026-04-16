from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.contacts import Vendor
from app.schemas.contacts import VendorCreate, VendorResponse, VendorUpdate
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


def _validate_default_expense_account(db: Session, account_id: int | None) -> None:
    if account_id is None:
        return
    account = db.query(Account).filter(Account.id == account_id, Account.is_active == True).first()
    if not account:
        raise HTTPException(status_code=404, detail="Default expense account not found")
    if account.account_type != AccountType.EXPENSE:
        raise HTTPException(status_code=400, detail="Default expense account must be an expense account")



@router.get("", response_model=list[VendorResponse])
def list_vendors(
    active_only: bool = False,
    search: str = None,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("contacts.view")),
):
    q = db.query(Vendor)
    if active_only:
        q = q.filter(Vendor.is_active == True)
    if search:
        q = q.filter(Vendor.name.ilike(f"%{search}%"))
    return q.order_by(Vendor.name).all()


@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(
    vendor_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("contacts.view")),
):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


@router.post("", response_model=VendorResponse, status_code=201)
def create_vendor(
    data: VendorCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("contacts.manage")),
):
    _validate_default_expense_account(db, data.default_expense_account_id)
    vendor = Vendor(**data.model_dump())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.put("/{vendor_id}", response_model=VendorResponse)
def update_vendor(
    vendor_id: int,
    data: VendorUpdate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("contacts.manage")),
):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    updates = data.model_dump(exclude_unset=True)
    _validate_default_expense_account(db, updates.get("default_expense_account_id"))
    for key, val in updates.items():
        setattr(vendor, key, val)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.delete("/{vendor_id}")
def delete_vendor(
    vendor_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("contacts.manage")),
):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    vendor.is_active = False
    db.commit()
    return {"message": "Vendor deactivated"}
