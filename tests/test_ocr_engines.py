"""Engine seam (delivery-plan step 1.5): selection, TSV word extraction,
and the review fixes from #71 (tesseract '+' language join, 400 on
unreadable input).

The Vision/WinRT adapters are hardware-verify-pending and only their
selection/degrade logic is testable here; the tesseract path is fully
exercised (CI installs tesseract)."""

import shutil
from pathlib import Path

import pytest

from app.services import ocr_engines, ocr_service

_FIX_DIR = Path(__file__).parent / "fixtures" / "ocr"
FIXTURES = sorted(
    p for p in _FIX_DIR.glob("*") if p.suffix.lower() in (".png", ".jpg", ".pdf")
)
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".pdf": "application/pdf"}

SAMPLE_TSV = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
1\t1\t0\t0\t0\t0\t0\t0\t400\t600\t-1\t
5\t1\t1\t1\t1\t1\t10\t12\t80\t20\t96.5\tACME
5\t1\t1\t1\t1\t2\t95\t12\t90\t20\t95.0\tSUPPLY
5\t1\t1\t1\t2\t1\t10\t40\t60\t18\t91.2\tTOTAL
5\t1\t1\t1\t2\t2\t80\t40\t70\t18\t93.0\t$49.13
5\t1\t2\t1\t1\t1\t10\t80\t95\t18\t88.8\tTHANKS
"""


def test_tsv_parser_reconstructs_lines_and_words():
    text, words = ocr_service._parse_tesseract_tsv(SAMPLE_TSV)
    lines = [ln for ln in text.splitlines() if ln]
    assert lines[0] == "ACME SUPPLY"
    assert lines[1] == "TOTAL $49.13"
    assert lines[2] == "THANKS"
    assert len(words) == 5
    total_word = next(w for w in words if w["text"] == "$49.13")
    assert (total_word["left"], total_word["top"]) == (80, 40)
    assert total_word["conf"] == 93.0


def test_reconstructed_text_still_parses():
    text, _ = ocr_service._parse_tesseract_tsv(SAMPLE_TSV)
    total, conf = ocr_service.parse_total(text)
    assert total == "49.13" and conf == "high"


def test_language_join_uses_plus(monkeypatch):
    """#71 review fix: tesseract -l is '+'-separated; ',' errors out."""
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5", "languages": ["fra", "spa"]},
    )
    assert ocr_service.ocr_language() == "fra+spa"


def test_engine_selection_env_override(monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "tesseract")
    assert ocr_engines.get_engine().name == "tesseract"
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "vision")
    engine = ocr_engines.get_engine()
    assert engine.name == "vision"
    # On Linux CI the Vision bridge can't import: it must degrade, not raise
    assert engine.available() is False
    assert engine.unavailable_reason()


def test_engine_selection_auto_falls_back_to_tesseract(monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "auto")
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5", "languages": ["eng"]},
    )
    engine = ocr_engines.get_engine()
    assert engine.name == "tesseract"
    assert engine.available() is True


def test_status_endpoint_reports_engine(client, monkeypatch):
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5.5.0", "languages": ["eng"]},
    )
    body = client.get("/api/ocr/status").json()
    assert body["engine"] == "tesseract"
    assert body["available"] is True


needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed"
)


@needs_tesseract
def test_unreadable_image_is_400_not_500(client, monkeypatch, tmp_path):
    """#71 review fix: junk bytes with a valid image MIME must 400."""
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("junk.png", b"this is not a png", "image/png")},
    )
    assert r.status_code == 400
    assert "read the image" in r.json()["detail"]


@needs_tesseract
@pytest.mark.skipif(not FIXTURES, reason="no OCR fixtures")
def test_real_scan_returns_words_and_engine(client, monkeypatch, tmp_path):
    fixture = FIXTURES[0]
    if fixture.suffix.lower() == ".pdf" and shutil.which("pdftoppm") is None:
        pytest.skip("pdf fixture but poppler not installed")
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    r = client.post(
        "/api/ocr/receipt",
        files={
            "file": (
                fixture.name,
                fixture.read_bytes(),
                _MIME[fixture.suffix.lower()],
            )
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "tesseract"
    assert body["words"], "expected word boxes from the tsv path"
    first = body["words"][0]
    assert {"text", "left", "top", "width", "height", "conf"} <= set(first)


def test_total_anchor_on_last_line_does_not_crash():
    """Corpus-found crash: a TOTAL anchor as the final OCR line with no
    amount after it indexed past the end of the line list."""
    total, conf = ocr_service.parse_total("SOME SHOP\nitems here\nTOTAL")
    assert total is None and conf == "missing"
    # Anchor on last line WITH an amount on it still works
    total, conf = ocr_service.parse_total("SOME SHOP\nTOTAL 12.34")
    assert total == "12.34" and conf == "high"
    # And the legitimate two-line-lookahead case is preserved
    total, conf = ocr_service.parse_total("AMOUNT DUE\n\n$123.45")
    assert total == "123.45" and conf == "high"


def test_recognize_rescales_boxes_to_original_space(monkeypatch):
    """preprocess_page upscales; recognize must divide word boxes back so
    the canvas draws in the original image's coordinates."""
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5", "languages": ["eng"]},
    )
    monkeypatch.setattr(ocr_service, "preprocess_page", lambda data: (data, 3))
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_words",
        lambda data, lang=None: (
            "TOTAL 49.13",
            [
                {
                    "text": "49.13",
                    "left": 300,
                    "top": 90,
                    "width": 60,
                    "height": 30,
                    "conf": 90.0,
                }
            ],
        ),
    )
    result = ocr_engines.TesseractEngine().recognize(b"png")
    box = result.words[0]
    assert (box.left, box.top, box.width, box.height) == (100, 30, 20, 10)


def test_preprocess_page_survives_junk_bytes():
    data, factor = ocr_service.preprocess_page(b"not an image at all")
    assert data == b"not an image at all" and factor == 1


def test_engine_preference_from_settings(monkeypatch):
    """The stored ocr_engine setting steers selection; the env override
    (support tool) still outranks it; junk values fall through to auto."""
    from app.services import ocr_engines

    monkeypatch.delenv("SLOWBOOKS_OCR_ENGINE", raising=False)
    assert ocr_engines.get_engine("tesseract").name == "tesseract"
    # Unrecognized preference -> auto path (never raises)
    assert ocr_engines.get_engine("copperplate").name in (
        "tesseract",
        "vision",
        "winrt",
    )
    # Env override wins over the stored preference
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "tesseract")
    assert ocr_engines.get_engine("winrt").name == "tesseract"


def test_status_endpoint_honors_setting(client, db_session, monkeypatch):
    """Setting ocr_engine=tesseract makes /api/ocr/status report tesseract
    even where a native engine would win auto-selection."""
    from app.services import ocr_service
    from app.services.settings_service import set_setting

    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5.0-test", "languages": ["eng"]},
    )

    set_setting(db_session, "ocr_engine", "tesseract")
    db_session.commit()

    r = client.get("/api/ocr/status")
    assert r.status_code == 200
    assert r.json()["engine"] == "tesseract"
