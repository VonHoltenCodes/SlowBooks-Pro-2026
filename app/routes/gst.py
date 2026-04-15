from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.gst import GstCode, ensure_default_gst_codes
from app.schemas.gst import GstCodeResponse
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/gst-codes", tags=["gst"])


@router.get("", response_model=list[GstCodeResponse])
def list_gst_codes(
    active_only: bool = True,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions()),
):
    ensure_default_gst_codes(db)
    query = db.query(GstCode)
    if active_only:
        query = query.filter(GstCode.is_active == True)
    return query.order_by(GstCode.sort_order, GstCode.code).all()


@router.get("/{code}", response_model=GstCodeResponse)
def get_gst_code(
    code: str,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions()),
):
    ensure_default_gst_codes(db)
    gst_code = db.query(GstCode).filter(GstCode.code == code).first()
    if not gst_code:
        raise HTTPException(status_code=404, detail="GST code not found")
    return gst_code
