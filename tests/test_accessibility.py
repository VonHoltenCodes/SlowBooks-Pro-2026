"""Accessibility guards: the audit fixes stay fixed.

Source-level checks (every table header has a scope, icon-only buttons
carry a label, the toast region is live, the modal is a dialog, every PDF
template declares a language and a title) plus one behavioural check: the
PDFs we generate are tagged (PDF/UA-1), so a screen reader gets structure
instead of a picture of text."""

import glob
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = sorted(glob.glob(str(ROOT / "app/static/js/*.js")))
TEMPLATES = sorted(glob.glob(str(ROOT / "app/templates/*.html")))


def test_every_table_header_has_a_scope():
    offenders = []
    for f in JS + [str(ROOT / "index.html")]:
        for m in re.finditer(r"<th(?![^>]*\bscope=)[\s>]", Path(f).read_text()):
            offenders.append(f"{Path(f).name}:{m.start()}")
    assert not offenders, offenders[:10]


def test_icon_only_remove_buttons_have_labels():
    offenders = []
    for f in JS:
        for m in re.finditer(
            r"<button[^>]*>\s*(X|×|&times;)\s*</button>", Path(f).read_text()
        ):
            if "aria-label" not in m.group(0):
                offenders.append(f"{Path(f).name}: {m.group(0)[:80]}")
    assert not offenders, offenders


def test_toast_region_is_live_and_modal_is_a_dialog():
    html = (ROOT / "index.html").read_text()
    toast = re.search(r'<div id="toast-container"[^>]*>', html).group(0)
    assert 'aria-live="polite"' in toast and 'role="status"' in toast
    modal = re.search(r'<div id="modal"[^>]*>', html).group(0)
    assert (
        'role="dialog"' in modal
        and 'aria-modal="true"' in modal
        and 'aria-labelledby="modal-title"' in modal
    )
    utils = (ROOT / "app/static/js/utils.js").read_text()
    assert (
        "Escape" in utils and "_modalOpener" in utils
    )  # focus trap + restore live here


def test_pdf_templates_declare_language_and_title():
    missing = []
    for f in TEMPLATES:
        text = Path(f).read_text()
        if "<html" in text and not re.search(r"<html[^>]*\blang=", text):
            missing.append(f"{Path(f).name}: lang")
        if (
            "<head>" in text
            and "<title>" not in text
            and not Path(f).name.startswith("_")
        ):
            missing.append(f"{Path(f).name}: title")
    assert not missing, missing


def test_muted_text_token_clears_aa_contrast():
    """--gray-400 is used as body-text colour in 16 places; it must clear
    4.5:1 against the light ground it sits on."""

    def lum(hexcolor):
        r, g, b = (int(hexcolor[i : i + 2], 16) / 255 for i in (1, 3, 5))

        def f(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    css = (ROOT / "app/static/css/style.css").read_text()
    tok = re.search(r"--gray-400:\s*(#[0-9a-fA-F]{6})", css).group(1)
    ratio = (lum("#ffffff") + 0.05) / (lum(tok) + 0.05)
    assert ratio >= 4.5, f"--gray-400 {tok} is {ratio:.2f}:1 on white"


def test_generated_pdfs_are_tagged(client, seed_accounts, seed_customer):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "lines": [{"description": "Accessible invoice", "quantity": 1, "rate": 10}],
        },
    )
    assert inv.status_code in (200, 201), inv.text
    resp = client.get(f"/api/invoices/{inv.json()['id']}/pdf")
    assert resp.status_code == 200, resp.text[:200]
    pdf = resp.content
    assert pdf.startswith(b"%PDF")
    # WeasyPrint writes object streams, so the structure tree isn't visible
    # in the raw bytes; ask poppler (installed in CI for the OCR tests).
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("pdfinfo"):
        pytest.skip("pdfinfo (poppler-utils) not available")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(pdf)
        path = fh.name
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout
    assert "Tagged:          yes" in info, info
    try:
        from pypdf import PdfReader

        root = PdfReader(path).trailer["/Root"]
        assert "/StructTreeRoot" in root and str(root.get("/Lang", "")).startswith("en")
    except ImportError:  # pragma: no cover
        pass
