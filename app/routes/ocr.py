# ============================================================================
# Receipt / Document Intake — OCR endpoints (Tier 2)
# See docs/design/receipt-intake.md + docs/design/receipt-intake-spec.md.
#
# POST /api/ocr/receipt          scan an image/PDF and extract fields
# GET  /api/ocr/status           is Tesseract available? (frontend gating)
# POST /api/ocr/intake/{id}/attach  link a stored scan to a saved document
# DELETE /api/ocr/intake/{id}    discard a pending scan
# ============================================================================

import logging
import re
from datetime import date as date_cls
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attachments import Attachment
from app.models.bills import Bill
from app.models.invoices import Invoice
from app.models.transactions import Transaction
from app.schemas.attachments import AttachmentResponse
from app.schemas.ocr import (
    OcrAttachRequest,
    OcrField,
    OcrReceiptResponse,
    OcrRegionRequest,
    OcrRegionResponse,
    OcrStatusResponse,
    OcrWordBox,
)
from app.services import (
    ocr_engines,
    ocr_regions,
    ocr_service,
    ocr_template_store,
    storage,
)
from app.services.rate_limit import limiter
from app.services.settings_service import get_setting_raw
from app.services.upload_limits import MAX_IMPORT_BYTES, read_limited

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

# Same MIME/extension philosophy as app/routes/attachments.py: reject anything
# a browser could render-and-execute; OCR input is images + PDFs only.
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9 ._()\-]")

STATIC_BASE = storage.files_root().resolve()
UPLOAD_BASE = (STATIC_BASE / "uploads" / "attachments").resolve()


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
def ocr_status(db: Session = Depends(get_db)):
    """Frontend gating + Settings-page status row (spec §6.6). Reports the
    active engine for this platform (engine seam, design doc §engines)."""
    info = ocr_engines.engine_status(get_setting_raw(db, "ocr_engine"))
    return OcrStatusResponse(
        available=info["available"],
        version=info["version"],
        languages=info["languages"] or None,
        engine=info["engine"],
    )


@router.post("/receipt", response_model=OcrReceiptResponse)
@limiter.limit("30/minute")
async def scan_receipt(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
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

    engine = ocr_engines.get_engine(get_setting_raw(db, "ocr_engine"))
    reason = engine.unavailable_reason()
    if reason:
        return OcrReceiptResponse(ocr_available=False, message=reason)

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

    try:
        result = engine.recognize(ocr_input)
    except ocr_engines.EngineUnavailable as exc:
        return OcrReceiptResponse(ocr_available=False, message=str(exc))
    except ocr_service.OCRRuntimeError as exc:
        # The engine ran and rejected the input — a corrupt or non-image
        # file with a valid MIME type is the user's to fix, not a 500.
        raise HTTPException(
            status_code=400,
            detail=f"Could not read the image — is it a valid receipt scan? ({exc})",
        )
    raw_text = result.text
    lang = result.language

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

    # v3 template memory: if this merchant has a saved layout, region-OCR the
    # remembered boxes and prefer those reads (any engine — native engines
    # read prepared crops too). Fails closed to the v1 parse + canvas.
    template_fields: list[str] = []
    template_applied = False
    if result.words:
        word_dicts = [vars(w) for w in result.words]
        template = ocr_template_store.find_for_scan(
            db, extracted["merchant"]["value"], raw_text
        )
        if template is not None:
            reads = ocr_template_store.apply_template(
                db, template, ocr_input, word_dicts, engine=engine
            )
            template_fields = sorted(reads)
            for key in ("total", "subtotal", "tax"):
                if key in reads:
                    extracted[key] = reads[key]["value"]
                    if key == "total":
                        extracted["total_confidence"] = reads[key]["confidence"]
                    if key == "tax":
                        extracted["tax_detected"] = True
            if "date" in reads:
                date_value = reads["date"]["value"]
                date_is_default = False
                partial_reasons = [
                    r for r in partial_reasons if not r.startswith("date not found")
                ]
            if "merchant" in reads:
                extracted["merchant"] = {
                    "value": reads["merchant"]["value"],
                    "confidence": reads["merchant"]["confidence"],
                }
            if ocr_template_store.reads_are_clean(reads):
                template_applied = True
                partial_reasons = []

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
        engine=result.engine,
        words=[OcrWordBox(**vars(w)) for w in result.words] or None,
        multi_page=multi_page,
        partial=bool(partial_reasons),
        partial_reasons=partial_reasons,
        raw_text=raw_text[:4000],
        template_applied=template_applied,
        template_fields=template_fields,
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
    if body.entity_type not in ("invoice", "bill", "expense"):
        raise HTTPException(
            status_code=400,
            detail="entity_type must be 'invoice', 'bill' or 'expense'",
        )

    intake = ocr_service.get_intake(intake_id)
    if intake is None:
        raise HTTPException(
            status_code=404,
            detail="Scan not found or expired — scan the receipt again",
        )

    if body.entity_type == "invoice":
        entity = db.query(Invoice).filter(Invoice.id == body.entity_id).first()
    elif body.entity_type == "expense":
        entity = (
            db.query(Transaction)
            .filter(
                Transaction.id == body.entity_id,
                Transaction.source_type == "expense",
            )
            .first()
        )
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


def _intake_image_bytes(intake: dict) -> bytes:
    """The intake as image bytes: PDFs rasterize (page 1) so the canvas and
    region OCR share one coordinate space; images pass through."""
    if (intake.get("mime_type") or "").lower() == "application/pdf":
        png, _pages = ocr_service.rasterize_pdf(intake["data"])
        return png
    return intake["data"]


@router.get("/intake/{intake_id}/image")
def intake_image(intake_id: str):
    """Serve the stored scan as a PNG/image for the box-to-fix canvas.
    Auth'd like everything else; the intake id is unguessable and expiring,
    but this endpoint still sits behind the session like the rest."""
    intake = ocr_service.get_intake(intake_id)
    if intake is None:
        raise HTTPException(
            status_code=404,
            detail="Scan not found or expired — scan the receipt again",
        )
    mime = (intake.get("mime_type") or "").lower()
    if mime == "application/pdf":
        try:
            data = _intake_image_bytes(intake)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        mime = "image/png"
    else:
        data = intake["data"]
    from fastapi.responses import Response

    return Response(content=data, media_type=mime)


@router.post("/intake/{intake_id}/region", response_model=OcrRegionResponse)
@limiter.limit("60/minute")
def ocr_intake_region(
    intake_id: str,
    body: OcrRegionRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """OCR one user-drawn rectangle of a stored scan with field-aware
    settings (crop + upscale + contrast + single-line PSM + charset). The
    canvas calls this when the operator adjusts or draws a box."""
    intake = ocr_service.get_intake(intake_id)
    if intake is None:
        raise HTTPException(
            status_code=404,
            detail="Scan not found or expired — scan the receipt again",
        )
    engine = ocr_engines.get_engine(get_setting_raw(db, "ocr_engine"))
    reason = engine.unavailable_reason()
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    try:
        image_data = _intake_image_bytes(intake)
        result = ocr_regions.ocr_region(
            image_data,
            left=body.left,
            top=body.top,
            width=body.width,
            height=body.height,
            field_type=body.field_type,
            engine=engine,
        )
    except ocr_regions.RegionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ocr_service.OCRRuntimeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not read that region — try a larger box. ({exc})",
        )

    # v3 template memory: an operator-blessed read teaches the merchant
    # template (anchor-relative box). Fails closed — a save miss never
    # breaks the read that just succeeded.
    template_saved = False
    if body.save_template and body.merchant and body.field_key and result.get("value"):
        try:
            page = engine.recognize(image_data)
            words = [vars(w) for w in page.words]
            if words:
                # A corrected merchant box should teach the template under
                # the value the operator just blessed, not the stale scan.
                merchant_name = (
                    result["value"] if body.field_key == "merchant" else body.merchant
                )
                template_saved = ocr_template_store.record_correction(
                    db,
                    merchant_name,
                    body.field_key,
                    {
                        "left": body.left,
                        "top": body.top,
                        "width": body.width,
                        "height": body.height,
                    },
                    words,
                )
        except Exception:
            logger.debug("template save skipped", exc_info=True)
    return OcrRegionResponse(**result, template_saved=template_saved)


@router.delete("/intake/{intake_id}")
def discard_intake(intake_id: str):
    """Discard a pending scan (modal cancel / unsaved form). Idempotent."""
    ocr_service.delete_intake(intake_id)
    return {"status": "deleted"}
