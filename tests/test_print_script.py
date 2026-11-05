"""Every print page prints through one script (NEW-208, 2.31.7 gate).

A print page used to say ``window.onload=function(){window.print();}``. In
the Mac desktop app that opened no dialog, ever: a WKWebView's own
``window.print()`` does nothing, and the shell's print hook is put in after
load (pywebview injects it at didFinishNavigation, then fires
``pywebviewready``). The shared script prints at load in a browser, and in
the shell once, at ``pywebviewready`` or a moment after load, whichever
comes first; a Print button on the sheet, not printed, starts it again."""

import re
from pathlib import Path


from app.routes._print import PRINT_SCRIPT, print_html

ROOT = Path(__file__).resolve().parents[1]


def test_no_route_writes_its_own_on_load_print():
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "app").rglob("*.py")
        if "window.onload=function(){window.print" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    for name in (
        "bills.py",
        "purchase_orders.py",
        "deposits.py",
        "credit_memos.py",
        "estimates.py",
        "invoices/documents.py",
    ):
        assert "print_html(html_str)" in (ROOT / "app/routes" / name).read_text(
            encoding="utf-8"
        ), name


def test_the_script_waits_for_the_shell_and_carries_a_button():
    assert "pywebviewready" in PRINT_SCRIPT
    assert PRINT_SCRIPT.count("window.print()") == 2  # the page's, and the button's
    assert 'id="sb-print-again"' in PRINT_SCRIPT and ">Print</button>" in PRINT_SCRIPT
    assert "@media print{.sb-print-again{display:none}}" in PRINT_SCRIPT
    page = print_html("<html><body><p>x</p></body></html>")
    assert page.count(PRINT_SCRIPT) == 1 and page.endswith("</body></html>")
    assert print_html("<p>x</p>").endswith(PRINT_SCRIPT)


def test_a_report_print_page_is_titled_for_its_sheet(client, seed_accounts):
    resp = client.get("/api/budgets/print", params={"year": 2026})
    assert resp.status_code == 200
    title = re.search(r"<title>(.*?)</title>", resp.text).group(1)
    assert title != "Report" and "Budget" in title
    assert resp.text.count(PRINT_SCRIPT) == 1
