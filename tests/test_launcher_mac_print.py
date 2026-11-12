"""The Mac print panel (NEW-209, 2.31.7 gate).

pywebview's ``print_webview`` printed portrait with a panel that offers no
orientation, and WebKit ignores a sheet's ``@page { size: landscape }``, so
the Budgets sheets printed portrait on the Mac with no way to turn them. The
launcher replaces that one function: the sheet says how it wants to be
printed (``<html data-print-orientation="landscape">``) and the panel shows
Orientation, Paper Size and Scale. Driven here with stand-ins for AppKit,
Foundation and pywebview's Cocoa module; the real panel is the Mac lap's."""

import sys
import types

import pytest

import desktop_launcher as dl


class _Rect:
    def __init__(self):
        self.origin = types.SimpleNamespace(x=18.0, y=18.0)
        self.size = types.SimpleNamespace(width=576.0, height=756.0)


class _Info:
    def __init__(self):
        self.calls = []

    def copy(self):
        return self

    def imageablePageBounds(self):
        return _Rect()

    def paperSize(self):
        return types.SimpleNamespace(width=612.0, height=792.0)

    def __getattr__(self, name):
        if name.startswith("set"):
            return lambda *a: self.calls.append((name, a))
        raise AttributeError(name)


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
        self.ran_for = None

    def printPanel(self):
        return self.panel

    def runOperationModalForWindow_delegate_didRunSelector_contextInfo_(self, w, *a):
        self.ran_for = w


class _WebView:
    def __init__(self, answer="", fail_js=False, fail_op=False):
        self.answer, self.fail_js, self.fail_op = answer, fail_js, fail_op
        self.operation = _Operation()
        self.asked = None

    def evaluateJavaScript_completionHandler_(self, script, handler):
        if self.fail_js:
            raise RuntimeError("no script")
        self.asked = script
        handler(self.answer, None)

    def _printOperationWithPrintInfo_(self, info):
        if self.fail_op:
            raise RuntimeError("no operation")
        self.info = info
        return self.operation

    def window(self):
        return "the window"


@pytest.fixture
def mac(monkeypatch):
    """A Mac as far as install_mac_print_panel can tell."""
    info = _Info()
    appkit = types.SimpleNamespace(
        NSPrintInfo=types.SimpleNamespace(sharedPrintInfo=lambda: info),
        NSPaperOrientationLandscape=1,
        NSFitPagination=1,
        NSPrintPanelShowsOrientation=8,
        NSPrintPanelShowsPaperSize=4,
        NSPrintPanelShowsScaling=16,
    )
    foundation = types.SimpleNamespace(
        NO=False,
        NSWidth=lambda r: r.size.width,
        NSHeight=lambda r: r.size.height,
        NSMinX=lambda r: r.origin.x,
        NSMinY=lambda r: r.origin.y,
    )
    originals = []

    class BrowserView:
        @staticmethod
        def print_webview(webview):
            originals.append(webview)

    cocoa = types.ModuleType("webview.platforms.cocoa")
    cocoa.BrowserView = BrowserView
    platforms = types.ModuleType("webview.platforms")
    platforms.cocoa = cocoa
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", cocoa)
    return types.SimpleNamespace(
        BrowserView=BrowserView, info=info, originals=originals
    )


def test_it_is_a_mac_thing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert dl.install_mac_print_panel() is False
    monkeypatch.setattr(sys, "platform", "win32")
    assert dl.install_mac_print_panel() is False


def test_a_landscape_sheet_starts_landscape_and_the_panel_can_turn_it(mac):
    assert dl.install_mac_print_panel() is True
    view = _WebView(answer="landscape")
    mac.BrowserView.print_webview(view)
    assert "data-print-orientation" in view.asked
    assert ("setOrientation_", (1,)) in mac.info.calls
    # the panel keeps what it had and gains orientation, paper size and scale
    assert view.operation.panel.options() == 1 | 8 | 4 | 16
    assert view.operation.ran_for == "the window"
    assert mac.originals == []


def test_any_other_sheet_prints_as_before_but_can_still_be_turned(mac):
    dl.install_mac_print_panel()
    view = _WebView(answer="")
    mac.BrowserView.print_webview(view)
    assert not [c for c in mac.info.calls if c[0] == "setOrientation_"]
    assert view.operation.panel.options() == 1 | 8 | 4 | 16
    assert view.operation.ran_for == "the window"


def test_anything_unexpected_falls_back_to_pywebviews_own(mac):
    dl.install_mac_print_panel()
    for view in (_WebView(fail_js=True), _WebView(answer="landscape", fail_op=True)):
        mac.BrowserView.print_webview(view)
    assert len(mac.originals) == 2


def test_installing_twice_wraps_once(mac):
    assert dl.install_mac_print_panel() is True
    first = mac.BrowserView.print_webview
    assert dl.install_mac_print_panel() is True
    assert mac.BrowserView.print_webview is first


def test_the_budgets_sheets_ask_for_landscape_and_a_portrait_report_does_not(
    client, seed_accounts
):
    sheet = client.get("/api/budgets/print", params={"year": 2026}).text
    assert '<html lang="en" data-print-orientation="landscape">' in sheet
    variance = client.get("/api/budgets/variance/print", params={"year": 2026}).text
    assert 'data-print-orientation="landscape"' in variance
    tb = client.get("/api/reports/trial-balance/print").text
    assert "data-print-orientation" not in tb


def test_the_launcher_installs_it_before_the_window_starts():
    src = open(dl.__file__, encoding="utf-8").read()
    # the Mac Window menu (#813) is installed beside it, still before start
    assert (
        src.index(
            "install_mac_print_panel()\n        install_mac_window_menu()\n        webview.start("
        )
        > 0
    )
