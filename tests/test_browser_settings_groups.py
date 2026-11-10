"""Company Settings in nine groups, one shown at a time (#720), in
playwright's Chromium on the bakery's books of tests/test_theme_contrast.py.

The list down the left is built from the sections on the page (each one's
data-group), and lists only the groups this sign-in and company type can
see. A change in one group survives a switch to another and is saved by the
one Save Settings; the group holding it shows a dot, and the bar says how
many groups have changes. Save refused by a field in a group that is not
showing opens that group on the field. The other pages' settings_focus jumps
open their section's group. Find a setting finds a field by its label in any
group. The list is a tab list for the keyboard, the group is remembered per
computer, a narrow window puts the list above the panel, and at a large text
size the list scrolls inside itself with Save in view. An API token's boxes
(each token's in the list, and the new-token form's) are no Settings fields
and never a change of Users and security (NEW-221).

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

ALL_GROUPS = [
    "company",
    "sales",
    "email",
    "payroll",
    "lists",
    "connections",
    "preferences",
    "security",
    "backups",
]

# The groups listed, the group showing, the headings of the sections shown,
# and every section's group.
STATE = """() => {
    const shown = el => !!el && el.getClientRects().length > 0;
    const tabs = [...document.querySelectorAll('#settings-groups [role="tab"]')];
    const selected = tabs.find(t => t.getAttribute('aria-selected') === 'true');
    return {
        listed: tabs.filter(t => !t.hidden).map(t => t.dataset.groupTab),
        selected: selected ? selected.dataset.groupTab : null,
        headings: [...document.querySelectorAll('.settings-section')].filter(shown)
            .map(s => (s.querySelector('h3') || {}).textContent || ''),
        groups: [...document.querySelectorAll('.settings-section')]
            .map(s => [s.id || (s.querySelector('h3') || {}).textContent || '', s.dataset.group || null]),
        known: SettingsPage.GROUPS.map(g => g.key),
        panel: document.getElementById('settings-panel').getAttribute('aria-labelledby'),
        active: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null,
        dirty: tabs.filter(t => !t.querySelector('.settings-tab-dot').hidden).map(t => t.dataset.groupTab),
        count: document.getElementById('settings-dirty-groups').textContent,
        note: document.getElementById('settings-dirty-note').textContent,
        status: document.getElementById('settings-find-status').textContent,
    };
}"""


def _state(page):
    return page.evaluate(STATE)


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _desktop(page, handled):
    """The desktop app's Settings: its Sign-in section is shown (the one
    section of Users and security a single sign-in sees; Users, API Tokens
    and Company File Encryption are shown by their loaders on Server
    Edition and on an encryptable company file)."""
    page.route(
        "**/api/system",
        lambda route: route.fulfill(
            json={"version": "2.31.8", "desktop": True, "server_mode": False}
        ),
    )
    _visit(page, handled, "#/settings")


@pytest.mark.parametrize(
    "kind, role, desktop, listed, extra",
    [
        # the administrator: Users and security holds Users, API Tokens
        # and Company File Encryption, each shown by its loader once the
        # page is drawn
        ("business", "admin", False, ALL_GROUPS, None),
        ("nonprofit", "admin", False, ALL_GROUPS, ("sales", "Giving Statement")),
        ("construction", "admin", False, ALL_GROUPS, ("lists", "Trade Packages")),
        # a bookkeeper: the administrator's sections (those three, and
        # Recent sends) are hidden, and with them the whole group in a
        # browser; on the desktop Sign-in is still shown, locked
        (
            "business",
            "bookkeeper",
            False,
            [g for g in ALL_GROUPS if g != "security"],
            None,
        ),
        ("business", "bookkeeper", True, ALL_GROUPS, None),
    ],
)
def test_the_list_is_the_groups_this_sign_in_can_see(
    browser, company, books, kind, role, desktop, listed, extra
):
    if kind != "business":
        _ok(company.put("/api/settings", json={"company_type": kind}))
    page, handled = _open_at(browser, company, "#/settings")
    try:
        if role != "admin":
            page.evaluate(f"() => App.setRole('{role}')")
            _visit(page, handled, "#/settings")
        if desktop:
            _desktop(page, handled)
        if "security" in listed:
            page.wait_for_function(
                "() => !document.getElementById('settings-tab-security').hidden"
            )
        got = _state(page)
        # every section carries one of the known groups
        assert got["known"] == ALL_GROUPS
        assert [g for _, g in got["groups"] if g not in ALL_GROUPS] == [], got["groups"]
        assert got["listed"] == listed
        # nothing stored on this computer: Company
        assert got["selected"] == "company"
        assert got["headings"] == [
            "Company Information",
            "Company Logo",
            "Closing Date",
        ]
        assert got["panel"] == "settings-tab-company"
        if role != "admin":
            assert "Recent sends" not in page.evaluate(
                """() => [...document.querySelectorAll('[data-group="email"]')]
                    .filter(SettingsPage._available).map(s => s.querySelector('h3').textContent)"""
            )
        if extra:
            group, heading = extra
            page.click(f"#settings-tab-{group}")
            assert heading in _state(page)["headings"]
        if kind == "nonprofit":
            # the words that change with the company type still come from Terms
            page.click("#settings-tab-lists")
            assert _state(page)["headings"][0] == "Funds"
    finally:
        page.close()


def test_a_group_comes_into_the_list_when_a_loader_shows_its_section(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/settings")
    try:
        # a bookkeeper in a browser: nothing in Users and security is shown
        page.evaluate("() => App.setRole('bookkeeper')")
        _visit(page, handled, "#/settings")
        assert "security" not in _state(page)["listed"]
        # what loadSignInPref does in the desktop app, after the page is drawn
        page.evaluate(
            "() => { document.getElementById('settings-sign-in').hidden = false; }"
        )
        page.wait_for_function(
            "() => !document.getElementById('settings-tab-security').hidden"
        )
        page.click("#settings-tab-security")
        assert _state(page)["headings"] == ["Sign-in"]
        # and leaves it when it is hidden again: the page goes to the first
        # group listed
        page.evaluate(
            "() => { document.getElementById('settings-sign-in').hidden = true; }"
        )
        page.wait_for_function(
            "() => document.getElementById('settings-tab-security').hidden"
        )
        assert _state(page)["selected"] == "company"
    finally:
        page.close()


def test_a_change_survives_switching_groups_and_one_save_sends_them_all(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.fill('[name="company_phone"]', "503-555-0142")
        got = _state(page)
        assert got["dirty"] == ["company"] and got["note"] == "Unsaved changes"
        assert got["count"] == "1 group has unsaved changes: Company."
        page.click("#settings-tab-sales")
        page.fill('[name="invoice_prefix"]', "HLB-")
        got = _state(page)
        assert got["selected"] == "sales"
        assert got["dirty"] == ["company", "sales"]
        assert got["count"] == (
            "2 groups have unsaved changes: Company and Sales documents."
        )
        # the screen reader hears it on the tab, too
        name = page.get_attribute("#settings-tab-company", "aria-label")
        assert name == "Company, unsaved changes"
        assert page.get_attribute("#settings-tab-email", "aria-label") is None
        # back to Company: the phone typed is still there
        page.click("#settings-tab-company")
        assert page.input_value('[name="company_phone"]') == "503-555-0142"
        page.click("#settings-save-btn")
        page.wait_for_function(
            "() => !document.getElementById('settings-dirty-note').textContent"
        )
        got = _state(page)
        assert got["dirty"] == [] and got["count"] == ""
        saved = _ok(company.get("/api/settings"))
        assert saved["company_phone"] == "503-555-0142"
        assert saved["invoice_prefix"] == "HLB-"
        # undoing a change takes its dot away again
        page.fill('[name="company_phone"]', "503-555-0000")
        assert _state(page)["dirty"] == ["company"]
        page.fill('[name="company_phone"]', "503-555-0142")
        assert _state(page)["dirty"] == []
    finally:
        page.close()


def test_save_with_a_half_typed_date_in_another_group_opens_it_on_the_field(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/settings")
    puts = []
    page.on(
        "request",
        lambda r: (
            puts.append(r.url)
            if r.method == "PUT" and r.url.endswith("/api/settings")
            else None
        ),
    )
    errors = []
    page.on(
        "console", lambda m: errors.append(m.text) if "focusable" in m.text else None
    )
    try:
        page.click("#closing-date")
        page.keyboard.type("09")  # the month only: the field is badInput
        assert page.evaluate(
            "() => document.getElementById('closing-date').validity.badInput"
        )
        page.click("#settings-tab-email")
        assert "Closing Date" not in _state(page)["headings"]
        page.click("#settings-save-btn")
        page.wait_for_function(
            "() => document.activeElement && document.activeElement.id === 'closing-date'"
        )
        got = _state(page)
        assert got["selected"] == "company" and "Closing Date" in got["headings"]
        # its message: the page's own, as before
        page.wait_for_selector(
            ".toast:has-text('Nothing was saved: the closing date is not complete.')"
        )
        assert puts == [] and errors == []
    finally:
        page.close()


@pytest.mark.parametrize(
    "target, group, heading",
    [
        ("settings-ai", "connections", "settings-h-ai-insights"),  # Analytics' gear
        ("settings-h-classes", "lists", "settings-h-classes"),  # Classes
        # an invoice's email status and the reports dialog: Recent sends,
        # shown by its loader after the page opens
        ("settings-h-email-recent-sends", "email", "settings-h-email-recent-sends"),
    ],
)
def test_each_settings_focus_jump_opens_its_group_on_the_heading(
    browser, company, books, target, group, heading
):
    page, handled = _open_at(browser, company, "#/")
    try:
        page.evaluate(
            "(t) => sessionStorage.setItem('settings_focus', t)",
            target,
        )
        _visit(page, handled, "#/settings")
        page.wait_for_function(
            "(h) => document.activeElement && document.activeElement.id === h",
            arg=heading,
        )
        got = _state(page)
        assert got["selected"] == group
        top = page.evaluate(
            """(h) => { const r = document.getElementById(h).getBoundingClientRect();
                return r.top >= 0 && r.bottom <= innerHeight; }""",
            heading,
        )
        assert top
    finally:
        page.close()


def test_find_a_setting_finds_a_field_in_a_group_not_showing(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings")
    try:
        assert _state(page)["selected"] == "company"
        page.fill("#settings-find", "grace days")
        got = _state(page)
        # Late Fees' "Grace Days", in Sales documents
        assert got["headings"] == ["Late Fees"]
        assert got["status"] == "One section matches, in Sales documents."
        assert got["panel"] == "settings-find-label"
        page.fill("#settings-find", "password")
        got = _state(page)
        assert {"Closing Date", "Email (SMTP)"} <= set(got["headings"])
        page.fill("#settings-find", "no such setting anywhere")
        assert _state(page)["headings"] == []
        assert _state(page)["status"] == "No setting matches what you typed."
        # clearing it returns to the group that was open
        page.fill("#settings-find", "")
        got = _state(page)
        assert got["selected"] == "company" and got["headings"] == [
            "Company Information",
            "Company Logo",
            "Closing Date",
        ]
        assert got["status"] == ""
        # Escape clears it too; Enter opens the first match's group
        page.fill("#settings-find", "grace days")
        page.press("#settings-find", "Escape")
        assert _state(page)["selected"] == "company"
        assert page.input_value("#settings-find") == ""
        page.fill("#settings-find", "grace days")
        page.press("#settings-find", "Enter")
        got = _state(page)
        assert got["selected"] == "sales" and got["active"] == "settings-h-late-fees"
        assert page.input_value("#settings-find") == ""
    finally:
        page.close()


def test_the_group_list_is_a_tab_list_for_the_keyboard(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings")
    try:
        roles = page.evaluate("""() => {
            const list = document.getElementById('settings-groups');
            return { list: list.getAttribute('role'), label: list.getAttribute('aria-label'),
                     panel: document.getElementById('settings-panel').getAttribute('role'),
                     stops: [...list.querySelectorAll('[role="tab"]')].filter(t => t.tabIndex === 0)
                        .map(t => t.id) };
        }""")
        assert roles == {
            "list": "tablist",
            "label": "Settings groups",
            "panel": "tabpanel",
            "stops": ["settings-tab-company"],  # one Tab stop: the open group
        }
        # Tab from Find a setting lands on the open group
        page.focus("#settings-find")
        page.keyboard.press("Tab")
        assert _state(page)["active"] == "settings-tab-company"
        # the arrows move along the list; nothing opens until Enter
        page.keyboard.press("ArrowDown")
        got = _state(page)
        assert got["active"] == "settings-tab-sales" and got["selected"] == "company"
        page.keyboard.press("ArrowUp")
        assert _state(page)["active"] == "settings-tab-company"
        page.keyboard.press("ArrowUp")  # wraps to the end
        assert _state(page)["active"] == "settings-tab-backups"
        page.keyboard.press("Home")
        assert _state(page)["active"] == "settings-tab-company"
        page.keyboard.press("End")
        assert _state(page)["active"] == "settings-tab-backups"
        page.keyboard.press("ArrowRight")
        assert _state(page)["active"] == "settings-tab-company"
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        # Enter opens Email, labels the panel by its tab and goes to its
        # first heading
        page.keyboard.press("Enter")
        got = _state(page)
        assert got["selected"] == "email"
        assert got["panel"] == "settings-tab-email"
        assert got["active"] == "settings-h-email-smtp"
        assert got["headings"][0] == "Email (SMTP)"
        # Space does the same
        page.focus("#settings-tab-preferences")
        page.keyboard.press("Space")
        got = _state(page)
        assert got["selected"] == "preferences"
        assert got["active"] == "settings-h-my-preferences"
        # a click opens the group and leaves the focus on its tab
        page.click("#settings-tab-backups")
        got = _state(page)
        assert got["selected"] == "backups" and got["active"] == "settings-tab-backups"
    finally:
        page.close()


def test_the_open_group_is_remembered_on_this_computer(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.click("#settings-tab-lists")
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.evaluate(
            "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
        )
        settle(page, handled)
        page.wait_for_selector("#settings-tab-lists[aria-selected='true']")
        assert _state(page)["headings"][0] == "Classes"
    finally:
        page.close()


def test_a_narrow_window_puts_the_list_above_the_panel(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings", width=800, height=700)
    try:
        got = page.evaluate("""() => {
            const nav = document.getElementById('settings-nav').getBoundingClientRect();
            const panel = document.getElementById('settings-panel').getBoundingClientRect();
            const tabs = [...document.querySelectorAll('#settings-groups [role="tab"]')]
                .map(t => t.getBoundingClientRect());
            return { above: nav.bottom <= panel.top, wide: panel.width >= nav.width - 1,
                     rows: new Set(tabs.map(r => Math.round(r.top))).size, count: tabs.length,
                     orientation: document.getElementById('settings-groups').getAttribute('aria-orientation') };
        }""")
        assert got["above"] and got["wide"], got
        assert got["rows"] < got["count"], got  # a row of choices, not a column
        assert got["orientation"] == "horizontal"
    finally:
        page.close()


SAVE_IN_VIEW = """() => {
    const save = document.getElementById('settings-save-btn').getBoundingClientRect();
    const nav = document.getElementById('settings-nav');
    const r = nav.getBoundingClientRect();
    return { save_bottom: save.bottom, save_top: save.top, nav_bottom: r.bottom,
             height: innerHeight, nav_scrolls: nav.scrollHeight > nav.clientHeight };
}"""


@pytest.mark.parametrize("zoom, height", [("1", 768), ("1.5", 768), ("1.5", 560)])
def test_save_stays_in_view_and_the_list_scrolls_inside_itself(
    browser, company, books, zoom, height
):
    page, handled = _open_at(browser, company, "#/settings", width=1366, height=height)
    try:
        # A larger text size as a root zoom; the shell (#app, 100vh) is held
        # to the window, as a zoom must, since 100vh grows with the zoom.
        # Everything else is the page's own.
        page.evaluate(
            """(z) => { document.documentElement.style.zoom = z;
                document.getElementById('app').style.height = `calc(100vh / ${z})`; }""",
            zoom,
        )
        page.wait_for_timeout(100)  # the list is measured to the new size
        for group in ALL_GROUPS:
            page.evaluate(f"() => SettingsPage.openGroup('{group}')")
            page.evaluate("() => { document.getElementById('content').scrollTop = 0; }")
            got = page.evaluate(SAVE_IN_VIEW)
            assert got["save_top"] >= 0 and got["save_bottom"] <= got["height"], (
                group,
                got,
            )
        # scrolled to the end of a long group, the list is still beside it,
        # whole or scrolling inside itself, and Save is in view
        page.evaluate("() => SettingsPage.openGroup('connections')")
        page.evaluate(
            "() => { const c = document.getElementById('content'); c.scrollTop = c.scrollHeight; }"
        )
        got = page.evaluate(SAVE_IN_VIEW)
        assert got["save_bottom"] <= got["height"], got
        assert got["nav_bottom"] <= got["height"], got
        top = page.evaluate(
            "() => document.getElementById('settings-nav').getBoundingClientRect().top"
        )
        assert 0 <= top < got["height"] / 2, top
        # the short window at the large size: too tall for it, the list scrolls
        assert got["nav_scrolls"] is (height < 700), got
        if got["nav_scrolls"]:
            page.evaluate(
                "() => { const n = document.getElementById('settings-nav'); n.scrollTop = n.scrollHeight; }"
            )
            last = page.evaluate(
                "() => document.getElementById('settings-tab-backups').getBoundingClientRect().bottom"
            )
            assert last <= got["height"], last
    finally:
        page.close()


def test_the_form_is_a_box_and_save_is_still_its_own_button(browser, company, books):
    """NEW-210: the form drew no box of its own (display: contents), and an
    accessibility client on the Mac found the panel empty after a switch of
    group. The form is an ordinary box now, with the Save bar after it in
    the panel; the button is the form's submit by its form attribute, so
    Enter in a field saves as before."""
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.click("#settings-tab-email")
        got = page.evaluate("""() => {
            const form = document.getElementById('settings-form'),
                  bar = document.getElementById('settings-savebar'),
                  btn = document.getElementById('settings-save-btn'),
                  panel = document.getElementById('settings-panel');
            return {
                display: getComputedStyle(form).display,
                height: form.getBoundingClientRect().height,
                barInForm: form.contains(bar),
                barLast: panel.lastElementChild === bar,
                owner: btn.form && btn.form.id,
                fields: [...panel.querySelectorAll('.settings-section')]
                    .filter(s => s.getClientRects().length).length,
            };
        }""")
        assert got["display"] not in ("contents", "none"), got
        assert got["height"] > 0 and got["fields"] > 0, got
        assert got["barInForm"] is False and got["barLast"] is True, got
        assert got["owner"] == "settings-form", got
        # Enter in a field of the form still saves through the one button
        page.click("#settings-tab-company")
        box = page.locator("#settings-form [name=company_phone]")
        box.fill("555-0199")
        assert page.inner_text("#settings-dirty-note") == "Unsaved changes"
        box.press("Enter")
        page.wait_for_function(
            "() => !document.getElementById('settings-dirty-note').textContent"
        )
        assert _ok(company.get("/api/settings"))["company_phone"] == "555-0199"
    finally:
        page.close()


# NEW-221: the API Tokens boxes save through the token's own controls (each
# box in the list at once, the form's with Create Token), never Save Settings.
TOKEN_DIRTY = """() => ({
    dirty: SettingsPage.isDirty(),
    note: document.getElementById('settings-dirty-note').textContent,
    count: document.getElementById('settings-dirty-groups').textContent,
    groups: [...document.querySelectorAll('#settings-groups [role="tab"]')]
        .filter(t => !t.querySelector('.settings-tab-dot').hidden)
        .map(t => t.dataset.groupTab),
    keys: Object.keys(SettingsPage._formData()).filter(k => k.startsWith('token')),
})"""

TOKEN_CLEAN = {"dirty": False, "note": "", "count": "", "groups": [], "keys": []}


def _token_with_every_box(company):
    r = company.post(
        "/api/tokens",
        json={
            "label": "nightly-backup",
            "role": "bookkeeper",
            "allow_backups": True,
            "allow_payroll": True,
            "allow_imports": True,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_ticked_token_box_is_no_unsaved_change(browser, company, books):
    tid = _token_with_every_box(company)
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=800)
    try:
        # the list draws after the page has taken its clean copy
        page.wait_for_selector(
            '[aria-label="nightly-backup can make backups"]', state="attached"
        )
        settle(page, handled)
        for name in ("can make backups", "can run payroll", "can import"):
            assert page.is_checked(f'[aria-label="nightly-backup {name}"]'), name
        assert page.evaluate(TOKEN_DIRTY) == TOKEN_CLEAN
        # leaving asks nothing
        asked = []
        page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
        # an edit elsewhere is one group's, and its undo leaves none
        page.click("#settings-tab-company")
        name = page.input_value('[name="company_name"]')
        page.fill('[name="company_name"]', name + " & Cafe")
        got = page.evaluate(TOKEN_DIRTY)
        assert (got["dirty"], got["groups"], got["keys"]) == (True, ["company"], [])
        assert "Users and security" not in got["count"]
        page.fill('[name="company_name"]', name)
        assert page.evaluate(TOKEN_DIRTY) == TOKEN_CLEAN
        # the new-token form's boxes, ticked before Create Token
        page.click("#settings-tab-security")
        page.check("#token-new-backups")
        page.select_option("#token-new-role", "bookkeeper")
        page.check("#token-new-payroll")
        page.check("#token-new-imports")
        page.dispatch_event("#token-new-imports", "input")
        assert page.evaluate(TOKEN_DIRTY) == TOKEN_CLEAN
        # Save Settings, with an edit, sends no token key
        page.click("#settings-tab-company")
        page.fill('[name="company_name"]', name + " & Cafe")
        sent = []
        page.on(
            "request",
            lambda r: (
                sent.append(r.post_data)
                if r.method == "PUT" and r.url.endswith("/api/settings")
                else None
            ),
        )
        page.click("#settings-save-btn")
        page.wait_for_function(
            "() => !document.getElementById('settings-dirty-note').textContent"
        )
        assert len(sent) == 1
        body = json.loads(sent[0])
        assert body["company_name"] == name + " & Cafe"
        assert [k for k in body if k.startswith("token")] == []
        # the token itself is as it was
        (row,) = [t for t in company.get("/api/tokens").json() if t["id"] == tid]
        assert (row["allow_backups"], row["allow_payroll"], row["allow_imports"]) == (
            True,
            True,
            True,
        )
        page.evaluate("() => App.navigate('#/')")
        settle(page, handled)
        assert asked == []
    finally:
        page.close()
