"""On a Mac a document's Print prints its saved PDF (#1030, 2.32.0 gate
NEW-255).

WebKit ignores ``break-before: avoid`` and ``break-inside: avoid``, so an
invoice whose last rows ended near a sheet's foot printed one more sheet
carrying only its footer, or nothing, and no rule in the sheet could stop
it. WeasyPrint's PDF keeps the footer with the block before it, so the Mac
prints that: desktop_shim.js fetches the sheet's PDF (/pdf in place of
/print-preview, the same query) and print_document_pdf opens the Mac's print
panel on it through PDFKit. Windows and Linux print the sheet as before.
Driven here with stand-ins for AppKit, Foundation, Quartz and PyObjCTools;
the real panel is the Mac lap's."""

import re
import sys
import types
from pathlib import Path

import pytest

import desktop_launcher as dl
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
SHIM = (ROOT / "app/static/js/desktop_shim.js").read_text(encoding="utf-8")
PDF = b"%PDF-1.7\n%%EOF\n"


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


# ---- the route pairs the shim relies on ----------------------------------


def _get_paths():
    """Every GET path of the app with its path parameters, from the
    OpenAPI description (the route table wraps routers lazily)."""
    paths = {}
    for path, methods in app.openapi()["paths"].items():
        if "get" in methods:
            paths[path] = set(re.findall(r"{(\w+)", path))
    return paths


def test_every_print_preview_sheet_has_its_pdf_at_pdf():
    paths = _get_paths()
    previews = [p for p in paths if p.endswith("/print-preview")]
    assert previews, "no print-preview sheets?"
    for preview in previews:
        pdf = preview[: -len("/print-preview")] + "/pdf"
        assert pdf in paths, f"{preview} has no PDF at {pdf}"
        assert paths[pdf] == paths[preview], f"{pdf} takes other path parameters"


# ---- the bridge -------------------------------------------------------------


def test_elsewhere_than_a_mac_the_page_prints_itself(monkeypatch):
    api = dl.PickerApi(port=1)
    for platform in ("linux", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        result = api.print_document_pdf("Invoice_1001.pdf", _b64(PDF))
        assert result["success"] is False and result["unsupported"] is True
        assert result["error"] == "The page prints itself on this system"


class _Panel:
    def __init__(self):
        self._options = 1

    def options(self):
        return self._options

    def setOptions_(self, value):
        self._options = value


class _Operation:
    def __init__(self):
        self.panel = _Panel()
        self.title = None
        self.ran = 0

    def setJobTitle_(self, title):
        self.title = title

    def printPanel(self):
        return self.panel

    def runOperation(self):
        self.ran += 1
        return True


class _Document:
    made = []

    def __init__(self):
        self.operation = _Operation()

    @classmethod
    def alloc(cls):
        return cls()

    def initWithData_(self, nsdata):
        self.data = nsdata
        _Document.made.append(self)
        return None if nsdata == b"not a pdf" else self

    def printOperationForPrintInfo_scalingMode_autoRotate_(self, info, mode, rotate):
        self.printed = (info, mode, rotate)
        return self.operation


@pytest.fixture
def mac(monkeypatch):
    """A Mac as far as print_document_pdf can tell: the panel is queued for
    the main thread through AppHelper.callAfter, kept here to run by hand."""
    queued = []
    _Document.made = []
    appkit = types.SimpleNamespace(
        NSPrintInfo=types.SimpleNamespace(
            sharedPrintInfo=lambda: types.SimpleNamespace(copy=lambda: "the info")
        ),
        NSPrintPanelShowsOrientation=8,
        NSPrintPanelShowsPaperSize=4,
        NSPrintPanelShowsScaling=16,
    )
    foundation = types.SimpleNamespace(
        NSData=types.SimpleNamespace(dataWithBytes_length_=lambda b, n: b)
    )
    quartz = types.SimpleNamespace(PDFDocument=_Document, kPDFPrintPageScaleDownToFit=2)
    apphelper = types.SimpleNamespace(callAfter=lambda fn, *a: queued.append((fn, a)))
    tools = types.ModuleType("PyObjCTools")
    tools.AppHelper = apphelper
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "PyObjCTools", tools)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", apphelper)
    return queued


def test_a_mac_opens_the_panel_on_the_pdf_from_the_main_thread(mac):
    api = dl.PickerApi(port=1)
    result = api.print_document_pdf("Invoice_1001.pdf", _b64(PDF))
    assert result == {"success": True}
    # queued, not run, on the bridge thread
    assert len(mac) == 1 and _Document.made == []
    fn, args = mac[0]
    fn(*args)
    (doc,) = _Document.made
    assert doc.data == PDF
    info, mode, rotate = doc.printed
    assert (info, mode, rotate) == ("the info", 2, True)
    assert doc.operation.title == "Invoice_1001.pdf"
    assert doc.operation.panel.options() == 1 | 8 | 4 | 16
    assert doc.operation.ran == 1


def test_an_untitled_pdf_prints_as_the_app(mac):
    dl.PickerApi(port=1).print_document_pdf("", _b64(PDF))
    fn, args = mac[0]
    fn(*args)
    assert _Document.made[0].operation.title == "SlowBooks Pro 2026"


def test_what_is_not_a_pdf_is_refused_before_any_panel(mac):
    api = dl.PickerApi(port=1)
    assert api.print_document_pdf("x.pdf", _b64(b"<html>")) == {
        "success": False,
        "error": "That is not a PDF",
    }
    assert api.print_document_pdf("x.pdf", "not base64 at all!")["success"] is False
    assert mac == []


def test_a_panel_that_fails_on_the_main_thread_is_written_not_raised(mac, capsys):
    dl.PickerApi(port=1).print_document_pdf("x.pdf", _b64(PDF))
    fn, args = mac[0]
    _Document.made.clear()
    # PDFKit refuses the bytes: initWithData_ answers None for this payload
    dl.mac_print_pdf(b"not a pdf", "x.pdf")
    fn2, args2 = mac[1]
    fn2(*args2)
    assert (
        "Could not print the PDF: PDFKit could not read the PDF"
        in capsys.readouterr().out
    )


# ---- the page's side ----------------------------------------------------------


def test_the_shim_prints_a_documents_pdf_on_a_mac_before_anything_else():
    body = SHIM[SHIM.index("async function openSameOriginUrl") :]
    first, second = body.index("printDocumentPdf(url)"), body.index(
        "openPrintAddress(url)"
    )
    assert first < second, "the PDF print is tried before the sheet's address"
    assert "api.print_document_pdf(" in SHIM
    # only a Mac, only a /print-preview sheet, the PDF beside it
    assert "IS_MAC" in SHIM and "navigator.platform" in SHIM
    assert "sheet.path.replace(/\\/print-preview$/, '/pdf')" in SHIM
    assert "if (!IS_MAC || !api || !api.print_document_pdf) return false;" in SHIM


def test_the_shim_falls_back_to_the_sheet_when_the_pdf_cannot_be_printed():
    fn = SHIM[
        SHIM.index("async function printDocumentPdf") : SHIM.index(
            "async function openSameOriginUrl"
        )
    ]
    assert fn.count("return false;") >= 5
    assert "result.unsupported" in fn
    assert "Could not print the PDF: " in fn
