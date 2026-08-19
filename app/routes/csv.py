# ============================================================================
# CSV Import/Export Routes — unified import/export center
# Feature 14: Combined with IIF into Import/Export Center
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, Request, UploadFile, File, Query
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
from app.services.upload_limits import read_limited

router = APIRouter(prefix="/api/csv", tags=["csv"])

# Set by desktop_shim.js on every fetch() it makes on behalf of a same-origin
# link click. text/csv is browser-renderable, so a normal browser install
# (Docker/LAN, multiple users) keeps getting Content-Disposition: attachment
# for a direct download. The desktop shell needs the opposite: WebView2 (with
# ALLOW_DOWNLOADS on) intercepts an "attachment" response at the network
# layer as a native download even when the request came from the page's own
# fetch() rather than a real click -- the response never reaches the page's
# fetch() promise, which surfaces as a "Failed to fetch" error even though
# the server logs a normal 200. Serving "inline" instead lets that fetch()
# complete normally; the shim's own JS handles the actual save from there.
_DESKTOP_HEADER = "X-Slowbooks-Desktop"


def _csv_response(csv_data: str, filename: str, request: Request) -> Response:
    disposition = "inline" if request.headers.get(_DESKTOP_HEADER) else "attachment"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"{disposition}; filename={filename}"},
    )


@router.get("/export/customers")
def csv_export_customers(request: Request, db: Session = Depends(get_db)):
    csv_data = export_customers(db)
    return _csv_response(csv_data, "customers.csv", request)


@router.get("/export/vendors")
def csv_export_vendors(request: Request, db: Session = Depends(get_db)):
    csv_data = export_vendors(db)
    return _csv_response(csv_data, "vendors.csv", request)


@router.get("/export/items")
def csv_export_items(request: Request, db: Session = Depends(get_db)):
    csv_data = export_items(db)
    return _csv_response(csv_data, "items.csv", request)


@router.get("/export/invoices")
def csv_export_invoices(
    request: Request,
    date_from: date = Query(default=None),
    date_to: date = Query(default=None),
    db: Session = Depends(get_db),
):
    csv_data = export_invoices(db, date_from, date_to)
    return _csv_response(csv_data, "invoices.csv", request)


@router.get("/export/accounts")
def csv_export_accounts(request: Request, db: Session = Depends(get_db)):
    csv_data = export_accounts(db)
    return _csv_response(csv_data, "chart_of_accounts.csv", request)


@router.post("/import/customers")
async def csv_import_customers(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content = (await read_limited(file, label="CSV file")).decode("utf-8-sig")
    result = import_customers(db, content)
    return result


@router.post("/import/vendors")
async def csv_import_vendors(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content = (await read_limited(file, label="CSV file")).decode("utf-8-sig")
    result = import_vendors(db, content)
    return result


@router.post("/import/items")
async def csv_import_items(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = (await read_limited(file, label="CSV file")).decode("utf-8-sig")
    result = import_items(db, content)
    return result


@router.post("/import/sales-receipts")
async def csv_import_sales_receipts(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """Import sales receipts from a QuickBooks Desktop "Transaction Detail
    by Date" report CSV (filtered to Sales Receipt) — the documented
    fallback for Desktop, which can't export transactions to IIF."""
    from app.services.qb_report_import import import_sales_receipt_report

    content = (await read_limited(file, label="CSV file")).decode("utf-8-sig")
    return import_sales_receipt_report(db, content)
