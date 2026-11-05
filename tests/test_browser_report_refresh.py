"""Reports redraw after an edit, and a Refresh control (#482), in
playwright's Chromium on the bakery's books (tests/bakery_books.py):

- the issue's own check: with September's P&L open, a $1,000 journal
  entry posted from the page redraws the open P&L with the new net
  income, without leaving it; the dialog keeps its place and the redraw
  says so in a status ("Refreshed 3:41 PM");
- a change made in another window or tab of the same books (the API
  client's slowbooks-books message) and the window coming back to the
  front after a while away redraw it too;
- with "Refresh reports automatically" off (the per-user preference
  reports, auto_refresh false), none of that redraws it, and the Refresh
  button does; focus on the button stays there through the redraw;
- a report closed is not run again; the Refresh button meets AA in both
  themes; Settings → My Preferences has the checkbox, on by default.

Skipped where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_report_views import PL_URL, SEPT, _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _theme,
    below_threshold,
    browser_fixture,
    company_fixture,
    settle,
    sweep_of,
)

NET = """() => { const rows = [...document.querySelectorAll('#report-content tr')];
    const r = rows.find(tr => /net (income|ordinary income|change)/i.test(tr.textContent));
    return r ? r.querySelector('td:last-child').textContent.trim() : null; }"""


def _account(company, number):
    [a] = [
        x for x in company.get("/api/accounts").json() if x["account_number"] == number
    ]
    return a["id"]


def _journal(company, amount="1000.00"):
    return {
        "date": SEPT[0],
        "description": "Accrued September rent",
        "lines": [
            {"account_id": _account(company, "6300"), "debit": amount, "credit": "0"},
            {"account_id": _account(company, "2000"), "debit": "0", "credit": amount},
        ],
    }


def _cents(text):
    t = text.replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    return round(float(t) * 100)


def _pl_runs(handled):
    return sum(1 for h in handled if h.startswith("/api/reports/profit-loss"))


def _open_pl(browser, company):
    page, handled = _open_at(browser, company, PL_URL)
    page.wait_for_selector("#report-content table")
    settle(page, handled)
    return page, handled


def test_a_journal_posted_with_the_pl_open_redraws_it(browser, company, books):
    page, handled = _open_pl(browser, company)
    try:
        before, runs = page.evaluate(NET), _pl_runs(handled)
        assert before
        page.evaluate("(b) => API.post('/journal', b)", _journal(company))
        page.wait_for_function(
            f"(n) => document.querySelector('#report-content td') && ({NET})() !== n",
            arg=before,
        )
        settle(page, handled)
        assert _pl_runs(handled) == runs + 1  # once, not once per listener
        after = page.evaluate(NET)
        assert _cents(before) - _cents(after) == 100000
        assert page.evaluate(
            "() => document.getElementById('report-refreshed').textContent"
        ).startswith("Refreshed ")
        # still the P&L, on its dates
        assert (
            page.evaluate("() => document.getElementById('modal-title').textContent")
            == "Profit & Loss"
        )
    finally:
        page.close()


def test_another_window_and_coming_back_redraw_it(browser, company, books):
    page, handled = _open_pl(browser, company)
    try:
        runs = _pl_runs(handled)
        # another tab of the same books posted something
        company.post("/api/journal", json=_journal(company, "10.00"))
        page.evaluate(
            "() => new BroadcastChannel('slowbooks-books').postMessage({ company: localStorage.getItem('slowbooks_company') || '', path: '/journal' })"
        )
        page.wait_for_timeout(400)
        settle(page, handled)
        assert _pl_runs(handled) == runs + 1
        # a while away, then back to the window
        page.evaluate("() => window.dispatchEvent(new Event('blur'))")
        page.wait_for_timeout(1100)
        page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        page.wait_for_timeout(400)
        settle(page, handled)
        assert _pl_runs(handled) == runs + 2
        # a blink away (a confirm, a file dialog) is not a while
        page.evaluate(
            "() => { window.dispatchEvent(new Event('blur')); window.dispatchEvent(new Event('focus')); }"
        )
        page.wait_for_timeout(400)
        assert _pl_runs(handled) == runs + 2
    finally:
        page.close()


def test_off_only_the_refresh_button_redraws_it(browser, company, books):
    page, handled = _open_pl(browser, company)
    try:
        page.evaluate("() => { ReportsPage.autoRefresh = false; }")
        before, runs = page.evaluate(NET), _pl_runs(handled)
        page.evaluate("(b) => API.post('/journal', b)", _journal(company))
        page.wait_for_timeout(500)
        settle(page, handled)
        assert _pl_runs(handled) == runs and page.evaluate(NET) == before
        # the button, by keyboard: the report runs again, focus stays on it
        page.focus("#report-refresh-btn")
        page.keyboard.press("Enter")
        page.wait_for_function(f"(n) => ({NET})() !== n", arg=before)
        settle(page, handled)
        assert _pl_runs(handled) == runs + 1
        assert page.evaluate("() => document.activeElement.id") == "report-refresh-btn"
        assert (
            page.evaluate(
                "() => document.getElementById('report-refresh-btn').textContent.trim()"
            )
            == "Refresh"
        )
    finally:
        page.close()


def test_a_closed_report_is_not_run_again(browser, company, books):
    page, handled = _open_pl(browser, company)
    try:
        page.evaluate("() => closeModal()")
        settle(page, handled)
        runs = _pl_runs(handled)
        page.evaluate("(b) => API.post('/journal', b)", _journal(company))
        page.wait_for_timeout(500)
        settle(page, handled)
        assert _pl_runs(handled) == runs
    finally:
        page.close()


def test_the_refresh_control_meets_aa_in_both_themes(browser, company, books):
    page, handled = _open_pl(browser, company)
    swept = {}
    try:
        page.evaluate("() => ReportsPage.refresh()")
        settle(page, handled)
        for theme in ("light", "dark"):
            _theme(page, theme)
            swept[(theme, "report actions")] = page.evaluate(
                sweep_of("document.querySelector('#modal-body .form-actions')")
            )
    finally:
        page.close()
    texts = {it["text"] for items in swept.values() for it in items}
    assert "Refresh" in texts and any(t.startswith("Refreshed ") for t in texts)
    assert below_threshold(swept) == []


def test_my_preferences_has_the_checkbox_on_by_default(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.click("#settings-tab-preferences")  # its group (#720)
        page.wait_for_selector("#pref-report-refresh")
        settle(page, handled)
        assert page.is_checked("#pref-report-refresh")
        assert (
            page.evaluate(
                "() => document.getElementById('pref-report-refresh').labels[0].textContent.trim()"
            )
            == "Refresh reports automatically"
        )
        page.uncheck("#pref-report-refresh")
        settle(page, handled)
        assert company.get("/api/preferences/reports").json()["value"] == {
            "auto_refresh": False
        }
        assert page.evaluate("() => ReportsPage.autoRefresh") is False
        swept = {}
        for theme in ("light", "dark"):
            _theme(page, theme)
            swept[(theme, "my preferences")] = page.evaluate(
                sweep_of("document.getElementById('settings-my-preferences')")
            )
        assert below_threshold(swept) == []
    finally:
        page.close()


def test_a_report_that_only_opens_a_file_is_not_run_again(browser, company, books):
    """The Financial Statements Pack's render opens its PDF (window.open):
    the desktop app saves it and opens a viewer, and closing the viewer
    brings the window back. Run again on that focus, or after a write, it
    saved another copy and opened another viewer, round and round. A
    file-only report runs again only when asked (its dates, Refresh)."""
    page, handled = _open_at(browser, company, "#/")
    try:
        page.evaluate(
            "() => { window._opened = []; window.open = (u) => { window._opened.push(u); return null; }; }"
        )
        page.evaluate("() => ReportsPage.financialStatementsPdf()")
        page.wait_for_function("() => window._opened.length === 1")
        settle(page, handled)
        # the viewer closed: a while away, then back to the window
        page.evaluate("() => window.dispatchEvent(new Event('blur'))")
        page.wait_for_timeout(1100)
        page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        # a write in this window, and one from another window
        page.evaluate("(b) => API.post('/journal', b)", _journal(company, "5.00"))
        page.evaluate(
            "() => new BroadcastChannel('slowbooks-books').postMessage({ company: localStorage.getItem('slowbooks_company') || '', path: '/journal' })"
        )
        page.wait_for_timeout(600)
        settle(page, handled)
        opened = page.evaluate("() => window._opened.length")
        # Refresh, pressed, still makes it again
        page.click("#report-refresh-btn")
        page.wait_for_function(f"() => window._opened.length > {opened}")
    finally:
        page.close()

    assert opened == 1
