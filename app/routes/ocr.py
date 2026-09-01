# ============================================================================
# Receipt / Document Intake — OCR endpoints (Tier 2)
# See docs/design/receipt-intake.md + docs/design/receipt-intake-spec.md.
#
# POST /api/ocr/receipt          scan an image/PDF and extract fields
# GET  /api/ocr/status           is Tesseract available? (frontend gating)
# POST /api/ocr/intake/{id}/attach  link a stored scan to a saved document
# DELETE /api/ocr/intake/{id}    discard a pending scan
# ============================================================================

import re
from datetime import date as date_cls
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attachments import Attachment
from app.models.bills import Bill
from app.models.invoices import Invoice
from app.schemas.attachments import AttachmentResponse
from app.schemas.ocr import (
    OcrAttachRequest,
    OcrField,
    OcrReceiptResponse,
    OcrStatusResponse,
)
from app.services import ocr_service, storage
from app.services.rate_limit import limiter
from app.services.upload_limits import MAX_IMPORT_BYTES, read_limited

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

# Same MIME/extension philosophy as app/routes/attachments.py: reject anything
# a browser could render-and-execute; OCR input is images + PDFs only.
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9 ._()\-]")

STATIC_BASE = storage.files_root().resolve()
UPLOAD_BASE = (STATIC_BASE / "uploads" / "attachments").resolve()

_TESSERACT_MESSAGE = (
    "Tesseract OCR is not installed. Install it to enable scanning "
    "(Ubuntu: sudo apt-get install tesseract-ocr; macOS: brew install "
    "tesseract; Windows: see Settings for an installer link)."
)


def _sanitize_filename(raw: str) -> str:
    """Mirror of attachments._sanitize_filename: strip path separators and
    restrict to a safe character set before touching the filesystem."""
    base = Path(raw or "").name
    if not base or base.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip()
    if not cleaned or cleaned.startswith(".") or "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return cleaned


@router.get("/status", response_model=OcrStatusResponse)
def ocr_status():
    """Frontend gating + Settings-page status row (spec §6.6)."""
    info = ocr_service.tesseract_info()
    return OcrStatusResponse(
        available=info["available"],
        version=info["version"],
        languages=info["languages"] or None,
    )


@router.post("/receipt", response_model=OcrReceiptResponse)
@limiter.limit("30/minute")
async def scan_receipt(request: Request, file: UploadFile = File(...)):
    """Scan a receipt image/PDF, extract fields, store the scan for later
    attachment. Synchronous with a bounded Tesseract call (spec §5.2)."""
    content = await read_limited(file, MAX_IMPORT_BYTES, "Receipt scan")

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Receipt must be a PNG, JPEG, WebP image or PDF "
            f"(got '{file.content_type or 'unknown'}').",
        )
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail=f"File extension '{extension}' not allowed"
        )

    if not ocr_service.tesseract_available():
        return OcrReceiptResponse(ocr_available=False, message=_TESSERACT_MESSAGE)

    # Rasterize PDFs via poppler-utils (page 1, per spec §3); images pass
    # through as-is — Tesseract decodes PNG/JPEG/WebP natively, so no image
    # library is involved on the Python side.
    multi_page = False
    try:
        if content_type == "application/pdf":
            ocr_input, page_count = ocr_service.rasterize_pdf(content)
            multi_page = page_count > 1
        else:
            ocr_input = content
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    lang = ocr_service.ocr_language()
    if lang is None:
        return OcrReceiptResponse(
            ocr_available=False,
            message=(
                "Tesseract is installed but has no usable language data "
                "(expected at least 'eng'). Install the tesseract-ocr "
                "language packs and try again."
            ),
        )

    try:
        raw_text = ocr_service.ocr_image_bytes(ocr_input, lang=lang)
    except ocr_service.OCRRuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}")

    extracted = ocr_service.extract_receipt(raw_text)
    intake_id = ocr_service.save_intake(
        content, file.filename or "receipt", content_type
    )

    # Date default: receipts without a readable date use today (flagged).
    date_value = extracted["date"]
    date_is_default = False
    if not date_value:
        date_value = date_cls.today().isoformat()
        date_is_default = True

    partial_reasons = list(extracted["partial_reasons"])
    if date_is_default:
        partial_reasons.append("date not found — today's date used")

    return OcrReceiptResponse(
        intake_id=intake_id,
        merchant=(
            OcrField(
                value=extracted["merchant"]["value"],
                confidence=extracted["merchant"]["confidence"],
            )
        ),
        date=date_value,
        date_is_default=date_is_default,
        total=extracted["total"],
        total_confidence=extracted["total_confidence"],
        subtotal=extracted["subtotal"],
        tax=extracted["tax"],
        tax_detected=extracted["tax_detected"],
        language=lang,
        multi_page=multi_page,
        partial=bool(partial_reasons),
        partial_reasons=partial_reasons,
        raw_text=raw_text[:4000],
    )


@router.post(
    "/intake/{intake_id}/attach", response_model=AttachmentResponse, status_code=201
)
def attach_intake(
    intake_id: str, body: OcrAttachRequest, db: Session = Depends(get_db)
):
    """Move a stored scan into the attachments store for a saved document.
    The frontend calls this after the bill/receipt is created (spec §6.5);
    sales receipts attach as entity_type='invoice'."""
    if body.entity_type not in ("invoice", "bill"):
        raise HTTPException(
            status_code=400,
            detail="entity_type must be 'invoice' or 'bill'",
        )

    intake = ocr_service.get_intake(intake_id)
    if intake is None:
        raise HTTPException(
            status_code=404,
            detail="Scan not found or expired — scan the receipt again",
        )

    if body.entity_type == "invoice":
        entity = db.query(Invoice).filter(Invoice.id == body.entity_id).first()
    else:
        entity = db.query(Bill).filter(Bill.id == body.entity_id).first()
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail=f"{body.entity_type} {body.entity_id} not found",
        )

    # Original filename is user input — sanitize, and prefix with the intake
    # id so repeated scans of same-named files never collide.
    safe_filename = _sanitize_filename(intake.get("original_filename") or "receipt")
    target_name = f"{intake_id[:8]}-{safe_filename}"

    upload_dir = (UPLOAD_BASE / body.entity_type / str(body.entity_id)).resolve()
    if not upload_dir.is_relative_to(UPLOAD_BASE):
        raise HTTPException(status_code=400, detail="Invalid path")
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = (upload_dir / target_name).resolve()
    if not dest.is_relative_to(upload_dir):
        raise HTTPException(status_code=400, detail="Invalid path")
    dest.write_bytes(intake["data"])

    attachment = Attachment(
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        filename=safe_filename,
        file_path=str(dest.relative_to(STATIC_BASE)),
        mime_type=intake.get("mime_type"),
        file_size=intake.get("size"),
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)

    ocr_service.delete_intake(intake_id)
    return attachment


@router.delete("/intake/{intake_id}")
def discard_intake(intake_id: str):
    """Discard a pending scan (modal cancel / unsaved form). Idempotent."""
    ocr_service.delete_intake(intake_id)
    return {"status": "deleted"}
