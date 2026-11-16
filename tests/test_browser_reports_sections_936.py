"""The Reports page's All reports in sections (#936), in a real browser, on
the bakery's books served by the app itself.

All reports was one grid of tiles in a hand-kept order with no headings.
It is now in sections, each under a heading, the sections in a fixed order
(Statements, the customer side, the vendor side, Jobs, Payroll and tax,
Books tools) and the cards alphabetical within each by the title shown, the
company's own words. Checked here, for a business, a nonprofit and a
construction company:

- every card the page shows is shown once, starred or under exactly one
  heading; no heading stands over an empty section;
- the headings are real headings, in the fixed order, in the company's
  words; the cards under each are alphabetical, ignoring case;
- the starred grid is as it was (#395, #794): the starting stars, first,
  and All reports folded while there are stars (out of sight, not only
  marked hidden), the toggle opening it;
- the keyboard: Tab from a section's heading area reaches its first card,
  and each card's star right after the card.

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

# Each section's key and its heading in each company type's words
HEADS = {
    "business": [
        ("statements", "Statements"),
        ("customers", "Customers and sales"),
        ("vendors", "Vendors and purchases"),
        ("jobs", "Jobs"),
        ("books", "Books tools"),
    ],
}
ORDER = ["statements", "customers", "vendors", "jobs", "payroll", "books"]

LAYOUT = """() => {
    const key = (a) => a.dataset.rowKey.slice(7);
    const starred = [...document.querySelectorAll('#starred-reports a.card-link')].map(key);
    const sections = [...document.querySelectorAll('#all-reports .report-section')].map(s => {
        const h = s.querySelector('h3');
        const grid = s.querySelector('.card-grid');
        return {
            key: s.dataset.section,
            head: h ? h.textContent.trim() : null,
            headIsFirst: s.firstElementChild === h,
            labelled: grid && grid.getAttribute('aria-labelledby') === h.id,
            cards: [...s.querySelectorAll('a.card-link')].map(a => ({
                view: key(a), title: a.querySelector('.card-header').textContent.trim(),
                star: !!a.parentElement.querySelector('.card-pin'),
            })),
        };
    });
    const sorted = sections.every(s => s.cards.every((c, i) => !i
        || s.cards[i - 1].title.localeCompare(c.title, undefined, { sensitivity: 'base', numeric: true }) <= 0));
    const all = [...document.querySelectorAll('#page-content a.card-link')].map(key);
    const cards = ReportsPage._cards().map(c => c.view);
    const loose = document.querySelectorAll('#all-reports > .card-slot, #all-reports > .card-grid').length;
    return { starred, sections, sorted, all, cards, loose,
             folded: document.getElementById('all-reports').hidden,
             seen: document.getElementById('all-reports').getClientRects().length > 0 };
}"""


def _layout(page, handled, company_type):
    _visit(page, handled, "#/reports")
    toggle = page.locator("#all-reports-toggle")
    got = page.evaluate(LAYOUT)
    assert got["folded"], "All reports is folded while there are stars (#794)"
    # and folded for real: its cards were a .card-grid, whose display: grid
    # outdid [hidden], so the fold hid nothing; now nothing of it shows
    assert not got["seen"]
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    got = page.evaluate(LAYOUT)
    assert not got["folded"] and got["seen"]
    # every card once: starred, or under exactly one heading
    under = [c["view"] for s in got["sections"] for c in s["cards"]]
    assert sorted(got["all"]) == sorted(got["cards"]), got
    assert len(got["all"]) == len(set(got["all"])), got["all"]
    assert sorted(got["starred"] + under) == sorted(got["cards"])
    assert not set(got["starred"]) & set(under)
    assert got["loose"] == 0
    # the headings: real, first in their section, naming the grid, no empty
    # one, in the fixed order; the cards alphabetical under each
    keys = [s["key"] for s in got["sections"]]
    assert keys == sorted(keys, key=ORDER.index), keys
    for s in got["sections"]:
        assert s["head"] and s["headIsFirst"] and s["labelled"], s
        assert s["cards"], s
        assert all(c["star"] for c in s["cards"]), s
    assert got["sorted"], got["sections"]
    return got


def _tab_walk(page, got):
    """Tab reaches the first card of each section in turn, the star right
    after each card."""
    for s in got["sections"]:
        cards = [c["view"] for c in s["cards"]]
        first = f'#all-reports a[data-row-key="report:{cards[0]}"]'
        page.focus(first)
        page.keyboard.press("Tab")
        assert (
            page.evaluate(
                "() => document.activeElement.matches('.card-pin') && document.activeElement.dataset.pin"
            )
            == cards[0]
        ), s
        page.keyboard.press("Tab")
        nxt = page.evaluate(
            "() => document.activeElement.dataset.rowKey || document.activeElement.id"
        )
        if len(cards) > 1:
            assert nxt == f"report:{cards[1]}", (s, nxt)
        # Shift+Tab twice from the next card lands back on the first card
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Shift+Tab")
        assert (
            page.evaluate("() => document.activeElement.dataset.rowKey")
            == f"report:{cards[0]}"
        )
    # and from the last card of one section, Tab and Tab again reach the
    # first card of the next
    for a, b in zip(got["sections"], got["sections"][1:]):
        last = a["cards"][-1]["view"]
        page.focus(f'#all-reports a[data-row-key="report:{last}"]')
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        assert (
            page.evaluate("() => document.activeElement.dataset.rowKey")
            == f"report:{b['cards'][0]['view']}"
        ), (a["key"], b["key"])


def _no_splash(page):
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )


def test_a_business_sees_all_reports_in_sections_alphabetical_within_each(
    browser, company
):
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        got = _layout(page, handled, "business")
        # the starred grid as it was: the starting stars, in their order
        assert got["starred"] == [
            "profit-loss",
            "balance-sheet",
            "ar-aging",
            "ap-aging",
        ]
        assert [(s["key"], s["head"].lower()) for s in got["sections"]] == [
            (k, h.lower()) for k, h in HEADS["business"]
        ]
        by = {s["key"]: [c["view"] for c in s["cards"]] for s in got["sections"]}
        assert by["books"][:3] == [
            "accountants-package",
            "adjusting-entries",
            "books-check",
        ]
        assert "trial-balance" in by["books"] and "general-ledger" in by["books"]
        assert {"profit-loss-detail", "cash-flow", "financial-statements"} <= set(
            by["statements"]
        )
        assert "estimate-closing-rate" in by["customers"]
        assert "1099-summary" in by["vendors"]
        assert set(by["jobs"]) == {"job-budget-vs-actual", "job-profitability"}
        _tab_walk(page, got)
        # a star moved to the top leaves its section; one emptied leaves no
        # heading behind
        for view in ("job-budget-vs-actual", "job-profitability"):
            page.click(f'#all-reports .card-pin[data-pin="{view}"]')
            page.wait_for_function(
                "(v) => !!document.querySelector(`#starred-reports a[data-row-key='report:${v}']`)",
                arg=view,
            )
        got = page.evaluate(LAYOUT)
        assert "jobs" not in [s["key"] for s in got["sections"]]
        assert got["starred"][-2:] == ["job-budget-vs-actual", "job-profitability"]
        for view in ("job-budget-vs-actual", "job-profitability"):
            page.click(f'#starred-reports .card-pin[data-pin="{view}"]')
            page.wait_for_function(
                "(v) => !!document.querySelector(`#all-reports a[data-row-key='report:${v}']`)",
                arg=view,
            )
        got = page.evaluate(LAYOUT)
        assert "jobs" in [s["key"] for s in got["sections"]]
    finally:
        page.close()


def test_a_nonprofit_sees_its_own_words_in_the_headings_and_the_order(browser, company):
    _ok(company.put("/api/settings", json={"company_type": "nonprofit"}))
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        got = _layout(page, handled, "nonprofit")
        assert got["starred"] == [
            "statement-of-activities",
            "statement-of-financial-position",
            "ar-aging",
            "ap-aging",
        ]
        words = page.evaluate("() => [T('Customers'), 'Vendors', T('Jobs')]")
        heads = {s["key"]: s["head"] for s in got["sections"]}
        assert heads["customers"] == f"{words[0]} and sales"
        assert heads["vendors"] == f"{words[1]} and purchases"
        assert heads["jobs"] == words[2]
        by = {s["key"]: [c["view"] for c in s["cards"]] for s in got["sections"]}
        assert {"fund-balances", "functional-expenses"} <= set(by["statements"])
        assert {"pledges", "giving-statements"} <= set(by["customers"])
        assert "profit-loss" not in got["cards"] and "balance-sheet" not in got["cards"]
        _tab_walk(page, got)
    finally:
        page.close()


def test_a_construction_company_has_a_jobs_section_with_its_job_reports(
    browser, company
):
    _ok(company.put("/api/settings", json={"company_type": "construction"}))
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        got = _layout(page, handled, "construction")
        assert got["starred"] == [
            "profit-loss",
            "balance-sheet",
            "ar-aging",
            "ap-aging",
            "job-profitability",
        ]
        by = {s["key"]: [c["view"] for c in s["cards"]] for s in got["sections"]}
        assert set(by["jobs"]) == {
            "buyout",
            "change-order-log",
            "commitment-status",
            "cost-to-complete",
            "cost-to-complete-forecast",
            "field-ticket-log",
            "job-budget-vs-actual",
            "job-cost-by-vendor",
            "retainage",
            "compliance",
            "wip-schedule",
        }
        assert len(got["cards"]) == 41
        _tab_walk(page, got)
    finally:
        page.close()
