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


# A report's Print is the same on a Mac (#1040): every /print is drawn from
# its /pdf, but for the sheets that have no PDF, which print as before.
NO_PDF = {"/api/budgets/print", "/api/budgets/variance/print"}


def test_every_reports_print_has_its_pdf_but_the_sheets_named_here():
    paths = _get_paths()
    prints = [p for p in paths if p.endswith("/print") and "/checks/" not in p]
    assert len(prints) > 20
    without = {p for p in prints if p[: -len("/print")] + "/pdf" not in paths}
    assert without == NO_PDF
    assert "/api/reports/customer-statement/{customer_id}/pdf" in paths
    for path in set(prints) - without:
        assert paths[path[: -len("/print")] + "/pdf"] == paths[path]


# ---- the bridge -------------------------------------------------------------


def test_elsewhere_than_a_mac_the_page_prints_itself(monkeypatch):
    api = dl.PickerApi(port=1)
    for platform in ("linux", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        result = api.print_document_pdf("Invoice_1001.pdf", _b64(PDF))
        assert result["success"] is False and result["unsupported"] is True
        assert result["error"] == "The page prints itself on this system"


# the print info the stand-in AppKit hands out, and the orientations set on it
queued_info: list = []


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
    page_size = (612.0, 792.0)

    def __init__(self):
        self.operation = _Operation()

    def pageAtIndex_(self, index):
        width, height = self.page_size
        size = types.SimpleNamespace(width=width, height=height)
        return types.SimpleNamespace(
            boundsForBox_=lambda box: types.SimpleNamespace(size=size)
        )

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
    _Document.page_size = (612.0, 792.0)
    oriented = []
    appkit = types.SimpleNamespace(
        NSPrintInfo=types.SimpleNamespace(
            sharedPrintInfo=lambda: types.SimpleNamespace(copy=lambda: info)
        ),
        NSPaperOrientationLandscape=1,
        NSPrintPanelShowsOrientation=8,
        NSPrintPanelShowsPaperSize=4,
        NSPrintPanelShowsScaling=16,
    )
    info = types.SimpleNamespace(setOrientation_=oriented.append)
    queued_info.clear()
    queued_info.extend([info, oriented])
    foundation = types.SimpleNamespace(
        NSData=types.SimpleNamespace(dataWithBytes_length_=lambda b, n: b)
    )
    quartz = types.SimpleNamespace(
        PDFDocument=_Document,
        kPDFPrintPageScaleNone=0,
        kPDFPrintPageScaleDownToFit=2,
        kPDFDisplayBoxMediaBox=0,
    )
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
    # at the PDF's own size (#1040), turned to fit the sheet
    assert (info, mode, rotate) == (queued_info[0], 0, True)
    # a portrait page leaves the panel on Portrait
    assert queued_info[1] == []
    assert doc.operation.title == "Invoice_1001.pdf"
    assert doc.operation.panel.options() == 1 | 8 | 4 | 16
    assert doc.operation.ran == 1


def test_a_landscape_pdf_opens_the_panel_on_landscape(mac):
    """#1044: Profit & Loss by month is 792 x 612; the panel opened on
    Portrait with the page turned sideways on the sheet."""
    _Document.page_size = (792.0, 612.0)
    dl.PickerApi(port=1).print_document_pdf("ProfitLoss.pdf", _b64(PDF))
    fn, args = mac[0]
    fn(*args)
    assert queued_info[1] == [1]
    assert _Document.made[0].operation.ran == 1


def test_a_page_that_cannot_be_measured_prints_portrait(mac):
    assert dl._pdf_is_landscape(object(), sys.modules["Quartz"]) is False


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
    assert "sheet.path.replace(/\\/(print-preview|print)$/, '/pdf')" in SHIM
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
