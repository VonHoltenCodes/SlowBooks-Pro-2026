"""Hidden menu items that stay hidden (#457), in playwright's Chromium on the
bakery's books, by the reporter's steps: in Settings → Sidebar a ticked box
shows the item and an unticked one hides it; hide four, leave Settings, come
back, hide two more, and all six are hidden; tick one again and only it
returns. The list used to drop every item already hidden (so it could not be
shown again) and the next save wrote only what was on the page. Skipped where
playwright or its Chromium is not installed."""

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at as _open_sized  # noqa: E402
from tests.test_browser_report_views import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

FIRST = ["reseller-permits", "estimates", "purchase-orders", "hr-deductions"]
MORE = ["vendor-credits", "batch-payments"]

BOXES = """() => Object.fromEntries([...document.querySelectorAll('#hidden-menu-choices input[data-show-menu]')]
    .map(b => [b.dataset.showMenu, b.checked]))"""
SIDEBAR_HIDDEN = """(pages) => pages.filter(p => {
    const a = document.querySelector(`#sidebar .nav-link[data-page="${p}"]`);
    return a && a.closest('li').hidden; })"""


def _save(page, handled):
    page.click("#settings-save-btn")
    page.wait_for_function(
        "() => !document.getElementById('settings-save-btn').disabled"
    )
    settle(page, handled)


def _untick(page, keys):
    for k in keys:
        page.uncheck(f'#hidden-menu-choices input[data-show-menu="{k}"]')


def _back_to_settings(page, handled):
    _visit(page, handled, "#/customers")
    _visit(page, handled, "#/settings")
    page.wait_for_selector("#hidden-menu-choices input[data-show-menu]")


def test_hide_some_leave_return_hide_more_then_show_one(browser, company, books):
    every = FIRST + MORE
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.wait_for_selector("#hidden-menu-choices input[data-show-menu]")
        boxes = page.evaluate(BOXES)
        # everything ticked (shown) to start with
        assert all(boxes[k] for k in every)
        _untick(page, FIRST)
        _save(page, handled)
        assert sorted(page.evaluate(SIDEBAR_HIDDEN, every)) == sorted(FIRST)

        # leave Settings and come back: the hidden four are still listed, unticked
        _back_to_settings(page, handled)
        boxes = page.evaluate(BOXES)
        for k in FIRST:
            assert k in boxes and boxes[k] is False, k
        for k in MORE:
            assert boxes[k] is True, k

        # two more: all six hidden, the first four stay hidden
        _untick(page, MORE)
        _save(page, handled)
        assert sorted(page.evaluate(SIDEBAR_HIDDEN, every)) == sorted(every)
        stored = json.loads(company.get("/api/settings").json()["hidden_menu"])
        assert sorted(stored) == sorted(every)

        # back again, tick one: only it returns
        _back_to_settings(page, handled)
        page.check('#hidden-menu-choices input[data-show-menu="estimates"]')
        _save(page, handled)
        rest = [k for k in every if k != "estimates"]
        assert sorted(page.evaluate(SIDEBAR_HIDDEN, every)) == sorted(rest)
        stored = json.loads(company.get("/api/settings").json()["hidden_menu"])
        assert sorted(stored) == sorted(rest)
    finally:
        page.close()
        company.put("/api/settings", json={"hidden_menu": "[]"})


def test_a_stored_key_the_page_does_not_offer_is_kept(browser, company, books):
    """A nonprofit page hidden while the company was a nonprofit is not on
    offer on a business company; a save there keeps its choice."""
    company.put(
        "/api/settings", json={"hidden_menu": json.dumps(["in-kind-gifts", "jobs"])}
    )
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.wait_for_selector("#hidden-menu-choices input[data-show-menu]")
        boxes = page.evaluate(BOXES)
        assert "in-kind-gifts" not in boxes and boxes["jobs"] is False
        page.check('#hidden-menu-choices input[data-show-menu="jobs"]')
        _save(page, handled)
        stored = json.loads(company.get("/api/settings").json()["hidden_menu"])
        assert stored == ["in-kind-gifts"]
        assert not page.evaluate(SIDEBAR_HIDDEN, ["jobs"])
    finally:
        page.close()
        company.put("/api/settings", json={"hidden_menu": "[]"})


def test_a_nonprofit_keeps_an_unticked_nonprofit_item_hidden_after_reload(
    browser, company, books
):
    """The menu setting ran before the company's kind showed the nonprofit
    pages, so one unticked came back on every reload."""
    company.put("/api/settings", json={"company_type": "nonprofit"})
    company.put("/api/settings", json={"hidden_menu": json.dumps(["in-kind-gifts"])})
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.wait_for_selector("#hidden-menu-choices input[data-show-menu]")
        assert page.evaluate(SIDEBAR_HIDDEN, ["in-kind-gifts", "releases"]) == [
            "in-kind-gifts"
        ]
        boxes = page.evaluate(BOXES)
        assert boxes["in-kind-gifts"] is False and boxes["releases"] is True
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        settle(page, handled)
        page.wait_for_selector("#hidden-menu-choices input[data-show-menu]")
        assert page.evaluate(SIDEBAR_HIDDEN, ["in-kind-gifts", "releases"]) == [
            "in-kind-gifts"
        ]
        assert page.evaluate(BOXES)["in-kind-gifts"] is False
    finally:
        page.close()
        company.put(
            "/api/settings", json={"hidden_menu": "[]", "company_type": "business"}
        )


# The start-up steps, in either order: the menu setting, the company's kind,
# and the clerk's role (as App.initSystemInfo marks it). The Settings list and
# the sidebar come out the same, without the admin pages.
STEPS_IN_ORDER = """(order) => {
    document.querySelectorAll('#sidebar li').forEach(li => {
        li.dataset.hiddenByMenu = ''; li.dataset.hiddenRole = ''; });
    const steps = {
        menu: () => App.applyHiddenMenu({ hidden_menu: JSON.stringify(['jobs']) }),
        kind: () => App.applyTerminology(),
        role: () => {
            App.ADMIN_ONLY_PAGES.forEach(p => {
                const a = document.querySelector(`#sidebar .nav-link[data-page="${p}"]`);
                if (a) a.parentElement.dataset.hiddenRole = '1';
            });
            App.syncSidebarHidden();
        },
    };
    order.forEach(s => steps[s]());
    const div = document.createElement('div');
    div.innerHTML = SettingsPage._hiddenMenuChoices(JSON.stringify(['jobs']));
    return {
        offered: [...div.querySelectorAll('input[data-show-menu]')].map(b => b.dataset.showMenu),
        hidden: [...document.querySelectorAll('#sidebar li')].filter(li => li.hidden)
            .map(li => li.textContent.trim()),
    };
}"""


def test_the_settings_list_does_not_hang_on_the_start_up_order(browser, company, books):
    page, handled = _open_at(browser, company, "#/customers")
    try:
        a = page.evaluate(STEPS_IN_ORDER, ["menu", "role", "kind"])
        b = page.evaluate(STEPS_IN_ORDER, ["role", "kind", "menu"])
        c = page.evaluate(STEPS_IN_ORDER, ["kind", "menu", "role"])
        assert a == b == c
        admin = page.evaluate("() => App.ADMIN_ONLY_PAGES")
        assert not set(admin) & set(a["offered"])
        assert "jobs" in a["offered"] and "in-kind-gifts" not in a["offered"]
        assert "hr-time-entries" in a["offered"]
    finally:
        page.close()


SAVE_BUTTON_HIT = """() => {
    const b = document.getElementById('settings-save-btn').getBoundingClientRect();
    const x = (b.left + b.right) / 2, y = (b.top + b.bottom) / 2;
    const hit = document.elementFromPoint(x, y);
    const toasts = [...document.querySelectorAll('#toast-container .toast')].map(t => {
        const r = t.getBoundingClientRect();
        return { text: t.textContent, rect: [r.left, r.top, r.right, r.bottom],
            over: r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top }; });
    return { x, y, hit: hit ? (hit.id || hit.className) : null, button_rect: [b.left, b.top, b.right, b.bottom],
        button: !!hit && hit.closest('#settings-save-btn') !== null, toasts }; }"""


def test_a_save_while_the_saved_toast_shows_still_saves(browser, company, books):
    """After Save Settings, "Settings saved" sits at the bottom right, over the
    Save Settings button, for the toast's lifetime. A second change saved in
    that time used to be a click on the toast: nothing saved, nothing said
    (2.29.0 gate, skytech W-35). Clicks go through a toast now: in the gate's
    1280 x 800 window, with the toast up and over the button, a real click at
    the button's centre reaches the button and the change is saved."""
    page, handled = _open_sized(browser, company, "#/settings", 1280, 800)
    try:
        page.wait_for_selector("#hidden-menu-choices input[data-show-menu]")
        _untick(page, FIRST[:1])
        page.click("#settings-save-btn")
        page.wait_for_selector("#toast-container .toast", state="attached")
        page.wait_for_function(
            "() => !document.getElementById('settings-save-btn').disabled"
        )
        # the toast slides up (toast-in, 0.2 s); measure where it settles
        page.evaluate("() => document.getAnimations().forEach(a => a.finish())")
        seen = page.evaluate(SAVE_BUTTON_HIT)
        assert seen["toasts"] and seen["toasts"][0]["text"] == "Settings saved", seen
        assert seen["toasts"][0]["over"], ("the toast should sit over the button", seen)
        assert seen["button"], seen
        # the second change: the click lands where the toast is
        _untick(page, FIRST[1:2])
        page.mouse.click(seen["x"], seen["y"])
        page.wait_for_function(
            "() => !document.getElementById('settings-save-btn').disabled"
        )
        settle(page, handled)
        stored = json.loads(company.get("/api/settings").json()["hidden_menu"])
        assert sorted(stored) == sorted(FIRST[:2]), stored
        assert (
            page.evaluate(
                "() => document.querySelectorAll('#toast-container .toast').length"
            )
            >= 1
        )
    finally:
        page.close()
        company.put("/api/settings", json={"hidden_menu": "[]"})
