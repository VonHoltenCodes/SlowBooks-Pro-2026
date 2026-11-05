"""Show excluded (W-116, #705) in a real browser: under the bank account's
review list, a button that says how many statement lines are excluded opens
the list of them, each with why it is out of To review and Restore. A
read-only or accountant's sign-in sees the list but no Restore (the page's
own App.hideWriteControls, as it runs on every page drawn). The page is the
real one (tests/test_browser_ui.py's fixtures); the panel is drawn by
BankingPage._excludedPanel."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401

EXCLUDED = {
    "count": 2,
    "lines": [
        {
            "id": 21,
            "date": "2026-10-02",
            "payee": "CHK #2201 CLEARED",
            "description": "",
            "amount": "-310.00",
            "reason": "in_books",
        },
        {
            "id": 22,
            "date": "2026-09-30",
            "payee": "OLD FEE",
            "description": "",
            "amount": "-4.00",
            "reason": "excluded",
        },
    ],
}

DRAW = """([excluded, role]) => {
    App.role = role;
    const root = document.getElementById('page-content');
    root.innerHTML = BankingPage._excludedPanel(excluded, 9);
    App.hideWriteControls(root);
    const shown = (el) => !!el && el.offsetParent !== null;
    const btn = document.getElementById('excluded-toggle');
    const before = { expanded: btn.getAttribute('aria-expanded'),
                     listShown: shown(document.getElementById('excluded-list')) };
    btn.focus();
    return { before, label: btn.textContent.trim(), focused: document.activeElement === btn };
}"""

AFTER = """() => {
    const shown = (el) => !!el && el.offsetParent !== null;
    const btn = document.getElementById('excluded-toggle');
    const rows = [...document.querySelectorAll('#excluded-list tbody tr')];
    return {
        expanded: btn.getAttribute('aria-expanded'),
        label: btn.textContent.trim(),
        listShown: shown(document.getElementById('excluded-list')),
        rows: rows.map(tr => tr.innerText),
        restores: rows.map(tr => shown([...tr.querySelectorAll('button')]
            .find(b => b.textContent.trim() === 'Restore'))),
    };
}"""


@pytest.mark.parametrize("role", ["admin", "bookkeeper", "readonly", "accountant"])
def test_show_excluded_opens_the_list_and_restore_is_the_writers(browser, role):
    page = _open(browser, 1280, 800, "#/")
    try:
        got = page.evaluate(DRAW, [EXCLUDED, role])
        assert got["label"] == "Show excluded (2)"
        assert got["before"] == {"expanded": "false", "listShown": False}
        assert got["focused"]
        # the keyboard opens it: a real button answers Enter
        page.keyboard.press("Enter")
        after = page.evaluate(AFTER)
        assert after["expanded"] == "true" and after["listShown"], after
        assert after["label"] == "Hide excluded (2)"
        assert len(after["rows"]) == 2
        assert "Already in the books: same date and amount as a matched line" in (
            after["rows"][0]
        )
        assert "Excluded by you" in after["rows"][1]
        locked = role in ("readonly", "accountant")
        assert after["restores"] == [not locked, not locked], (role, after)
        page.keyboard.press("Space")
        closed = page.evaluate(AFTER)
        assert closed["expanded"] == "false" and not closed["listShown"]
    finally:
        page.close()


def test_restore_calls_the_restore_route(browser):
    page = _open(browser, 1280, 800, "#/")
    posted = []

    def restore(route):
        posted.append(route.request.url)
        route.fulfill(json={"id": 21, "match_status": "unmatched"})

    page.route("**/api/banking/transactions/*/restore", restore)
    try:
        page.evaluate(
            """([excluded]) => {
            BankingPage._backToRegister = (id) => { window.__back = id; };
            const root = document.getElementById('page-content');
            root.innerHTML = BankingPage._excludedPanel(excluded, 9);
            document.getElementById('excluded-toggle').click(); }""",
            [EXCLUDED],
        )
        page.click('#excluded-list tr[data-excluded="21"] button')
        page.wait_for_function("window.__back === 9")
        assert len(posted) == 1 and posted[0].endswith(
            "/api/banking/transactions/21/restore"
        )
    finally:
        page.close()
