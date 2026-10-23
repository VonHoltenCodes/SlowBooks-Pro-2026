"""The keyboard fixes of the 2.22.0 gate's second round, pinned in the
sources, so they hold where playwright's Chromium is not installed (the
Windows and macOS suites); what they do in a browser is
tests/test_browser_keyboard.py.

- NEW-25: the dialog's Tab is walked in script (utils.js modalKeydown)
  through every control the browser's own Tab would reach.
- NEW-33: an Escape at a date or time field leaves the field and does not
  close the dialog, in both Escape handlers.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


# ── NEW-25 ────────────────────────────────────────────────────────────────


def test_the_dialogs_tab_is_walked_in_script_in_the_browsers_order():
    utils = _src("utils.js")
    walk = utils[utils.index("function modalKeydown(e)") :]
    walk = walk[: walk.index("document.addEventListener('keydown', modalKeydown)")]
    assert "if (e.key !== 'Tab' || e.defaultPrevented) return;" in walk
    assert "const stops = _tabStops(modal, active);" in walk
    # wraps at the ends, and from the dialog itself goes to its first or last
    assert "stops[(i + (back ? -1 : 1) + stops.length) % stops.length]" in walk
    assert "next = back ? stops[stops.length - 1] : stops[0];" in walk
    # a date field's segments are the browser's; a wrong landing is put right
    assert "_hasSegments(active) || _hasSegments(next)" in walk
    assert "active.addEventListener('focusout', left, { once: true });" in walk
    assert "e.preventDefault();\n    _tabTo(next);" in walk
    stops = utils[utils.index("function _tabStops(root, from)") :]
    stops = stops[: stops.index("\n}\n")]
    # the browser's rules: enabled, tabbable, drawn; tabindex above 0 first
    assert "el.tabIndex >= 0 && !el.matches(':disabled') && _drawn(el)" in stops
    assert ".sort((a, b) => a.tabIndex - b.tabIndex)" in stops
    assert "return ahead.concat(stops.filter(el => el.tabIndex === 0));" in stops
    assert re.search(
        r"const _TAB_SEL = 'a\[href\], area\[href\], button, input, select, textarea, summary, iframe, \[tabindex\]",
        utils,
    )
    # the dialog's first control is found the same way — passing a form's
    # own skip link by (#266), which stays the first stop for Tab
    assert "const stops = _tabStops($('#modal-body'), null);" in utils
    assert (
        "const first = stops.find(el => !el.matches('.skip-link')) || stops[0];"
        in utils
    )
    # a text box's words are selected on the way in, a textarea's are not
    tab_to = utils[utils.index("function _tabTo(el)") :]
    tab_to = tab_to[: tab_to.index("\n}\n")]
    assert (
        "el.tagName === 'INPUT' && /^(text|search|url|tel|password|email|number)$/.test(el.type)"
        in tab_to
    )
    assert "TEXTAREA" not in tab_to


# ── NEW-33 ────────────────────────────────────────────────────────────────


def test_an_escape_at_a_date_field_leaves_the_field_in_both_handlers():
    utils = _src("utils.js")
    leaves = utils[utils.index("function escapeLeavesPicker(e)") :]
    leaves = leaves[: leaves.index("\n}\n")]
    assert "if (!_hasSegments(e.target)) return false;" in leaves
    assert "modal.contains(e.target)) modal.focus();" in leaves
    assert "return true;" in leaves
    assert re.search(
        r"/\^\(date\|time\|datetime-local\|month\|week\)\$/\.test\(el\.type\)", utils
    )
    assert (
        "if (escapeLeavesPicker(e)) return;\n        e.preventDefault(); closeModal(); return;"
        in utils
    )
    app = _src("app.js")
    assert "if (e.key === 'Escape' && !escapeLeavesPicker(e)) { closeModal(); }" in app


# ── NEW-41 ────────────────────────────────────────────────────────────────


def test_the_alt_shortcuts_compare_the_keys_position_and_cmd_k_finds():
    app = _src("app.js")
    keys = app[app.index("// Keyboard shortcuts.") :]
    keys = keys[: keys.index("// Close search dropdown")]
    assert "const alt = e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey;" in keys
    # inside a field — an input, a textarea, a select, an editable region —
    # no Alt letter is a shortcut, on any platform: a Mac's Option is
    # typing there, and Windows sends the plain letter, which let Alt+N
    # over a half-typed bill open New Invoice in its place (W-6); outside,
    # the key's place is enough
    assert (
        "e.target.closest('input, textarea, select, [contenteditable]:not([contenteditable=\"false\"])')"
        in keys
    )
    assert "const letter = (code) => alt && !editing && e.code === code;" in keys
    assert "e.key).toLowerCase()" not in keys, "the character typed is not consulted"
    # the four that leave what is open do nothing over a form being filled
    # in (App.editingDialog, as Back is), from any of its controls — on
    # Save or the × they opened another form in its place (W-6 review);
    # the theme fires anywhere but a field
    assert "const leaves = (code) => letter(code) && !App.editingDialog();" in keys
    for code in ("KeyN", "KeyP", "KeyQ", "KeyH", "KeyB", "KeyE"):
        assert f"if (leaves('{code}'))" in keys, code
    assert "if (letter('KeyD'))" in keys
    assert (
        "const entry = leaves('KeyN') || leaves('KeyP') || leaves('KeyQ') || leaves('KeyB') || leaves('KeyE');"
        in keys
    )
    assert "if (entry && App.isReadOnly())" in keys
    # ⌘⌥[ is not Back
    assert (
        "e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && (e.code === 'BracketLeft' || e.key === '[')"
        in app
    )
    assert not re.search(r"(?<!!)e\.altKey && e\.key === '[a-z]'", keys)
    assert "((e.ctrlKey || e.metaKey) && !e.altKey && e.key === 'k')" in keys


# ── NEW-30 ────────────────────────────────────────────────────────────────


def test_the_toolbar_has_a_back_that_knows_whether_there_is_somewhere_to_go():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    btn = re.search(r'<button[^>]*id="back-btn"[^>]*>', html).group(0)
    assert 'aria-label="Back"' in btn and "disabled" in btn and 'type="button"' in btn
    assert "App.goBack && App.goBack()" in _src("bootstrap.js")
    app = _src("app.js")
    assert "canGoBack() { return !!(history.state && history.state.from); }" in app
    assert "goBack() { if (App.backAllowed()) history.back(); }" in app
    assert "history.length" not in re.sub(r"//.*", "", app)  # not in the code
    # a link's entry is stamped when first seen; the first entry at start
    assert (
        "else if (!history.state) history.replaceState(App.entryState(App._here), '', here);"
        in app
    )
    assert (
        "if (!history.state) history.replaceState({ from: null, n: 0 }, '', location.hash || '#/');"
        in app
    )
    # the button follows every push, stamp and history move
    assert app.count("App.addressShown();") >= 3
    assert "closeModal({ keepAddress: true });\n            App.syncBack();" in app
    assert "App.dialogAddressed();  // the view's own address" in _src("reports.js")
    # the shortcut: ⌘[ on a Mac, Alt+← elsewhere, by key position
    assert (
        "if (App.isBackKey(e) && App.backAllowed()) { e.preventDefault(); App.goBack(); return; }"
        in app
    )
    assert (
        "!e.altKey && !e.shiftKey && (e.code === 'BracketLeft' || e.key === '[')" in app
    )
    assert (
        "e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.code === 'ArrowLeft'"
        in app
    )
    assert "btn.title = mac ? 'Back (⌘[)' : 'Back (Alt+←)';" in app
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert ".tb-btn:disabled {" in css
    docs = (ROOT / "docs/accessibility.md").read_text(encoding="utf-8")
    assert "⌘[" in docs and "Alt+←" in docs


# ── NEW-32 ────────────────────────────────────────────────────────────────

# Every button that says Void or Delete carries the marker openModal reads,
# in a dialog or a list (the rule is one rule); the three the gate named
# are among them
DESTRUCTIVE = [
    ("journal.js", "JournalPage.void(${entry.id})"),
    ("deposits.js", "DepositsPage.voidDeposit(${d.id})"),
    ("job_costs.js", "JobCostsPage.voidEntry(${jc.id})"),
]


def test_every_void_or_delete_is_marked_and_never_focused_first():
    """A button that says Void or Delete, or is named so (an icon button's
    aria-label, "Delete attachment"), carries the marker."""
    unmarked = []
    for p in sorted(JS.glob("*.js")):
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"<button([^>]*)>", src):
            attrs = m.group(1)
            says = re.match(r"\s*(Void|Delete)\b", src[m.end() : m.end() + 40])
            named = re.search(r'aria-label="(Void|Delete)\b', attrs)
            if (says or named) and "data-destructive" not in attrs:
                unmarked.append(f"{p.name}: {m.group(0)[:80]}")
    assert not unmarked, unmarked
    assert (
        sum(
            _src(n).count('data-destructive aria-label="Delete attachment"')
            for n in ("bills.js", "expenses.js", "invoices.js")
        )
        == 3
    )
    for name, call in DESTRUCTIVE:
        assert f'onclick="{call}"' in _src(name), (name, call)


# ── NEW-38 ────────────────────────────────────────────────────────────────


def test_the_customer_pages_invoice_and_payment_rows_carry_links():
    src = _src("customers.js")
    assert (
        "rowLink(`#/invoices/${i.id}`, `invoice:${i.id}`, i.invoice_number || `#${i.id}`)"
        in src
    )
    assert (
        "rowLink(`#/payments/${p.id}`, `payment:${p.id}`, p.date || `#${p.id}`)" in src
    )
    assert 'onclick="event.preventDefault()">${escapeHtml(text)}</a>' in src
    # the row's click still opens the document, through the router (a
    # history entry, Back returns), noting the link left for the focus: the
    # row passes itself, its link is the one noted (review of W-7)
    assert (
        "const hop = (href) => `ReportsPage._leaveFrom(this);closeModal({ keepAddress: true });App.navigate('${href}')`;"
        in src
    )
    assert 'onclick="${hop(`#/invoices/${i.id}`)}"' in src
    assert 'onclick="${hop(`#/payments/${p.id}`)}"' in src
    assert "PaymentsPage.view(${p.id})" not in src
    # and the page's render puts the focus back on it
    assert (
        "if (history.state && history.state.focus) setTimeout(() => ReportsPage._refocusRow($('#modal-body')), 0);"
        in _src("app.js")
    )


# ── NEW-37 ────────────────────────────────────────────────────────────────


def test_the_line_class_and_function_cells_leave_their_names_to_the_grid_rule():
    utils = _src("utils.js")
    assert "for this line" not in utils
    assert (
        '<select class="${cls}-fund" data-no-search><option value="">Same as header</option>'
        in utils
    )
    assert '<select class="${cls}">${Nonprofit.optionsHtml(selected' in utils
    # the rule that names them: the column heading and "line N"
    assert "rowName = `line ${row.sectionRowIndex + 1}`;" in utils
    assert "return [heading, rowName].filter(Boolean).join(', ');" in utils


# ── NEW-39 ────────────────────────────────────────────────────────────────


def test_the_drill_downs_position_is_a_live_status_with_the_account():
    src = _src("reports.js")
    assert (
        '<span id="drill-position" class="grid-live" role="status" aria-live="polite"'
        in src
    )
    assert (
        "pos.textContent = list.length > 1 && i >= 0 ? `${i + 1} of ${list.length}: ${list[i].label}` : '';"
        in src
    )


# ── Review, round 2: Back is inert over a form ───────────────────────────


def test_back_is_inert_over_a_dialog_with_no_address_of_its_own():
    app = _src("app.js")
    assert "backAllowed() { return App.canGoBack() && !App.editingDialog(); }" in app
    assert "return (m.dataset.address || '') !== (location.hash || '#/');" in app
    # the three ways the router gives a dialog its address mark it
    assert "App.dialogAddressed();  // the open dialog's own" in app  # documentAddress
    assert (
        "App.dialogAddressed();\n                    if (history.state && history.state.focus)"
        in app
    )  # withDocument
    assert "App.dialogAddressed();  // the view's own address" in _src(
        "reports.js"
    )  # setAddress
    # a plain form clears the mark, and the button follows; a mark the
    # opener itself set (its address first, then openModal) stays
    utils = _src("utils.js")
    assert "const wasOpen = !$('#modal-overlay').classList.contains('hidden');" in utils
    assert (
        "const own = modal.dataset.address === (location.hash || '#/') && (!wasOpen || !!(window.App && window.App._marking));"
        in utils
    )
    assert utils.count("if (!own) delete modal.dataset.address;") == 1  # openModal
    assert (
        "App._marking = true;\n        setTimeout(() => { App._marking = false; }, 0);"
        in app
    )
    assert utils.count("delete $('#modal').dataset.address;") == 1  # closeModal
    assert utils.count("window.App.syncBack();") == 2
    assert "btn.classList.toggle('tb-back--under', App.editingDialog());" in app
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert ".tb-back.tb-back--under {\n    z-index: auto;\n}" in css
    # a customer's or vendor's page opens the dialog, then gives it its address
    for name, call in (
        ("customers.js", "App.documentAddress(`#/customers/${customer.id}`)"),
        ("vendors.js", "App.documentAddress(`#/vendors/${vendor.id}`)"),
    ):
        src = _src(name)
        at = src.index(call)
        assert "openModal(" in src[at - 200 : at], name


# ── Review, round 2: a replaced address keeps the entry's state ──────────


def test_a_replaced_address_keeps_the_entrys_state_and_the_button_follows():
    """Never lit with canGoBack() false: every replaceState of the app's
    carries history.state along and re-syncs the button."""
    for name in (
        "app.js",
        "reports.js",
        "settings.js",
        "utils.js",
        "customers.js",
        "vendors.js",
    ):
        assert "replaceState(null" not in _src(name), name
    reports = _src("reports.js")
    assert reports.count("ReportsPage._addressReportCenter();") == 3
    assert (
        "history.replaceState(history.state, '', '#/reports');\n        App.addressShown();"
        in reports
    )
    assert "if (location.hash !== '#/settings') App.stayPut();" in _src("settings.js")


# ── Review, round 2: the nits ────────────────────────────────────────────


def test_an_unchecked_radio_group_is_one_stop_its_first_radio():
    utils = _src("utils.js")
    stops = utils[utils.index("function _tabStops(root, from)") :]
    stops = stops[: stops.index("\n}\n")]
    assert "if (picked.has(g)) return el.checked;" in stops
    assert (
        "if (seen.has(g)) return false;\n        seen.add(g);\n        return true;"
        in stops
    )


def test_the_shortcut_list_follows_the_screen_reader_bullets():
    docs = (ROOT / "docs/accessibility.md").read_text(encoding="utf-8")
    bullets_end = docs.index("State is never conveyed by colour alone")
    assert docs.index("### Keyboard shortcuts") > bullets_end
    assert docs.index("### Keyboard shortcuts") < docs.index(
        "## What we know is still open"
    )
    assert docs.index("### Keyboard shortcuts") > docs.index(
        "### Screen readers and keyboards"
    )


# ── Review, round 3: a declined leave goes back to its entry ─────────────


def test_a_declined_leave_goes_back_to_the_entry_it_left():
    app = _src("app.js")
    assert "entryState(from) { return { from, n: App._nShown + 1 }; }" in app
    assert (
        "if (typeof n === 'number' && n < App._nShown) history.forward(); else history.back();"
        in app
    )
    assert (
        "if (App._stay) { App._stay = false; App.addressShown(); return; }" in app
    )  # navigate
    assert "if (App._stay) { App.syncBack(); return; }" in app  # popstate
    # every entry the app makes carries its place
    assert app.count("App.entryState(") == 3
    assert "history.pushState(App.entryState(here), '', url);" in _src("reports.js")
    assert "if (location.hash !== '#/settings') App.stayPut();" in _src("settings.js")


# ── Round 3, NEW-42: Back is a bordered gold button that reads "← Back" ──


def test_back_is_a_bordered_gold_button_in_both_themes():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<button[^>]*id="back-btn"[^>]*>&larr; Back</button>', html)
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    lit = css[css.index(".tb-back {") : css.index(".tb-back.tb-back--under {")]
    # a deep gold border: 3:1 against the bar's darker stop (WCAG 1.4.11)
    assert "border: 2px solid #8f6a1e;" in lit
    assert "color: var(--qb-navy);" in lit and "background: #fdf3dc;" in lit
    assert "font-size: 15px" not in lit  # words, not a glyph
    # the pointer over it keeps a gold border: the grey hover border of the
    # other buttons (.tb-btn:hover) does not win
    assert ".tb-back:not(:disabled):hover {" in lit
    assert "border-color: #75561a;" in lit
    # nowhere to go: muted, borderless, in its place
    off = css[css.index(".tb-back:disabled {") :]
    off = off[: off.index("}")]
    for line in (
        "color: var(--text-muted);",
        "background: transparent;",
        "border-color: transparent;",
    ):
        assert line in off, line
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    on = dark[dark.index('[data-theme="dark"] .tb-back {') :]
    on = on[: on.index("}")]
    assert "color: var(--qb-gold);" in on and "border-color: var(--qb-gold);" in on
    assert '[data-theme="dark"] .tb-back:not(:disabled):hover {' in dark
    off = dark[dark.index('[data-theme="dark"] .tb-back:disabled {') :]
    off = off[: off.index("}")]
    assert "border-color: transparent;" in off and "color: var(--text-muted);" in off


# ── Round 3, NEW-43: focus is visible on every control, by keyboard only ─


def _rule(css, head, last=False):
    """The body of the (first, or `last`) rule whose selector starts with
    `head`."""
    start = css.rindex(head) if last else css.index(head)
    return css[start : css.index("}", start)]


def test_focus_is_visible_on_every_control_by_keyboard_only():
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    ring = _rule(css, ":where(a:focus-visible,")
    for sel in (
        "a",
        "button",
        "summary",
        '[tabindex]:not([tabindex="-1"])',
        'input[type="checkbox"]',
        'input[type="radio"]',
        'input[type="file"]',
    ):
        assert f"{sel}:focus-visible" in ring, sel
    # a file's picker is a button as the keyboard has it, in a form too:
    # the gold ring and its halo, not a field's focus style
    assert 'input[type="file"]' in _rule(css, ":where(a, button, summary,")
    assert (
        'input:is([type="checkbox"], [type="radio"], [type="file"]):focus-visible {\n'
        "    box-shadow: 0 0 0 8px var(--focus-halo);" in css
    )
    assert "outline: 2px solid var(--focus-ring);" in ring
    assert "outline-offset: 2px;" in ring
    assert ":focus {" not in ring and ":focus," not in ring  # never a mouse click's
    # a field keeps its own focus style (the blue border and pale ground):
    # no ring on a click into a box of a dense form
    for sel in ("input:", "select", "textarea"):
        assert sel not in ring, sel
    # the ring sits first
    assert css.index(":where(a:focus-visible,") < css.index(":root {")
    # the token: a deeper gold than the brand's in light (3.9:1 on white,
    # WCAG 1.4.11), the brand's own in dark
    assert "--focus-ring:     #a37a29;" in css
    # the outline is dropped in four places only: a field's focus style (a
    # form's, and every field's at the end of the sheet: the exception
    # above), the toolbar's search box's own, and what takes focus by
    # script alone — the dialog itself and the skip link's target, a place
    # rather than a control
    assert css.count("outline: none") == 4
    assert ".form-group textarea:focus {\n    outline: none;" in css
    assert ":focus {\n    outline: none;\n    border-color: var(--input-focus);" in css
    assert ".tb-search:focus {\n    outline: none;" in css
    assert '[tabindex="-1"]:focus {\n    outline: none;\n}' in css
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    assert "--focus-ring:     var(--qb-gold);" in dark
    docs = (ROOT / "docs/accessibility.md").read_text(encoding="utf-8")
    assert "**The keyboard's place is visible**" in docs
    assert "A field keeps its own focus style" in docs


# ── Round 3 review: the ring is two colours, and a control's own ring wins ─


def test_the_generic_ring_counts_for_nothing_against_a_controls_own():
    """Bare, [tabindex]:not([tabindex="-1"]):focus-visible is (0,3,0) and
    beat the search results' and the grid's own rings, (0,2,0): a result
    drew the gold, cut to its top line, and the grid the gold. The rule is
    wholly inside :where(), :focus-visible too, so it counts for nothing."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    start = css.index(":where(a:focus-visible,")
    selector = css[start : css.index("{", start)]
    # every selector of the ring inside the one :where(...)
    assert selector.rstrip().endswith(")"), selector
    assert selector.count(":where(") == 1 and ":is(" not in selector
    assert "\n" + selector.split(",")[0] in css  # the rule begins at :where
    # the controls' own rings, as they were before the gold came
    grid = _rule(css, ".grid-scroll:focus-visible {")
    assert "outline: 2px solid var(--qb-navy);" in grid
    item = _rule(css, ".search-item:focus-visible {", last=True)
    assert "outline: 2px solid var(--qb-blue, #2a6fb4);" in item
    assert "outline-offset: -2px;" in item
    assert (
        '[data-theme="dark"] .grid-scroll:focus-visible { outline-color: var(--text-link); }'
        in dark
    )
    # the search results' in dark: the link blue (3.5:1 and more on every
    # side), --qb-blue's 3.0 against the border below was too thin
    assert (
        '[data-theme="dark"] .search-item:focus-visible { outline-color: var(--text-link); }'
        in dark
    )


def test_the_ring_has_a_halo_of_the_themes_own_ground():
    """The gold alone fell under 3:1 on the toolbar's grey gradient (2.54:1)
    and a dialog's blue title bar (1.73): it meets a halo of the theme's own
    ground on both its sides, and the control is lifted so what follows it
    cannot paint over the halo. A control's own ring has none."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    ring = _rule(css, ":where(a:focus-visible,")
    # from the border box out to 8px, the ring drawn over it from 2 to 4:
    # 2px of halo inside the gold and 4px outside it, where the gate reads
    # the ground, on every control: a link in running text too (the
    # General Ledger's account names met the table header beyond at 2.24:1
    # with 2px), so there is no wider rule for buttons and table links
    assert "box-shadow: 0 0 0 8px var(--focus-halo);" in ring
    assert "position: relative;" in ring
    assert ":where(button:focus-visible," not in css
    assert "0 0 0 6px var(--focus-halo)" not in css
    assert "--focus-halo:     #ffffff;" in css
    assert "--focus-halo:     #14161c;" in dark
    # on the toolbar the light theme's gold is the Back border's deeper one,
    # which clears the bar itself (3.2:1 against its darker stop); the dark
    # theme's own there
    assert "--focus-ring: #8f6a1e;" in _rule(css, "#topbar {")
    assert "--focus-ring: var(--qb-gold);" in _rule(
        dark, '[data-theme="dark"] #topbar {'
    )
    # a table clips what overflows it, as it did: the halo below its last
    # row has the room inside, the container's below its table (on the
    # last row's cells it set a one-row table's words high in their row;
    # letting the overflow out while focused spilled a wide table past
    # its side)
    container = _rule(css, ".table-container {")
    assert "overflow-x: auto;" in container and "overflow-y: hidden;" in container
    assert ":focus-within {\n    overflow" not in css
    below = _rule(css, ".table-container:not(.table-container--scroll) {")
    assert "padding-bottom: 8px;" in below
    assert "tr:last-child > :is(td, th) {" not in css
    # the dialog's title bar has room for it above the × (it scrolls), and
    # Tab scrolls a control into view with room for it
    assert "margin: 2px 0;" in _rule(css, ".modal-close {")
    assert "scroll-margin: 8px;" in _rule(css, ":where(a, button, summary,")
    # the controls' own rings take none; the grid's sits on its edge, where
    # the frozen header and Account column do not paint over it
    grid = _rule(css, ".grid-scroll:focus-visible {")
    assert "outline-offset: 0; box-shadow: none;" in grid
    assert "box-shadow: none;" in _rule(css, ".search-item:focus-visible {", last=True)


def test_a_checkbox_or_a_radio_takes_the_ring_and_a_field_does_not():
    """`.form-group input:focus { outline: none }` took the outline off a
    checkbox too, whose border and ground do not show: the P&L by Class
    chooser's classes and "Show classes with no activity" showed nothing
    on focus. The field rule leaves checkboxes and radios out; their halo
    is set over the sunken shadow every input is given."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    assert (
        '.form-group input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):focus,\n'
        ".form-group select:focus,\n"
        ".form-group textarea:focus {\n"
        "    outline: none;" in css
    )
    assert ".form-group input:focus" not in css
    halo = 'input:is([type="checkbox"], [type="radio"], [type="file"]):focus-visible {\n    box-shadow: 0 0 0 8px var(--focus-halo);\n}'
    assert halo in css
    # (0,2,1) beats .form-group input and the line-item and toolbar inputs,
    # (0,1,1); in dark, [data-theme] .form-group input is (0,2,1) and later
    assert f'[data-theme="dark"] {halo}' in dark
    # the chooser scrolls: room in it for the first row's ring and halo; and
    # room between "Show classes with no activity" and its words
    assert "padding: 8px;" in _rule(css, ".grid-chooser__list {")
    assert "#grid-empty { margin-right: 5px; }" in css


def test_the_skip_link_draws_the_ring_and_goes_to_the_content_in_place():
    """The skip link kept the brand's pale gold, 1.95:1 on the toolbar, at
    the window's corner, where its top and left lines fell outside; and
    Enter on it set the address to #page-content, which the router showed
    as "Page not found". It draws the ring and halo 8px in from the corner,
    in the toolbar's gold (it lands on the toolbar, outside it: the light
    theme's own gold was 2.51:1 against the bar's darker stop), and it
    moves focus to the content, which takes it as a place, with the
    address unchanged."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    skip = _rule(css, ".skip-link:focus {")
    for line in (
        "top: 8px;",
        "left: 8px;",
        "--focus-ring: #8f6a1e;",
        "outline: 2px solid var(--focus-ring);",
        "outline-offset: 2px;",
        "box-shadow: 0 0 0 8px var(--focus-halo);",
    ):
        assert line in skip, line
    assert "--qb-gold" not in skip
    assert "--focus-ring: #8f6a1e;" in _rule(css, "#topbar {")
    assert "--focus-ring: var(--qb-gold);" in _rule(
        dark, '[data-theme="dark"] .skip-link:focus {'
    )
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '<a href="#page-content" class="skip-link">Skip to main content</a>' in html
    # the content takes focus only while the skip link puts it there: a
    # standing tabindex made any click on blank content focus it
    assert '<div id="page-content"></div>' in html
    assert 'id="page-content" tabindex' not in html
    boot = _src("bootstrap.js")
    wire = boot[boot.index("const skip = document.querySelector('.skip-link');") :]
    wire = wire[: wire.index("main.focus();") + len("main.focus();")]
    assert "e.preventDefault();" in wire
    assert "const main = document.getElementById('page-content');" in wire
    assert "main.setAttribute('tabindex', '-1');" in wire
    assert (
        "main.addEventListener('blur', () => main.removeAttribute('tabindex'), { once: true });"
        in wire
    )


def test_a_sidebar_link_draws_the_ring_inside_its_edge():
    """#sidebar scrolls (overflow-y), which cut the ring at +2px to its top
    and bottom lines. On a sidebar link the ring and its halo are inside;
    the halo is the sidebar's own navy."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    link = _rule(css, "#sidebar .nav-link:focus-visible {")
    # the ring 4px in, 4px of halo outside it and 2px inside
    assert "outline-offset: -6px;" in link
    assert "box-shadow: inset 0 0 0 8px var(--focus-halo);" in link
    # the left border (the open page's marker too) takes the halo's colour
    assert "border-left-color: var(--focus-halo);" in link
    assert "--focus-halo: var(--qb-navy-dark);" in _rule(css, "#sidebar {")
    assert "--focus-halo: #080c14;" in _rule(dark, '[data-theme="dark"] #sidebar {')


# ── Round 3, NEW-44: a page's Notes box saves only a change ──────────────


def test_a_pages_notes_box_saves_only_a_change():
    for name, page, box, api in (
        ("customers.js", "CustomersPage", "cust-notes", "customers"),
        ("vendors.js", "VendorsPage", "vend-notes", "vendors"),
    ):
        src = _src(name)
        notes = src[src.index("async _saveNotes(id, value)") :]
        notes = notes[: notes.index("\n    },")]
        assert f"document.getElementById(`{box}-${{id}}`)" in notes, name
        assert "_notesSaving: {}," in src, name
        # what is on file: the save on its way, else what the box was
        # loaded with (none and empty alike) and then what was saved
        assert f"const pending = {page}._notesSaving[id];" in notes, name
        assert (
            "const onFile = pending ? pending.value : box ? box.defaultValue : null;"
            in notes
        ), name
        # compared trimmed; a note of spaces is no note
        assert (
            "if (onFile !== null && value.trim() === onFile.trim()) return;" in notes
        ), name
        assert "if (!value.trim()) value = '';" in notes, name
        assert notes.index("return;") < notes.index("'saving…'"), name
        # one save at a time, after the one on its way; saved as typed
        assert "if (pending) await pending.done.catch(() => {});" in notes, name
        assert f"await API.put(`/{api}/${{id}}`, {{ notes: value }});" in notes, name
        assert "if (box) box.defaultValue = value;" in notes, name
        assert (
            f"if ({page}._notesSaving[id] === save) delete {page}._notesSaving[id];"
            in notes
        ), name
        # the word beside the box, one at a time: a new one stops the
        # clearing a "✓ saved" set going (it wiped a "⚠ save failed")
        for said in (
            f"{page}._noteStatus(id, 'saving…');",
            f"{page}._noteStatus(id, '✓ saved', 1500);",
            f"{page}._noteStatus(id, '⚠ save failed');",
        ):
            assert said in notes, (name, said)
        assert "setTimeout" not in notes and "textContent" not in notes, name
        status = src[src.index("    _noteStatus(id, text, clearAfter = 0) {") :]
        status = status[: status.index("\n    },")]
        assert status.index(f"clearTimeout({page}._noteTimers[id]);") < status.index(
            "status.textContent = text;"
        ), name
        assert f"{page}._noteTimers[id] = setTimeout(" in status, name


# ── Round 3, W-7: Back refocuses the chart's account and the drill-down's line ──


def test_back_refocuses_the_charts_account_link_and_the_drill_downs_line():
    app = _src("app.js")
    # the link passes itself: a mouse click does not focus a link in WebKit
    assert (
        'data-row-key="account:${Number(a.id)}" onclick="ReportsPage._leaveFrom(this)"'
        in app
    )
    nav = app[app.index("async navigate(hash) {") :]
    nav = nav[: nav.index("\n    },")]
    hook = "if (history.state && history.state.focus) ReportsPage._refocusRow($('#page-content'));"
    assert hook in nav
    assert nav.index("$('#page-content').innerHTML = html;") < nav.index(hook)
    reports = _src("reports.js")
    assert (
        "data-row-key=\"line:${escapeHtml(String(e.line_id || e.transaction_id || ''))}\""
        ' onclick="ReportsPage._leaveFrom(this)"' in reports
    )
    leave = reports[reports.index("_leaveFrom(from) {") :]
    leave = leave[: leave.index("\n    },")]
    # the element passed, else the one whose click is being handled, else
    # the focused one; a row is left from by its keyed link
    for line in (
        "let el = (from && from.nodeType === 1) ? from",
        ": (ev && ev.type === 'click' && ev.currentTarget && ev.currentTarget.nodeType === 1) ? ev.currentTarget",
        ": document.activeElement;",
        "if (el && el.tagName === 'TR') el = el.querySelector('[data-row-key]') || el;",
    ):
        assert line in leave, line
    assert "const inDialog = !!el.closest('#modal-body');" in leave
    assert (
        "const n = inDialog ? [...document.querySelectorAll('#modal-body a[href], #modal-body button')].indexOf(el) : -1;"
        in leave
    )
    refocus = reports[reports.index("_refocusRow(root) {") :]
    refocus = refocus[: refocus.index("\n    },")]
    assert (
        "if (key.startsWith('n:') && !root.closest('#modal-body')) return false;"
        in refocus
    )


# ── Round 3 review, Q6: every row hop keys on the element clicked ────────


def test_every_row_hop_passes_the_element_clicked():
    """A mouse click focuses nothing in WebKit, so a hop that noted the
    focused element noted whatever had focus before: each passes its own
    `this` — a report's customer and vendor rows, a customer's and a
    vendor's document rows, Job Profitability's rows, a class's heading or
    fund row (whose own push comes after an await), P&L by Job's heading."""
    reports = _src("reports.js")
    for page in ("Customer", "Vendor"):
        fn = reports[reports.index(f"open{page}(id, from) {{") :]
        fn = fn[: fn.index("\n    },")]
        assert "ReportsPage._leaveFrom(from);" in fn, page
    assert reports.count("ReportsPage.openCustomer(${i.customer_id}, this)") == 2
    assert reports.count("ReportsPage.openVendor(${i.vendor_id}, this)") == 2
    assert "ReportsPage.openCustomer(${j.customer_id}, this)" in reports
    # the Past customers row (#436) passes the element too
    assert "ReportsPage.openCustomer(${r.customer_id}, this)" in reports
    calls = re.findall(r"ReportsPage\.open(?:Customer|Vendor)\(([^)]*)\)", reports)
    assert len(calls) == 6 and all(c.endswith(", this") for c in calls), calls
    # Job Profitability: the job's link passes itself, the row its job's link
    assert "`ReportsPage._leaveFrom(${from});App.navigate(" in reports
    assert """jobCall(j, "this.querySelector('[data-row-key^=job]')")""" in reports
    assert "jobCall(j, 'this')" in reports
    # a class's heading, subtotal and fund row pass the link through the await
    assert reports.count("${args(c.class_id, c.class_name, dates)}, null, this)") == 2
    assert "'fund-balances', this)" in reports
    plc = reports[reports.index("ReportsPage.profitLossOfClass = async function") :]
    plc = plc[: plc.index("await API.get('/classes")]
    assert "(classId, className, prefill, from = null, row = null)" in plc
    assert "if (row) ReportsPage._leaveFrom(row);" in plc
    assert (
        "`ReportsPage._leaveFrom(this);App.navigate(${JSON.stringify(jobUrl(c))})`"
        in reports
    )
    # no hop is left noting the focused element alone
    assert "ReportsPage._leaveFrom();App.navigate" not in reports
    for name in ("customers.js", "vendors.js"):
        src = _src(name)
        assert (
            "const hop = (href) => `ReportsPage._leaveFrom(this);closeModal({ keepAddress: true });App.navigate('${href}')`;"
            in src
        ), name


def test_a_pages_report_links_note_themselves():
    """A customer's and a vendor's page's Reports links noted no row, so
    Back from the report did not put focus back on the link: each is a
    real link with a key that notes itself on its click; the statement
    (a download) notes nothing."""
    for name in ("customers.js", "vendors.js"):
        src = _src(name)
        row = src[src.index("    _reportsRow(") :]
        row = row[: row.index("\n    },")]
        assert (
            '<a class="btn btn-sm btn-secondary" href="${ReportsPage.viewUrl(view, params)}"'
            ' data-row-key="report:${view}" onclick="ReportsPage._leaveFrom(this)">'
            in row
        ), name
    row = _src("customers.js")
    row = row[row.index("    _reportsRow(id) {") :]
    row = row[: row.index("\n    },")]
    assert "onclick=\"window.open('/api/reports/customer-statement/${id}/pdf" in row
    assert row.count("_leaveFrom") == 1


def test_tab_brings_a_grid_stop_out_from_under_the_frozen_parts():
    """The P&L by Class / by Job grid: its scroll padding is the frozen
    header row's height and Account column's width, measured whenever it
    is drawn and the window changes; a keyboard stop in it is brought in
    whole (Chromium leaves one partly in view where it is), a mouse's not;
    and its headings and last row have the ring and halo's room inside."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert (
        "scroll-padding: var(--grid-head, 0px) 0 0 var(--grid-frozen, 0px);"
        in _rule(css, ".grid-scroll {")
    )
    assert "padding-top: 8px;" in _rule(css, ".pivot-grid thead th {", last=True)
    assert "padding-bottom: 8px;" in _rule(css, ".grid-scroll {")
    src = _src("reports.js")
    fit = src[src.index("ReportsPage._gridFit = function () {") :]
    fit = fit[: fit.index("\n};")]
    assert "g.style.setProperty('--grid-frozen', `${corner.offsetWidth}px`);" in fit
    assert "g.style.setProperty('--grid-head', `${head.offsetHeight}px`);" in fit
    for view in ("profit_loss_by_class", "profit_loss_by_job"):
        opts = src[src.index(f"reportType: '{view}'") :]
        opts = opts[: opts.index("\n")]
        assert "afterRender:" in opts and "ReportsPage._gridFit()" in opts, view
    reveal = src[src.index("ReportsPage._gridReveal = function (e) {") :]
    reveal = reveal[: reveal.index("\n};")]
    assert "a.closest('#grid-scroll')" in reveal
    assert (
        "if (g && a !== g && App.keyboardFocus()) "
        "a.scrollIntoView({ block: 'nearest', inline: 'nearest' });" in reveal
    )
    # wired once, when the app starts (not as the script loads, which the
    # node probes of these scripts do without a document)
    init = _src("app.js")
    init = init[init.index("    init() {") :]
    init = init[: init.index("\n    },")]
    assert "ReportsPage._gridReveal(e);" in init
    assert "window.addEventListener('resize', () => ReportsPage._gridFit());" in init
    assert "document.addEventListener('focusin'" not in src
    assert "window.addEventListener('resize'" not in src


def test_a_click_that_opens_elsewhere_notes_nothing():
    """A Ctrl, ⌘, Shift or Alt click, or another button, on a real link
    whose following no handler stopped opens it somewhere else and leaves
    the page: _leaveFrom notes nothing then, before anything else."""
    src = _src("reports.js")
    leave = src[src.index("    _leaveFrom(from) {") :]
    leave = leave[: leave.index("\n    },")]
    first = leave.split("\n")[1:3]
    assert first == [
        "        const ev = window.event;",
        "        if (ReportsPage._opensElsewhere(ev)) return;",
    ], first
    away = src[src.index("    _opensElsewhere(ev) {") :]
    away = away[: away.index("\n    },")]
    assert "ev.type !== 'click' && ev.type !== 'auxclick'" in away
    # a new tab is ⌘-click on a Mac, Ctrl-click elsewhere
    assert "const newTab = App.isMac() ? ev.metaKey : ev.ctrlKey;" in away
    assert (
        "if (!(newTab || ev.shiftKey || ev.altKey || ev.button !== 0)) return false;"
        in away
    )
    assert "ev.target.closest('a[href]')" in away
    assert "!/^\\s*javascript:/i.test(a.getAttribute('href'))" in away
    assert "!ev.defaultPrevented" in away


# Every field on a page or in a dialog that is not a control (a checkbox,
# a radio, a file, a button), as style.css and dark.css select it
FIELD = (
    ':where(#page-content, #modal) :where(input:not([type="checkbox"], [type="radio"],'
    ' [type="file"], [type="range"], [type="color"], [type="hidden"], [type="submit"],'
    ' [type="button"], [type="reset"], [type="image"]), select, textarea)'
)
FIELD_FOCUSED = FIELD.replace(":where(input", ":is(input", 1) + ":focus"


def test_every_field_takes_a_fields_look_and_focus_style():
    """Every field on a page or in a dialog, not only a form group's — a
    list's search box and filters, a line item, a type-ahead box, Budgets'
    cells, Settings' lists, the chart's filter, a report's pickers and
    dates, a page's Notes box — takes a field's look at no specificity (a
    look of its own wins) and a field's focus style over every look (last
    in the sheet), in both themes; with the system's high-contrast colours
    the system's highlight is its ring. The dark theme's focus for a form's
    own text box outranks the light one again (round 3 had given the light
    rule two :not()s, and the pale ground showed in the dark theme)."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    rest = _rule(
        css,
        f".form-group input,\n.form-group select,\n.form-group textarea,\n{FIELD} {{",
    )
    for line in (
        "padding: 4px 6px;",
        "border: 1px solid var(--input-border);",
        "color: var(--text-primary);",
        "background: #ffffff;",
    ):
        assert line in rest, line
    assert "background: linear-gradient(180deg, #ffffff 0%, #f0f2f4 100%);" in _rule(
        css, ".form-group select,\n:where(#page-content, #modal) :where(select) {"
    )
    assert "background: #f1f3f6;" in _rule(
        css,
        ".form-group input[disabled],\n.form-group select[disabled],\n"
        ":where(#page-content, #modal) :where(input, select, textarea):disabled {",
    )
    # the focus: last in the sheet (before the high-contrast block), over
    # every look of a field's own
    forced_at = css.rindex("@media (forced-colors: active) {")
    at = css.rindex(FIELD_FOCUSED + " {", 0, forced_at)
    focus = css[at : css.index("}", at)]
    for line in (
        "outline: none;",
        "border-color: var(--input-focus);",
        "background: #fffff8;",
    ):
        assert line in focus, line
    assert at > css.index(".ai-settings-form input:focus,")
    forced = css[forced_at:]
    assert FIELD_FOCUSED in forced and "outline: 2px solid Highlight;" in forced
    # the dark theme's
    assert "background: #14161c;" in _rule(
        dark,
        f'[data-theme="dark"] .form-group textarea,\n[data-theme="dark"] {FIELD} {{',
    )
    dfocus = _rule(
        dark,
        '[data-theme="dark"] .form-group input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):focus,',
    )
    assert f'[data-theme="dark"] {FIELD_FOCUSED}' in dfocus
    assert "background: #1a1e28;" in dfocus
    assert '[data-theme="dark"] .form-group input:focus' not in dark
    # no field of a page or a dialog has a border or a class of its own in
    # place of the field's: the quick-add boxes and the payment's amounts
    for name in (
        "invoices.js",
        "estimates.js",
        "sales_receipts.js",
        "vendors.js",
        "payments.js",
    ):
        assert not re.search(
            r"<input[^>]*border:1px solid var\(--gray-300\)", _src(name)
        ), name
    assert 'class="field"' not in _src("customers.js") + _src("vendors.js") + _src(
        "reports.js"
    )


def test_a_control_the_keyboard_puts_under_settings_save_bar_is_scrolled_clear():
    """Settings' save bar is held to the window's bottom (sticky): a control
    the keyboard puts under it is scrolled up clear of it, its ring and halo
    too, by measure (a text box is brought into view only by its caret
    line); not the bar's own Save, not a mouse's focus."""
    src = _src("settings.js")
    assert 'id="settings-savebar"' in src and "position:sticky; bottom:0;" in src
    clear = src[src.index("SettingsPage._clearSaveBar = function (e) {") :]
    clear = clear[: clear.index("\n};")]
    init = _src("app.js")
    init = init[init.index("    init() {") :]
    init = init[: init.index("\n    },")]
    assert "SettingsPage._clearSaveBar(e);" in init
    assert "document.addEventListener('focusin'" not in src
    for line in (
        "const a = e.target, bar = document.getElementById('settings-savebar');",
        "if (!bar || !a || !a.closest || bar.contains(a) || !a.closest('#settings-form')) return;",
        "if (!App.keyboardFocus()) return;",
        "if (!b.height || !content) return;",
        "const over = a.getBoundingClientRect().bottom + 8 - b.top;",
        "if (over > 0) content.scrollTop += Math.ceil(over);",
    ):
        assert line in clear, line


def test_a_report_center_card_is_a_link_that_looks_as_the_card_did():
    """The Report Center's cards are links to their reports
    (ReportsPage._card, pinned in tests/test_report_views.py): no link
    colour or underline, and the keyboard's halo in place of the card's own
    shadow while focused, over the dark theme's .card too; Space opens one
    as Enter does."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    card = _rule(css, ".card-link {")
    for line in ("display: block;", "color: inherit;", "text-decoration: none;"):
        assert line in card, line
    assert ".card-link:hover { text-decoration: none; }" in css
    assert (
        "a.card-link:focus-visible { box-shadow: 0 0 0 8px var(--focus-halo); }" in css
    )
    src = _src("reports.js")
    key = src[src.index("    _cardKey(e) {") :]
    key = key[: key.index("\n    },")]
    assert "if (e.key !== ' ' || e.altKey || e.ctrlKey || e.metaKey) return;" in key
    assert "e.preventDefault();" in key and "e.currentTarget.click();" in key


def test_banking_and_the_dashboards_bank_cards_are_links():
    """Banking's account cards and the dashboard's bank cards are links to
    their registers, keyed, named by the account and described by the rest
    of the card, noting themselves on their click; Space opens one as Enter
    does. No card anywhere is a click alone any more."""
    banking = _src("banking.js")
    assert (
        '<a class="card card-link" href="#/banking/${a.account_id}"'
        ' data-row-key="bank:${a.account_id}"' in banking
    )
    assert (
        'aria-label="${escapeHtml(a.name)}" aria-describedby="${id}-kind ${id}-value ${id}-feed"'
        in banking
    )
    dash = _src("dashboard.js")
    assert (
        '<a class="card card-link" href="#/banking/${b.id}" data-row-key="bank:${b.id}"'
        in dash
    )
    assert (
        'aria-label="${escapeHtml(b.name)}" aria-describedby="dash-bank-${b.id}-value'
        in dash
    )
    for src in (banking, dash):
        assert (
            'onclick="ReportsPage._leaveFrom(this)" onkeydown="ReportsPage._cardKey(event)"'
            in src
        )
    for f in sorted(JS.glob("*.js")):
        assert 'class="card" style="cursor:pointer" onclick=' not in f.read_text(
            encoding="utf-8"
        ), f.name


def test_the_docs_say_what_tab_reaches_on_a_mac_page():
    """docs/accessibility.md says plainly that on a Mac with Keyboard
    navigation off, as macOS ships, plain Tab on a page moves between text
    fields and lists only, Option+Tab (or the setting turned on) reaching
    links and buttons, while in a dialog Tab reaches every control."""
    docs = (ROOT / "docs/accessibility.md").read_text(encoding="utf-8")
    dialogs = docs[docs.index("- **Dialogs are real dialogs:**") :]
    dialogs = " ".join(dialogs[: dialogs.index("\n- **")].split())
    for words in (
        'whatever the Mac\'s "keyboard navigation" setting: the dialog moves focus itself',
        "On a page, outside a dialog, the Mac's setting applies",
        "with Keyboard navigation off, as macOS ships, plain Tab moves between"
        " text fields and lists only",
        "Option+Tab — or Keyboard navigation turned on in System Settings → Keyboard"
        " — reaches links and buttons too",
    ):
        assert words in dialogs, words


def test_a_dark_themes_fields_say_they_are_dark():
    """The browser draws a field's own parts (a date's calendar icon, a
    select's arrow) for a light page unless the field says it is dark: the
    dark theme's fields say so; the light theme sets no color-scheme, and
    checkboxes, radios and a file's button keep their look."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    assert "color-scheme" not in css
    head = (
        '[data-theme="dark"] .form-group :is(input:not([type="checkbox"], [type="radio"],'
        ' [type="file"]), select, textarea),\n'
        f'[data-theme="dark"] {FIELD} {{'
    )
    assert "color-scheme: dark;" in _rule(dark, head)
    assert dark.count("color-scheme") == 1


def test_what_keeps_the_keyboards_place_in_sight_asks_what_moved_focus():
    """A text box matches :focus-visible on a click as well as on Tab, so
    what scrolls a control into sight for the keyboard (Settings' save
    bar, the grid) asks what moved focus last: a key (any but a modifier
    alone), or a pointer (App.init keeps it, ahead of anyone else's
    handlers)."""
    app = _src("app.js")
    assert "keyboardFocus() { return App._input !== 'pointer'; }," in app
    init = app[app.index("    init() {") :]
    init = init[: init.index("\n    },")]
    # any key but a modifier held alone is the keyboard's (a click, then the
    # Mac's ⌘[ Back, is), a pointer's press the pointer's
    assert "if (!App._MODIFIER_KEYS.includes(e.key)) App._input = 'keyboard';" in init
    keys = app[app.index("    _MODIFIER_KEYS: [") :]
    keys = keys[: keys.index("\n")]
    for key in ("'Shift'", "'Control'", "'Alt'", "'Meta'", "'AltGraph'", "'CapsLock'"):
        assert key in keys, key
    assert "'[" not in keys and "'Tab'" not in keys
    assert (
        "document.addEventListener('pointerdown', () => { App._input = 'pointer'; }, true);"
        in init
    )
    for name, fn in (("settings.js", "_clearSaveBar"), ("reports.js", "_gridReveal")):
        src = _src(name)
        body = src[src.index(f".{fn} = function (e) {{") :]
        body = body[: body.index("\n};")]
        assert "App.keyboardFocus()" in body and ":focus-visible" not in body, name


def test_a_table_wider_than_its_container_scrolls_and_tab_brings_a_stop_in_whole():
    """A plain table container scrolls a table wider than itself sideways
    (it clipped it) and clips up and down as it did; a stop the keyboard
    moves to in one is brought in whole, ring and halo with it (Chromium
    leaves one partly in view where it is), a pointer's focus left alone;
    the P&L grid has its own."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    container = _rule(css, ".table-container {")
    assert "overflow-x: auto;" in container and "overflow-y: hidden;" in container
    assert "overflow: hidden" not in container
    app = _src("app.js")
    reveal = app[app.index("    revealInTable(e) {") :]
    reveal = reveal[: reveal.index("\n    },")]
    for line in (
        "const a = e.target, c = a && a.closest && a.closest('.table-container');",
        "if (!c || a === c || c.id === 'grid-scroll' || !App.keyboardFocus()) return;",
        "if (c.scrollWidth > c.clientWidth) a.scrollIntoView({ block: 'nearest', inline: 'nearest' });",
    ):
        assert line in reveal, line
    init = app[app.index("    init() {") :]
    init = init[: init.index("\n    },")]
    assert "App.revealInTable(e);" in init


def test_a_scrolling_table_container_has_the_keyboards_room_too():
    """A line-item table's scrolling container has 8px below its table and
    at its right end, as the keyboard's halo needs, and none at its left,
    where the pay run's Employee column is held."""
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    scroll = _rule(css, ".table-container--scroll {")
    for line in ("overflow-x: auto;", "padding-right: 8px;", "padding-bottom: 8px;"):
        assert line in scroll, line
    assert "padding-left" not in scroll


def test_a_wide_table_with_no_control_takes_tab_itself():
    """A table container wider than itself with no control in it takes
    Tab itself, a named region the arrow keys scroll; one that fits or
    holds a control is left (or put back) as it was; the P&L grid is a
    region of its own. Marked whenever the page or a dialog changes and
    when the window does."""
    app = _src("app.js")
    mark = app[app.index("    markScrollRegions() {") :]
    mark = mark[: mark.index("\n    },")]
    for line in (
        "const wide = c.scrollWidth > c.clientWidth;",
        "const empty = !c.querySelector('a[href], button, input, select, textarea, summary, [tabindex]');",
        "c.setAttribute('tabindex', '0');",
        "c.setAttribute('role', 'region');",
        "c.setAttribute('aria-label', App._regionName(c));",
        "['data-scroll-region', 'tabindex', 'role', 'aria-label'].forEach(a => c.removeAttribute(a));",
        "if (c.id === 'grid-scroll') continue;",
    ):
        assert line in mark, line
    name = app[app.index("    _regionName(c) {") :]
    name = name[: name.index("\n    },")]
    assert "c.querySelector('caption')" in name and "'modal-title'" in name
    init = app[app.index("    init() {") :]
    init = init[: init.index("\n    },")]
    assert (
        "requestAnimationFrame(() => { marking = false; App.markScrollRegions(); });"
        in init
    )
    assert (
        "new MutationObserver(mark).observe(document.body, { childList: true, subtree: true });"
        in init
    )
    assert "window.addEventListener('resize', mark);" in init


# ── The gate's third round (macOS) ──────────────────────────────────────────


def test_focus_the_app_moves_for_the_keyboard_says_so_outright():
    """NEW-43, round 3: WebKit counts focus moved in script as the
    keyboard's — and draws the ring — only when the focus before it was the
    keyboard's, so in a dialog opened with a click Tab moved focus with no
    ring. focusFor says it outright where the keyboard moved it."""
    utils = _src("utils.js")
    helper = utils[utils.index("function focusFor(el, keyboard) {") :]
    helper = helper[: helper.index("\n}\n")]
    assert "el.focus(keyboard ? { focusVisible: true } : undefined)" in helper
    tab = utils[utils.index("function _tabTo(el) {") :]
    tab = tab[: tab.index("\n}\n")]
    assert "if (!focusFor(el, true)) return;" in tab and "el.focus()" not in tab
    assert "focusFor(target, _byKeyboard());" in utils  # a dialog's first control
    assert "focusFor(opener, _byKeyboard());" in utils  # the opener, on close
    reports = _src("reports.js")
    for line in (
        "focusFor(el, _byKeyboard());  // the ring by keyboard",  # Back's row
        "if (takeFocus) focusFor(el, _byKeyboard());",  # a page's spotlit row
        "focusFor(prev, _byKeyboard());",  # Next at the last account
        "focusFor(next, _byKeyboard());",
        "focusFor(panel.querySelector('input'), _byKeyboard());",  # the chooser
        "focusFor(btn, _byKeyboard());",
    ):
        assert line in reports, line
    app = _src("app.js")
    assert "return focusFor(first, true);" in app  # the search's hits
    assert "focusFor(items[i + 1], true);" in app


def test_the_search_shortcut_is_inert_over_a_dialog():
    """NEW-45: ⌘K over an open dialog moved focus to the search box under
    its overlay, and the hits opened behind the dialog."""
    app = _src("app.js")
    block = app[app.index("macOS gate NEW-45") :]
    block = block[: block.index("search.focus()")]
    assert "if (overlay && !overlay.classList.contains('hidden'))" in block
    assert "return; }" in block
