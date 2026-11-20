"""Landmarks and skip links on the chart of accounts and the long entry
forms (issue #266), pinned in the sources so they hold where playwright's
Chromium is not installed; what they do in a browser is
tests/test_browser_keyboard.py.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


def test_the_long_forms_are_landmarks_that_open_with_a_skip_to_their_lines():
    for file, form, target, text in (
        (
            "invoices.js",
            '<form id="invoice-form"',
            "#inv-lines",
            "Skip to the line items",
        ),
        ("bills.js", '<form id="bill-form"', "#bill-lines", "Skip to the line items"),
        ("journal.js", "<form aria-labelledby", "#je-lines", "Skip to the lines"),
    ):
        src = _src(file)
        start = src.index(form)
        tag = src[start : src.index(">", start) + 1]
        # the form landmark takes the dialog's title as its name, and says
        # where its lines are; openModal places the skip link (the page
        # scripts call nothing new, so every probe that renders them holds)
        assert 'aria-labelledby="modal-title"' in tag, file
        assert f'data-skip-to="{target}" data-skip-text="{text}"' in tag, file
        assert "skipLinkHtml" not in src, file
    utils = _src("utils.js")
    opening = utils[utils.index("function openModal(") :]
    opening = opening[: opening.index("function closeModal(")]
    assert (
        "for (const form of $('#modal-body').querySelectorAll('form[data-skip-to]'))"
        in opening
    )
    assert (
        "form.insertAdjacentHTML('afterbegin', skipLinkHtml(form.dataset.skipTo, form.dataset.skipText || 'Skip to the lines'));"
        in opening
    )


def test_the_skip_link_lands_on_the_first_control_inside_with_the_ring():
    utils = _src("utils.js")
    assert (
        'return `<a href="#" class="skip-link skip-link--inline" onclick="return skipTo(event, \'${escapeHtml(target)}\')">${escapeHtml(text)}</a>`;'
        in utils
    )
    skip = utils[utils.index("function skipTo(e, target)") :]
    skip = skip[: skip.index("\n}\n") + 3]
    assert (
        "const into = root.matches(_TAB_SEL) && root.tabIndex >= 0 ? root : _tabStops(root, null)[0];"
        in skip
    )
    assert "if (into) { focusFor(into, true); return false; }" in skip
    # with nothing inside to land on, the place itself, as the main skip link
    assert "root.setAttribute('tabindex', '-1');" in skip


def test_a_dialog_still_opens_on_its_first_field_never_on_void_or_delete():
    utils = _src("utils.js")
    opening = utils[utils.index("function openModal(") :]
    opening = opening[: opening.index("function closeModal(")]
    assert "const stops = _tabStops($('#modal-body'), null);" in opening
    assert (
        "const first = stops.find(el => !el.matches('.skip-link, [data-hint], [data-activity-toggle]'))"
        " || stops.find(el => !el.matches('[data-hint], [data-activity-toggle]'));"
        in opening
    )
    assert (
        "const target = first && !first.matches('[data-destructive]') ? first : modal;"
        in opening
    )


def test_the_chart_of_accounts_is_a_navigation_landmark_with_a_skip_to_its_filter():
    app = _src("app.js")
    page = app[app.index("async renderAccounts()") :]
    page = page[: page.index("async showAccountForm(")]
    assert "${skipLinkHtml('#accounts-filter', 'Skip to the account filter')}" in page
    assert page.index("skipLinkHtml") < page.index('<div class="page-header">')
    assert '<h2 id="accounts-heading">Chart of Accounts</h2>' in page
    assert (
        '<nav aria-labelledby="accounts-heading"><div class="table-container"><table>'
        in page
    )
    assert "</tbody></table></div></nav>`;" in page


def test_the_sidebars_navigation_landmark_has_a_name():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '<nav id="sidebar" aria-label="Main menu">' in html  # NEW-59
    # its icons are decoration, not part of each link's name (NEW-66 / W-7)
    assert '<span class="nav-icon">' not in html
    assert html.count('<span class="nav-icon" aria-hidden="true">') >= 40


def test_the_inline_skip_link_is_hidden_until_focused_then_shown_in_place():
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    hidden = css[css.index(".skip-link--inline {") :]
    hidden = hidden[: hidden.index("}")]
    assert "clip: rect(0 0 0 0);" in hidden and "position: absolute;" in hidden
    shown = css[css.index(".skip-link--inline:focus {") :]
    shown = shown[: shown.index("}")]
    assert "position: static;" in shown and "clip: auto;" in shown


def test_the_statement_closes_the_open_item():
    docs = (ROOT / "docs/accessibility.md").read_text(encoding="utf-8")
    still_open = docs.split("## What we know is still open")[1]
    assert "landmark regions and skip links" not in still_open
    assert "Skip to the line items" in docs and "navigation\n  landmark" in docs
