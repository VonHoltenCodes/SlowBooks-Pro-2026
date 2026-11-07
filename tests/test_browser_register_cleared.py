"""The register's ✓ column set by hand (#715) in a real browser: on a line
no completed reconciliation holds, the cell is a button named for the row
("Mark check 1042 cleared"), Space, Enter or a click marks it and the cell
shows ✓ at once, and the mark is the server's, so it is there after a
reload. An R line is no control and says why; a voided entry gets no
button; a read-only or accountant's sign-in sees the marks and no button.
The API is stubbed with a register that keeps what was posted; the page is
the real one (tests/test_browser_ui.py's fixtures)."""

import copy
import json

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_hide_voided import _row  # noqa: E402
from tests.test_browser_register_quick_add import CHART, REGISTER  # noqa: E402
from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)


def _register():
    reg = copy.deepcopy(REGISTER)
    reg["balance"] = 610
    reg["entries"] = [
        _row(71, 1000, 1000, reconciliation_id=4, cleared=True),
        _row(72, 850, -150, reference="1042"),
        _row(73, 810, -40, voided=True, voidable=False),
        _row(74, 610, -200, reference="1043", cleared=True),
    ]
    return reg


def _page(browser):
    page = _open(browser, 1280, 800, "#/")
    reg = _register()
    posted = []

    def cleared(route):
        line = int(route.request.url.rstrip("/").split("/")[-2])
        want = json.loads(route.request.post_data)["cleared"]
        posted.append((line, want))
        for e in reg["entries"]:
            if e["line_id"] == line:
                e["cleared"] = want
        route.fulfill(
            json={"line_id": line, "cleared": want, "reconciliation_id": None}
        )

    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=reg))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/banking/registers/1/lines/*/cleared", cleared)
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("#page-content tr[data-txn]")
    return page, posted


CELLS = """() => [...document.querySelectorAll('#page-content tr[data-txn]')].map(r => {
    const c = r.cells[9], b = c.querySelector('button');  // ✓, after Category (#729)
    const shown = !!b && b.offsetParent !== null;
    return { txn: +r.dataset.txn, text: c.textContent.trim(),
             button: shown ? b.getAttribute('aria-label') : null,
             title: (c.querySelector('[title]') || {}).title || '' };
})"""


def test_the_cell_marks_a_line_cleared_and_the_mark_survives_a_reload(browser):
    page, posted = _page(browser)
    try:
        cells = {c["txn"]: c for c in page.evaluate(CELLS)}
        # R: no control, the tooltip says what holds it
        assert cells[71]["text"] == "R" and cells[71]["button"] is None
        assert "reconciliation holds" in cells[71]["title"]
        # a voided entry: no button
        assert cells[73]["button"] is None and cells[73]["text"] == ""
        assert cells[72]["button"] == "Mark check 1042 cleared"
        assert cells[72]["text"] == ""
        assert cells[74]["button"] == "Mark check 1043 not cleared"
        assert cells[74]["text"] == "✓"

        # the keyboard: the button is reached and Space marks it
        btn = page.get_by_role("button", name="Mark check 1042 cleared")
        btn.focus()
        page.keyboard.press("Space")
        page.get_by_role("button", name="Mark check 1042 not cleared").wait_for()
        assert posted == [(72, True)]
        cells = {c["txn"]: c for c in page.evaluate(CELLS)}
        assert cells[72]["text"] == "✓"
        # the keyboard is still on it
        assert page.evaluate(
            "() => document.activeElement.getAttribute('aria-label')"
        ) == ("Mark check 1042 not cleared")

        # a click takes the other mark off
        page.get_by_role("button", name="Mark check 1043 not cleared").click()
        page.get_by_role("button", name="Mark check 1043 cleared").wait_for()
        assert posted == [(72, True), (74, False)]

        # Enter marks it back
        page.get_by_role("button", name="Mark check 1043 cleared").focus()
        page.keyboard.press("Enter")
        page.get_by_role("button", name="Mark check 1043 not cleared").wait_for()

        page.reload()
        page.wait_for_selector("#page-content tr[data-txn]")
        cells = {c["txn"]: c for c in page.evaluate(CELLS)}
        assert cells[72]["text"] == "✓"
        assert cells[72]["button"] == "Mark check 1042 not cleared"
        assert cells[74]["text"] == "✓"
    finally:
        page.close()


@pytest.mark.parametrize("role", ["readonly", "accountant"])
def test_a_read_only_sign_in_sees_the_marks_and_no_control(browser, role):
    page, posted = _page(browser)
    try:
        page.evaluate(
            """async (role) => { App.role = role;
            await BankingPage.go('#/banking/1'); }""",
            role,
        )
        page.wait_for_selector("#page-content tr[data-txn]")
        cells = {c["txn"]: c for c in page.evaluate(CELLS)}
        assert [cells[t]["button"] for t in (71, 72, 73, 74)] == [None] * 4
        assert [cells[t]["text"] for t in (71, 72, 73, 74)] == ["R", "", "", "✓"]
        assert page.locator("#page-content button.reg-cleared").count() == 0
        assert posted == []
    finally:
        page.close()
