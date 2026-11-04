"""The Journal Entries page's Sort and From / To controls (#687), in
playwright's Chromium on the bakery's books of tests/test_theme_contrast.py:
the controls are named by their labels, the Sort choice is kept across a
reload (per computer, in localStorage), the range narrows the list and the
count follows it.

Skipped where playwright or its Chromium is not installed
(tests/test_journal_sort_range.py checks the route).
"""

from datetime import date, datetime, timezone

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_journal_sort_range import _journal  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

IDS = "() => [...document.querySelectorAll('#page-content tbody tr')].map(r => Number(r.cells[0].textContent))"


def test_sort_is_named_and_kept_and_the_range_narrows_the_list(
    browser, company, books, db_session
):
    at = lambda d: datetime(2026, 11, d, 9, tzinfo=timezone.utc)  # noqa: E731
    early = _journal(db_session, date(2026, 1, 5), "Entered last", at(3))
    late = _journal(db_session, date(2026, 1, 20), "Entered first", at(1))
    db_session.commit()

    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/journal")
        sort = page.get_by_label("Sort", exact=True)
        assert sort.evaluate("e => e.id") == "journal-sort"
        assert page.get_by_label("From", exact=True).evaluate("e => e.type") == "date"
        assert page.get_by_label("To", exact=True).evaluate("e => e.type") == "date"
        assert sort.input_value() == "date"
        ids = page.evaluate(IDS)
        assert ids.index(late) < ids.index(early)

        sort.select_option("entered")
        settle(page, handled)
        ids = page.evaluate(IDS)
        assert ids.index(early) < ids.index(late)
        assert page.evaluate("document.activeElement.id") == "journal-sort"
        assert (
            page.evaluate("localStorage.getItem('slowbooks-journal-sort')") == "entered"
        )

        # kept across a reload
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        _visit(page, handled, "#/journal")
        assert page.get_by_label("Sort", exact=True).input_value() == "entered"
        ids = page.evaluate(IDS)
        assert ids.index(early) < ids.index(late)

        # the range: both ends inclusive; the count follows it
        # a range takes effect when the box is left or Enter is pressed, never
        # while it is being typed into (2.31.5 gate, W-99): the date box
        # fires `change` on the first year digit, and a redraw then ate the
        # rest of the year
        page.get_by_label("From", exact=True).fill("2026-01-05")
        page.get_by_label("From", exact=True).press("Enter")
        settle(page, handled)
        page.get_by_label("To", exact=True).fill("2026-01-05")
        page.get_by_label("To", exact=True).press("Enter")
        settle(page, handled)
        _ranged(page, "1 entry in this date range")
        assert page.evaluate(IDS) == [early]
        assert page.inner_text("#journal-count") == "1 entry in this date range"
        assert page.get_by_label("From", exact=True).input_value() == "2026-01-05"

        # typed a digit at a time, as skytech did: the box keeps every digit
        # and the range applies once, on Enter
        box = page.get_by_label("From", exact=True)
        box.focus()  # the month segment first, as a click on it does
        page.keyboard.type("10152026")
        page.keyboard.press("Enter")
        settle(page, handled)
        assert box.input_value() == "2026-10-15"
        _ranged(page, "0 entries in this date range")

        # a blur that fires with the keyboard already on a row's button (Tab
        # out of the To box) leaves it on the count line, not on <body>
        # (2.31.5 round 2, W-104)
        page.evaluate(
            "() => { document.getElementById('journal-from').value = '';"
            " document.getElementById('journal-to').value = '';"
            " return JournalPage.setRange(); }"  # every row back
        )
        settle(page, handled)
        page.evaluate(
            "() => { document.querySelector('#journal-list button').focus();"
            " document.getElementById('journal-from').value = '2025-12-01';"
            " document.getElementById('journal-to').value = '2026-01-05';"
            " return JournalPage.setRange(); }"
        )
        settle(page, handled)
        assert page.evaluate("document.activeElement.id") == "journal-count"

        page.get_by_label("From", exact=True).fill("2025-01-01")
        page.get_by_label("From", exact=True).press("Enter")
        settle(page, handled)
        page.get_by_label("To", exact=True).fill("2025-01-31")
        page.get_by_label("To", exact=True).press("Enter")
        settle(page, handled)
        _ranged(page, "0 entries in this date range")
        assert page.evaluate(IDS) == []
        assert "No journal entries in this date range" in page.inner_text(
            "#page-content"
        )
        assert page.inner_text("#journal-count") == "0 entries in this date range"
    finally:
        page.close()


def _ranged(page, count_text):
    """The list is drawn again on its own (W-99), after the request the
    range makes: wait for the count line to say so."""
    page.wait_for_function(
        "t => document.querySelector('#journal-count').textContent === t",
        arg=count_text,
        timeout=5000,
    )
