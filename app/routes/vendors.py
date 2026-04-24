from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Vendor
from app.schemas.contacts import VendorCreate, VendorUpdate, VendorResponse
from app.routes._helpers import get_or_404

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


@router.get("", response_model=list[VendorResponse])
def list_vendors(active_only: bool = False, search: str = None, db: Session = Depends(get_db)):
    q = db.query(Vendor)
    if active_only:
        q = q.filter(Vendor.is_active == True)
    if search:
        q = q.filter(Vendor.name.ilike(f"%{search}%"))
    return q.order_by(Vendor.name).all()


@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, Vendor, vendor_id)


@router.post("", response_model=VendorResponse, status_code=201)
def create_vendor(data: VendorCreate, db: Session = Depends(get_db)):
    vendor = Vendor(**data.model_dump())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.put("/{vendor_id}", response_model=VendorResponse)
def update_vendor(vendor_id: int, data: VendorUpdate, db: Session = Depends(get_db)):
    vendor = get_or_404(db, Vendor, vendor_id)
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(vendor, key, val)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.delete("/{vendor_id}")
def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    vendor = get_or_404(db, Vendor, vendor_id)
    vendor.is_active = False
    db.commit()
    return {"message": "Vendor deactivated"}
