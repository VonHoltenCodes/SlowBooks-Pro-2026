"""#977, Settings: one tortoise per section, in playwright's Chromium on the
bakery's books of tests/test_theme_contrast.py. The densest tab, Payroll and
checks, has a tortoise beside each section heading and none on a row; the
Check Printing note, the longest, opens from the keyboard, stays inside the
window at the default size, scrolls inside its own fixed height when Tab
takes focus into it, and Escape shuts it with focus back on its button. A
field still reads its description at the field (aria-describedby).

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)


def test_the_check_printing_note_opens_scrolls_and_shuts_from_the_keyboard(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=800)
    try:
        page.click("#settings-tab-payroll")
        settle(page, handled)
        shown = page.evaluate(
            """() => [...document.querySelectorAll('.settings-section')]
            .filter(s => s.offsetParent !== null)
            .map(s => [s.querySelectorAll('.hint-wrap--list').length,
                       s.querySelectorAll('.hint-wrap:not(.hint-wrap--list)').length])"""
        )
        assert shown and all(row == [1, 0] for row in shown), shown

        button = page.locator('button[aria-label="About Check Printing"]')
        # the heading low in the window: the note opens upward to stay in it
        page.evaluate("""() => document.getElementById('settings-h-check-printing')
            .scrollIntoView({ block: 'end' })""")
        button.focus()
        page.keyboard.press("Enter")
        note_id = button.get_attribute("aria-controls")
        note = page.locator(f"#{note_id}")
        assert note.is_visible()
        assert button.get_attribute("aria-expanded") == "true"
        box = page.evaluate(f"""() => {{
            const r = document.getElementById('{note_id}').getBoundingClientRect();
            const n = document.getElementById('{note_id}');
            return {{ inside: r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight,
                      tall: n.scrollHeight > n.clientHeight, height: r.height }};
        }}""")
        assert box["inside"] and box["tall"], box
        assert box["height"] <= 24 * 16 + 2, "never taller than 24rem"
        # nothing on the page covers it
        assert page.evaluate(f"""() => {{
            const n = document.getElementById('{note_id}');
            const r = n.getBoundingClientRect();
            return n.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2));
        }}""")
        # Tab goes into the note, and the keyboard scrolls it
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.id") == note_id
        page.keyboard.press("PageDown")
        page.wait_for_function(
            f"() => document.getElementById('{note_id}').scrollTop > 0"
        )
        assert note.is_visible()
        page.keyboard.press("Escape")
        assert not note.is_visible()
        assert (
            page.evaluate("() => document.activeElement.getAttribute('aria-label')")
            == "About Check Printing"
        )

        # the field reads its own entry, with the note shut
        described = page.evaluate("""() => {
            const f = document.getElementById('settings-check-position');
            const d = document.getElementById(f.getAttribute('aria-describedby'));
            return d.textContent.slice(0, 32);
        }""")
        assert described == "Where the check sits on the shee"
    finally:
        page.close()
