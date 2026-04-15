# ============================================================================
# IIF Import/Export API — QuickBooks 2003 Pro Interoperability
#
# Intuit Interchange Format (IIF) is the only practical file-based import/export
# path for QB2003. These endpoints generate and consume .iif files that QB2003
# can import via File > Utilities > Import > IIF Files.
#
# Export: GET /api/iif/export/{section} -> downloads .iif file
# Import: POST /api/iif/import -> uploads and imports .iif file
# Validate: POST /api/iif/validate -> checks .iif without importing
# ============================================================================

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.iif import IIFImportResult, IIFValidationReport
from app.services.iif_export import (
    export_all, export_accounts, export_customers, export_vendors,
    export_items, export_invoices, export_payments, export_estimates,
)
from app.services.iif_import import import_all, validate_iif
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/iif", tags=["iif"])


def _iif_response(content: str, filename: str) -> Response:
    """Return IIF content as a downloadable text file."""
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse_date(s: str) -> date:
    """Parse YYYY-MM-DD date string from query param."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"Invalid date format: {s} (expected YYYY-MM-DD)")


# ============================================================================
# Export endpoints
# ============================================================================

@router.get("/export/all")
def export_all_iif(db: Session = Depends(get_db), auth=Depends(require_permissions("import_export.view"))):
    content = export_all(db)
    return _iif_response(content, "slowbooks_export.iif")


@router.get("/export/accounts")
def export_accounts_iif(db: Session = Depends(get_db), auth=Depends(require_permissions("import_export.view"))):
    content = export_accounts(db)
    return _iif_response(content, "accounts.iif")


@router.get("/export/customers")
def export_customers_iif(db: Session = Depends(get_db), auth=Depends(require_permissions("import_export.view"))):
    content = export_customers(db)
    return _iif_response(content, "customers.iif")


@router.get("/export/vendors")
def export_vendors_iif(db: Session = Depends(get_db), auth=Depends(require_permissions("import_export.view"))):
    content = export_vendors(db)
    return _iif_response(content, "vendors.iif")


@router.get("/export/items")
def export_items_iif(db: Session = Depends(get_db), auth=Depends(require_permissions("import_export.view"))):
    content = export_items(db)
    return _iif_response(content, "items.iif")


@router.get("/export/invoices")
def export_invoices_iif(
    date_from: str = Query(None),
    date_to: str = Query(None),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.view")),
):
    content = export_invoices(db, _parse_date(date_from), _parse_date(date_to))
    return _iif_response(content, "invoices.iif")


@router.get("/export/payments")
def export_payments_iif(
    date_from: str = Query(None),
    date_to: str = Query(None),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("import_export.view")),
):
    content = export_payments(db, _parse_date(date_from), _parse_date(date_to))
    return _iif_response(content, "payments.iif")


@router.get("/export/estimates")
def export_estimates_iif(db: Session = Depends(get_db), auth=Depends(require_permissions("import_export.view"))):
    content = export_estimates(db)
    return _iif_response(content, "estimates.iif")


# ============================================================================
# Import endpoints
# ============================================================================

@router.post("/import", response_model=IIFImportResult)
async def import_iif(file: UploadFile = File(...), db: Session = Depends(get_db), auth=Depends(require_permissions("import_export.manage"))):
    """Upload and import an IIF file into Slowbooks.

    Processes accounts, customers, vendors, items, and transactions.
    Skips duplicates and collects per-row errors.
    """
    if not file.filename.lower().endswith(".iif"):
        raise HTTPException(400, "File must have .iif extension")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        # IIF files from QB2003 may be Windows-1252 encoded
        text = content.decode("cp1252", errors="replace")

    try:
        result = import_all(db, text)
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Import failed: {str(e)}")

    return result


@router.post("/validate", response_model=IIFValidationReport)
async def validate_iif_file(file: UploadFile = File(...), auth=Depends(require_permissions("import_export.manage"))):
    """Validate an IIF file without importing — pre-flight check."""
    if not file.filename.lower().endswith(".iif"):
        raise HTTPException(400, "File must have .iif extension")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("cp1252", errors="replace")

    report = validate_iif(text)
    return report
