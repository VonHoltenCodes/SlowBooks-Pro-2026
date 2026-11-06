"""The reconcile list sorted by its headings (#716) in a real browser:
Date, Payee / Description, Ref # and Amount are buttons, as the Invoices
list's are (#650), the header cell says the order (aria-sort); Ref # sorts
check numbers as numbers (998 before 1002) both ways with a blank last; the
order holds, and the page keeps its scroll, through a tick, Mark all and the
Through filter, which all draw the view again; it is kept per account on
this computer; and with nothing kept the list is in date order. The API is
stubbed with a session that keeps its ticks; the page is the real one
(tests/test_browser_ui.py's fixtures)."""

import json

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

COMPANIES = [{"name": "Harbor Light Bakery", "file": "harbor.db", "is_current": True}]


def _line(i, date, ref, amount, payee):
    return {
        "id": i,
        "transaction_id": i,
        "date": date,
        "payee": payee,
        "description": "",
        "reference": ref,
        "check_number": ref or None,
        "amount": amount,
        "reconciled": False,
        "matched": False,
        "source_type": "bank_entry",
        "source_link": None,
    }


def _session():
    lines = [
        _line(1, "2026-09-01", "1002", -50.0, "Grid Power"),
        _line(2, "2026-09-02", "", 900.0, "Walk-in deposit"),
        _line(3, "2026-09-03", "998", -20.0, "Corner Hardware"),
        _line(4, "2026-09-04", "EFT", -12.5, "Bank fee"),
        _line(5, "2026-09-05", "1010", -75.0, "Main Street Properties"),
    ]
    # enough lines to scroll the page
    lines += [
        _line(100 + n, "2026-09-10", str(2000 + n), -1.0 - n, f"Supplier {n:02d}")
        for n in range(40)
    ]
    return {
        "reconciliation_id": 7,
        "account_id": 1,
        "statement_date": "2026-09-30",
        "statement_balance": 500.0,
        "beginning_balance": 0.0,
        "cleared_total": 0.0,
        "uncleared_total": 0.0,
        "difference": 500.0,
        "status": "in_progress",
        "ticked_after_date": 0,
        "transactions": lines,
    }


def _page(browser):
    page = _open(browser, 1280, 800, "#/")
    data = _session()
    posted = []

    def toggle(route):
        line = int(route.request.url.rstrip("/").split("/")[-1])
        posted.append(("toggle", line))
        for t in data["transactions"]:
            if t["id"] == line:
                t["reconciled"] = not t["reconciled"]
        route.fulfill(json={"id": line, "reconciled": True})

    def mark_all(route):
        body = json.loads(route.request.post_data)
        posted.append(("mark-all", body))
        for t in data["transactions"]:
            t["reconciled"] = body["cleared"]
        route.fulfill(json={"changed": len(data["transactions"]), **data})

    page.route("**/api/companies", lambda r: r.fulfill(json=COMPANIES))
    page.route(
        "**/api/banking/reconciliations/7/transactions",
        lambda r: r.fulfill(json=data),
    )
    page.route("**/api/banking/reconciliations/7/toggle/*", toggle)
    page.route("**/api/banking/reconciliations/7/mark-all", mark_all)
    _show(page)
    return page, posted


def _show(page):
    # the app draws its first page when it starts; on a slow machine that
    # finished after the view below was shown, and drew over it
    page.wait_for_function(
        "() => { const t = (document.getElementById('status-text') || {}).textContent || '';"
        " return t !== '' && !/^Loading /.test(t); }"
    )
    page.evaluate("() => BankingPage.showReconcileView(7)")
    page.wait_for_selector("#recon-view tbody tr[data-line]")


REFS = """() => [...document.querySelectorAll('#recon-view tbody tr[data-line]')]
    .map(r => r.cells[3].textContent.trim())"""
DATES = """() => [...document.querySelectorAll('#recon-view tbody tr[data-line]')]
    .map(r => +r.dataset.line)"""
SORTS = """() => Object.fromEntries([...document.querySelectorAll('#recon-view thead th[aria-sort]')]
    .map(th => [th.textContent.replace(/[▲▼]/g, '').trim(), th.getAttribute('aria-sort')]))"""
TOP = "() => document.getElementById('content').scrollTop"


def _redrawn(page, before):
    """Wait for the view to be drawn again (its table is a new element)."""
    page.wait_for_function(
        "(old) => document.querySelector('#recon-view table') !== window[old]",
        arg=before,
    )


def _mark_table(page, name):
    page.evaluate(
        f"() => {{ window['{name}'] = document.querySelector('#recon-view table'); }}"
    )


def test_ref_sorts_by_number_both_ways_and_holds_through_every_redraw(browser):
    page, posted = _page(browser)
    try:
        # nothing kept: date order, Date ascending
        assert page.evaluate(DATES)[:5] == [1, 2, 3, 4, 5]
        assert page.evaluate(SORTS) == {
            "Date": "ascending",
            "Payee / Description": "none",
            "Ref #": "none",
            "Amount": "none",
        }

        ref = page.get_by_role("button", name="Ref #", exact=True)
        ref.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => document.querySelector('#recon-view thead th:nth-child(4)')"
            ".getAttribute('aria-sort') === 'ascending'"
        )
        refs = page.evaluate(REFS)
        numbers = ["998", "1002", "1010"] + [str(2000 + n) for n in range(40)]
        assert refs == numbers + ["EFT", ""]
        assert page.evaluate(SORTS)["Date"] == "none"
        # the keyboard stays on the heading
        assert page.evaluate("() => document.activeElement.id") == "recon-sort-ref"

        page.keyboard.press("Space")
        page.wait_for_function(
            "() => document.querySelector('#recon-view thead th:nth-child(4)')"
            ".getAttribute('aria-sort') === 'descending'"
        )
        down = ["EFT"] + list(reversed(numbers)) + [""]
        assert page.evaluate(REFS) == down

        # a tick down the page: the order and the scroll hold
        page.evaluate("() => { document.getElementById('content').scrollTop = 400; }")
        top = page.evaluate(TOP)
        assert top > 0
        _mark_table(page, "__t1")
        # Space on the box, which the keyboard is on without moving the page
        page.evaluate(
            "() => document.getElementById('recon-line-3').focus({ preventScroll: true })"
        )
        page.keyboard.press("Space")
        _redrawn(page, "__t1")
        assert posted[-1] == ("toggle", 3)
        assert page.is_checked("#recon-line-3")
        assert page.evaluate("() => document.activeElement.id") == "recon-line-3"
        assert page.evaluate(REFS) == down
        assert page.evaluate(TOP) == top

        # Mark all
        _mark_table(page, "__t2")
        page.evaluate(
            "() => [...document.querySelectorAll('#recon-view button')]"
            ".find(b => b.textContent.trim() === 'Mark all').focus({ preventScroll: true })"
        )
        page.keyboard.press("Enter")
        _redrawn(page, "__t2")
        assert posted[-1][0] == "mark-all"
        assert page.evaluate(REFS) == down
        assert page.evaluate(TOP) == top

        # the Through filter: only the lines through the 5th, still by Ref #
        _mark_table(page, "__t3")
        page.fill("#recon-through", "2026-09-05")
        page.check("#recon-only-through")
        _redrawn(page, "__t3")
        assert page.evaluate(REFS) == ["EFT", "1010", "1002", "998", ""]
        assert page.evaluate(SORTS)["Ref #"] == "descending"

        # kept for this account: the view opened afresh comes back so
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        _show(page)
        assert page.evaluate(SORTS)["Ref #"] == "descending"
        assert page.evaluate(REFS)[0] == "EFT" and page.evaluate(REFS)[-1] == ""
    finally:
        page.close()


def test_the_other_headings_sort_and_say_so(browser):
    page, _ = _page(browser)
    try:
        page.get_by_role("button", name="Amount", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('#recon-view thead th:nth-child(5)')"
            ".getAttribute('aria-sort') === 'ascending'"
        )
        amounts = page.evaluate(
            "() => [...document.querySelectorAll('#recon-view tbody tr[data-line]')]"
            ".map(r => +r.dataset.line)"
        )
        assert amounts[0] == 5 and amounts[-1] == 2  # -75.00 first, +900 last
        page.get_by_role("button", name="Payee / Description", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('#recon-view thead th:nth-child(3)')"
            ".getAttribute('aria-sort') === 'ascending'"
        )
        payees = page.evaluate(
            "() => [...document.querySelectorAll('#recon-view tbody tr[data-line]')]"
            ".map(r => r.cells[2].textContent.trim())"
        )
        assert payees[:3] == ["Bank fee", "Corner Hardware", "Grid Power"]
        assert payees[-1] == "Walk-in deposit"
        # Date back: ascending, the server's order
        page.get_by_role("button", name="Date", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('#recon-view thead th:nth-child(2)')"
            ".getAttribute('aria-sort') === 'ascending'"
        )
        assert page.evaluate(DATES)[:5] == [1, 2, 3, 4, 5]
    finally:
        page.close()


def test_a_read_only_sign_in_sorts_too(browser):
    """Sorting reads the list: the headings stay buttons for every role,
    while the ticks, which write, are locked."""
    page, _ = _page(browser)
    try:
        page.evaluate(
            """async () => { App.role = 'readonly'; await BankingPage.showReconcileView(7);
            App.hideWriteControls(document.getElementById('page-content')); }"""
        )
        shown = page.evaluate(
            "() => [...document.querySelectorAll('#recon-view thead .th-sort')]"
            ".filter(b => b.offsetParent !== null).length"
        )
        assert shown == 4
        # the ticks are writes: shown, and locked (a read-only sign-in was
        # offered them enabled and refused by the server on a click)
        ticks = page.evaluate(
            "() => [...document.querySelectorAll('#recon-view input[id^=recon-line-]')]"
            ".map(b => b.disabled)"
        )
        assert ticks and all(ticks)
        page.get_by_role("button", name="Ref #", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('#recon-view thead th:nth-child(4)')"
            ".getAttribute('aria-sort') === 'ascending'"
        )
    finally:
        page.close()
