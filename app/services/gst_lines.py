from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.gst import GstCode, ensure_default_gst_codes


DEFAULT_LINE_GST_CODE = "GST15"


def resolve_line_gst(db: Session, line_data) -> tuple[str, Decimal]:
    ensure_default_gst_codes(db, commit=False)
    code = getattr(line_data, "gst_code", None) or DEFAULT_LINE_GST_CODE
    gst_code = db.query(GstCode).filter(GstCode.code == code, GstCode.is_active == True).first()
    if not gst_code:
        raise HTTPException(status_code=400, detail=f"Invalid GST code: {code}")
    return gst_code.code, Decimal(str(gst_code.rate))
