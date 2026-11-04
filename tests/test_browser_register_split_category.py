"""The + Entry dialog's Category box and a split (from a user's email,
2.31.6). A split's first line is the category the entry posts under, so
while the split lines show the single Category box goes away (it was
visible and ignored before), its pick carried to the first line; "Not a
split" brings it back with the first line's account. The API is stubbed;
the page is the real one."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_date import _register_page  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)


def test_the_category_box_goes_away_while_the_split_lines_show(browser):
    posted = []
    page = _register_page(browser, posted)
    try:
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        page.select_option("#entry-category", "2")
        assert page.is_visible("#entry-category-group")

        page.click("#entry-split-toggle")
        page.wait_for_selector("#entry-split-rows tr")
        assert not page.is_visible("#entry-category-group")
        assert page.inner_text("#entry-split-toggle") == "Not a split"
        # the pick is the first line's account
        assert page.input_value("#entry-split-rows tr:first-child .split-cat") == "2"

        # another account on the first line, then the split undone: the box
        # is back with that account
        page.select_option("#entry-split-rows tr:first-child .split-cat", "3")
        page.click("#entry-split-toggle")
        assert page.is_visible("#entry-category-group")
        assert page.input_value("#entry-category") == "3"
        assert page.inner_text("#entry-split-toggle") == "Split this entry…"
    finally:
        page.close()
