"""Integration tests for the Tier 2 OCR receipt pipeline.

Runs the **real** Tesseract + poppler pipeline against ground-truth PDF
fixtures in tests/fixtures/ocr/.  Skips cleanly when either binary is
absent so CI without the packages still passes.

CI setup (Ubuntu):
    apt-get install -y tesseract-ocr poppler-utils
"""

from pathlib import Path

import pytest

from app.services.ocr_service import (
    extract_receipt,
    ocr_image_bytes,
    poppler_available,
    rasterize_pdf,
    tesseract_available,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ocr"

# Ground-truth values for each fixture (from the README / verify_receipts.py).
GROUND_TRUTH = {
    "vendor-office-supply-receipt.pdf": {
        "merchant": "acme office supply",
        "date": "2026-08-14",
        "subtotal": "71.73",
        "tax": "5.20",
        "total": "76.93",
    },
    "meal-business-lunch-receipt.pdf": {
        "merchant": "olive & oak bistro",
        "date": "2026-08-15",
        "subtotal": "48.00",
        "tax": "4.20",
        "total": "52.20",
    },
    "vendor-fuel-receipt.pdf": {
        "merchant": "sunrise fuel",
        "date": "2026-08-10",
        "subtotal": None,
        "tax": None,
        "total": "39.31",
    },
}

needs_tesseract = pytest.mark.skipif(
    not tesseract_available(), reason="tesseract not installed"
)
needs_poppler = pytest.mark.skipif(
    not poppler_available(), reason="poppler-utils not installed"
)


def _run_pipeline(pdf_name: str) -> dict:
    """Rasterize → OCR → parse a fixture PDF through the real pipeline."""
    pdf_path = FIXTURES / pdf_name
    assert pdf_path.is_file(), f"fixture missing: {pdf_path}"

    png_bytes, page_count = rasterize_pdf(pdf_path.read_bytes())
    assert page_count >= 1
    assert len(png_bytes) > 0

    raw_text = ocr_image_bytes(png_bytes)
    assert raw_text.strip(), "tesseract returned empty text"

    return extract_receipt(raw_text)


# --- Tests ---


@pytest.mark.integration
@needs_tesseract
@needs_poppler
class TestOCRIntegration:
    """Each fixture exercises a distinct receipt pattern."""

    def test_office_supply(self):
        """Multi-line items, subtotal + tax + total."""
        result = _run_pipeline("vendor-office-supply-receipt.pdf")
        truth = GROUND_TRUTH["vendor-office-supply-receipt.pdf"]

        assert truth["merchant"] in result["merchant"]["value"].lower()
        assert result["date"] == truth["date"]
        assert result["subtotal"] == truth["subtotal"]
        assert result["tax"] == truth["tax"]
        assert result["total"] == truth["total"]
        assert result["total_confidence"] == "high"

    def test_meal_with_tip(self):
        """Tip line must NOT be picked as the total."""
        result = _run_pipeline("meal-business-lunch-receipt.pdf")
        truth = GROUND_TRUTH["meal-business-lunch-receipt.pdf"]

        assert truth["merchant"] in result["merchant"]["value"].lower()
        assert result["date"] == truth["date"]
        assert result["subtotal"] == truth["subtotal"]
        assert result["tax"] == truth["tax"]
        assert result["total"] == truth["total"]
        assert result["total_confidence"] == "high"

    def test_fuel_total_only(self):
        """No tax line — total-only via anchor detection."""
        result = _run_pipeline("vendor-fuel-receipt.pdf")
        truth = GROUND_TRUTH["vendor-fuel-receipt.pdf"]

        assert truth["merchant"] in result["merchant"]["value"].lower()
        assert result["date"] == truth["date"]
        assert result["tax"] is None
        assert result["total"] == truth["total"]
        assert result["total_confidence"] == "high"
