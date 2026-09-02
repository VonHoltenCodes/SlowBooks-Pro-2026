# ============================================================================
# Receipt / Document Intake — OCR API schemas (Tier 2)
# See docs/design/receipt-intake.md + docs/design/receipt-intake-spec.md.
# ============================================================================

from typing import Optional

from pydantic import BaseModel


class OcrStatusResponse(BaseModel):
    """GET /api/ocr/status — is the Tesseract binary usable?"""

    available: bool
    version: Optional[str] = None
    languages: Optional[list[str]] = None


class OcrField(BaseModel):
    """An extracted value plus how much the parser trusts it."""

    value: Optional[str] = None
    # high | low | missing
    confidence: str = "missing"


class OcrReceiptResponse(BaseModel):
    """POST /api/ocr/receipt — extraction result for a scanned receipt.

    When `ocr_available` is False the scan could not run (Tesseract missing)
    and `message` explains how to enable it; the other fields stay default.
    `intake_id` references the stored scan so the frontend can attach it to
    the document after save (POST /api/ocr/intake/{id}/attach).
    """

    ocr_available: bool = True
    message: Optional[str] = None
    intake_id: Optional[str] = None

    merchant: Optional[OcrField] = None
    date: Optional[str] = None
    date_is_default: bool = False
    total: Optional[str] = None
    total_confidence: str = "missing"  # high | low | missing
    subtotal: Optional[str] = None
    tax: Optional[str] = None
    tax_detected: bool = False

    language: Optional[str] = None
    multi_page: bool = False

    partial: bool = False
    partial_reasons: list[str] = []
    raw_text: Optional[str] = None


class OcrAttachRequest(BaseModel):
    """POST /api/ocr/intake/{intake_id}/attach — link the scan to a document.

    A sales receipt is stored as an invoice (is_sales_receipt=true), so the
    frontend sends entity_type="invoice" for both sales receipts and regular
    invoices; "bill" covers the Enter Bill form.
    """

    entity_type: str  # "invoice" | "bill" (validated in the route)
    entity_id: int
