from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Vendor
from app.schemas.contacts import VendorCreate, VendorUpdate, VendorResponse
from app.routes._helpers import get_or_404
from app.services.contact_balances import ZERO, vendor_balances
from app.services.duplicate_detection import find_duplicates
from app.services.form_1099 import clear_type_unless_1099

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


def _responses(db: Session, vendors: list[Vendor]) -> list[VendorResponse]:
    """The vendors with what is owed to each, summed from the open bills,
    credits and unapplied payments (Vendor.balance is never written)."""
    balances = vendor_balances(db, [v.id for v in vendors])
    out = []
    for v in vendors:
        resp = VendorResponse.model_validate(v)
        resp.balance = balances.get(v.id, ZERO)
        out.append(resp)
    return out


def _response(db: Session, vendor: Vendor) -> VendorResponse:
    return _responses(db, [vendor])[0]


@router.get("", response_model=list[VendorResponse])
def list_vendors(
    active_only: bool = False, search: str = None, db: Session = Depends(get_db)
):
    q = db.query(Vendor)
    if active_only:
        q = q.filter(Vendor.is_active)
    if search:
        q = q.filter(Vendor.name.ilike(f"%{search}%"))
    return _responses(db, q.order_by(Vendor.name).all())


@router.get("/check-duplicate")
def check_duplicate(
    name: str = Query(..., min_length=1), db: Session = Depends(get_db)
):
    """Phase 11: standalone duplicate-check endpoint for pre-submit UI warnings."""
    existing = db.query(Vendor).filter(Vendor.is_active == True).all()  # noqa
    return {"duplicates": find_duplicates(name, existing)}


@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    return _response(db, get_or_404(db, Vendor, vendor_id))


@router.post("", response_model=VendorResponse, status_code=201)
def create_vendor(
    data: VendorCreate,
    force: bool = Query(False, description="Bypass duplicate-name warning"),
    db: Session = Depends(get_db),
):
    if not force:
        existing = db.query(Vendor).filter(Vendor.is_active == True).all()  # noqa
        dupes = find_duplicates(data.name, existing)
        if dupes:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "possible_duplicate",
                    "message": "A similarly-named vendor already exists. Pass ?force=true to create anyway.",
                    "duplicates": dupes,
                },
            )
    vendor = Vendor(**data.model_dump())
    clear_type_unless_1099(vendor)
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return _response(db, vendor)


@router.put("/{vendor_id}", response_model=VendorResponse)
def update_vendor(vendor_id: int, data: VendorUpdate, db: Session = Depends(get_db)):
    vendor = get_or_404(db, Vendor, vendor_id)
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(vendor, key, val)
    clear_type_unless_1099(vendor)
    db.commit()
    db.refresh(vendor)
    return _response(db, vendor)


@router.delete("/{vendor_id}")
def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    vendor = get_or_404(db, Vendor, vendor_id)
    vendor.is_active = False
    db.commit()
    return {"message": "Vendor deactivated"}
