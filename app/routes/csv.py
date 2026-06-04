# ============================================================================
# CSV Import/Export Routes — unified import/export center
# Feature 14: Combined with IIF into Import/Export Center
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, UploadFile, File, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.csv_export import (
    export_customers,
    export_vendors,
    export_items,
    export_invoices,
    export_accounts,
)
from app.services.csv_import import import_customers, import_vendors, import_items

router = APIRouter(prefix="/api/csv", tags=["csv"])


@router.get("/export/customers")
def csv_export_customers(db: Session = Depends(get_db)):
    csv_data = export_customers(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=customers.csv"},
    )


@router.get("/export/vendors")
def csv_export_vendors(db: Session = Depends(get_db)):
    csv_data = export_vendors(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vendors.csv"},
    )


@router.get("/export/items")
def csv_export_items(db: Session = Depends(get_db)):
    csv_data = export_items(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=items.csv"},
    )


@router.get("/export/invoices")
def csv_export_invoices(
    date_from: date = Query(default=None),
    date_to: date = Query(default=None),
    db: Session = Depends(get_db),
):
    csv_data = export_invoices(db, date_from, date_to)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoices.csv"},
    )


@router.get("/export/accounts")
def csv_export_accounts(db: Session = Depends(get_db)):
    csv_data = export_accounts(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=chart_of_accounts.csv"},
    )


@router.post("/import/customers")
async def csv_import_customers(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content = (await file.read()).decode("utf-8-sig")
    result = import_customers(db, content)
    return result


@router.post("/import/vendors")
async def csv_import_vendors(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content = (await file.read()).decode("utf-8-sig")
    result = import_vendors(db, content)
    return result


@router.post("/import/items")
async def csv_import_items(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = (await file.read()).decode("utf-8-sig")
    result = import_items(db, content)
    return result
