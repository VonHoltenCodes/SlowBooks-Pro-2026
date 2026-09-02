# ============================================================================
# Receipt / Document Intake — OCR API schemas (Tier 2)
# See docs/design/receipt-intake.md + docs/design/receipt-intake-spec.md.
# ============================================================================

from typing import Optional

from pydantic import BaseModel


class OcrStatusResponse(BaseModel):
    """GET /api/ocr/status — is an OCR engine usable, and which one?"""

    available: bool
    version: Optional[str] = None
    languages: Optional[list[str]] = None
    # Which engine answers scans on this platform: tesseract | vision | winrt
    engine: str = "tesseract"


class OcrWordBox(BaseModel):
    """A recognized word (or line, engine-dependent) with pixel bounds —
    the v2 box-to-fix canvas draws these over the scanned image."""

    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float = -1.0


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
    # The vendor's own document number, when the receipt prints one.
    reference: Optional[str] = None

    language: Optional[str] = None
    engine: Optional[str] = None
    words: Optional[list[OcrWordBox]] = None
    multi_page: bool = False

    partial: bool = False
    partial_reasons: list[str] = []
    raw_text: Optional[str] = None

    # v3 template memory: True when a saved merchant template supplied the
    # amount fields cleanly (all high confidence, arithmetic closes) — the
    # frontend can skip the review canvas. template_fields lists which
    # fields came from the template regardless of the clean gate.
    template_applied: bool = False
    template_fields: list[str] = []


class OcrAttachRequest(BaseModel):
    """POST /api/ocr/intake/{intake_id}/attach — link the scan to a document.

    A sales receipt is stored as an invoice (is_sales_receipt=true), so the
    frontend sends entity_type="invoice" for both sales receipts and regular
    invoices; "bill" covers the Enter Bill form.
    """

    entity_type: str  # "invoice" | "bill" (validated in the route)
    entity_id: int


class OcrRegionRequest(BaseModel):
    """POST /api/ocr/intake/{intake_id}/region — OCR one typed rectangle of
    a stored scan (the v2 box-to-fix canvas). Pixel coordinates in the
    intake image's space (the same space /intake/{id}/image serves)."""

    left: int
    top: int
    width: int
    height: int
    # amount | date | merchant | text
    field_type: str = "text"

    # v3 template memory: when the canvas knows the merchant and which form
    # field this box feeds, a successful read teaches the merchant template.
    merchant: Optional[str] = None
    field_key: Optional[str] = None
    save_template: bool = False


class OcrRegionResponse(BaseModel):
    text: str
    value: Optional[str] = None
    field_type: str
    confidence: str = "missing"  # high | low | missing | multiple
    # v3: did this read get saved into the merchant's template?
    template_saved: bool = False
