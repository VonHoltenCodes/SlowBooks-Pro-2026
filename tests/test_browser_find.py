"""Find on this screen (find.js), the desktop window's find bar, in
playwright's Chromium:

- Ctrl+F opens it in the desktop window with the cursor in its box; the
  query is the selected text when there is one.
- It finds what is drawn on the page: not what is hidden, folded or
  written for a screen reader only, and not the sidebar or the top bar.
  A field's value counts, and the field is outlined.
- Each match gets a box laid over its text (#find-marks), the page
  untouched; the boxes sit on their text at any text size and follow a
  scroll. Enter, Shift+Enter, Ctrl+G and F3 step through them, wrapping.
- An amount is found however it is written ($, commas, parentheses).
- Over a dialog it finds in the dialog; Escape closes the bar and leaves
  the dialog open, and Tab stays among the bar's controls.
- The page changing under it is searched again, the bar left open.
- No matches offers the record search, which gets the query.

Skipped, as one module, where playwright or its Chromium is not installed.
The matching rules themselves are tests/js/find_on_screen.test.cjs.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
)

DESKTOP = (
    "() => { window.pywebview = { api: {} };"
    " window.dispatchEvent(new Event('pywebviewready')); }"
)
# A page whose words are known: a table of names and amounts, a field, a
# drop-down, and text that is on the page but not on the screen
PAGE = """() => {
    document.getElementById('page-content').innerHTML = `
        <h2>Vendors</h2>
        <table><tbody>
            <tr><td>Acme Supply</td><td>$1,234.50</td></tr>
            <tr><td>ACME Tools</td><td>(200.00)</td></tr>
            <tr><td><span>Ac</span><b>me</b> Parts</td><td>$12.00</td></tr>
        </tbody></table>
        <div hidden>Acme hidden</div>
        <div style="display:none">Acme undisplayed</div>
        <span class="visually-hidden">Acme for a screen reader</span>
        <details><summary>More</summary>Acme folded</details>
        <label>Memo <input id="memo" type="text" value="Paid acme in full"></label>
        <select id="pick"><option>Other</option><option selected>Acme Corp</option></select>
        <input type="password" value="acme">
        <div style="height: 2000px"></div>
        <p id="far">Acme far below</p>`;
}"""
COUNT = "() => document.getElementById('find-count').textContent"
# The boxes drawn (a match across two text nodes is two), the current
# match's text when it is text, and whether the current box sits on it
HITS = """() => {
    const boxes = document.querySelectorAll('#find-marks .find-mark');
    const cur = document.querySelector('#find-marks .find-mark--current');
    const m = FindBar._matches[FindBar._index];
    let aligned = null;
    if (m && m.range && cur) {
        const a = cur.getBoundingClientRect(), b = m.parts[0].getClientRects()[0];
        aligned = ['left', 'top', 'width', 'height'].every(k => Math.abs(a[k] - b[k]) < 1.5);
    }
    return { all: boxes.length, current: m && m.range && cur ? [m.range.toString()] : [], aligned };
}"""
FIELD_HITS = """() => Object.fromEntries([...document.querySelectorAll('[data-find-hit]')]
    .map(el => [el.id, el.getAttribute('data-find-hit')]))"""
MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)


def _find(page, query):
    """The query typed into the open bar, searched"""
    page.fill("#find-input", query)
    page.wait_for_function(
        "(q) => FindBar._input.value === q && !FindBar._typing", arg=query
    )


@pytest.fixture(name="desk")
def desk_fixture(browser, company, books):
    page, handled = _open(browser, company)
    _visit(page, handled, "#/customers")
    page.evaluate(DESKTOP)
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )
    page.evaluate(PAGE)
    yield page
    page.close()


def test_ctrl_f_opens_the_bar_and_finds_what_is_on_the_screen(desk):
    page = desk
    page.evaluate("() => document.activeElement.blur()")
    page.keyboard.press("Control+KeyF")
    assert page.evaluate("() => FindBar.isOpen()") is True
    assert page.evaluate("() => document.activeElement.id") == "find-input"
    _find(page, "acme")
    # three in the table (one across <span> and <b>), the memo, the
    # drop-down and the one far below; nothing hidden, folded, read only to
    # a screen reader or typed into a password box
    assert page.evaluate(COUNT) == "1 of 6"
    # boxes for the table's three ("Ac" and "me" are two), none for the one
    # out of sight below, the current one on its text
    assert page.evaluate(HITS) == {"all": 4, "current": ["Acme"], "aligned": True}
    assert page.evaluate(FIELD_HITS) == {"memo": "match", "pick": "match"}
    # not the sidebar's or the top bar's words
    _find(page, "Customers")
    assert page.evaluate(COUNT) == "No matches"


def test_enter_shift_enter_ctrl_g_and_f3_step_through_the_matches(desk):
    page = desk
    page.evaluate("() => FindBar.open()")
    _find(page, "acme")
    page.keyboard.press("Enter")
    assert page.evaluate(COUNT) == "2 of 6"
    assert page.evaluate(HITS)["current"] == ["ACME"]
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    assert page.evaluate(COUNT) == "4 of 6"
    # the memo field is the current match: outlined as such, no text range
    assert page.evaluate(FIELD_HITS) == {"memo": "current", "pick": "match"}
    assert page.evaluate(HITS)["current"] == []
    page.keyboard.press("Shift+Enter")
    assert page.evaluate(COUNT) == "3 of 6"
    # Ctrl+G and F3 from anywhere while the bar is open
    page.evaluate("() => document.activeElement.blur()")
    page.keyboard.press("Control+KeyG")
    assert page.evaluate(COUNT) == "4 of 6"
    page.keyboard.press("F3")
    page.keyboard.press("F3")
    assert page.evaluate(COUNT) == "6 of 6"
    # the far one was scrolled to
    assert page.evaluate(
        """() => { const r = document.getElementById('far').getBoundingClientRect();
            return r.top >= 0 && r.bottom <= window.innerHeight; }"""
    )
    page.keyboard.press("F3")
    assert page.evaluate(COUNT) == "1 of 6"  # wraps
    page.keyboard.press("Shift+F3")
    assert page.evaluate(COUNT) == "6 of 6"


def test_match_case(desk):
    page = desk
    page.evaluate("() => FindBar.open()")
    _find(page, "ACME")
    assert page.evaluate(COUNT) == "1 of 6"
    page.click("[data-find='case']")
    page.wait_for_function(
        "() => document.getElementById('find-count').textContent === '1 of 1'"
    )
    assert page.evaluate(HITS)["current"] == ["ACME"]
    assert page.get_attribute("[data-find='case']", "aria-pressed") == "true"


def test_an_amount_is_found_however_it_is_written(desk):
    page = desk
    page.evaluate("() => FindBar.open()")
    _find(page, "1234.5")
    assert page.evaluate(COUNT) == "1 of 1"
    assert page.evaluate(HITS)["current"] == ["1,234.5"]
    _find(page, "-200")
    assert page.evaluate(COUNT) == "1 of 1"
    assert page.evaluate(HITS)["current"] == ["(200"]


def test_the_selected_text_is_the_query(desk):
    page = desk
    page.evaluate("""() => { const td = document.querySelector('#page-content td');
            const r = document.createRange(); r.selectNodeContents(td);
            const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
    page.keyboard.press("Control+KeyF")
    assert page.input_value("#find-input") == "Acme Supply"
    assert page.evaluate(COUNT) == "1 of 1"


def test_over_a_dialog_it_finds_in_the_dialog_and_escape_closes_only_the_bar(
    desk,
):
    page = desk
    page.evaluate(
        "() => openModal('Acme dialog', '<p>Hello acme</p><button type=\"button\" id=\"ok\">OK</button>')"
    )
    page.wait_for_function(MODAL_SHOWN)
    page.focus("#ok")
    page.keyboard.press("Control+KeyF")
    _find(page, "acme")
    # the title and the body, not the page beneath
    assert page.evaluate(COUNT) == "1 of 2"
    # Tab stays among the bar's controls, not the dialog's trap
    for _ in range(6):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.getElementById('find-bar').contains(document.activeElement)"
        )
    page.focus("#find-input")
    page.keyboard.press("Escape")
    assert page.evaluate("() => FindBar.isOpen()") is False
    assert page.evaluate(MODAL_SHOWN) is True
    # back where it was, highlights gone
    assert page.evaluate("() => document.activeElement.id") == "ok"
    assert page.evaluate(HITS) == {"all": 0, "current": [], "aligned": None}
    # the next Escape closes the dialog, as ever
    page.keyboard.press("Escape")
    assert page.evaluate(MODAL_SHOWN) is False


def test_a_dialog_opening_and_closing_changes_what_is_searched(desk):
    page = desk
    page.evaluate("() => FindBar.open()")
    _find(page, "acme")
    assert page.evaluate(COUNT) == "1 of 6"
    page.evaluate("() => openModal('A dialog', '<p>acme</p>')")
    page.wait_for_function(
        "() => document.getElementById('find-count').textContent === '1 of 1'"
    )
    page.evaluate("() => closeModal()")
    page.wait_for_function(
        "() => document.getElementById('find-count').textContent.endsWith('of 6')"
    )
    assert page.evaluate("() => FindBar.isOpen()") is True


def test_the_page_changing_is_searched_again_holding_the_place(desk):
    page = desk
    page.evaluate("() => FindBar.open()")
    _find(page, "acme")
    page.keyboard.press("Enter")
    assert page.evaluate(COUNT) == "2 of 6"
    # a row added above: the bar stays on ACME Tools, now the third
    page.evaluate(
        """() => document.querySelector('#page-content tbody')
            .insertAdjacentHTML('afterbegin', '<tr><td>Acme New</td><td>$1.00</td></tr>')"""
    )
    page.wait_for_function(
        "() => document.getElementById('find-count').textContent === '3 of 7'"
    )
    assert page.evaluate(HITS)["current"] == ["ACME"]
    # typing in a field is a change too
    page.fill("#memo", "nothing here")
    page.wait_for_function(
        "() => document.getElementById('find-count').textContent.endsWith('of 6')"
    )


def test_no_matches_offers_the_record_search_with_the_query(desk):
    page = desk
    page.evaluate("() => FindBar.open()")
    _find(page, "zebra")
    assert page.evaluate(COUNT) == "No matches"
    assert page.is_visible(".find-empty")
    assert page.is_visible(".find-records")
    page.click(".find-records")
    assert page.evaluate("() => FindBar.isOpen()") is False
    assert page.evaluate("() => document.activeElement.id") == "global-search"
    assert page.input_value("#global-search") == "zebra"
    # over a dialog the record search is out of reach, so not offered
    page.evaluate("() => openModal('A dialog', '<p>Hello</p>')")
    page.wait_for_function(MODAL_SHOWN)
    page.evaluate("() => FindBar.open()")
    _find(page, "zebra")
    assert page.is_visible(".find-empty")
    assert not page.is_visible(".find-records")


def test_the_boxes_sit_on_their_text_at_any_text_size_and_follow_a_scroll(desk):
    page = desk
    for size in ("125", "150"):
        page.evaluate(
            "(s) => document.documentElement.setAttribute('data-text-size', s)", size
        )
        page.evaluate("() => FindBar.open()")
        _find(page, "acme")
        assert page.evaluate(HITS)["aligned"] is True, size
    page.evaluate("() => document.documentElement.removeAttribute('data-text-size')")
    page.evaluate("() => FindBar.search({})")
    # the far one, scrolled to: its box comes with it
    for _ in range(5):
        page.keyboard.press("Enter")
    assert page.evaluate(COUNT) == "6 of 6"
    assert page.evaluate(HITS) == {
        "all": 1,
        "current": ["Acme"],
        "aligned": True,
    }
    # scrolled away by hand: the box goes out of sight with its text
    page.evaluate("() => { document.getElementById('content').scrollTop = 0; }")
    page.wait_for_function(
        "() => !document.querySelector('#find-marks .find-mark--current')"
    )
