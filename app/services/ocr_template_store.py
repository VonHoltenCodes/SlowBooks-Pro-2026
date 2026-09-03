# ============================================================================
# Merchant template persistence + scan-path application (receipt intake v3).
#
# Sits between the pure logic (services/ocr_templates.py) and the routes:
#   - record_correction(): a successful canvas region read teaches the
#     template — encode the box anchor-relative, upsert the merchant row.
#   - find_for_scan(): match a new scan to a stored template by extracted
#     merchant OR by the receipt's header lines (merchant parse misses are
#     common; the header text usually still OCRs).
#   - apply_template(): region-OCR the remembered boxes on the new scan.
#     Everything fails closed — any miss just means the canvas takes over.
# ============================================================================

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.ocr_templates import OcrTemplate
from app.services import ocr_regions, ocr_templates

logger = logging.getLogger(__name__)

# Same mapping as the canvas frontend (FIELD_OCR_TYPE in ocr_canvas.js).
FIELD_OCR_TYPE = {
    "total": "amount",
    "tax": "amount",
    "subtotal": "amount",
    "date": "date",
    "merchant": "merchant",
    "reference": "reference",
}


def record_correction(
    db: Session,
    merchant_name: str,
    field_key: str,
    box: dict,
    words: list[dict],
) -> bool:
    """Store WHERE a field lives for this merchant, from an operator-blessed
    canvas box. Returns True when something was saved."""
    if field_key not in FIELD_OCR_TYPE:
        return False
    key = ocr_templates.merchant_key(merchant_name)
    if not key:
        return False
    encoded = ocr_templates.encode_field(box, words)
    if encoded is None:
        return False

    row = db.query(OcrTemplate).filter(OcrTemplate.merchant_key == key).first()
    if row is None:
        row = OcrTemplate(
            merchant_key=key, merchant_name=merchant_name[:200], fields_json="{}"
        )
        db.add(row)
    fields = _fields(row)
    fields[field_key] = encoded
    row.fields_json = json.dumps(fields)
    db.commit()
    return True


def find_for_scan(
    db: Session, merchant_value: Optional[str], raw_text: str
) -> Optional[OcrTemplate]:
    """Match a scan to a stored template: by the extracted merchant first,
    then by the receipt's first few header lines (prefix key matching)."""
    candidates: list[str] = []
    key = ocr_templates.merchant_key(merchant_value or "")
    if key:
        candidates.append(key)
    for line in (raw_text or "").splitlines()[:4]:
        k = ocr_templates.merchant_key(line)
        if k and k not in candidates:
            candidates.append(k)
    if not candidates:
        return None
    for row in db.query(OcrTemplate).all():
        if any(ocr_templates.keys_match(row.merchant_key, c) for c in candidates):
            return row
    return None


def apply_template(
    db: Session,
    template: OcrTemplate,
    image_data: bytes,
    words: list[dict],
    engine=None,
) -> dict:
    """Region-OCR every remembered field box against a new scan. Returns
    {field_key: {"value": str, "confidence": str}} for the boxes that both
    resolved (anchor found) and read something. Never raises."""
    reads: dict = {}
    for field_key, encoded in _fields(template).items():
        box = ocr_templates.resolve_field(encoded, words)
        if box is None:
            continue
        try:
            result = ocr_regions.ocr_region(
                image_data,
                left=box["left"],
                top=box["top"],
                width=box["width"],
                height=box["height"],
                field_type=FIELD_OCR_TYPE.get(field_key, "text"),
                engine=engine,
            )
        except Exception as exc:  # fail closed, canvas takes over
            logger.debug("template region read failed for %s: %s", field_key, exc)
            continue
        confidence = result.get("confidence", "low")
        # An amount/date box that read only a bare digit run ("506739" off
        # a GST ID line — SkyTech lap, 2026-09-02) is a template that landed
        # somewhere else on this print.  A low read is worse than the v1
        # parse it would replace: drop it and let the parse + canvas stand.
        if (
            FIELD_OCR_TYPE.get(field_key) in ("amount", "date", "reference")
            and confidence != "high"
        ):
            continue
        if result.get("value"):
            reads[field_key] = {"value": result["value"], "confidence": confidence}
    if reads:
        template.use_count = (template.use_count or 0) + 1
        db.commit()
    return reads


def reads_are_clean(reads: dict) -> bool:
    """The skip-the-canvas gate: total/tax/subtotal all present with high
    confidence AND the arithmetic closes (subtotal + tax == total to 2c)."""
    needed = ("total", "tax", "subtotal")
    if not all(k in reads for k in needed):
        return False
    if not all(reads[k]["confidence"] == "high" for k in needed):
        return False
    try:
        total = float(reads["total"]["value"])
        tax = float(reads["tax"]["value"])
        subtotal = float(reads["subtotal"]["value"])
    except (TypeError, ValueError):
        return False
    return abs((subtotal + tax) - total) <= 0.02


def _fields(row: OcrTemplate) -> dict:
    try:
        data = json.loads(row.fields_json or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}
