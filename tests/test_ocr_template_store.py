"""Merchant template memory (receipt intake v3): persistence, scan-path
application, and the skip-the-canvas gate.

The core geometric logic (anchor-relative encode/resolve) is covered in
test_ocr_templates.py; here we test the layer above it — recording operator
corrections, matching new scans to stored templates, region-reading the
remembered boxes, and the endpoint wiring — with region OCR faked so no
tesseract binary is needed."""

import io

from app.models.ocr_templates import OcrTemplate
from app.services import ocr_regions, ocr_service, ocr_template_store

# A fake receipt: word boxes in "scan A" space, and the same layout at 2x
# scale + 7px shift for "scan B" (a rescan at different DPI/placement).
TEXT = (
    "NEON PULSE TECHSHOP\n"
    "DATE 08/30/2026\n"
    "SUBTOTAL 46.24\n"
    "TAX 2.89\n"
    "TOTAL 49.13\n"
)


def _words(scale=1, shift=0):
    base = [
        ("NEON", 10, 10, 60, 20),
        ("PULSE", 75, 10, 60, 20),
        ("TECHSHOP", 140, 10, 90, 20),
        ("DATE", 10, 40, 40, 16),
        ("08/30/2026", 60, 40, 90, 16),
        ("SUBTOTAL", 10, 70, 80, 16),
        ("46.24", 120, 70, 50, 16),
        ("TAX", 10, 100, 30, 16),
        ("2.89", 120, 100, 45, 16),
        ("TOTAL", 10, 130, 50, 16),
        ("49.13", 120, 130, 50, 16),
    ]
    return [
        {
            "text": t,
            "left": left * scale + shift,
            "top": top * scale + shift,
            "width": w * scale,
            "height": h * scale,
            "conf": 90.0,
        }
        for (t, left, top, w, h) in base
    ]


def _box_around(word):
    return {
        "left": word["left"] - 4,
        "top": word["top"] - 4,
        "width": word["width"] + 8,
        "height": word["height"] + 8,
    }


def _fake_region_reader(words):
    """Region OCR fake: return the text of the word whose center falls in
    the requested box — position-faithful, no tesseract."""

    def fake(image_data, left, top, width, height, field_type, engine=None):
        for w in words:
            cx = w["left"] + w["width"] / 2
            cy = w["top"] + w["height"] / 2
            if left <= cx <= left + width and top <= cy <= top + height:
                value = w["text"].replace("$", "")
                return {
                    "text": w["text"],
                    "value": value,
                    "field_type": field_type,
                    "confidence": "high",
                }
        return {
            "text": "",
            "value": None,
            "field_type": field_type,
            "confidence": "missing",
        }

    return fake


def _teach(db, merchant="NEON PULSE TECHSHOP"):
    words = _words()
    by_text = {w["text"]: w for w in words}
    for field, word in (
        ("total", by_text["49.13"]),
        ("tax", by_text["2.89"]),
        ("subtotal", by_text["46.24"]),
    ):
        assert ocr_template_store.record_correction(
            db, merchant, field, _box_around(word), words
        )


def test_record_and_find(db_session):
    _teach(db_session)
    row = db_session.query(OcrTemplate).one()
    assert row.merchant_key == "NEON PULSE TECHSHOP"
    assert set(ocr_template_store._fields(row)) == {"total", "tax", "subtotal"}

    # Exact merchant, store-number variant, and header-line fallback all hit
    assert (
        ocr_template_store.find_for_scan(db_session, "NEON PULSE TECHSHOP", "") is row
    )
    assert (
        ocr_template_store.find_for_scan(db_session, "Neon Pulse Techshop #04", "")
        is row
    )
    assert ocr_template_store.find_for_scan(db_session, None, TEXT) is row
    assert ocr_template_store.find_for_scan(db_session, "HOME DEPOT", "") is None


def test_record_rejects_bad_input(db_session):
    words = _words()
    box = _box_around(words[-1])
    assert not ocr_template_store.record_correction(db_session, "", "total", box, words)
    assert not ocr_template_store.record_correction(
        db_session, "NEON PULSE", "salary", box, words
    )
    assert db_session.query(OcrTemplate).count() == 0


def test_apply_template_survives_scale_and_shift(db_session, monkeypatch):
    """Boxes taught on scan A must land on the right words in a 2x-scaled,
    shifted scan B — and a successful application bumps use_count."""
    _teach(db_session)
    template = db_session.query(OcrTemplate).one()

    words_b = _words(scale=2, shift=7)
    monkeypatch.setattr(ocr_regions, "ocr_region", _fake_region_reader(words_b))
    reads = ocr_template_store.apply_template(db_session, template, b"png", words_b)

    assert {k: v["value"] for k, v in reads.items()} == {
        "total": "49.13",
        "tax": "2.89",
        "subtotal": "46.24",
    }
    assert ocr_template_store.reads_are_clean(reads)
    db_session.refresh(template)
    assert template.use_count == 1


def test_reads_are_clean_gate():
    good = {
        k: {"value": v, "confidence": "high"}
        for k, v in (("total", "49.13"), ("tax", "2.89"), ("subtotal", "46.24"))
    }
    assert ocr_template_store.reads_are_clean(good)

    low = {**good, "tax": {"value": "2.89", "confidence": "low"}}
    assert not ocr_template_store.reads_are_clean(low)

    off = {**good, "total": {"value": "50.00", "confidence": "high"}}
    assert not ocr_template_store.reads_are_clean(off)

    assert not ocr_template_store.reads_are_clean(
        {"total": {"value": "49.13", "confidence": "high"}}
    )


def _mock_engine(monkeypatch, tmp_path, words):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")
    monkeypatch.setattr(ocr_service, "preprocess_page", lambda data: (data, 1))
    monkeypatch.setattr(
        ocr_service, "ocr_image_words", lambda data, lang=None: (TEXT, words)
    )


def test_scan_applies_template_end_to_end(client, db_session, monkeypatch, tmp_path):
    """Teach a template on scan A, then POST scan B (2x + shift) through the
    API: template reads override the parse, the clean gate flips
    template_applied, and the response says which fields came from memory."""
    _teach(db_session)
    words_b = _words(scale=2, shift=7)
    _mock_engine(monkeypatch, tmp_path, words_b)
    monkeypatch.setattr(ocr_regions, "ocr_region", _fake_region_reader(words_b))

    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("r.png", io.BytesIO(b"fakepng").read(), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["template_applied"] is True
    assert body["template_fields"] == ["subtotal", "tax", "total"]
    assert body["total"] == "49.13"
    assert body["tax"] == "2.89"
    assert body["subtotal"] == "46.24"
    assert body["partial"] is False


def test_region_save_teaches_template(client, db_session, monkeypatch, tmp_path):
    """A canvas region read with save_template records the merchant layout;
    without the flag nothing is stored."""
    words = _words()
    _mock_engine(monkeypatch, tmp_path, words)
    monkeypatch.setattr(ocr_regions, "ocr_region", _fake_region_reader(words))

    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("r.png", io.BytesIO(b"fakepng").read(), "image/png")},
    )
    intake_id = r.json()["intake_id"]
    total_box = _box_around(_words()[-1])

    rr = client.post(
        f"/api/ocr/intake/{intake_id}/region",
        json={**total_box, "field_type": "amount"},
    )
    assert rr.status_code == 200 and rr.json()["template_saved"] is False
    assert db_session.query(OcrTemplate).count() == 0

    rr = client.post(
        f"/api/ocr/intake/{intake_id}/region",
        json={
            **total_box,
            "field_type": "amount",
            "merchant": "NEON PULSE TECHSHOP",
            "field_key": "total",
            "save_template": True,
        },
    )
    assert rr.status_code == 200, rr.text
    assert rr.json()["template_saved"] is True
    row = db_session.query(OcrTemplate).one()
    assert row.merchant_key == "NEON PULSE TECHSHOP"
    assert "total" in ocr_template_store._fields(row)
