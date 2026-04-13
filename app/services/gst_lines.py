from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.gst import GstCode, ensure_default_gst_codes
from app.services.gst_calculations import GstLineInput


DEFAULT_LINE_GST_CODE = "GST15"


def resolve_line_gst(db: Session, line_data) -> tuple[str, Decimal]:
    gst_code = resolve_line_gst_code(db, line_data)
    return gst_code.code, Decimal(str(gst_code.rate))


def resolve_line_gst_code(db: Session, line_data) -> GstCode:
    ensure_default_gst_codes(db, commit=False)
    code = getattr(line_data, "gst_code", None) or DEFAULT_LINE_GST_CODE
    gst_code = db.query(GstCode).filter(GstCode.code == code, GstCode.is_active == True).first()
    if not gst_code:
        raise HTTPException(status_code=400, detail=f"Invalid GST code: {code}")
    return gst_code


def resolve_gst_line_inputs(db: Session, lines_data) -> list[GstLineInput]:
    result = []
    for line_data in lines_data:
        gst_code = resolve_line_gst_code(db, line_data)
        result.append(GstLineInput(
            quantity=Decimal(str(line_data.quantity)),
            rate=Decimal(str(line_data.rate)),
            gst_code=gst_code.code,
            gst_rate=Decimal(str(gst_code.rate)),
            category=gst_code.category,
        ))
    return result


def stored_gst_line_inputs(db: Session, lines) -> list[GstLineInput]:
    ensure_default_gst_codes(db, commit=False)
    codes = {
        row.code: row
        for row in db.query(GstCode).filter(GstCode.code.in_([line.gst_code for line in lines])).all()
    }
    result = []
    for line in lines:
        gst_code = codes.get(line.gst_code)
        category = gst_code.category if gst_code else "taxable"
        result.append(GstLineInput(
            quantity=Decimal(str(line.quantity)),
            rate=Decimal(str(line.rate)),
            gst_code=line.gst_code,
            gst_rate=Decimal(str(line.gst_rate)),
            category=category,
        ))
    return result
