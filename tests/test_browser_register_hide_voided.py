"""The register's Hide voided box in a real browser (#694): a named
checkbox in the filter bar, reached with Tab, toggled with Space, its state
announced; the voided rows go and come back, the count is said under the
register, the choice is kept over a reload, and the keyboard stays on the
box. Readable (AA) in both themes. The API is stubbed; the page is the
real one (tests/test_browser_ui.py's fixtures)."""

import copy

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_quick_add import CHART, REGISTER  # noqa: E402
from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)


def _row(txn, balance, amount, **extra):
    row = {
        "transaction_id": txn,
        "line_id": txn,
        "date": "2026-09-14",
        "payee": "Main Street Properties",
        "description": "",
        "reference": "",
        "source_type": "bank_entry",
        "source_kind": None,
        "source_link": None,
        "payment": -amount if amount < 0 else 0,
        "deposit": amount if amount > 0 else 0,
        "amount": amount,
        "balance": balance,
        "voided": False,
        "voidable": True,
        "editable": False,
        "printable": False,
        "reconciliation_id": None,
        "cleared": False,
    }
    row.update(extra)
    return row


def _register():
    reg = copy.deepcopy(REGISTER)
    reg["balance"] = 860
    reg["entries"] = [
        _row(71, 1000, 1000),
        _row(72, 750, -250, voided=True, voidable=False),
        _row(73, 650, -100),
        _row(74, 900, 250, voided=True, voidable=False, source_type="bank_entry_void"),
        _row(75, 860, -40),
    ]
    return reg


def _page(browser):
    page = _open(browser, 1280, 800, "#/")
    reg = _register()
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=reg))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("#reg-hide-voided")
    return page


ROWS = "() => [...document.querySelectorAll('#page-content tr[data-txn]')].map(r => +r.dataset.txn)"


def test_hide_voided_is_named_reached_by_tab_and_remembered(browser):
    page = _page(browser)
    try:
        box = page.get_by_role("checkbox", name="Hide voided")
        assert box.count() == 1
        # off by default: every row, no count
        assert not box.is_checked()
        assert page.evaluate(ROWS) == [71, 72, 73, 74, 75]
        assert page.locator("#reg-voided-note").count() == 0

        # Tab from the class filter reaches it
        page.focus("#reg-class")
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.id") == "reg-hide-voided"
        # visible focus on it
        ring = page.evaluate(
            "() => { const s = getComputedStyle(document.activeElement);"
            " return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0"
            " || s.boxShadow !== 'none'; }"
        )
        assert ring

        page.keyboard.press("Space")
        page.wait_for_selector("#reg-voided-note")
        assert page.evaluate(ROWS) == [71, 73, 75]
        note = page.locator("#reg-voided-note")
        assert note.inner_text() == "2 rows hidden: voided entries and their reversals"
        assert note.get_attribute("role") == "status"
        # the keyboard is back on the box, its state checked
        assert page.evaluate("() => document.activeElement.id") == "reg-hide-voided"
        assert page.get_by_role("checkbox", name="Hide voided").is_checked()
        # the rows shown add up: the row between the void and its reversal
        balances = page.evaluate(
            "() => [...document.querySelectorAll('#page-content tr[data-txn]')]"
            ".map(r => r.cells[7].textContent.trim())"
        )
        assert balances == ["$1,000.00", "$900.00", "$860.00"]
        # the entry row is still at the foot, the next stop after the box's
        # rows still the register's own controls
        assert page.locator("#qe-date").count() == 1

        # kept over a reload
        page.reload()
        page.wait_for_selector("#reg-hide-voided")
        assert page.get_by_role("checkbox", name="Hide voided").is_checked()
        assert page.evaluate(ROWS) == [71, 73, 75]

        # and off again: every row, no count
        page.get_by_role("checkbox", name="Hide voided").click()
        page.wait_for_function(
            "() => document.querySelectorAll('#page-content tr[data-txn]').length === 5"
        )
        assert page.locator("#reg-voided-note").count() == 0
        assert page.evaluate("() => document.activeElement.id") == "reg-hide-voided"
    finally:
        page.close()


CONTRAST = """(sel) => {
    const lum = (c) => {
        const m = c.match(/[\\d.]+/g).map(Number);
        const ch = m.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
    };
    const bgOf = (el) => {
        for (let e = el; e; e = e.parentElement) {
            const b = getComputedStyle(e).backgroundColor;
            if (b && !/rgba\\(.*,\\s*0\\)$/.test(b) && b !== 'transparent') return b;
        }
        return getComputedStyle(document.body).backgroundColor;
    };
    const el = document.querySelector(sel);
    const a = lum(getComputedStyle(el).color), b = lum(bgOf(el));
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_box_and_the_count_read_in_both_themes(browser, theme):
    page = _page(browser)
    try:
        page.evaluate(
            f"() => document.documentElement.setAttribute('data-theme', '{theme}')"
        )
        page.get_by_role("checkbox", name="Hide voided").click()
        page.wait_for_selector("#reg-voided-note")
        page.evaluate(
            f"() => document.documentElement.setAttribute('data-theme', '{theme}')"
        )
        for sel in ("label[for='reg-hide-voided']", "#reg-voided-note"):
            ratio = page.evaluate(CONTRAST, sel)
            assert ratio >= 4.5, (theme, sel, ratio)
    finally:
        page.close()
