"""Save PDF in the desktop app names and files a document sensibly
(macbase1, F24).

* An invoice saved as "Invoice_1002-pdf.pdf": the server's filename was
  made safe with its extension still on, so the dot became a dash.
* Invoices and statements landed in Documents/SlowBooks Pro/Reports beside
  the P&L. A document someone is sent now goes to a Documents folder beside
  Reports; reports stay where they were.
* The Financial Statements Pack said it "opened in a new tab" — in the
  desktop app it is saved and opened in a window of its own.
"""

import base64
import sys
import types
from pathlib import Path

import pytest

REPORTS_JS = (
    Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "reports.js"
)


@pytest.fixture
def home(monkeypatch, tmp_path):
    import desktop_launcher

    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(desktop_launcher.Path, "home", lambda: home)
    monkeypatch.setattr(desktop_launcher, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(desktop_launcher.sys, "platform", "linux")
    windows = []
    monkeypatch.setitem(
        sys.modules,
        "webview",
        types.SimpleNamespace(create_window=lambda *a, **k: windows.append(a)),
    )
    return home, windows


def _open(title):
    import desktop_launcher

    pdf = base64.b64encode(b"%PDF-1.7 test").decode()
    return desktop_launcher.PickerApi(3001).open_document_pdf(title, pdf)


@pytest.mark.parametrize(
    "title, saved",
    [
        ("Invoice_1002.pdf", "Documents/Invoice_1002.pdf"),
        ("SalesReceipt_1003.PDF", "Documents/SalesReceipt_1003.pdf"),
        ("Estimate_E-1001.pdf", "Documents/Estimate_E-1001.pdf"),
        ("Statement_Acme Diner.pdf", "Documents/Statement_Acme-Diner.pdf"),
        ("CreditMemo_CM-0001.pdf", "Documents/CreditMemo_CM-0001.pdf"),
        (
            "profit-and-loss_2026-01-01_2026-12-31.pdf",
            "Reports/profit-and-loss_2026-01-01_2026-12-31.pdf",
        ),
        (
            "trial-balance_2026-01-01_2026-12-31.pdf",
            "Reports/trial-balance_2026-01-01_2026-12-31.pdf",
        ),
    ],
)
def test_a_saved_pdf_is_named_once_and_filed_by_kind(home, title, saved):
    root, windows = home
    result = _open(title)
    assert result["success"], result
    expected = root / "Documents" / "SlowBooks Pro" / saved
    assert Path(result["path"]) == expected
    assert expected.read_bytes() == b"%PDF-1.7 test"
    assert "-pdf" not in expected.name
    assert windows and windows[0][1] == expected.as_uri()


def test_the_show_in_folder_bridge_accepts_the_documents_folders(home, monkeypatch):
    import desktop_launcher

    monkeypatch.setattr(desktop_launcher.subprocess, "Popen", lambda *a, **k: None)
    result = _open("Invoice_1002.pdf")
    api = desktop_launcher.PickerApi(3001)
    assert api.reveal_path(result["path"]) == {"success": True}
    fallback = desktop_launcher._fallback_reports_dir("Documents") / "Invoice_1.pdf"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"%PDF")
    assert api.reveal_path(str(fallback)) == {"success": True}


def test_the_statements_pack_does_not_promise_a_new_tab():
    js = REPORTS_JS.read_text(encoding="utf-8")
    pack = js[js.index("ReportsPage.financialStatementsPdf = ") :][:1200]
    assert "new tab" not in pack
    assert "The statements pack has opened as a PDF" in pack
