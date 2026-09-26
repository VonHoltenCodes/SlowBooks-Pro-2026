# ============================================================================
# CSV Import/Export Routes — unified import/export center
# Feature 14: Combined with IIF into Import/Export Center
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import csv_export
from app.services.csv_export import (
    export_customers,
    export_vendors,
    export_items,
    export_invoices,
    export_accounts,
)
from app.services.csv_import import import_customers, import_vendors, import_items
from app.services.chart_import import import_chart
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

# Excel on Windows reads a CSV in the machine's ANSI code page unless the
# file opens with a UTF-8 byte-order mark: "Bäckerei Müller & Söhne" came
# out as "BÃ¤ckerei MÃ¼ller & SÃ¶hne" (2.17.3 exploratory test, W-M14).
# Every CSV the app hands out — lists, ledger reports, nonprofit statements,
# analytics — goes through this one helper, so the mark is added here once.
# Our own importers read utf-8-sig, so a file exported here re-imports as is.
UTF8_BOM = "\ufeff"


def _csv_response(
    csv_data: str, filename: str, request: Request | None = None
) -> Response:
    desktop = request is not None and request.headers.get(_DESKTOP_HEADER)
    disposition = "inline" if desktop else "attachment"
    if not csv_data.startswith(UTF8_BOM):
        csv_data = UTF8_BOM + csv_data
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"{disposition}; filename={filename}"},
    )


@router.get("/export/customers")
def csv_export_customers(request: Request, db: Session = Depends(get_db)):
    csv_data = export_customers(db)
    return _csv_response(csv_data, "customers.csv", request)


@router.get("/export/classes")
def csv_export_classes(request: Request, db: Session = Depends(get_db)):
    return _csv_response(csv_export.export_classes(db), "classes.csv", request)


@router.get("/export/jobs")
def csv_export_jobs(request: Request, db: Session = Depends(get_db)):
    return _csv_response(csv_export.export_jobs(db), "jobs.csv", request)


@router.get("/export/bills")
def csv_export_bills(request: Request, db: Session = Depends(get_db)):
    return _csv_response(csv_export.export_bills(db), "bills.csv", request)


@router.get("/export/deposits")
def csv_export_deposits(request: Request, db: Session = Depends(get_db)):
    return _csv_response(csv_export.export_deposits(db), "deposits.csv", request)


@router.get("/export/sales-receipts")
def csv_export_sales_receipts(request: Request, db: Session = Depends(get_db)):
    return _csv_response(
        csv_export.export_sales_receipts(db), "sales_receipts.csv", request
    )


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


def _decode_csv_upload(raw: bytes) -> str:
    """Decode an uploaded CSV: UTF-8 (BOM-tolerant) first, then
    Windows-1252 — QB Desktop's Print -> Save as CSV frequently writes
    ANSI, and a payee like "José" previously 500'd (#62 review)."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Could not read the file as text. Re-export it as a "
                "UTF-8 / standard CSV and try again.",
            )


@router.post("/import/customers")
async def csv_import_customers(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content = _decode_csv_upload(await read_limited(file, label="CSV file"))
    result = import_customers(db, content)
    return result


@router.post("/import/vendors")
async def csv_import_vendors(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content = _decode_csv_upload(await read_limited(file, label="CSV file"))
    result = import_vendors(db, content)
    return result


@router.post("/import/items")
async def csv_import_items(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = _decode_csv_upload(await read_limited(file, label="CSV file"))
    result = import_items(db, content)
    return result


@router.post("/import/accounts")
async def csv_import_accounts(
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    replace: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Import a chart of accounts (#139 / #161): SlowBooks' own export
    columns, any CSV with Number/Name/Type headers, or hledger's account
    list (`hledger accounts`, `accounts --types`, `balance -O csv`).

    `dry_run=true` (the default) returns the plan and writes nothing; the
    page shows it and posts again with `dry_run=false` to apply exactly that
    plan. `replace=true` also deactivates every account the file does not
    name that has never been posted to — control accounts and accounts with
    history are kept either way."""
    content = _decode_csv_upload(await read_limited(file, label="chart file"))
    if not content.strip():
        raise HTTPException(status_code=400, detail="The file is empty.")
    return import_chart(db, content, replace=replace, dry_run=dry_run)


@router.post("/import/qb-report")
async def csv_import_qb_report(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """Import a QuickBooks Desktop report CSV — the documented fallback
    for Desktop, which can't export transactions to IIF. Auto-detects the
    report by its columns: Transaction Detail filtered to Sales Receipt,
    Deposit Detail, or Check Detail."""
    from app.services.qb_report_import import import_qb_report

    content = _decode_csv_upload(await read_limited(file, label="CSV file"))
    return import_qb_report(db, content)
