# ============================================================================
# CSV Import/Export Routes — unified import/export center
# Feature 14: Combined with IIF into Import/Export Center
# ============================================================================

from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
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
from app.services.request_utils import content_disposition

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
        headers={"Content-Disposition": content_disposition(filename, disposition)},
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


@router.get("/import/invoices/template")
def csv_invoice_template(request: Request):
    """The invoice import's layout (#882): the header row and an example
    of two invoices, the first of two lines."""
    from app.services.csv_invoice_import import invoice_template_csv

    return _csv_response(invoice_template_csv(), "invoices-template.csv", request)


@router.post("/import/invoices")
async def csv_import_invoices(
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Import invoices in SlowBooks' own layout (#882; the template above):
    one row per invoice line, rows sharing an invoice_number one invoice.
    Each is made by the invoice form's own path. `dry_run=true` (the
    default) makes them and rolls them back, answering with what would
    post; `dry_run=false` posts them, and only when no row is refused."""
    from app.services.csv_invoice_import import import_invoices

    content = _decode_csv_upload(await read_limited(file, label="CSV file"))
    if not content.strip():
        raise HTTPException(status_code=400, detail="The file is empty.")
    return import_invoices(db, content, dry_run=dry_run)


@router.post("/import/qb-report")
async def csv_import_qb_report(
    request: Request,
    file: UploadFile = File(...),
    adjusting_only: bool = Form(False),
    add_employees: bool | None = Form(None),
    outflow_positive: bool = Form(False),
    answers: UploadFile | str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Import a QuickBooks Desktop report CSV — the documented fallback
    for Desktop, which can't export transactions to IIF. Auto-detects the
    report by its columns: Transaction Detail filtered to Sales Receipt,
    Deposit Detail, or Check Detail.

    A Transaction Detail by Account file with problems posts nothing and
    answers with them and the file's sha256 (#663); ``answers``, a JSON
    object, is the person's answers to them, sent with the same file again
    and carrying that sha256: a text field, or a part of its own, as the
    page sends it (a text field holds 1 MB, and thousands of ticked
    transactions can be more). Answers made for another file are refused.

    ``add_employees`` (W-138): whether a time report's people who are not
    on the Employees list are added from their names. Adding employees is
    an administrator's, or a token's let run payroll: left out of the
    request, it is yes for those and no for anyone else, whose entries for
    those people are left out and named; ``true`` from anyone else is
    refused before the file is read."""
    from app.routes._roles import may_run_payroll
    from app.services.qb_ledger_import import (
        check_answers_file,
        file_sha256,
        read_answers,
    )
    from app.services.qb_report_import import import_qb_report
    from app.services.safe_errors import DataProblem

    may_add = may_run_payroll(request)
    if add_employees and not may_add:
        raise HTTPException(
            status_code=403,
            detail=(
                "Adding people to the Employees list needs an administrator, or "
                "a token that may also run payroll. Leave add_employees out, or "
                "send it false, to import the time of the people already on the "
                "list. Nothing was imported."
            ),
        )
    if answers is not None and not isinstance(answers, str):
        answers = (await read_limited(answers, label="The answers")).decode(
            "utf-8", "replace"
        )
    try:
        answered = read_answers(answers)
    except DataProblem as e:
        raise HTTPException(status_code=400, detail=e.user_text)
    raw = await read_limited(file, label="CSV file")
    sha = file_sha256(raw)
    try:
        check_answers_file(answered, sha)
    except DataProblem as e:
        raise HTTPException(status_code=400, detail=e.user_text)
    if (file.filename or "").lower().endswith(".xlsx"):
        # a report saved as Excel (the timesheet reports usually are, #323):
        # its first sheet, cells as they read
        from app.routes.migration import _sheet_as_csv

        content = _sheet_as_csv(raw, computed=True)
    else:
        content = _decode_csv_upload(raw)
    # ``adjusting_only`` (#344): the report is an accountant's General
    # Journal entries; anything else in it is refused whole, and what posts
    # is marked adjusting in the sign-in's name
    # ``outflow_positive`` (#328): a categorized bank sheet whose amounts
    # read money out as positive
    try:
        return import_qb_report(
            db,
            content,
            adjusting_only=adjusting_only,
            posted_by=db.info.get("acting_username"),
            outflow_positive=outflow_positive,
            answers=answered,
            file_sha=sha,
            add_employees=may_add if add_employees is None else add_employees,
            may_add_employees=may_add,
        )
    except DataProblem as e:
        # an answer the chart's rules refuse (an account made with a type
        # its parent does not take, #663): nothing is kept
        db.rollback()
        raise HTTPException(status_code=400, detail=e.user_text)
