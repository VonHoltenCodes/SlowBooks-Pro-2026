"""Settings' panel after many switches of group (#725), in playwright's
Chromium and WebKit on the bakery's books of tests/test_theme_contrast.py.

On the Mac (WKWebView) a client walking the accessibility tree found
settings-panel empty after some switches, the fields still on screen: WebKit
missed a children-changed notice for a section shown or hidden inside the
form. When the set of sections showing changes (a switch of group, a loader
showing or hiding its section, a loader drawing into a section showing),
the panel's renderer is dropped and restored in one turn, so WebKit builds
that part of the tree again (SettingsPage._rebuildPanelTree). It runs in
Apple's WebKit only; here it is also forced on in Chromium, to show what it
must never do: lose a typed value, move focus, move the scroll, or leave a
field or Save Settings out of the panel.

The Mac's own accessibility tree is not reachable from here: the gate's
probe on the Mac is that test. Skipped where playwright is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright import sync_api  # noqa: E402

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import company_fixture  # noqa: E402,F401
from tests.test_browser_settings_groups import ALL_GROUPS  # noqa: E402


@pytest.fixture(scope="module", name="engines")
def engines_fixture():
    with sync_api.sync_playwright() as p:
        got = {}
        for name in ("chromium", "webkit"):
            try:
                got[name] = getattr(p, name).launch()
            except Exception:  # the package without that browser
                pass
        if not got:
            pytest.skip("no playwright browser is installed")
        yield got
        for b in got.values():
            b.close()


def _engine(engines, name):
    if name not in engines:
        pytest.skip(f"playwright's {name} is not installed")
    return engines[name]


# What the panel holds now: each shown section's heading, whether each is
# inside the panel and drawn, Save Settings drawn and in the panel, and the
# rebuilds made so far.
PANEL = """() => {
    const panel = document.getElementById('settings-panel');
    const drawn = el => !!el && el.getClientRects().length > 0;
    const shown = [...document.querySelectorAll('.settings-section')]
        .filter(s => !s.classList.contains('settings-off') && SettingsPage._available(s));
    const save = document.getElementById('settings-save-btn');
    return {
        selected: SettingsPage._shown,
        sections: shown.map(s => (s.querySelector('h3') || {}).textContent || s.id),
        inPanel: shown.every(s => panel.contains(s)),
        drawn: shown.every(drawn),
        fields: shown.reduce((n, s) => n + s.querySelectorAll('input, select, textarea, button').length, 0),
        save: drawn(save) && panel.contains(save),
        display: panel.style.display,
        active: document.activeElement ? (document.activeElement.name || document.activeElement.id
            || document.activeElement.tagName) : null,
        rebuilds: SettingsPage._treeRebuilds,
    };
}"""

# A text field of the group showing that takes typing (not a date, not
# locked), by its name.
A_FIELD = """() => {
    const ok = el => el.type === 'text' && el.getClientRects().length > 0 && !el.disabled
        && !el.readOnly && !el.closest('[data-cbx], .cbx');
    const f = [...document.querySelectorAll('#settings-form .settings-section:not(.settings-off) input[name]')]
        .find(ok);
    return f ? [f.name, f.maxLength > 0 ? f.maxLength : 40] : null;
}"""


@pytest.mark.parametrize(
    "engine, force",
    [("chromium", True), ("webkit", None)],
    ids=["chromium-forced", "webkit"],
)
def test_three_sweeps_of_every_group_keep_the_panel_whole(
    engines, company, books, engine, force
):
    page, handled = _open_at(_engine(engines, engine), company, "#/settings")
    try:
        if force is not None:
            page.evaluate(
                f"() => {{ SettingsPage._AX_REBUILD = {str(force).lower()}; }}"
            )
        assert page.evaluate("() => SettingsPage._needsTreeRebuild()") is True
        page.wait_for_function(
            "() => { const t = document.getElementById('settings-tab-security'); return !!t && !t.hidden; }"
        )
        typed = {}
        start = page.evaluate(PANEL)["rebuilds"]
        for sweep in range(3):
            for group in ALL_GROUPS:
                page.click(f"#settings-tab-{group}")
                got = page.evaluate(PANEL)
                assert got["selected"] == group, (sweep, got)
                assert got["sections"] and got["inPanel"] and got["drawn"], (sweep, got)
                assert got["fields"] > 0 and got["save"], (sweep, got)
                # dropped and restored, never left hidden
                assert got["display"] == "", (sweep, got)
                # the click's focus is where the click put it
                assert got["active"] == f"settings-tab-{group}", got
                if sweep == 0:
                    field = page.evaluate(A_FIELD)
                    if field:
                        name, room = field
                        value = f"725-{group}"[:room]
                        page.fill(f'#settings-form [name="{name}"]', value)
                        typed[name] = value
                for name, value in typed.items():
                    assert (
                        page.input_value(f'#settings-form [name="{name}"]') == value
                    ), (
                        sweep,
                        group,
                        name,
                    )
        # a rebuild at every switch (the set showing changed each time)
        assert page.evaluate(PANEL)["rebuilds"] - start >= 3 * len(ALL_GROUPS) - 1
        assert len(typed) >= 3, typed
    finally:
        page.close()


def test_chromium_is_left_as_it_was(engines, company, books):
    """WebView2 and the browsers on Chromium never lost the tree: no
    rebuild runs there."""
    page, handled = _open_at(_engine(engines, "chromium"), company, "#/settings")
    try:
        assert page.evaluate("() => SettingsPage._needsTreeRebuild()") is False
        for group in ALL_GROUPS:
            page.click(f"#settings-tab-{group}")
        assert page.evaluate(PANEL)["rebuilds"] == 0
    finally:
        page.close()


def test_a_loader_drawing_into_a_shown_section_keeps_the_scroll(
    engines, company, books
):
    page, handled = _open_at(_engine(engines, "chromium"), company, "#/settings")
    try:
        page.evaluate("() => { SettingsPage._AX_REBUILD = true; }")
        page.click("#settings-tab-connections")
        top = page.evaluate(
            """() => { const c = document.getElementById('content');
            c.scrollTop = Math.floor((c.scrollHeight - c.clientHeight) / 2); return c.scrollTop; }"""
        )
        assert top > 0
        before = page.evaluate(PANEL)["rebuilds"]
        # what a loader does once its section is drawn into
        page.evaluate(
            "() => SettingsPage._showSection(document.getElementById('settings-ai'), true)"
        )
        assert page.evaluate(PANEL)["rebuilds"] == before + 1
        assert (
            page.evaluate("() => document.getElementById('content').scrollTop") == top
        )
        # a section of a group not showing changes nothing showing: no rebuild
        page.evaluate(
            "() => SettingsPage._showSection(document.getElementById('settings-backups'), true)"
        )
        assert page.evaluate(PANEL)["rebuilds"] == before + 1
    finally:
        page.close()


def test_focus_in_the_panel_puts_the_rebuild_off_until_it_leaves(
    engines, company, books
):
    page, handled = _open_at(_engine(engines, "chromium"), company, "#/settings")
    try:
        page.evaluate("() => { SettingsPage._AX_REBUILD = true; }")
        page.click("#settings-tab-company")
        box = page.locator('#settings-form [name="company_phone"]')
        box.fill("503-555-0725")
        before = page.evaluate(PANEL)["rebuilds"]
        page.evaluate(
            "() => SettingsPage._showSection(document.getElementById('settings-closing-date'), true)"
        )
        got = page.evaluate(PANEL)
        # left alone: the field keeps focus, its value and its caret
        assert got["rebuilds"] == before and got["active"] == "company_phone", got
        assert page.evaluate("() => SettingsPage._treeStale") is True
        assert page.evaluate("() => document.activeElement.selectionStart") == len(
            "503-555-0725"
        )
        # focus leaves the panel (to the list): the rebuild is made then
        page.focus("#settings-tab-company")
        page.wait_for_function("() => !SettingsPage._treeStale")
        got = page.evaluate(PANEL)
        assert (
            got["rebuilds"] == before + 1 and got["active"] == "settings-tab-company"
        ), got
        assert box.input_value() == "503-555-0725"
    finally:
        page.close()
