"""The register row of a bank entry, in a real browser: its memo is words,
the link beside it reads Posting and says what it opens, and the posting's
own page says it was posted from the register and links back; a void
bank entry and a bill payment keep the plain memo link.

Reuses the register edit tests' harness (tests/test_register_edit_browser.py).
Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dialog_contrast import OPEN  # noqa: E402
from tests.test_register_edit_browser import _entry  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

TITLE = "The journal posting behind this bank entry; use Edit to change it"

# The Memo cells of the register rows that belong to a posting: the row
# whose Edit opens it, and any row whose memo links to its journal (a void
# bank entry has two: the entry struck through, and its reversal).
MEMO_CELLS = """(txn) => [...document.querySelectorAll('tbody tr')]
    .filter(tr => [...tr.querySelectorAll('button')]
            .some(b => (b.getAttribute('onclick') || '').includes(`editEntry(${txn},`))
        || [...tr.querySelectorAll('a')]
            .some(a => a.getAttribute('href') === `/#/journal/${txn}`))
    .map(tr => {
        const cell = tr.cells[2];
        const a = cell.querySelector('a');
        return {
            text: cell.textContent.trim(),
            link: a ? a.textContent : null,
            title: a ? a.getAttribute('title') : null,
            href: a ? a.getAttribute('href') : null,
            edit: [...tr.querySelectorAll('button')].some(b => b.textContent === 'Edit'),
        };
    })"""


def test_the_row_says_posting_and_the_posting_says_where_to_edit(
    browser, company, books, client, seed_accounts
):
    checking = books["checking"]
    txn = _entry(client, books, seed_accounts, description="November water")
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, f"#/banking/{checking}")
        assert page.evaluate(MEMO_CELLS, txn) == [
            {
                "text": "November water Posting",
                "link": "Posting",
                "title": TITLE,
                "href": f"/#/journal/{txn}",
                "edit": True,
            }
        ]
        # the posting's page: where it came from, and the way back
        page.evaluate("async (id) => { await JournalPage.view(id); }", txn)
        page.wait_for_function(OPEN, timeout=5000)
        settle(page, handled)
        note = page.evaluate("""() => {
            const n = document.getElementById('journal-register-note');
            const a = n && n.querySelector('a');
            return n && { text: n.textContent, href: a && a.getAttribute('href') };
        }""")
        assert note == {
            "text": "Posted from the bank register of Checking; edit it there.",
            "href": f"#/banking/{checking}",
        }
        # the link goes back to the register, over which nothing stays open
        page.evaluate(
            "() => document.querySelector('#journal-register-note a').click()"
        )
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        settle(page, handled)
        assert page.evaluate("() => location.hash") == f"#/banking/{checking}"
        assert page.evaluate(MEMO_CELLS, txn)[0]["link"] == "Posting"
    finally:
        page.close()


def test_a_void_bank_entry_keeps_the_plain_memo_link(
    browser, company, books, client, seed_accounts
):
    checking = books["checking"]
    txn = _entry(client, books, seed_accounts, description="Posted twice")
    r = client.post(f"/api/banking/entries/{txn}/void")
    assert r.status_code == 200, r.text
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, f"#/banking/{checking}")
        cells = page.evaluate(MEMO_CELLS, txn)
        # the entry, struck through, and its reversal: each memo is the link,
        # named for nothing but itself, with no Edit on either row
        assert len(cells) == 2
        for cell in cells:
            assert cell["edit"] is False and cell["title"] is None
            assert cell["link"] == cell["text"] and cell["link"]
            assert cell["href"] == f"/#/journal/{txn}"
    finally:
        page.close()
