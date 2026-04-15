# ============================================================================
# CSV Import/Export Routes — unified import/export center
# Feature 14: Combined with IIF into Import/Export Center
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import require_permissions
from app.services.csv_export import (
    export_accounts,
    export_customers,
    export_invoices,
    export_items,
    export_vendors,
)
from app.services.csv_import import import_customers, import_items, import_vendors

router = APIRouter(prefix="/api/csv", tags=["csv"])

CSV_DECODE_ERROR = "CSV file must be UTF-8 encoded"
CSV_IMPORT_ERROR = "CSV import failed"


async def _read_csv_upload(file: UploadFile) -> str:
    try:
        return (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=CSV_DECODE_ERROR) from exc


async def _run_csv_import(importer, file: UploadFile, db: Session) -> dict:
    content = await _read_csv_upload(file)
    try:
        return importer(db, content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=CSV_IMPORT_ERROR) from exc


@router.get("/export/customers")
def csv_export_customers(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.view")),
):
    csv_data = export_customers(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=customers.csv"},
    )


@router.get("/export/vendors")
def csv_export_vendors(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.view")),
):
    csv_data = export_vendors(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vendors.csv"},
    )


@router.get("/export/items")
def csv_export_items(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.view")),
):
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
    auth=Depends(require_permissions("import_export.view")),
):
    csv_data = export_invoices(db, date_from, date_to)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoices.csv"},
    )


@router.get("/export/accounts")
def csv_export_accounts(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.view")),
):
    csv_data = export_accounts(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=chart_of_accounts.csv"},
    )


@router.post("/import/customers")
async def csv_import_customers(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.manage")),
):
    return await _run_csv_import(import_customers, file, db)


@router.post("/import/vendors")
async def csv_import_vendors(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.manage")),
):
    return await _run_csv_import(import_vendors, file, db)


@router.post("/import/items")
async def csv_import_items(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.manage")),
):
    return await _run_csv_import(import_items, file, db)
