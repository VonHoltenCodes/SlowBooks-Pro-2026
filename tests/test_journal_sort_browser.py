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
        page.get_by_label("From", exact=True).fill("2026-01-05")
        settle(page, handled)
        page.get_by_label("To", exact=True).fill("2026-01-05")
        settle(page, handled)
        assert page.evaluate(IDS) == [early]
        assert page.inner_text("#journal-count") == "1 entry in this date range"
        assert page.get_by_label("From", exact=True).input_value() == "2026-01-05"

        page.get_by_label("From", exact=True).fill("2025-01-01")
        settle(page, handled)
        page.get_by_label("To", exact=True).fill("2025-01-31")
        settle(page, handled)
        assert page.evaluate(IDS) == []
        assert "No journal entries in this date range" in page.inner_text(
            "#page-content"
        )
        assert page.inner_text("#journal-count") == "0 entries in this date range"
    finally:
        page.close()
