# ============================================================================
# Attachments — file upload/download for invoices, bills, etc.
# Phase 10: Quick Wins + Medium Effort Features
#
# The bytes are kept in the company's own database (stored_files, see
# app/services/file_store.py). They used to be written to
# uploads/attachments/<type>/<record id>/<name> in a folder every company on
# a desktop install shared, so company B's invoice 1 "receipt.pdf" replaced
# company A's, and deleting B's made A's answer 404 (2.18.0 gate, skytech).
# ============================================================================

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attachments import Attachment
from app.models.bills import Bill
from app.models.contacts import Customer, Vendor
from app.models.estimates import Estimate
from app.models.field_tickets import FieldTicket
from app.models.invoices import Invoice
from app.models.purchase_orders import PurchaseOrder
from app.models.vendor_credits import VendorCredit
from app.models.transactions import Transaction
from app.schemas.attachments import AttachmentResponse, PendingAttachmentResponse
from app.services import file_store, pending_attachments

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

# The record types a file can be attached to. The user-provided entity_type
# is routed through this dict, so only these words are ever stored. (It was
# also the folder name when attachments were files on disk.)
_ENTITY_TYPE_DIRS = {
    "invoice": "invoice",
    "bill": "bill",
    "expense": "expense",
    "estimate": "estimate",
    "purchase_order": "purchase_order",
    # The supplier's own credit note is the evidence behind a vendor credit,
    # the same way their invoice is the evidence behind a bill.
    "vendor_credit": "vendor_credit",
    "vendor": "vendor",
    "customer": "customer",
    # A manual journal entry is sometimes the only right record of a real
    # transaction (an owner paying a business expense personally, credited
    # to equity — an Expense can only be paid from an asset or liability),
    # and its receipt belongs in the company file with it (#281).
    "journal_entry": "journal_entry",
    # A check, deposit or card charge typed straight into a register has its
    # receipt too, keyed by the entry's id, so it need not be re-entered as
    # an Expense just to hold the file (#600).
    "bank_entry": "bank_entry",
    # A charge entered on Credit Card Charges: its receipt is kept with it,
    # attached before the first save or after (#796).
    "cc_charge": "cc_charge",
    # A field ticket's photo of the signed paper (construction mode): v1's
    # signature is the typed name and date plus this.
    "field_ticket": "field_ticket",
}

# Whitelist of MIME types we accept for attachments. An attachment is served
# back under its stored type, so we reject anything that a browser would
# render and potentially execute (HTML, SVG with scripts, executables).
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "text/plain",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
}
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".txt",
    ".csv",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
}

# Only plain word chars, spaces, hyphens, dots, and parens in filenames.
# Everything else gets rewritten out: the name is shown in lists and sent in
# the download's Content-Disposition. Path(...).name strips any directory.
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9 ._()\-]")


def _sanitize_filename(raw: str) -> str:
    """Strip path separators and restrict to a safe character set."""
    base = Path(raw or "").name
    if not base or base.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip()
    # Belt and braces: even after Path(...).name, reject anything that still
    # contains path separators or parent refs.
    if not cleaned or cleaned.startswith(".") or "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return cleaned


def _entity_type(entity_type: str) -> str:
    """The whitelisted record type, or 400. Employee documents are not one:
    they are HR's, reached only through /api/employees (admin only)."""
    type_dir = _ENTITY_TYPE_DIRS.get(entity_type)
    if type_dir is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entity type. Allowed: {', '.join(sorted(_ENTITY_TYPE_DIRS))}",
        )
    return type_dir


# The table a record type's id is a row of, and the record's name in the
# 404. A register entry, an expense and a journal entry are transactions,
# checked by their source below.
_RECORD_TABLES = {
    "invoice": (Invoice, "Invoice"),
    "bill": (Bill, "Bill"),
    "estimate": (Estimate, "Estimate"),
    "purchase_order": (PurchaseOrder, "Purchase order"),
    "vendor_credit": (VendorCredit, "Vendor credit"),
    "vendor": (Vendor, "Vendor"),
    "customer": (Customer, "Customer"),
    "field_ticket": (FieldTicket, "Field ticket"),
}


# The record types that are a transaction: an expense and a card charge of
# their own kind, a journal entry any transaction.
_TRANSACTION_LABELS = {
    "expense": "Expense",
    "cc_charge": "Card charge",
    "journal_entry": "Journal entry",
}


def _check_record(db: Session, entity_type: str, entity_id: int) -> None:
    """A register entry's id must be a register entry's: 404 for any other
    transaction (an invoice's posting, a transfer) or none at all."""
    if entity_type != "bank_entry":
        return
    found = (
        db.query(Transaction.id)
        .filter(Transaction.id == entity_id, Transaction.source_type == "bank_entry")
        .first()
    )
    if not found:
        raise HTTPException(status_code=404, detail="Bank entry not found")


def _check_upload_record(db: Session, entity_type: str, entity_id: int) -> None:
    """A file is attached only to a record that is there: 404 otherwise. A
    file accepted for a record not yet made was shown on the next record
    given that id (a field ticket's, #731). Existence only: a void record
    still takes a file. An expense or a journal entry is a transaction of
    its kind."""
    _check_record(db, entity_type, entity_id)
    if entity_type in _TRANSACTION_LABELS:
        query = db.query(Transaction.id).filter(Transaction.id == entity_id)
        if entity_type in ("expense", "cc_charge"):
            query = query.filter(Transaction.source_type == entity_type)
        if query.first() is None:
            label = _TRANSACTION_LABELS[entity_type]
            raise HTTPException(status_code=404, detail=f"{label} not found")
        return
    if entity_type not in _RECORD_TABLES:
        return
    table, label = _RECORD_TABLES[entity_type]
    if db.query(table.id).filter(table.id == entity_id).first() is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")


def _record_attachment(db: Session, attachment_id: int) -> Attachment | None:
    """An attachment on a record — never an employee document. Those share
    the table, and this route has none of their admin-only rule: a read-only
    sign-in could download a W-4 by its id here, or a bookkeeper delete one."""
    return (
        db.query(Attachment)
        .filter(
            Attachment.id == attachment_id,
            Attachment.employee_id.is_(None),
            Attachment.entity_type.in_(list(_ENTITY_TYPE_DIRS)),
        )
        .first()
    )


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    """The upload's safe name and bytes, or the 400 that says what is wrong
    with it: the same refusals for a saved record and a form not yet saved."""
    safe_filename = _sanitize_filename(file.filename or "")
    extension = Path(safe_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File extension '{extension}' not allowed",
        )
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"MIME type '{file.content_type}' not allowed",
        )

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")
    return safe_filename, content


# A file for a new bill, expense or card charge, before its first save
# (#796): held until the create call names its id, then attached to the
# record it made. Declared above the catch-all routes, like download below.
@router.post("/pending", response_model=PendingAttachmentResponse, status_code=201)
async def upload_pending_attachment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    safe_filename, content = await _read_upload(file)
    row = pending_attachments.hold(
        db, filename=safe_filename, content_type=file.content_type, data=content
    )
    db.commit()
    return PendingAttachmentResponse(
        id=row.token,
        filename=row.original_name,
        mime_type=row.content_type,
        file_size=row.size,
        expires_at=pending_attachments.expires_at(row),
    )


@router.delete("/pending/{pending_id}")
def discard_pending_attachment(pending_id: str, db: Session = Depends(get_db)):
    """Taken off the form before it was saved, or the form was cancelled.
    Idempotent: a file already gone (saved, swept) is not an error."""
    pending_attachments.discard(db, pending_id)
    db.commit()
    return {"status": "deleted"}


@router.post(
    "/{entity_type}/{entity_id}", response_model=AttachmentResponse, status_code=201
)
async def upload_attachment(
    entity_type: str,
    entity_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _entity_type(entity_type)
    _check_upload_record(db, entity_type, entity_id)
    safe_filename, content = await _read_upload(file)

    # A row of its own every time: a second "receipt.pdf" on the same record
    # is a second file, and the first is kept.
    attachment = file_store.add_attachment(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=safe_filename,
        content_type=file.content_type,
        data=content,
    )
    db.commit()
    db.refresh(attachment)
    return attachment


# DECLARATION ORDER MATTERS HERE. FastAPI matches in the order routes are
# declared, and "/{entity_type}/{entity_id}" will happily match
# "/download/2" — entity_type "download", entity_id 2 — answering `[]`
# instead of the file. That is what shipped: a user could attach a file and
# never get it back, and no test caught it because they all called the
# handler rather than the URL (found by macbase1, reproduced by skytech,
# 2.10.3 gate). The literal-prefix route must stay ABOVE the catch-all.
@router.get("/download/{attachment_id}")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    attachment = _record_attachment(db, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return file_store.attachment_response(db, attachment)


@router.get("/{entity_type}/{entity_id}", response_model=list[AttachmentResponse])
def list_attachments(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    _entity_type(entity_type)
    _check_record(db, entity_type, entity_id)
    return (
        db.query(Attachment)
        .filter(
            Attachment.entity_type == entity_type,
            Attachment.entity_id == entity_id,
            Attachment.employee_id.is_(None),
        )
        .order_by(Attachment.uploaded_at.desc(), Attachment.id.desc())
        .all()
    )


@router.delete("/{attachment_id}")
def delete_attachment(attachment_id: int, db: Session = Depends(get_db)):
    attachment = _record_attachment(db, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    # The bytes go with the row: nothing is left behind anywhere.
    file_store.delete_attachment(db, attachment)
    db.commit()
    return {"status": "deleted"}
