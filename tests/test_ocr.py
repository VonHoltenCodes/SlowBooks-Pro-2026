# ============================================================================
# Receipt / Document Intake — Tier 2 OCR tests.
#
# Unit tests exercise the deterministic parsers directly; endpoint tests mock
# the Tesseract call (ocr_image) so they run everywhere, including CI without
# the binary. Integration tests against real Tesseract + the ground-truth
# PDFs in assets/sample-receipts land in the finishing-touches slice.
# ============================================================================

import json
import os
from datetime import datetime, timedelta

from app.services import ocr_service

# Mirror of assets/sample-receipts/vendor-office-supply-receipt.pdf
CANNED_RECEIPT_TEXT = """ACME OFFICE SUPPLY CO.
4820 Industrial Parkway
Columbus, OH 43215
(614) 555-0164
Invoice/Receipt #: 88312    Date: 08/14/2026
Staples, 1/2" (box)      $8.75
Toner Cartridge, black  $54.99
Printer Paper, 500 ct   $7.99
Subtotal    $71.73
Tax (7.25%) $5.20
TOTAL      $76.93
VISA **** 2271
"""


def _png_bytes() -> bytes:
    """Arbitrary image-ish bytes. The route no longer decodes images with PIL
    (Tesseract reads them natively), and ocr_image_bytes is mocked in these
    endpoint tests, so content is irrelevant here."""
    return b"\x89PNG\r\n\x1a\nfake-image-data"


# ---------------------------------------------------------------------------
# Parser units
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_mmddyyyy(self):
        assert ocr_service.parse_date("Date: 08/14/2026") == "2026-08-14"

    def test_iso(self):
        assert ocr_service.parse_date("2026-08-14") == "2026-08-14"

    def test_month_name(self):
        assert ocr_service.parse_date("Aug 14, 2026") == "2026-08-14"

    def test_day_month_name(self):
        assert ocr_service.parse_date("14 Aug 2026") == "2026-08-14"

    def test_invalid_date_ignored(self):
        assert ocr_service.parse_date("13/45/2026") is None

    def test_first_date_wins(self):
        text = "Card valid thru 01/2027\nDate: 08/14/2026"
        assert ocr_service.parse_date(text) == "2026-08-14"

    def test_missing(self):
        assert ocr_service.parse_date("no dates here") is None


class TestParseTotal:
    def test_anchor_high_confidence(self):
        total, conf = ocr_service.parse_total(CANNED_RECEIPT_TEXT)
        assert total == "76.93" and conf == "high"

    def test_amount_due_anchor(self):
        total, conf = ocr_service.parse_total("AMOUNT DUE\n$123.45")
        assert total == "123.45" and conf == "high"

    def test_fallback_largest_low_confidence(self):
        total, conf = ocr_service.parse_total("Item $5.00\nItem $12.50\nItem $8.00")
        assert total == "12.50" and conf == "low"

    def test_tip_not_picked_as_total(self):
        text = "Subtotal $48.00\nTax $4.20\nTip $9.00\nTOTAL $52.20"
        total, conf = ocr_service.parse_total(text)
        assert total == "52.20" and conf == "high"

    def test_thousands_separator(self):
        total, conf = ocr_service.parse_total("TOTAL $1,234.56")
        assert total == "1234.56" and conf == "high"

    def test_missing(self):
        total, conf = ocr_service.parse_total("no amounts here")
        assert total is None and conf == "missing"


class TestParseTax:
    def test_tax_and_subtotal(self):
        tax, subtotal = ocr_service.parse_tax(CANNED_RECEIPT_TEXT)
        assert tax == "5.20" and subtotal == "71.73"

    def test_tax_included_excluded(self):
        tax, subtotal = ocr_service.parse_tax(
            "Subtotal $10.00\nTax included\nTotal $10.00"
        )
        assert tax is None

    def test_no_tax_line(self):
        tax, subtotal = ocr_service.parse_tax("Unleaded $39.31\nTOTAL $39.31")
        assert tax is None


class TestParseMerchant:
    def test_first_line_high_confidence(self):
        val, conf = ocr_service.parse_merchant(CANNED_RECEIPT_TEXT)
        assert val == "ACME OFFICE SUPPLY CO." and conf == "high"

    def test_skips_amount_and_date_lines(self):
        val, conf = ocr_service.parse_merchant("$76.93\n08/14/2026\nACME SUPPLY")
        assert val == "ACME SUPPLY" and conf == "low"

    def test_skips_phone_number(self):
        val, conf = ocr_service.parse_merchant("(614) 555-0164\nACME SUPPLY")
        assert val == "ACME SUPPLY"

    def test_missing(self):
        val, conf = ocr_service.parse_merchant("$1.00\n08/14/2026\n")
        assert val is None and conf == "missing"


class TestExtract:
    def test_full_receipt(self):
        r = ocr_service.extract_receipt(CANNED_RECEIPT_TEXT)
        assert r["merchant"]["value"] == "ACME OFFICE SUPPLY CO."
        assert r["merchant"]["confidence"] == "high"
        assert r["date"] == "2026-08-14"
        assert r["total"] == "76.93"
        assert r["total_confidence"] == "high"
        assert r["subtotal"] == "71.73"
        assert r["tax"] == "5.20"
        assert r["tax_detected"] is True
        assert r["partial_reasons"] == []

    def test_blank_receipt_flags_partial(self):
        # single-word OCR junk — no merchant, no amounts, no dates
        r = ocr_service.extract_receipt("ZZZZZZ\nQQQQQQ\n")
        assert r["total"] is None
        assert r["merchant"]["value"] is None
        assert any("total not detected" in x for x in r["partial_reasons"])


# ---------------------------------------------------------------------------
# Endpoint tests (Tesseract mocked; intake bucket redirected to tmp_path)
# ---------------------------------------------------------------------------


def _scan(client, monkeypatch, tmp_path, text=CANNED_RECEIPT_TEXT) -> str:
    """Helper: run a mocked scan and return the intake id."""
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")
    monkeypatch.setattr(
        ocr_service, "ocr_image_words", lambda data, lang=None: (text, [])
    )
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200, r.text
    return r.json()["intake_id"]


def test_status_available(client, monkeypatch):
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5.5.0", "languages": ["eng"]},
    )
    r = client.get("/api/ocr/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["version"] == "5.5.0"
    assert body["languages"] == ["eng"]


def test_requires_auth(unauthed_client):
    assert unauthed_client.get("/api/ocr/status").status_code == 401
    assert (
        unauthed_client.post(
            "/api/ocr/receipt",
            files={"file": ("r.png", _png_bytes(), "image/png")},
        ).status_code
        == 401
    )


def test_scan_happy_path(client, monkeypatch, tmp_path):
    intake_id = _scan(client, monkeypatch, tmp_path)
    # intake file + sidecar stored under the (tmp) intake dir
    assert list(tmp_path.glob(f"{intake_id}.*"))


def test_scan_response_fields(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_words",
        lambda data, lang=None: (CANNED_RECEIPT_TEXT, []),
    )
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ocr_available"] is True
    assert len(body["intake_id"]) == 32  # uuid4().hex
    assert body["merchant"]["value"] == "ACME OFFICE SUPPLY CO."
    assert body["date"] == "2026-08-14" and body["date_is_default"] is False
    assert body["total"] == "76.93" and body["total_confidence"] == "high"
    assert body["subtotal"] == "71.73"
    assert body["tax"] == "5.20" and body["tax_detected"] is True
    assert body["language"] == "eng"
    assert body["multi_page"] is False
    assert body["partial"] is False and body["partial_reasons"] == []
    assert "ACME OFFICE SUPPLY" in body["raw_text"]


def test_scan_tesseract_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: False)
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ocr_available"] is False
    assert "Tesseract OCR is not installed" in body["message"]
    assert body["intake_id"] is None


def test_scan_missing_date_defaults_today(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_words",
        lambda data, lang=None: ("MY VENDOR\nTOTAL $10.00\n", []),
    )
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["date"] is not None and body["date_is_default"] is True
    assert body["partial"] is True
    assert any("today's date" in x for x in body["partial_reasons"])


def test_scan_unusable_language_data(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: None)
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["ocr_available"] is False
    assert "language data" in r.json()["message"]


def test_scan_bad_content_type(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_scan_oversize(client, monkeypatch, tmp_path):
    from fastapi import HTTPException

    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)

    async def tiny_read_limited(file, max_bytes=0, label="File"):
        content = await file.read(1024 + 1)
        if len(content) > 1024:
            raise HTTPException(status_code=413, detail=f"{label} too large")
        return content

    monkeypatch.setattr("app.routes.ocr.read_limited", tiny_read_limited)
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("big.png", b"x" * 2048, "image/png")},
    )
    assert r.status_code == 413


def test_scan_pdf_multi_page(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_words",
        lambda data, lang=None: (CANNED_RECEIPT_TEXT, []),
    )
    monkeypatch.setattr(
        ocr_service,
        "rasterize_pdf",
        lambda data, dpi=200: (b"\x89PNG-rasterized", 3),
    )
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 200
    assert r.json()["multi_page"] is True


def test_scan_pdf_without_poppler_400(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "poppler_available", lambda: False)
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("receipt.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400
    assert "poppler-utils" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Intake lifecycle
# ---------------------------------------------------------------------------


def test_attach_to_invoice(
    client, db_session, monkeypatch, tmp_path, seed_accounts, seed_customer
):
    intake_id = _scan(client, monkeypatch, tmp_path)

    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-08-01",
            "terms": "Net 30",
            "lines": [{"description": "Test line", "quantity": 1, "rate": "10.00"}],
        },
    )
    assert inv.status_code == 201, inv.text
    inv_id = inv.json()["id"]

    r = client.post(
        f"/api/ocr/intake/{intake_id}/attach",
        json={"entity_type": "invoice", "entity_id": inv_id},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["entity_type"] == "invoice"
    assert body["entity_id"] == inv_id
    assert "receipt.png" in body["filename"]

    from app.models.attachments import Attachment

    row = (
        db_session.query(Attachment)
        .filter(Attachment.entity_type == "invoice", Attachment.entity_id == inv_id)
        .first()
    )
    assert row is not None
    # intake consumed
    assert list(tmp_path.glob(f"{intake_id}.*")) == []


def test_attach_to_expense(client, db_session, monkeypatch, tmp_path, seed_accounts):
    """Paid receipts land on the one-step Expense form; the scan attaches to
    the posted transaction."""
    intake_id = _scan(client, monkeypatch, tmp_path)
    exp = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-02",
            "payee": "Sweet Forest Cafe",
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "30.30",
        },
    )
    assert exp.status_code == 201, exp.text
    exp_id = exp.json()["id"]

    r = client.post(
        f"/api/ocr/intake/{intake_id}/attach",
        json={"entity_type": "expense", "entity_id": exp_id},
    )
    assert r.status_code == 201, r.text
    assert r.json()["entity_type"] == "expense"
    listed = client.get(f"/api/attachments/expense/{exp_id}")
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert list(tmp_path.glob(f"{intake_id}.*")) == []

    # A non-expense transaction id is not an expense.
    intake_id = _scan(client, monkeypatch, tmp_path)
    r = client.post(
        f"/api/ocr/intake/{intake_id}/attach",
        json={"entity_type": "expense", "entity_id": 999999},
    )
    assert r.status_code == 404


def test_attach_missing_entity(client, monkeypatch, tmp_path):
    intake_id = _scan(client, monkeypatch, tmp_path)
    r = client.post(
        f"/api/ocr/intake/{intake_id}/attach",
        json={"entity_type": "invoice", "entity_id": 999999},
    )
    assert r.status_code == 404
    # intake survives a failed attach
    assert list(tmp_path.glob(f"{intake_id}.*"))


def test_attach_bad_entity_type(client, monkeypatch, tmp_path):
    intake_id = _scan(client, monkeypatch, tmp_path)
    r = client.post(
        f"/api/ocr/intake/{intake_id}/attach",
        json={"entity_type": "vendor", "entity_id": 1},
    )
    assert r.status_code == 400


def test_attach_expired_intake_404(client, monkeypatch, tmp_path):
    intake_id = _scan(client, monkeypatch, tmp_path)
    # backdate the sidecar beyond the TTL
    meta_path = tmp_path / f"{intake_id}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = (datetime.now() - timedelta(hours=25)).isoformat()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    r = client.post(
        f"/api/ocr/intake/{intake_id}/attach",
        json={"entity_type": "invoice", "entity_id": 1},
    )
    assert r.status_code == 404
    assert "expired" in r.json()["detail"]


def test_delete_intake(client, monkeypatch, tmp_path):
    intake_id = _scan(client, monkeypatch, tmp_path)
    assert client.delete(f"/api/ocr/intake/{intake_id}").status_code == 200
    assert list(tmp_path.glob(f"{intake_id}.*")) == []
    # idempotent
    assert client.delete(f"/api/ocr/intake/{intake_id}").status_code == 200


def test_sweep_expires_old_intakes(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    old_id = ocr_service.save_intake(b"x", "old.png", "image/png")
    fresh_id = ocr_service.save_intake(b"y", "fresh.png", "image/png")
    # backdate only the OLD intake (after both saves, since each save sweeps)
    meta_path = tmp_path / f"{old_id}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = (datetime.now() - timedelta(hours=25)).isoformat()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert ocr_service.sweep_intake() == 1
    assert ocr_service.get_intake(old_id) is None
    assert ocr_service.get_intake(fresh_id) is not None


def test_get_intake_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    assert ocr_service.get_intake("..%2f..%2fetc") is None
    assert ocr_service.get_intake("nothex") is None


# ---------------------------------------------------------------------------
# Direct subprocess plumbing (no pytesseract)
# ---------------------------------------------------------------------------


def test_direct_subprocess_tesseract(tmp_path, monkeypatch):
    """Prove the no-wrapper design: a fake `tesseract` on PATH is invoked via
    subprocess with stdin/stdout, and its stdout becomes the OCR text. This
    exercises the real plumbing (args, stdin pipe, stdout parse) without the
    real binary — so it runs in CI even when tesseract is absent."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "tesseract"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        '  echo "tesseract 9.9.9"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "--list-langs" ]; then\n'
        '  echo "eng"\n'
        "  exit 0\n"
        "fi\n"
        "cat > /dev/null\n"
        'printf "FAKE MERCHANT\\nTOTAL $42.00\\n"\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    # tesseract_info is cached for 60s — force a fresh probe against the fake
    monkeypatch.setattr(ocr_service, "_cache", {"at": 0.0, "info": None})

    assert ocr_service.tesseract_available() is True
    info = ocr_service.tesseract_info()
    assert info["version"] == "9.9.9"
    assert "eng" in info["languages"]
    assert ocr_service.ocr_language() == "eng"

    text = ocr_service.ocr_image_bytes(b"pretend-image-bytes", lang="eng")
    assert "FAKE MERCHANT" in text
    assert "TOTAL" in text


def test_ocr_image_bytes_failure_raises(tmp_path, monkeypatch):
    """A nonzero tesseract exit surfaces as OCRRuntimeError, not a crash."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "tesseract"
    fake.write_text("#!/bin/sh\necho 'bad image data' >&2\nexit 2\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setattr(ocr_service, "_cache", {"at": 0.0, "info": None})

    import pytest

    with pytest.raises(ocr_service.OCRRuntimeError) as excinfo:
        ocr_service.ocr_image_bytes(b"junk", lang="eng")
    assert "exit 2" in str(excinfo.value)


def test_parse_tax_looks_ahead_across_split_lines():
    """WinRT puts anchor and amount on separate lines; subtotal/tax get the
    same bounded lookahead as total (VH308 hardware-lap regression)."""
    text = "NEON PULSE\nSUBTOTAL\n46.24\nTAX 6.25%\n2.89\nTOTAL\n49.13\n"
    tax, subtotal = ocr_service.parse_tax(text)
    assert tax == "2.89"
    assert subtotal == "46.24"


def test_parse_tax_trailing_anchor_does_not_crash():
    tax, subtotal = ocr_service.parse_tax("stuff\nSUBTOTAL\nTAX")
    assert tax is None and subtotal is None


# ---------------------------------------------------------------------------
# Real-receipt parser behaviour (SROIE corpus eval, 2026-09-02).  The
# synthetic receipts hid every one of these; real thermal prints exposed
# them.  Texts below are trimmed OCR output with the merchant renamed.
# ---------------------------------------------------------------------------

GST_RECEIPT = """\
SAMPLE HARDWARE TRADING
NO 290, JALAN AIR PANAS.
| SIMPLIFIED TAX INVOICE
Salesperson ~ Ref. :
4 742 7.42 SR
"Tetal Oy" 4 742
| Total Sales (Excluding GST) . 7.00
if Discount ‘ 0.00
: TotalGST . 0.42
| Rounding . 0.00
| Total Sales (inclusive of GST) : ( 7.42)
{ CASH : T.A2
Change : 0.00
GST SUMMARY
Tax Code % Amt (RM) Tax (RM)
SR 6 7 00 042
Totel: 7.00 0.42
"""


def test_parse_total_prefers_tax_inclusive_total_over_first_anchor():
    """GST/VAT receipts print "Total (Excluding GST)" BEFORE the real total;
    first-anchor-wins picked the pre-tax figure on 11/20 real receipts."""
    total, conf = ocr_service.parse_total(GST_RECEIPT)
    assert (total, conf) == ("7.42", "high")


def test_parse_tax_reads_gst_and_excluding_total_as_subtotal():
    tax, subtotal = ocr_service.parse_tax(GST_RECEIPT)
    assert tax == "0.42"
    assert subtotal == "7.00"


def test_parse_total_handles_ocr_misspellings_of_total():
    text = "Cashier: X\nTatal (Excluding GST): 4130\nGST Payable: 2.f3\ntotal (Inclusive af GST): 48.15\nTOTAL: 48.15\nCASH : 50.60\n"
    assert ocr_service.parse_total(text) == ("48.15", "high")
    # "Tota!" — the fuzzy word must still be recognised as a subtotal anchor
    _, subtotal = ocr_service.parse_tax(
        "Tota! ':ales (Excluding GST) - 89 50\nTotal GST : 5.37\n"
    )
    assert subtotal == "89.50"


def test_parse_total_takes_post_rounding_total_when_no_strong_anchor():
    text = "Subtotal 87.80\nGST @6% 5.81\nService 8.78\nTotal: 102.39\nTotal 102.40\nCASH 110.00\n"
    assert ocr_service.parse_total(text) == ("102.40", "high")


def test_parse_total_lookahead_only_accepts_amount_only_line():
    """A column header ending in TOTAL / TAX must not swallow the first line
    item on the next line (h007: total=22.50, tax=22.50 before the fix)."""
    text = (
        "Description Qty U.price Total TAX\nBURGER 1 x 22.50 22.50 SR\nTOTAL: 48.15\n"
    )
    assert ocr_service.parse_total(text) == ("48.15", "high")
    tax, _ = ocr_service.parse_tax(text)
    assert tax is None
    # ...but a genuinely split anchor/amount pair still works (WinRT).
    assert ocr_service.parse_total("TOTAL\n49.13\n") == ("49.13", "high")


def test_parse_total_loose_decimal_on_anchor_line():
    """OCR drops the decimal point on thermal prints: "7 00", "140. 00"."""
    assert ocr_service.parse_total(
        "Subtotal 7.06\nTotal Incl. of GST 7 00\nPayment 7.00\n"
    ) == ("7.00", "high")
    assert ocr_service.parse_total("Total Sales (Inclusive of GST) : 140. 00\n") == (
        "140.00",
        "high",
    )


def test_parse_total_skips_excluded_and_zero_anchors():
    text = "Total Qty 3 23.32\nTotal Items: 3\nSub Total 22.00\nTotal GST 1.32\nBalance Due 0.00\nTOTAL 23.32\n"
    assert ocr_service.parse_total(text) == ("23.32", "high")
    tax, subtotal = ocr_service.parse_tax(text)
    assert (tax, subtotal) == ("1.32", "22.00")


def test_parse_tax_ignores_total_lines_that_mention_gst():
    """ "Tota] RM Sucluding GST 6% 73.30" is a total line, not the tax."""
    text = "Tota] RM Sucluding GST 6% 73.30\nRounding 0.00\nTotal Rounded 73.30\nGST SR 69.15 4.18\n"
    tax, _ = ocr_service.parse_tax(text)
    assert tax == "4.18"
    assert ocr_service.parse_total(text) == ("73.30", "high")


def test_parse_tax_skips_headers_and_percent_only_lines():
    text = "Tax Invoice\nGST ID : 000750673920\nGST 6.00 % RO. 2\nTax Code % Amt (RM) Tax (RM)\nSR 6 12.50 0.75\n"
    tax, _ = ocr_service.parse_tax(text)
    assert tax is None


def test_extract_receipt_reconciles_pre_tax_total_with_tax():
    """Total == subtotal means we read the pre-tax line; with tax known the
    real total is the sum, offered at low confidence."""
    r = ocr_service.extract_receipt(
        "SHOP NAME HERE\nSub Total 28.00\nTax 1.68\nTotal 28.00\n"
    )
    assert r["total"] == "29.68"
    assert r["total_confidence"] == "low"
    assert any("low-confidence" in x for x in r["partial_reasons"])


def test_parse_merchant_skips_ocr_noise_lines():
    text = "0) y BO3Z0ly\n—  ByBOlOtd\nSWEET FOREST CAFE\nNO 21,JLN BUNGA KANTAN\n"
    merchant, conf = ocr_service.parse_merchant(text)
    assert merchant == "SWEET FOREST CAFE"
    assert conf == "low"


def test_tesseract_cmd_falls_back_to_stock_install_dir(tmp_path, monkeypatch):
    """The UB Mannheim Windows installer (and Homebrew on a macOS GUI app)
    leave tesseract off PATH; the resolver checks the stock locations."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(ocr_service, "_cache", {"at": 0.0, "info": None})
    assert ocr_service.tesseract_cmd() is None
    assert ocr_service.tesseract_info()["available"] is False

    stock = tmp_path / "Tesseract-OCR" / "tesseract"
    stock.parent.mkdir()
    stock.write_text("#!/bin/sh\nexit 0\n")
    stock.chmod(0o755)
    monkeypatch.setattr(ocr_service, "_tesseract_candidates", lambda: [stock])
    monkeypatch.setattr(ocr_service, "_cache", {"at": 0.0, "info": None})
    assert ocr_service.tesseract_cmd() == str(stock)
    info = ocr_service.tesseract_info()
    assert info["available"] is True
    assert info["path"] == str(stock)


def test_tesseract_candidates_windows_locations(monkeypatch):
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/x/AppData/Local")
    cands = [c.as_posix() for c in ocr_service._tesseract_candidates(windows=True)]
    assert "C:/Program Files/Tesseract-OCR/tesseract.exe" in cands
    assert "C:/Users/x/AppData/Local/Programs/Tesseract-OCR/tesseract.exe" in cands
    posix = [c.as_posix() for c in ocr_service._tesseract_candidates(windows=False)]
    assert "/opt/homebrew/bin/tesseract" in posix
