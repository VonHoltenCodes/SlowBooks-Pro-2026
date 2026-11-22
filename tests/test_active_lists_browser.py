"""Active / Inactive / All and one-click Make Inactive (#210), in a real
browser on the bakery's books of tests/test_theme_contrast.py: the Show
picker on each list, a row leaving the Active view and turning up under
Inactive, the questions asked before hiding money or a pay run, and what a
read-only sign-in and a bookkeeper are offered.

Skipped, as one module, where playwright or its Chromium is not installed
(tests/test_active_lists.py checks the API and the page sources).
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

LISTS = {
    "customers": "#/customers",
    "vendors": "#/vendors",
    "items": "#/items",
    "employees": "#/employees",
    "jobs": "#/jobs",
}

ROWS = "() => [...document.querySelectorAll('#page-content tbody tr')].map(r => r.textContent.replace(/\\s+/g, ' ').trim())"
VISIBLE_BUTTONS = """(label) => [...document.querySelectorAll('#page-content button')]
    .filter(b => b.textContent.trim() === label && b.getClientRects().length).length"""


def _start(browser, company, show="active"):
    page, handled = _open(browser, company)
    # the start-up splash sits over the page until it's dismissed
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )
    # the harness opens the lists on All; these start where a person does
    page.evaluate(
        "(v) => Object.keys(ActiveLists.KINDS).forEach(k => localStorage.setItem('slowbooks-show-' + k, v))",
        show,
    )
    return page, handled


# The list drawn again: what #page-content held before (`old`) is gone and
# a list with its Show picker is in its place.
REDRAWN = """(old) => !old.isConnected
    && !!document.querySelector('#page-content #list-show')"""


def _redrawn(page, handled, act):
    """`act` (a Show choice, a Make Inactive) and the list drawn again after
    it. Each of them goes on through App.navigate, which is not awaited: a
    change, the settings check, then the list's read and its drawing. settle
    alone is a quiet moment in the requests, and a slow runner's gap between
    two of them was one: the list was read before it was drawn again, with
    the row still in it (CI, once). The wait is for the redraw itself."""
    old = page.evaluate_handle(
        "() => document.getElementById('page-content').firstElementChild"
    )
    act()
    page.wait_for_function(REDRAWN, arg=old)
    settle(page, handled)


def _show(page, handled, value):
    _redrawn(page, handled, lambda: page.select_option("#list-show", value))


def _rows(page):
    return page.evaluate(ROWS)


def _active(client, api, name, key="name"):
    row = next(r for r in client.get(api).json() if r[key] == name)
    return row["is_active"]


def test_each_list_has_show_and_starts_on_active(browser, company, books):
    page, handled = _start(browser, company)
    try:
        for kind, route in LISTS.items():
            _visit(page, handled, route)
            picker = page.get_by_role("combobox", name="Show", exact=True)
            assert picker.count() == 1, kind
            assert (
                page.evaluate("() => document.getElementById('list-show').value")
                == "active"
            ), kind
            labels = page.evaluate(
                "() => [...document.getElementById('list-show').options].map(o => o.textContent.trim())"
            )
            assert labels == ["Active", "Inactive", "All"], kind  # no counts

        # the books keep an inactive vendor and item: out of Active, in
        # Inactive, and in All beside the active ones
        for route, gone, kept in (
            ("#/vendors", "Old Harbor Ice Co.", "Cascade Flour Mill"),
            ("#/items", "Holiday Stollen", "Sourdough Loaf"),
        ):
            _visit(page, handled, route)
            rows = " | ".join(_rows(page))
            assert gone not in rows and kept in rows, route
            _show(page, handled, "inactive")
            rows = " | ".join(_rows(page))
            assert gone in rows and kept not in rows, route
            assert "Make Active" in rows
            _show(page, handled, "all")
            rows = " | ".join(_rows(page))
            assert gone in rows and kept in rows, route
            # and the choice is the list's own: the next visit keeps it
            _visit(page, handled, route)
            assert (
                page.evaluate("() => document.getElementById('list-show').value")
                == "all"
            )
            _show(page, handled, "active")
    finally:
        page.close()


def test_make_inactive_moves_the_row_and_make_active_brings_it_back(
    browser, company, books
):
    company.post("/api/vendors", json={"name": "Lakeside Ice"})
    page, handled = _start(browser, company)
    try:
        _visit(page, handled, "#/vendors")
        assert any("Lakeside Ice" in r for r in _rows(page))
        _redrawn(
            page,
            handled,
            page.get_by_role("button", name="Make inactive: Lakeside Ice").click,
        )
        assert not any("Lakeside Ice" in r for r in _rows(page))
        assert _active(company, "/api/vendors", "Lakeside Ice") is False
        toast = page.evaluate(
            "() => [...document.querySelectorAll('#toast-container .toast')].pop().textContent"
        )
        assert toast.startswith("Lakeside Ice is inactive")

        _show(page, handled, "inactive")
        _redrawn(
            page,
            handled,
            page.get_by_role("button", name="Make active: Lakeside Ice").click,
        )
        assert _active(company, "/api/vendors", "Lakeside Ice") is True
        assert not any("Lakeside Ice" in r for r in _rows(page))  # back in Active
    finally:
        page.close()


def test_an_open_balance_asks_first(browser, company, books):
    owed = [c for c in company.get("/api/customers").json() if float(c["balance"]) > 0]
    assert owed, "the books have a customer with an open invoice"
    who = owed[0]
    page, handled = _start(browser, company)
    asked = []
    try:
        _visit(page, handled, "#/customers")
        page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
        page.get_by_role("button", name=f"Make inactive: {who['name']}").click()
        settle(page, handled)
        assert asked and who["name"] in asked[0] and "open" in asked[0], asked
        assert f"{float(who['balance']):,.2f}" in asked[0], asked
        assert _active(company, "/api/customers", who["name"]) is True  # kept

        page.once("dialog", lambda d: d.accept())
        page.get_by_role("button", name=f"Make inactive: {who['name']}").click()
        settle(page, handled)
        assert _active(company, "/api/customers", who["name"]) is False
    finally:
        page.close()


def test_an_employee_asks_about_pay_runs(browser, company, books):
    page, handled = _start(browser, company)
    asked = []
    try:
        _visit(page, handled, "#/employees")
        page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
        page.get_by_role("button", name="Make inactive: Lena Ortiz").click()
        settle(page, handled)
        assert asked and "new pay runs" in asked[0], asked
        lena = next(
            e for e in company.get("/api/employees").json() if e["first_name"] == "Lena"
        )
        assert lena["is_active"] is False
    finally:
        page.close()


def test_a_read_only_sign_in_and_a_bookkeeper(browser, company, books):
    page, handled = _start(browser, company)
    try:
        page.evaluate("() => App.setRole('readonly')")
        for kind, route in LISTS.items():
            if kind == "employees":
                continue  # the administrator's page: not drawn for this role
            _visit(page, handled, route)
            assert page.evaluate(VISIBLE_BUTTONS, "Make Inactive") == 0, kind
            # Show is a view, not a change: it stays usable
            assert page.evaluate(
                "() => !document.getElementById('list-show').disabled"
            ), kind

        # a bookkeeper changes the books' lists; staff records stay the
        # administrator's (the page says so, and the button is data-admin)
        page.evaluate("() => App.setRole('bookkeeper')")
        _visit(page, handled, "#/employees")
        assert page.evaluate(VISIBLE_BUTTONS, "Make Inactive") == 0
        for route in ("#/customers", "#/vendors", "#/items", "#/jobs"):
            _visit(page, handled, route)
            assert page.evaluate(VISIBLE_BUTTONS, "Make Inactive") > 0, route
    finally:
        page.close()
