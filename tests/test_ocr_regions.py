"""Region OCR (v2 box-to-fix backend): geometry validation, field-aware
normalization, and a self-locating integration test — full-page words find
the largest amount's box, then region-OCR of that box must read the same
value back through the crop/upscale/threshold/whitelist pipeline."""

import re
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import ocr_regions, ocr_service

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "ocr").glob("*.pdf"))

needs_pipeline = pytest.mark.skipif(
    shutil.which("tesseract") is None or shutil.which("pdftoppm") is None,
    reason="tesseract/poppler not installed",
)


def _png_1x1() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (60, 40), 255).save(buf, format="PNG")
    return buf.getvalue()


def test_unknown_field_type_rejected():
    with pytest.raises(ocr_regions.RegionError):
        ocr_regions.ocr_region(_png_1x1(), 0, 0, 20, 20, field_type="salary")


def test_tiny_region_rejected():
    with pytest.raises(ocr_regions.RegionError):
        ocr_regions.ocr_region(_png_1x1(), 0, 0, 1, 1, field_type="text")


def test_unreadable_image_rejected():
    with pytest.raises(ocr_regions.RegionError):
        ocr_regions.ocr_region(b"not an image", 0, 0, 20, 20, field_type="text")


def test_amount_normalization():
    assert ocr_regions._normalize("$1,234.56", "amount") == ("1234.56", "high")
    assert ocr_regions._normalize("4913", "amount") == ("4913", "low")
    assert ocr_regions._normalize("", "amount") == (None, "missing")


def test_date_normalization():
    assert ocr_regions._normalize("08/30/2026", "date") == ("2026-08-30", "high")
    value, conf = ocr_regions._normalize("3O/O8/26", "date")
    assert conf == "low"


@needs_pipeline
@pytest.mark.skipif(not FIXTURES, reason="no OCR fixtures")
def test_region_reads_back_the_word_the_page_found():
    """Self-locating: rasterize a fixture, find the largest X.XX amount's
    word box via the full-page tsv pass, then region-OCR that box (padded)
    as an amount — the pipeline must read the same number."""
    png, _ = ocr_service.rasterize_pdf(FIXTURES[0].read_bytes())
    _text, words = ocr_service.ocr_image_words(png)
    amounts = [
        w for w in words if re.fullmatch(r"\$?\d{1,3}(,\d{3})*\.\d{2}", w["text"])
    ]
    assert amounts, "fixture contains no amount-shaped words"
    target = max(
        amounts,
        key=lambda w: Decimal(w["text"].replace("$", "").replace(",", "")),
    )
    expected = f'{Decimal(target["text"].replace("$", "").replace(",", "")):.2f}'

    pad = 6
    result = ocr_regions.ocr_region(
        png,
        left=target["left"] - pad,
        top=target["top"] - pad,
        width=target["width"] + 2 * pad,
        height=target["height"] + 2 * pad,
        field_type="amount",
    )
    assert result["value"] == expected, result
    assert result["confidence"] == "high"


@needs_pipeline
@pytest.mark.skipif(not FIXTURES, reason="no OCR fixtures")
def test_region_endpoint_end_to_end(client, monkeypatch, tmp_path):
    """Scan a fixture through the API, then region-OCR a full-page merchant
    box via the endpoint using the returned intake id + image endpoint."""
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    fx = FIXTURES[0]
    r = client.post(
        "/api/ocr/receipt",
        files={"file": (fx.name, fx.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    intake_id = r.json()["intake_id"]

    img = client.get(f"/api/ocr/intake/{intake_id}/image")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")

    # Aim the merchant box at the actual top text band — a fixed top strip
    # can land in the page margin at 300 DPI.
    _text, words = ocr_service.ocr_image_words(img.content)
    assert words
    band_top = min(w["top"] for w in words)
    band_bottom = max(
        w["top"] + w["height"] for w in words if w["top"] < band_top + 160
    )
    from PIL import Image
    import io

    with Image.open(io.BytesIO(img.content)) as im:
        width, _height = im.size
    rr = client.post(
        f"/api/ocr/intake/{intake_id}/region",
        json={
            "left": 0,
            "top": max(0, band_top - 8),
            "width": width,
            "height": (band_bottom - band_top) + 16,
            "field_type": "merchant",
        },
    )
    assert rr.status_code == 200, rr.text
    body = rr.json()
    assert body["field_type"] == "merchant"
    assert body["value"], body

    bad = client.post(
        f"/api/ocr/intake/{intake_id}/region",
        json={"left": 0, "top": 0, "width": 2, "height": 2, "field_type": "text"},
    )
    assert bad.status_code == 400


class _FakeNativeEngine:
    """Native-engine stand-in: name != tesseract, recognizes a prepared crop."""

    name = "winrt"

    def __init__(self, text):
        self._text = text
        self.saw = None

    def recognize(self, data):
        from app.services.ocr_engines import OcrResult

        self.saw = data
        return OcrResult(text=self._text, words=[], language="en-US", engine=self.name)


def test_native_engine_region_path_skips_tesseract(monkeypatch):
    """With a native engine, ocr_region must read via engine.recognize and
    never spawn tesseract (frozen builds without it stay fully featured)."""
    import subprocess as sp

    def boom(*a, **k):
        raise AssertionError("tesseract must not be spawned on the native path")

    monkeypatch.setattr(sp, "run", boom)

    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("L", (200, 80), 255).save(buf, format="PNG")

    eng = _FakeNativeEngine("$1,234.56")
    result = ocr_regions.ocr_region(
        buf.getvalue(), 10, 10, 120, 40, field_type="amount", engine=eng
    )
    assert result["value"] == "1234.56"
    assert result["confidence"] == "high"
    assert eng.saw is not None  # the engine really was handed the crop
    with Image.open(_io.BytesIO(eng.saw)) as crop:
        assert crop.size == (120 * ocr_regions.UPSCALE, 40 * ocr_regions.UPSCALE)


def test_tesseract_engine_still_uses_subprocess_path(monkeypatch):
    """A tesseract engine (or engine=None) keeps the PSM/whitelist path."""

    class _Tess:
        name = "tesseract"

        def recognize(self, data):  # pragma: no cover — must not be called
            raise AssertionError("tesseract engine must use the subprocess path")

    import subprocess as sp

    class _Proc:
        returncode = 0
        stdout = b"49.13"
        stderr = b""

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")

    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("L", (200, 80), 255).save(buf, format="PNG")
    result = ocr_regions.ocr_region(
        buf.getvalue(), 10, 10, 120, 40, field_type="amount", engine=_Tess()
    )
    assert result["value"] == "49.13"


def test_amount_and_date_normalization_tolerates_winrt_spacing():
    """WinRT splits tokens in upscaled crops ("49 .13"); the normalizer
    must still produce clean values (VH308 hardware-lap regression)."""
    assert ocr_regions._normalize("49 .13", "amount") == ("49.13", "high")
    assert ocr_regions._normalize("46 . 24", "amount") == ("46.24", "high")
    assert ocr_regions._normalize("08 / 30 / 2026", "date") == ("2026-08-30", "high")
