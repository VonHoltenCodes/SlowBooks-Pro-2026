"""#977, Reports and Construction, in a real browser on the bakery's books
(a construction company for the job reports), served by the app itself.

Each report below drew a teaching clause after its facts on its header
line. Checked here as the page draws it:

- the clause is in a note, hidden, behind one tortoise named for the report;
  the header's facts (the dates, "As of") still read in the open;
- the tortoise opens its note on a click and Escape shuts it;
- the button's aria-controls and aria-describedby name the note on the page;
- no dialog or page shows three or more tortoises at once (the owner's
  density rule: one or two per-field tortoises, else a section list).

tests/test_hints_977_reports_construction.py checks the words against the
inventory, and that the printed, PDF and CSV reports gain no button.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _ok,
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

READ = """(where) => {
    const box = document.querySelector(where);
    const buttons = [...box.querySelectorAll('[data-hint]')];
    const shown = buttons.filter(b => b.getClientRects().length > 0);
    return {
        open: box.innerText,
        buttons: shown.map(b => {
            const note = document.getElementById(b.getAttribute('aria-controls'));
            return {
                name: b.getAttribute('aria-label'),
                describes: !!document.getElementById(b.getAttribute('aria-describedby') || ''),
                note: note ? note.textContent : null,
                hidden: note ? note.hidden : null,
            };
        }),
    };
}"""

# (address, the note's name, a piece of the moved clause, facts in the open)
REPORTS = [
    (
        "#/reports/wip-schedule?as_of_date=2026-09-30",
        "About WIP Schedule",
        "(percent complete = cost ÷ estimated cost)",
        ["As of 2026-09-30 · cost-to-cost"],
    ),
    (
        "#/reports/cost-to-complete?as_of_date=2026-09-30",
        "About Cost to Complete",
        "Cost to complete is the remaining budget plus open commitments",
        ["As of 2026-09-30."],
    ),
    (
        "#/reports/retainage?as_of_date=2026-09-30",
        "About Retainage",
        "Held less released is what is outstanding",
        ["As of 2026-09-30."],
    ),
    (
        "#/reports/field-ticket-log?as_of_date=2026-09-30",
        "About Field Ticket Log",
        "A signed ticket on a change order that is not yet approved",
        [
            "T&M tickets dated by 2026-09-30 that are not yet billed",
            "Status is today's.",
        ],
    ),
    (
        "#/reports/sales-by-item?start_date=2026-09-01&end_date=2026-09-30",
        "About Sales by Item",
        "Click an item for the lines behind it.",
        [],
    ),
    (
        "#/reports/purchases-by-item?start_date=2026-09-01&end_date=2026-09-30",
        "About Purchases by Item",
        "Click an item for the lines behind it.",
        ["Bills and vendor credits only."],
    ),
    (
        "#/reports/estimate-closing-rate?start_date=2026-09-01&end_date=2026-09-30",
        "About Estimate Closing Rate",
        "Won is accepted or converted",
        ["Estimates dated 2026-09-01 — 2026-09-30, by status today."],
    ),
]


def _no_splash(page):
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )


def _construction(company):
    _ok(company.put("/api/settings", json={"company_type": "construction"}))


def _check(page, where, name, piece, facts):
    got = page.evaluate(READ, where)
    assert len(got["buttons"]) <= 2, got["buttons"]  # the density rule
    mine = [b for b in got["buttons"] if b["name"] == name]
    assert len(mine) == 1, got["buttons"]
    b = mine[0]
    assert b["describes"] and b["hidden"] is True, b
    assert piece in b["note"], b
    assert piece not in got["open"], got["open"]
    for fact in facts:
        assert fact in got["open"], (fact, got["open"])
    # a click opens the note, Escape shuts it
    btn = page.locator(f'{where} [data-hint][aria-label="{name}"]')
    btn.click()
    note = page.locator(f"#{btn.get_attribute('aria-controls')}")
    assert note.is_visible()
    assert btn.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert not note.is_visible()
    assert btn.get_attribute("aria-expanded") == "false"


def test_the_reports_clauses_are_behind_their_tortoise_and_the_facts_stay(
    browser, company
):
    _construction(company)
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        for address, name, piece, facts in REPORTS:
            _visit(page, handled, address)
            page.wait_for_selector("#report-content [data-hint]")
            settle(page, handled)
            _check(page, "#report-content", name, piece, facts)
            # Escape on the note left the report open
            assert page.evaluate(
                "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
            ), address
    finally:
        page.close()


def test_the_job_cost_pages_lines_are_behind_their_tortoise(browser, company):
    _construction(company)
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/job-costs")
        _check(
            page,
            "#page-content",
            "About Job Cost Entries",
            "any job cost that isn't a bill",
            [],
        )
    finally:
        page.close()


def test_the_jobs_list_keeps_job_to_date_in_the_open(browser, company):
    _construction(company)
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/jobs")
        _check(
            page,
            "#page-content",
            "About Jobs",
            "Click a job to drill down.",
            ["Job-to-date figures from posted lines."],
        )
    finally:
        page.close()
