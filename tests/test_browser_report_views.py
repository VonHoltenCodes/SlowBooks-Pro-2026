"""Report views have addresses, and Back is real (R7, #237), in playwright's
Chromium on the bakery's books of tests/test_theme_contrast.py:

- a reload on #/reports/profit-loss?start_date=…&end_date=… reproduces the
  P&L on those dates, over the Report Center;
- changing the period inside the report rewrites the address in place
  (replaceState): no history entry per period tried;
- an account's drill-down pushes its own address, with the dates and the
  view it came from; browser Back returns to the report, and the
  drill-down's own "Back to Profit & Loss" button (named, keyboard) goes
  back through history too;
- a source link from the drill-down opens the document; Back returns to
  the drill-down, rebuilt from the address (the account's name comes from
  the server);
- the dashboard's "Full P&L" and "Full Balance Sheet" open the dated
  report; a saved report opens through the same address; a view nobody
  registered leaves the Report Center showing.

And, from the R7 review:

- browser Back from an open report closes it: the page gone back to (the
  Report Center, the dashboard) is not left under the dialog;
- a drill-down reached by its address alone (a hash set by hand, after
  history went elsewhere) sends "Back to …" forward to the report, never
  back through history to the wrong place;
- the three-deep chain by-class → class P&L → drill-down goes back twice
  through history, each view as it was left, period preset included, with
  no copy piled on; the drill-down's button names the class it returns to;
- Object.prototype names ('__proto__', 'constructor') are not registered
  views: no crash, no "Back to " button going nowhere;
- a date in the address that is not one is said so, not quietly replaced.

Skipped, as one module, where playwright or its Chromium is not installed
(tests/test_report_views.py drives the router in node).
"""

import datetime as dt
import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    ORIGIN,
    SERVED,
    _served_by,
    _visit,
    browser_fixture,
    new_page,
    company_fixture,
    settle,
)

SEPT = ("2026-09-01", "2026-09-30")
PL_URL = f"#/reports/profit-loss?start_date={SEPT[0]}&end_date={SEPT[1]}"

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
TITLE = "() => document.getElementById('modal-title').textContent"
PERIOD = """() => ({
    period: document.getElementById('report-period-select').value,
    start: document.getElementById('report-custom-start').value,
    end: document.getElementById('report-custom-end').value,
})"""


def _open_at(browser, client, page_hash):
    """A fresh document at the address: what a bookmark, a pasted link or a
    reload does."""
    handled = []
    page = new_page(browser, viewport={"width": 1500, "height": 980})
    page.route("**/*", _served_by(client, handled, SERVED))
    page.goto(f"{ORIGIN}/{page_hash}")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )
    settle(page, handled)
    return page, handled


def _hash(page):
    return page.evaluate("location.hash")


def _history(page):
    return page.evaluate("history.length")


def _query(h):
    return dict(p.split("=", 1) for p in h.split("?", 1)[1].split("&"))


def test_a_reload_on_the_address_reproduces_the_report_and_a_period_change_replaces_it(
    browser, company, books
):
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        assert page.evaluate(MODAL_SHOWN)
        assert page.evaluate(TITLE) == "Profit & Loss"
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": SEPT[0],
            "end": SEPT[1],
        }
        # the Report Center is the page under it
        assert page.locator("#page-content .card-grid .card").count() > 5
        assert _hash(page) == PL_URL
        # the report is on the dates asked for: the books' September income
        body = page.evaluate(
            "() => document.getElementById('report-content').textContent"
        )
        assert "Sep 1, 2026" in body and "Sep 30, 2026" in body

        # a new period rewrites the address in place
        length = _history(page)
        page.select_option("#report-period-select", "this_year_to_date")
        settle(page, handled)
        page.wait_for_selector("#report-content table")
        h = _hash(page)
        q = _query(h)
        assert h.startswith("#/reports/profit-loss?")
        assert q["period"] == "this_year_to_date"
        assert q["start_date"] == f"{dt.date.today().year}-01-01"
        assert q["end_date"] == dt.date.today().isoformat()
        assert _history(page) == length, "a period change must not pile up history"

        # custom dates, typed: the same
        page.select_option("#report-period-select", "custom")
        page.fill("#report-custom-start", "2026-08-01")
        page.fill("#report-custom-end", "2026-08-31")
        page.dispatch_event("#report-custom-end", "change")
        settle(page, handled)
        assert (
            _hash(page)
            == "#/reports/profit-loss?start_date=2026-08-01&end_date=2026-08-31"
        )
        assert _history(page) == length
    finally:
        page.close()


def test_a_drill_down_pushes_and_back_returns_to_the_report(browser, company, books):
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        length = _history(page)
        link = page.locator("#report-content tbody a").first
        account = link.text_content().strip()
        link.click()
        page.wait_for_selector("#drilldown-body table")
        h = _hash(page)
        q = _query(h)
        assert h.startswith("#/reports/account-transactions?")
        assert re.fullmatch(r"\d+", q["account_id"])
        assert (q["start_date"], q["end_date"]) == SEPT
        assert q["from"] == "profit-loss"
        assert _history(page) == length + 1, "a hop pushes"
        assert page.evaluate(TITLE) == f"Drill-down — {account}"
        back = page.get_by_role("button", name="Back to Profit & Loss")
        assert back.count() == 1

        # the browser's Back: the report, on the same dates
        page.go_back()
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "Profit & Loss"
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": SEPT[0],
            "end": SEPT[1],
        }
        assert _hash(page) == PL_URL

        # the drill-down's own button, by keyboard, goes back through history
        page.locator("#report-content tbody a").first.click()
        page.wait_for_selector("#drilldown-body table")
        assert _history(page) == length + 1
        page.get_by_role("button", name="Back to Profit & Loss").focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "Profit & Loss"
        assert _hash(page) == PL_URL
        assert (
            _history(page) == length + 1
        ), "the button went back, not forward onto a copy"
    finally:
        page.close()


def test_back_from_a_document_returns_to_the_drill_down(browser, company, books):
    # the income account the September invoices posted to
    pl = company.get(
        "/api/reports/profit-loss", params={"start_date": SEPT[0], "end_date": SEPT[1]}
    ).json()
    row = pl["income"][0]
    url = (
        f"#/reports/account-transactions?account_id={row['account_id']}"
        f"&start_date={SEPT[0]}&end_date={SEPT[1]}&from=profit-loss"
    )
    page, handled = _open_at(browser, company, url)
    try:
        # rebuilt from the address alone: the name is the server's
        page.wait_for_selector("#drilldown-body table")
        assert page.evaluate(TITLE) == f"Drill-down — {row['account_name']}"
        assert page.get_by_role("button", name="Back to Profit & Loss").count() == 1
        rows = page.locator("#drilldown-body tbody tr").count()
        assert rows >= 2

        source = page.locator('#drilldown-body a[href^="/#/invoices/"]').first
        number = source.text_content().strip()
        source.click()
        page.wait_for_function(
            "() => document.getElementById('modal-title').textContent.startsWith('Invoice #')"
        )
        assert _hash(page).startswith("#/invoices/")
        assert page.locator("#drilldown-body").count() == 0

        page.go_back()
        page.wait_for_selector("#drilldown-body table")
        assert _hash(page) == url
        assert page.evaluate(TITLE) == f"Drill-down — {row['account_name']}"
        assert page.locator("#drilldown-body tbody tr").count() == rows
        assert number in page.evaluate(
            "() => document.getElementById('drilldown-body').textContent"
        )

        # Back to the report from a drill-down reached by its address: the
        # report opens on the drill-down's dates
        page.get_by_role("button", name="Back to Profit & Loss").click()
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "Profit & Loss"
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": SEPT[0],
            "end": SEPT[1],
        }
        assert _hash(page) == PL_URL
    finally:
        page.close()


def test_the_dashboard_cards_open_the_dated_report(browser, company, books):
    r = company.put(
        "/api/preferences/dashboard",
        json={"value": {"order": ["pnl_month", "pnl_ytd", "balance_sheet_trend"]}},
    )
    assert r.status_code == 200, r.text
    page, handled = _open_at(browser, company, "#/")
    today = dt.date.today()
    try:
        page.wait_for_selector("#page-content a:has-text('Full P&L')")
        hrefs = page.evaluate(
            "() => [...document.querySelectorAll('#page-content a')]"
            ".filter(a => /^Full /.test(a.textContent)).map(a => [a.textContent, a.getAttribute('href')])"
        )
        assert [t for t, _ in hrefs] == ["Full P&L", "Full P&L", "Full Balance Sheet"]
        month = _query(hrefs[0][1])
        assert hrefs[0][1].startswith("#/reports/profit-loss?")
        assert month["period"] == "this_month"
        assert month["start_date"] == today.replace(day=1).isoformat()
        assert month["end_date"].startswith(today.strftime("%Y-%m-"))
        ytd = _query(hrefs[1][1])
        assert (
            ytd["period"] == "this_year_to_date"
            and ytd["start_date"] == f"{today.year}-01-01"
        )
        assert hrefs[2][1] == f"#/reports/balance-sheet?as_of_date={today.isoformat()}"

        page.click("#page-content a:has-text('Full Balance Sheet')")
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "Balance Sheet"
        assert page.evaluate(PERIOD)["period"] == "custom"
        assert page.evaluate(PERIOD)["end"] == today.isoformat()
        # and Back is the dashboard, with the report closed (review finding
        # 1: the dialog stayed open over the dashboard and swallowed every
        # click), so the card's own link opens the report again
        page.go_back()
        page.wait_for_selector("#page-content a:has-text('Full P&L')")
        assert _hash(page) == "#/"
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.click("#page-content a:has-text('Full P&L')", timeout=5000)
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "Profit & Loss"
        assert page.evaluate(PERIOD)["period"] == "this_month"
        assert _hash(page).startswith("#/reports/profit-loss?period=this_month")
    finally:
        page.close()


# ── the R7 review ────────────────────────────────────────────────────────


def _buttons(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#modal-body .form-actions button')].map(b => b.textContent.trim())"
    )


def _toasts(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#toast-container .toast')].map(t => t.textContent).join(' | ')"
    )


def test_browser_back_from_an_open_report_closes_it(browser, company, books):
    page, handled = _open_at(browser, company, "#/reports")
    try:
        length = _history(page)
        page.get_by_text("Profit & Loss", exact=True).click()
        page.wait_for_selector("#report-content table")
        assert page.evaluate(MODAL_SHOWN)
        assert _hash(page).startswith("#/reports/profit-loss?")
        assert _history(page) == length + 1

        page.go_back()
        page.wait_for_function("() => location.hash === '#/reports'")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        # the Report Center, usable: a card opens its report
        assert page.locator("#page-content .card-grid .card").count() > 5
        page.get_by_text("P&L by Class", exact=True).click(timeout=5000)
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "P&L by Class"
        assert page.evaluate(MODAL_SHOWN)

        # and Forward brings the report back, from its entry
        page.go_back()
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.go_forward()
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "P&L by Class"
    finally:
        page.close()


def test_a_drill_down_reached_by_address_alone_goes_forward_to_its_report(
    browser, company, books
):
    # Review finding 2: a drill-down pushed, left by browser Back, and
    # reached again by its address after history went elsewhere, sent
    # "Back to Profit & Loss" back through history to the Report Center
    # (the P&L never opened) because a note in memory was stale.
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        page.locator("#report-content tbody a").first.click()
        page.wait_for_selector("#drilldown-body table")
        drill = _hash(page)
        assert drill.startswith("#/reports/account-transactions?")
        page.go_back()
        page.wait_for_selector("#report-content table")
        _visit(page, handled, "#/")
        _visit(page, handled, "#/reports")
        assert not page.evaluate(MODAL_SHOWN)
        length = _history(page)

        page.evaluate("(h) => { location.hash = h; }", drill)
        page.wait_for_selector("#drilldown-body table")
        assert _history(page) == length + 1
        page.get_by_role("button", name="Back to Profit & Loss").click()
        page.wait_for_selector("#report-content table")
        assert page.evaluate(TITLE) == "Profit & Loss"
        assert page.evaluate(MODAL_SHOWN)
        assert _hash(page) == PL_URL
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": SEPT[0],
            "end": SEPT[1],
        }
        # forward, onto the report: the drill-down is one Back away
        assert _history(page) == length + 2
        page.go_back()
        page.wait_for_selector("#drilldown-body table")
        assert _hash(page) == drill
    finally:
        page.close()


def test_a_three_deep_chain_goes_back_twice_through_history_and_keeps_the_preset(
    browser, company, books
):
    # Review finding 3: by-class → class P&L → drill-down; the second
    # "Back to …" pushed a copy of the by-class report with custom dates,
    # losing the preset, and Back from there landed on the class P&L.
    page, handled = _open_at(browser, company, "#/reports")
    try:
        page.get_by_text("P&L by Class", exact=True).click()
        page.wait_for_selector("#report-content thead a")
        by_class = _hash(page)
        assert _query(by_class)["period"] == "this_year_to_date"
        length = _history(page)

        page.locator("#report-content thead a").first.click()
        page.wait_for_selector("#class-pl-body table")
        page.wait_for_function(
            "() => document.getElementById('modal-title').textContent === 'Profit & Loss — Uncategorized'"
        )
        class_pl = _hash(page)
        assert class_pl.startswith("#/reports/profit-loss-class?class_id=")
        assert _history(page) == length + 1

        page.locator("#class-pl-body a").first.click()
        page.wait_for_selector("#drilldown-body table")
        assert _hash(page).startswith("#/reports/account-transactions?")
        assert _query(_hash(page))["from"] == "profit-loss-class"
        assert _history(page) == length + 2
        # the button says which P&L it returns to (review nit)
        # (the drill-down can be saved since #241)
        assert _buttons(page) == [
            "Back to Profit & Loss — Uncategorized",
            "Add to Saved Reports…",
            "Close",
        ]
        back = page.get_by_role("button", name="Back to Profit & Loss — Uncategorized")
        assert back.count() == 1

        back.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#class-pl-body table")
        assert _hash(page) == class_pl
        assert _history(page) == length + 2, "through history, not onto a copy"
        page.wait_for_function(
            "() => document.getElementById('modal-title').textContent === 'Profit & Loss — Uncategorized'"
        )

        page.get_by_role("button", name="Back to P&L by Class").click()
        page.wait_for_selector("#report-content thead a")
        assert page.evaluate(TITLE) == "P&L by Class"
        assert _hash(page) == by_class
        assert page.evaluate(PERIOD)["period"] == "this_year_to_date"
        assert _history(page) == length + 2, "through history, not onto a copy"

        # and from there, browser Back is the Report Center, closed
        page.go_back()
        page.wait_for_function("() => location.hash === '#/reports'")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
    finally:
        page.close()


def test_the_drill_down_rebuilt_from_its_address_names_the_class_it_returns_to(
    browser, company, books
):
    pl = company.get(
        "/api/reports/profit-loss", params={"start_date": SEPT[0], "end_date": SEPT[1]}
    ).json()
    classes = company.get("/api/classes").json()
    uncategorized = next(c for c in classes if c["name"] == "Uncategorized")
    url = (
        f"#/reports/account-transactions?account_id={pl['income'][0]['account_id']}"
        f"&start_date={SEPT[0]}&end_date={SEPT[1]}&class_id={uncategorized['id']}&from=profit-loss-class"
    )
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#drilldown-body table")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#modal-body .form-actions button')]"
            ".some(b => b.textContent.trim() === 'Back to Profit & Loss — Uncategorized')"
        )
        # (the drill-down can be saved since #241)
        assert _buttons(page) == [
            "Back to Profit & Loss — Uncategorized",
            "Add to Saved Reports…",
            "Close",
        ]
        assert (
            page.get_by_role(
                "button", name="Back to Profit & Loss — Uncategorized"
            ).count()
            == 1
        )
    finally:
        page.close()


def test_object_prototype_names_are_not_registered_views(browser, company, books):
    # Review finding 4: '__proto__' opened nothing but errored past the
    # "no report called" path; a drill-down from=constructor had a
    # "Back to " button going to #/reports/constructor.
    page, handled = _open_at(
        browser, company, "#/reports/__proto__?start_date=2026-01-01"
    )
    try:
        page.wait_for_function("() => location.hash === '#/reports'")
        assert not page.evaluate(MODAL_SHOWN)
        assert 'no report called "__proto__"' in _toasts(page)

        pl = company.get(
            "/api/reports/profit-loss",
            params={"start_date": SEPT[0], "end_date": SEPT[1]},
        ).json()
        _visit(
            page,
            handled,
            f"#/reports/account-transactions?account_id={pl['income'][0]['account_id']}"
            f"&start_date={SEPT[0]}&end_date={SEPT[1]}&from=constructor",
        )
        page.wait_for_selector("#drilldown-body table")
        assert _buttons(page) == ["Add to Saved Reports…", "Close"]
        assert page.locator("#modal-body button", has_text="Back to").count() == 0
        assert "from=" not in _hash(page)

        _visit(page, handled, "#/reports/constructor?start_date=2026-01-01")
        page.wait_for_function("() => location.hash === '#/reports'")
        assert not page.evaluate(MODAL_SHOWN)
        assert 'no report called "constructor"' in _toasts(page)
    finally:
        page.close()


def test_a_date_in_the_address_that_is_not_one_is_said_so(browser, company, books):
    page, handled = _open_at(
        browser, company, "#/reports/profit-loss?start_date=garbage&end_date=2026-09-30"
    )
    try:
        page.wait_for_selector("#report-content table")
        assert "start_date in the address is not a date (garbage)" in _toasts(page)
        assert "end_date" not in _toasts(page)
        assert _hash(page) == (
            f"#/reports/profit-loss?start_date={dt.date.today().year}-01-01&end_date=2026-09-30"
        )
        # the From box shows the date the report ran on, not a blank
        # where the box refused "garbage" (NEW-24)
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": f"{dt.date.today().year}-01-01",
            "end": "2026-09-30",
        }
    finally:
        page.close()

    # an as-of view: the box shows the date used
    page, handled = _open_at(
        browser, company, "#/reports/balance-sheet?as_of_date=garbage"
    )
    try:
        page.wait_for_selector("#report-content table")
        assert "as_of_date in the address is not a date (garbage)" in _toasts(page)
        assert page.input_value("#report-custom-end") == dt.date.today().isoformat()
        assert _query(_hash(page))["as_of_date"] == dt.date.today().isoformat()
    finally:
        page.close()

    page, handled = _open_at(
        browser,
        company,
        "#/reports/profit-loss?period=fortnight&start_date=2026-09-01&end_date=2026-09-30",
    )
    try:
        page.wait_for_selector("#report-content table")
        assert "period in the address is not one of the choices (fortnight)" in _toasts(
            page
        )
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": SEPT[0],
            "end": SEPT[1],
        }
        assert _hash(page) == PL_URL
    finally:
        page.close()

    # an impossible day has the shape of a date, and Chromium's Date rolls
    # it over (2026-02-30 is March 2), so it passed as one; the date box
    # then refused it, and the report opened on January 1 with no toast
    # (W-5). A real calendar date is asked for, and said so the same way.
    page, handled = _open_at(
        browser,
        company,
        "#/reports/profit-loss?start_date=2026-02-30&end_date=2026-08-31",
    )
    try:
        page.wait_for_selector("#report-content table")
        assert (
            "start_date in the address is not a date (2026-02-30) — ignored"
            in _toasts(page)
        )
        assert "end_date" not in _toasts(page)
        assert _hash(page) == (
            f"#/reports/profit-loss?start_date={dt.date.today().year}-01-01&end_date=2026-08-31"
        )
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": f"{dt.date.today().year}-01-01",
            "end": "2026-08-31",
        }
        # the reader itself: a day a month does not have, a leap day in a
        # year without one, a month nobody has — none is a date; a real
        # leap day is
        assert page.evaluate(
            "() => ['2026-02-30', '2026-04-31', '2023-02-29', '2024-02-29', '2026-13-01', '2026-09-31']"
            ".map(d => datesFromQuery({ start_date: d }).start_date)"
        ) == ["", "", "", "2024-02-29", "", ""]
    finally:
        page.close()


def test_a_saved_report_opens_through_its_address_and_an_unknown_view_does_not_crash(
    browser, company, books
):
    r = company.post(
        "/api/saved-reports",
        json={
            "name": "September P&L",
            "report_type": "profit_loss",
            "parameters": {
                "period": "custom",
                "start_date": SEPT[0],
                "end_date": SEPT[1],
            },
        },
    )
    assert r.status_code in (200, 201), r.text
    page, handled = _open_at(browser, company, "#/reports")
    try:
        page.get_by_role("button", name="Open").first.click()
        page.wait_for_selector("#report-content table")
        assert _hash(page) == PL_URL
        assert page.evaluate(TITLE) == "Profit & Loss"
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": SEPT[0],
            "end": SEPT[1],
        }
    finally:
        page.close()

    page, handled = _open_at(
        browser, company, "#/reports/no-such-report?start_date=2026-01-01"
    )
    try:
        page.wait_for_selector("#page-content .card-grid")
        page.wait_for_function("() => location.hash === '#/reports'")
        assert not page.evaluate(MODAL_SHOWN)
        toast = page.evaluate(
            "() => [...document.querySelectorAll('#toast-container .toast')].map(t => t.textContent).join(' | ')"
        )
        assert 'no report called "no-such-report"' in toast
        # and a drill-down with no account falls back the same way
        _visit(page, handled, "#/reports/account-transactions?start_date=2026-01-01")
        page.wait_for_function("() => location.hash === '#/reports'")
        assert not page.evaluate(MODAL_SHOWN)
    finally:
        page.close()


def test_closing_a_view_leaves_its_address_for_the_page_under_it(
    browser, company, books
):
    """A closed report kept its address, so the app's refreshes
    (App.navigate(location.hash) after a save or a delete), a reload and
    Back reopened it (NEW-23). Close, × and Escape now replace the view's
    entry with the page it opened over; the app's own moves keep the
    address, so a hop's Back still returns to the view."""
    page, handled = _open_at(browser, company, "#/reports")
    try:
        length = _history(page)
        # the card pushes the view; Escape replaces that entry with the
        # Report Center's — no new entry, nothing to come Back to
        page.locator("#page-content .card", has_text="Profit & Loss").first.click()
        page.wait_for_selector("#report-content table")
        assert _hash(page).startswith("#/reports/profit-loss?")
        assert _history(page) == length + 1
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/reports"
        assert _history(page) == length + 1
        # the app's refresh after an action does not bring it back
        _visit(page, handled, "#/reports")
        assert not page.evaluate(MODAL_SHOWN)
        assert _hash(page) == "#/reports"
        # nor a reload
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.evaluate(
            "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
        )
        settle(page, handled)
        assert not page.evaluate(MODAL_SHOWN)
        assert _hash(page) == "#/reports"
        # nor Back from the page gone to next
        _visit(page, handled, "#/")
        page.go_back()
        page.wait_for_function("() => location.hash === '#/reports'")
        settle(page, handled)
        assert not page.evaluate(MODAL_SHOWN)

        # the × and the Close button do the same, from a view reached by
        # its address (over the Report Center)
        _visit(page, handled, PL_URL)
        page.wait_for_selector("#report-content table")
        page.locator("#modal-close-btn").click()
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/reports"
        _visit(page, handled, PL_URL)
        page.wait_for_selector("#report-content table")
        page.get_by_role("button", name="Close", exact=True).click()
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/reports"

        # a drill-down over the P&L is still over the Report Center
        _visit(page, handled, PL_URL)
        page.wait_for_selector("#report-content table")
        page.locator("#report-content tbody a").first.click()
        page.wait_for_selector("#drilldown-body table")
        assert _hash(page).startswith("#/reports/account-transactions?")
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/reports"

        # a view opened over another page closes to that page
        _visit(page, handled, "#/budgets")
        page.get_by_role("button", name="View Variance").click()
        page.wait_for_function(
            "() => location.hash.startsWith('#/reports/budget-vs-actual')"
        )
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/budgets"

        # a document over its list: the customer's page closes to the list
        _visit(page, handled, f"#/customers/{books['customer']}")
        page.locator("#modal-body h4", has_text="Recent invoices").wait_for()
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/customers"
        _visit(page, handled, "#/customers")
        assert not page.evaluate(MODAL_SHOWN)
        # … and so does one opened from its row
        page.locator("#page-content tr.customer-row").first.click()
        page.locator("#modal-body h4", has_text="Recent invoices").wait_for()
        assert re.fullmatch(r"#/customers/\d+", _hash(page))
        page.locator("#modal-close-btn").click()
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/customers"

        # the app's own move keeps the address: a hop from a report pushes
        # from the report's entry, and Back returns to the report
        _visit(page, handled, "#/reports/ar-aging?period=this_year_to_date")
        page.wait_for_selector("#report-content table")
        aging = _hash(page)
        page.locator("#report-content tbody a[data-row-key^='customer:']").first.click()
        page.locator("#modal-body h4", has_text="Recent invoices").wait_for()
        page.go_back()
        page.wait_for_selector("#report-content table")
        assert _hash(page) == aging
        assert page.evaluate(MODAL_SHOWN)
    finally:
        page.close()


def test_from_after_to_is_refused_and_the_dates_before_it_are_kept(
    browser, company, books
):
    """From after To was accepted silently — an empty report (NEW-35). In an
    address both dates are ignored, said so, and the view starts on its own
    period; typed into the boxes it is refused, said so, and the boxes go
    back to the range the report still shows. The pages that read dates
    from their address (the register, the Classes list) refuse it too."""
    page, handled = _open_at(
        browser,
        company,
        "#/reports/profit-loss?start_date=2026-12-26&end_date=2026-10-09",
    )
    try:
        page.wait_for_selector("#report-content table")
        assert (
            "start_date in the address (2026-12-26) is after end_date (2026-10-09) — both ignored"
            in _toasts(page)
        )
        # the P&L's own period, and the address rewritten to it
        assert page.input_value("#report-period-select") == "this_year_to_date"
        q = _query(_hash(page))
        assert q["period"] == "this_year_to_date"
        assert q["start_date"] == f"{dt.date.today().year}-01-01"
        assert q["end_date"] == dt.date.today().isoformat()

        # typed: a custom range, then From moved past To
        page.select_option("#report-period-select", "custom")
        page.fill("#report-custom-start", SEPT[0])
        page.fill("#report-custom-end", SEPT[1])
        page.dispatch_event("#report-custom-end", "change")
        page.wait_for_function("(u) => location.hash === u", arg=PL_URL)
        page.wait_for_selector("#report-content table")
        body = page.evaluate(
            "() => document.getElementById('report-content').textContent"
        )
        assert "Sep 1, 2026" in body and "Sep 30, 2026" in body
        page.fill("#report-custom-start", "2026-12-26")
        page.dispatch_event("#report-custom-start", "change")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')].some(t => t.textContent.includes('is after To'))"
        )
        assert (
            "From (Dec 26, 2026) is after To (Sep 30, 2026) — kept Sep 1, 2026 to Sep 30, 2026"
            in _toasts(page)
        )
        assert page.evaluate(PERIOD) == {
            "period": "custom",
            "start": SEPT[0],
            "end": SEPT[1],
        }
        assert _hash(page) == PL_URL
        assert "Sep 1, 2026" in page.evaluate(
            "() => document.getElementById('report-content').textContent"
        )
    finally:
        page.close()

    # the register's address: the filter is dropped, and said so
    bank = next(
        a for a in company.get("/api/accounts").json() if a["account_number"] == "1000"
    )
    page, handled = _open_at(
        browser,
        company,
        f"#/banking/{bank['id']}?start_date=2026-12-26&end_date=2026-10-09",
    )
    try:
        page.wait_for_selector("#reg-start")
        assert "is after end_date (2026-10-09) — both ignored" in _toasts(page)
        assert page.input_value("#reg-start") == ""
        assert page.input_value("#reg-end") == ""
        assert page.locator("#reg-filter-note").count() == 0
    finally:
        page.close()

    # the Classes list: the period falls back, the address is rewritten
    page, handled = _open_at(
        browser, company, "#/classes?start_date=2026-12-26&end_date=2026-10-09"
    )
    try:
        page.wait_for_selector("#classes-total-note")
        assert "is after end_date (2026-10-09) — both ignored" in _toasts(page)
        q = _query(_hash(page))
        assert q["start_date"] == f"{dt.date.today().year}-01-01"
        assert q["end_date"] == dt.date.today().isoformat()
    finally:
        page.close()


REFUSED = "() => [...document.querySelectorAll('#toast-container .toast')].some(t => t.textContent.includes('is after To'))"


def test_the_classes_list_and_a_class_page_refuse_from_after_to_typed(
    browser, company, books
):
    """The Classes list took From after To typed into its boxes: no toast,
    the address became the reversed range, and the note read "nets to
    $0.00" (W-3). Typed dates go through the refusal every period report
    has: said, naming both dates and the range kept; the boxes put back;
    the list, and its address, still on that range. The class page reads
    its boxes the same way."""
    url = f"#/classes?start_date={SEPT[0]}&end_date={SEPT[1]}"
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#classes-total-note")
        assert _hash(page) == url
        note = page.inner_text("#classes-total-note")
        length = _history(page)
        page.fill("#classes-start", "2026-12-26")
        page.dispatch_event("#classes-start", "change")
        page.wait_for_function(REFUSED)
        settle(page, handled)
        assert (
            "From (Dec 26, 2026) is after To (Sep 30, 2026) — kept Sep 1, 2026 to Sep 30, 2026"
            in _toasts(page)
        )
        assert "in the address" not in _toasts(page)
        assert page.input_value("#classes-period") == "custom"
        assert page.input_value("#classes-start") == SEPT[0]
        assert page.input_value("#classes-end") == SEPT[1]
        assert _hash(page) == url
        assert _history(page) == length
        assert page.inner_text("#classes-total-note") == note
        assert "$0.00, the Profit & Loss" not in note

        # the class page: To moved before From
        uncat = next(
            c for c in company.get("/api/classes").json() if c["is_system_default"]
        )
        url = f"#/classes/{uncat['id']}?start_date={SEPT[0]}&end_date={SEPT[1]}"
        _visit(page, handled, url)
        page.wait_for_selector("#class-stats")
        assert _hash(page) == url
        stats = page.inner_text("#class-stats")
        page.fill("#class-end", "2026-08-01")
        page.dispatch_event("#class-end", "change")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')].some(t => t.textContent.includes('is after To (Aug 1, 2026)'))"
        )
        settle(page, handled)
        assert (
            "From (Sep 1, 2026) is after To (Aug 1, 2026) — kept Sep 1, 2026 to Sep 30, 2026"
            in _toasts(page)
        )
        assert page.input_value("#class-start") == SEPT[0]
        assert page.input_value("#class-end") == SEPT[1]
        assert _hash(page) == url
        assert page.inner_text("#class-stats") == stats
    finally:
        page.close()


def test_a_register_refuses_from_after_to_typed_before_touching_its_address(
    browser, company, books
):
    """A register wrote the typed dates to its address and read them back
    through datesFromQuery, so From after To typed emptied both boxes and
    the toast blamed the address (W-4). It refuses them first, as the
    reports do: said, naming the dates and the filter kept; the boxes put
    back; the address, the history and the rows unchanged. With only a To
    in use, what is kept is "to <that date>"."""
    bank = next(
        a for a in company.get("/api/accounts").json() if a["account_number"] == "1000"
    )
    url = f"#/banking/{bank['id']}?start_date={SEPT[0]}&end_date={SEPT[1]}"
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#reg-filter-note")
        assert _hash(page) == url
        note = page.inner_text("#reg-filter-note")
        rows = page.locator("#page-content tbody tr").count()
        length = _history(page)
        page.fill("#reg-start", "2026-12-26")
        page.dispatch_event("#reg-start", "change")
        page.wait_for_function(REFUSED)
        settle(page, handled)
        assert (
            "From (Dec 26, 2026) is after To (Sep 30, 2026) — kept Sep 1, 2026 to Sep 30, 2026"
            in _toasts(page)
        )
        assert "in the address" not in _toasts(page)
        assert page.input_value("#reg-start") == SEPT[0]
        assert page.input_value("#reg-end") == SEPT[1]
        assert _hash(page) == url
        assert _history(page) == length
        assert page.inner_text("#reg-filter-note") == note
        assert page.locator("#page-content tbody tr").count() == rows

        # only a To in use: From typed past it is refused, and "to" is kept
        page.get_by_role("button", name="Clear").click()
        page.wait_for_function(
            "() => document.getElementById('reg-start').value === ''"
        )
        settle(page, handled)
        page.fill("#reg-end", "2026-08-31")
        page.dispatch_event("#reg-end", "change")
        page.wait_for_function("() => location.hash.includes('end_date=2026-08-31')")
        settle(page, handled)
        page.fill("#reg-start", "2026-09-01")
        page.dispatch_event("#reg-start", "change")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')].some(t => t.textContent.includes('is after To (Aug 31, 2026)'))"
        )
        settle(page, handled)
        assert (
            "From (Sep 1, 2026) is after To (Aug 31, 2026) — kept to Aug 31, 2026"
            in _toasts(page)
        )
        assert page.input_value("#reg-start") == ""
        assert page.input_value("#reg-end") == "2026-08-31"
        assert _query(_hash(page)) == {"end_date": "2026-08-31"}
    finally:
        page.close()


def test_the_job_page_refuses_from_after_to_typed_and_keeps_its_period(
    browser, company, books
):
    """The job page's period boxes had the gap the Classes list and the
    register had (W-3, W-4): setPeriod took From after To, put it on the
    address and reloaded the tab on it. It is refused the same way: said,
    the boxes put back, the page and its address on the period in use.
    Off the boxes (Job to date) with only a From typed, To typed before it
    is refused and "from" is what is kept."""
    url = f"#/jobs/{books['job']}?start_date={SEPT[0]}&end_date={SEPT[1]}"
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#job-tab-body")
        assert _hash(page) == url
        length = _history(page)
        page.fill("#job-period-start", "2026-12-26")
        page.dispatch_event("#job-period-start", "change")
        page.wait_for_function(REFUSED)
        settle(page, handled)
        assert (
            "From (Dec 26, 2026) is after To (Sep 30, 2026) — kept Sep 1, 2026 to Sep 30, 2026"
            in _toasts(page)
        )
        assert "in the address" not in _toasts(page)
        assert page.input_value("#job-period-start") == SEPT[0]
        assert page.input_value("#job-period-end") == SEPT[1]
        assert _hash(page) == url
        assert _history(page) == length

        page.get_by_role("button", name="JTD").click()
        page.wait_for_function(
            "(h) => location.hash === h", arg=f"#/jobs/{books['job']}"
        )
        settle(page, handled)
        page.fill("#job-period-start", "2026-09-01")
        page.dispatch_event("#job-period-start", "change")
        page.wait_for_function("() => location.hash.includes('start_date=2026-09-01')")
        settle(page, handled)
        page.fill("#job-period-end", "2026-08-31")
        page.dispatch_event("#job-period-end", "change")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')].some(t => t.textContent.includes('is after To (Aug 31, 2026)'))"
        )
        settle(page, handled)
        assert (
            "From (Sep 1, 2026) is after To (Aug 31, 2026) — kept from Sep 1, 2026"
            in _toasts(page)
        )
        assert page.input_value("#job-period-start") == "2026-09-01"
        assert page.input_value("#job-period-end") == ""
        assert _query(_hash(page)) == {"start_date": "2026-09-01"}
    finally:
        page.close()


def test_a_job_opened_from_the_list_starts_on_job_to_date_whatever_the_last_jobs_period(
    browser, company, books
):
    """The job page set its period only when the address carried one, so
    the September a P&L by Job hop put on job 1 stayed on job 2 opened
    from the Jobs list — its boxes and its figures, under an address that
    said nothing of it — and a refused range named that September as
    "kept" (round-3 review). The period is the address's every time: none
    is Job to date, and a reload agrees."""
    second = company.post(
        "/api/jobs", json={"customer_id": books["customer"], "name": "Second Job"}
    ).json()
    # a figure outside September, so job to date and September differ
    r = company.post(
        "/api/invoices",
        json={
            "customer_id": books["customer"],
            "job_id": second["id"],
            "date": "2026-08-15",
            "tax_rate": 0,
            "lines": [{"description": "August survey", "quantity": 1, "rate": 480}],
        },
    )
    assert r.status_code in (200, 201), r.text
    jtd = company.get(f"/api/jobs/{second['id']}/cost-tree").json()["totals"]
    sept = company.get(
        f"/api/jobs/{second['id']}/cost-tree",
        params={"start_date": SEPT[0], "end_date": SEPT[1]},
    ).json()["totals"]
    assert jtd["act_revenue"] == 480 and sept["act_revenue"] == 0

    page, handled = _open_at(
        browser,
        company,
        f"#/jobs/{books['job']}?start_date={SEPT[0]}&end_date={SEPT[1]}&from=profit-loss-by-job",
    )
    try:
        page.wait_for_selector("#job-tab-body")
        assert page.input_value("#job-period-start") == SEPT[0]
        assert page.get_by_role("button", name="Back to P&L by Job").count() == 1

        _visit(page, handled, "#/jobs")
        del handled[:]
        page.locator("#page-content tr.clickable", has_text="Second Job").first.click()
        page.wait_for_function(
            "(h) => location.hash === h", arg=f"#/jobs/{second['id']}"
        )
        page.wait_for_selector("#job-tab-body")
        settle(page, handled)
        assert page.input_value("#job-period-start") == ""
        assert page.input_value("#job-period-end") == ""
        assert page.evaluate("() => JobsPage._period") == {"start": "", "end": ""}
        assert page.get_by_role("button", name="Back to P&L by Job").count() == 0
        assert [h for h in handled if "/cost-tree" in h] == [
            f"/api/jobs/{second['id']}/cost-tree"
        ], "the figures are job to date: no dates asked for"
        body = page.inner_text("#job-tab-body")
        assert "$480.00" in body

        # a reload agrees
        del handled[:]
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.evaluate(
            "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
        )
        page.wait_for_selector("#job-tab-body")
        settle(page, handled)
        assert page.input_value("#job-period-start") == ""
        assert page.input_value("#job-period-end") == ""
        assert [h for h in handled if "/cost-tree" in h] == [
            f"/api/jobs/{second['id']}/cost-tree"
        ]
        assert page.inner_text("#job-tab-body") == body
    finally:
        page.close()


def test_saved_reports_are_listed_by_their_views_title_and_what_they_were_saved_on(
    browser, company, books
):
    """The Report column printed the type's code — "profit loss class",
    "account transactions" — which said nothing of which class or account
    (NEW-27). Each row names its view by the view's own title and, from the
    saved parameters, the class, job or account it was saved on."""
    uncat = next(
        c for c in company.get("/api/classes").json() if c["is_system_default"]
    )
    lucky = company.post("/api/classes", json={"name": "Lucky"}).json()
    cash = next(
        a for a in company.get("/api/accounts").json() if a["account_number"] == "1000"
    )
    saved = {
        "a plain one": ("profit_loss", {"period": "last_month"}),
        "one class": (
            "profit_loss_class",
            {"class_id": uncat["id"], "start_date": SEPT[0], "end_date": SEPT[1]},
        ),
        "a drill-down": (
            "account_transactions",
            {"account_id": cash["id"], "class_id": uncat["id"], "period": "last_month"},
        ),
        "a job's lines": (
            "account_transactions",
            {"account_id": cash["id"], "job_id": books["job"], "period": "this_year"},
        ),
        "no job": ("profit_loss_by_job", {"job_ids": "0", "period": "this_year"}),
        "two classes": (
            "profit_loss_by_class",
            {"class_ids": f"{uncat['id']},{lucky['id']}", "period": "this_year"},
        ),
        "one of them": (
            "profit_loss_by_class",
            {"class_ids": str(lucky["id"]), "period": "this_year"},
        ),
        "the ledger": (
            "general_ledger",
            {"account_id": cash["id"], "period": "this_year"},
        ),
        "gone": ("profit_loss_class", {"class_id": 999999, "period": "this_year"}),
        "aging": ("ar_aging", {"period": "this_year_to_date"}),
    }
    for name, (report_type, parameters) in saved.items():
        r = company.post(
            "/api/saved-reports",
            json={"name": name, "report_type": report_type, "parameters": parameters},
        )
        assert r.status_code == 201, r.text
    job_name = company.get(f"/api/jobs/{books['job']}").json()["name"]
    page, handled = _open_at(browser, company, "#/reports")
    try:
        # ten saved reports: the list starts folded
        toggle = page.locator("#saved-reports-toggle")
        toggle.wait_for()
        if toggle.get_attribute("aria-expanded") == "false":
            toggle.click()
        page.wait_for_selector("#saved-reports-list")
        rows = dict(
            page.eval_on_selector_all(
                ".saved-report-row",
                "rows => rows.map(r => [r.children[0].textContent.trim(), r.children[1].textContent.trim()])",
            )
        )
        assert rows["a plain one"] == "Profit & Loss"
        assert rows["one class"] == "Profit & Loss — Uncategorized"
        assert rows["a drill-down"] == f"Drill-down — {cash['name']} · Uncategorized"
        assert rows["a job's lines"] == f"Drill-down — {cash['name']} · Job: {job_name}"
        assert rows["no job"] == "P&L by Job — No job"
        assert rows["two classes"] == "P&L by Class — 2 classes chosen"
        assert rows["one of them"] == "P&L by Class — Lucky"
        assert rows["the ledger"] == f"General Ledger — {cash['name']}"
        assert rows["gone"] == "Profit & Loss — class #999999"
        assert rows["aging"] == "Accounts Receivable Aging"
        # the row still opens its report
        page.locator(".saved-report-row", has_text="one class").get_by_role(
            "button", name="Open"
        ).click()
        page.wait_for_selector("#class-pl-body")
        assert page.evaluate(TITLE) == "Profit & Loss — Uncategorized"
    finally:
        page.close()


def test_the_apps_own_moves_keep_the_address_and_bring_the_page_back(
    browser, company, books
):
    """NEW-23's review: a save, a void or a mark-as-sent is the app moving
    on, not a dismissal. The dialog closes with the address kept and the
    page is redrawn from it (App.refresh), so a customer's page comes back
    after an edit or a new invoice from it, and an invoice opened by its
    address comes back marked sent — as 2.21.0 had it. A dismissal (Escape)
    still gives the address back, and the entry keeps its state, so the
    toolbar's Back stays lit."""
    cid = books["customer"]
    page, handled = _open_at(browser, company, "#/")
    try:
        _visit(page, handled, f"#/customers/{cid}")
        page.locator("#modal-body h4", has_text="Recent invoices").wait_for()
        # Edit → Update: the page again, on its address
        page.locator("#modal-body").get_by_role(
            "button", name="Edit", exact=True
        ).click()
        page.wait_for_selector("#customer-form")
        page.click("#customer-form button[type=submit]")
        page.locator("#modal-body h4", has_text="Recent invoices").wait_for()
        assert _hash(page) == f"#/customers/{cid}"
        assert page.evaluate(MODAL_SHOWN)
        # New Invoice from the page → Save: the page again
        page.locator("#modal-body").get_by_role(
            "button", name=re.compile("New Invoice")
        ).click()
        page.wait_for_selector("#invoice-form")
        assert _hash(page) == f"#/customers/{cid}"
        page.fill("#inv-lines tr .line-desc", "Round two")
        page.fill("#inv-lines tr .line-rate", "12")
        page.click("#invoice-form button[type=submit]")
        page.locator("#modal-body h4", has_text="Recent invoices").wait_for()
        assert _hash(page) == f"#/customers/{cid}"
        newest = max(
            company.get(f"/api/invoices?customer_id={cid}").json(),
            key=lambda i: i["id"],
        )
        assert newest["status"] == "draft"
        # an invoice by its address, marked sent: the invoice again, sent
        _visit(page, handled, f"#/invoices/{newest['id']}")
        page.wait_for_function(f"({TITLE})().includes('Invoice')")
        page.locator("#modal-body").get_by_role("button", name="Mark Sent").click()
        # closed, redrawn from the address, open again without the button
        page.wait_for_function(
            f"() => ({MODAL_SHOWN})() && ({TITLE})().includes('Invoice') && "
            "![...document.querySelectorAll('#modal-body button')].some(b => b.textContent.trim() === 'Mark Sent')"
        )
        settle(page, handled)
        assert _hash(page) == f"#/invoices/{newest['id']}"
        assert page.evaluate(MODAL_SHOWN)
        assert company.get(f"/api/invoices/{newest['id']}").json()["status"] == "sent"
        assert (
            page.locator("#modal-body").get_by_role("button", name="Mark Sent").count()
            == 0
        )
        # a dismissal gives the address back and keeps the entry's state:
        # the toolbar's Back is lit and goes to the page behind
        _visit(page, handled, "#/reports")
        page.locator("#page-content .card", has_text="Profit & Loss").first.click()
        page.wait_for_selector("#report-content table")
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        assert _hash(page) == "#/reports"
        assert page.evaluate("() => !!(history.state && history.state.from)")
        assert not page.evaluate("() => document.getElementById('back-btn').disabled")
    finally:
        page.close()


def test_a_non_january_fiscal_year_offers_the_fiscal_presets(browser, company, books):
    """With a July fiscal year set, the period picker gains the four fiscal
    presets after the calendar ones (#444)."""
    company.put("/api/settings", json={"fiscal_year_start_month": "7"})
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        values = page.evaluate(
            "() => Array.from(document.querySelectorAll('#report-period-select option'))"
            ".map(o => o.value)"
        )
        for key in (
            "this_fiscal_year",
            "this_fiscal_year_to_date",
            "last_fiscal_year",
            "last_fiscal_year_to_date",
        ):
            assert key in values, key
        # the fiscal presets fall after the calendar ones and before custom
        assert values.index("last_year_to_date") < values.index("this_fiscal_year")
        assert values.index("last_fiscal_year_to_date") < values.index("custom")
    finally:
        page.close()
