"""Text size (#717) in a real browser, on the bakery's books, in the
1366 x 768 window of a common laptop, at each of the four sizes.

The size is the page's CSS zoom (text_size.js, style.css's TEXT SIZE
block). Under zoom a viewport unit grows with everything else, so a frame
sized by the window ran off it: at 125-150% the sidebar's last entries, the
bottom bar and a tall dialog's buttons were out of the window. Here, at
every size:

- the sidebar scrolls inside itself, and its last entry can be scrolled
  into view inside the window;
- the bottom bar is inside the window;
- a tall dialog (the register's + Entry with a split open, an invoice) has
  its Save in view without scrolling, and its title bar too;
- the page itself never scrolls sideways.

And the size is on the page before the first paint (it is set from <head>),
on the sign-in screen as on the app; the Settings control sets it and keeps
it on this computer; the desktop app's keys step the same setting.
Skipped, as one module, where playwright or its Chromium is not installed.
"""

import os
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    ORIGIN,
    SERVED,
    _served_by,
    browser_fixture,
    company_fixture,
    new_page,
    settle,
)

SIZES = ["100", "115", "125", "150"]
WIDTH, HEIGHT = 1366, 768
SHOTS = os.environ.get("TEXT_SIZE_SHOTS")


def _signed_in_as(client, handled, served, user):
    """_served_by, with the sign-in status saying a multi-user install and
    `user` signed in: the top bar shows the name (app.js, #user-chip)."""
    base = _served_by(client, handled, served)

    def serve(route):
        if urlsplit(route.request.url).path != "/api/auth/status":
            return base(route)
        status = client.get("/api/auth/status").json()
        status["multi_user"] = True
        status["user"] = dict(status.get("user") or {}, **user)
        handled.append("/api/auth/status")
        return route.fulfill(json=status)

    return serve


def _open(
    browser, client, page_hash, size, width=WIDTH, height=HEIGHT, user=None, theme=None
):
    """A fresh window on this computer with the text size stored, at the
    address: the size is read as the page loads."""
    handled = []
    page = new_page(
        browser, viewport={"width": width, "height": height}, bypass_csp=True
    )
    page.add_init_script(
        "try { if (!sessionStorage.getItem('sized')) {"
        f" localStorage.setItem('slowbooks.textsize', '{size}');"
        + (f" localStorage.setItem('slowbooks-theme', '{theme}');" if theme else "")
        + " sessionStorage.setItem('sized', '1'); } } catch (e) {}"
    )
    if user:
        page.route("**/*", _signed_in_as(client, handled, SERVED, user))
    else:
        page.route("**/*", _served_by(client, handled, SERVED))
    page.goto(f"{ORIGIN}/{page_hash}")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )
    settle(page, handled)
    return page, handled


def _go(page, handled, route):
    page.evaluate("async (h) => { await App.navigate(h); }", route)
    settle(page, handled)


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name + ".png"))


FRAME = """() => {
    const box = el => { const r = el.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; };
    const sidebar = document.getElementById('sidebar');
    const status = document.getElementById('statusbar');
    const links = [...sidebar.querySelectorAll('a, button')].filter(el => el.offsetParent !== null);
    const last = links[links.length - 1];
    last.scrollIntoView({ block: 'end' });
    const out = {
        zoom: getComputedStyle(document.documentElement).zoom,
        sidebar: box(sidebar),
        sidebarScrolls: sidebar.scrollHeight > sidebar.clientHeight
            ? getComputedStyle(sidebar).overflowY : 'fits',
        last: box(last),
        lastText: last.textContent.trim(),
        status: box(status),
        statusText: status.innerText.trim(),
        pageScrollsSideways: document.documentElement.scrollWidth > innerWidth + 1
            || document.body.scrollWidth > document.body.clientWidth + 1,
        window: { width: innerWidth, height: innerHeight },
    };
    sidebar.scrollTop = 0;
    return out;
}"""

DIALOG = """() => {
    const box = el => { const r = el.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; };
    const modal = document.getElementById('modal');
    // the form's own submit: Save on the invoice, Post Entry on the register's
    const save = [...modal.querySelectorAll('#modal-body .form-actions button[type="submit"]')]
        .filter(b => b.offsetParent !== null)[0];
    return {
        modal: box(modal),
        scrolls: modal.scrollHeight > modal.clientHeight,
        header: box(modal.querySelector('.modal-header')),
        save: save ? box(save) : null,
        saveText: save ? save.textContent.trim() : null,
        window: { width: innerWidth, height: innerHeight },
    };
}"""


def _inside(inner, outer, slack=0.5):
    return (
        inner["top"] >= outer["top"] - slack
        and inner["bottom"] <= outer["bottom"] + slack
        and inner["left"] >= outer["left"] - slack
        and inner["right"] <= outer["right"] + slack
    )


def _window(m):
    return {
        "top": 0,
        "left": 0,
        "bottom": m["window"]["height"],
        "right": m["window"]["width"],
    }


def _check_frame(m, where):
    win = _window(m)
    assert _inside(m["status"], win), (where, m["status"])
    assert m["status"]["bottom"] == pytest.approx(win["bottom"], abs=1), where
    assert m["statusText"], where
    assert m["sidebar"]["bottom"] <= m["status"]["top"] + 0.5, where
    assert m["sidebarScrolls"] in ("auto", "scroll", "fits"), where
    assert _inside(m["last"], m["sidebar"]), (where, m["lastText"], m["last"])
    assert not m["pageScrollsSideways"], where


PAGES = [
    ("dashboard", "#/", "#page-content .dashboard, #page-content"),
    ("invoices", "#/invoices", "#page-content table"),
    ("settings", "#/settings", "#settings-tab-preferences"),
]


@pytest.mark.parametrize("size", SIZES)
def test_the_frame_fits_the_window_at_each_size(browser, company, books, size):
    page, handled = _open(browser, company, "#/", size)
    try:
        expected = "1" if size == "100" else str(int(size) / 100)
        for name, route, ready in PAGES + [
            ("register", f"#/banking/{books['checking']}", "#reg-start")
        ]:
            _go(page, handled, route)
            page.wait_for_selector(ready)
            m = page.evaluate(FRAME)
            assert m["zoom"] == expected, (name, m["zoom"])
            _check_frame(m, (size, name))
            _shot(page, f"{size}-{name}")
    finally:
        page.close()


def _check_dialog(d, where):
    win = _window(d)
    assert _inside(d["modal"], win), (where, d["modal"])
    assert d["save"], where
    # Save in view, inside the dialog, without scrolling to it
    assert _inside(d["save"], d["modal"]), (where, d["save"], d["modal"])
    assert _inside(d["header"], d["modal"]), where
    assert d["header"]["top"] >= d["modal"]["top"] - 0.5, where


@pytest.mark.parametrize("size", SIZES)
def test_a_tall_dialogs_save_stays_in_view_at_each_size(browser, company, books, size):
    page, handled = _open(browser, company, f"#/banking/{books['checking']}", size)
    try:
        page.wait_for_selector("#reg-start")
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        page.click("#entry-split-toggle")
        page.wait_for_selector("#entry-split-rows tr")
        # three more split lines make it taller than the window at every size
        for _ in range(3):
            page.evaluate("BankingPage.addSplitRow()")
        page.evaluate("() => new Promise((done) => setTimeout(done, 0))")
        d = page.evaluate(DIALOG)
        _check_dialog(d, (size, "entry"))
        _shot(page, f"{size}-entry")
        # scrolled to the top and to the bottom, the title and Save stay
        for top in (0, 100000):
            page.evaluate(f"document.getElementById('modal').scrollTop = {top}")
            _check_dialog(page.evaluate(DIALOG), (size, "entry", top))
        page.evaluate("closeModal()")

        _go(page, handled, "#/invoices")
        page.evaluate("InvoicesPage.showForm()")
        page.wait_for_selector("#invoice-form")
        page.evaluate("() => new Promise((done) => setTimeout(done, 0))")
        d = page.evaluate(DIALOG)
        _check_dialog(d, (size, "invoice"))
        _shot(page, f"{size}-invoice")
    finally:
        page.close()


def test_the_type_ahead_list_opens_under_its_box_at_150(browser, company, books):
    """The type-ahead's list is placed from the box's measured position: at
    150% that is in the window's pixels, and set unconverted it landed half
    as far again down and across, away from its box."""
    page, handled = _open(browser, company, "#/invoices", "150")
    try:
        page.evaluate("InvoicesPage.showForm()")
        page.wait_for_selector("#invoice-form .cbx-input")
        box = page.locator("#invoice-form .cbx-input").first
        box.click()
        page.wait_for_selector(".cbx-popup:not([hidden])")
        got = page.evaluate(
            """() => {
            const b = document.querySelector('#invoice-form .cbx-input').getBoundingClientRect();
            const p = document.querySelector('.cbx-popup').getBoundingClientRect();
            return { box: [b.left, b.bottom, b.top], pop: [p.left, p.top, p.bottom] }; }"""
        )
        (bl, bb, bt), (pl, pt, pb) = got["box"], got["pop"]
        assert pl == pytest.approx(bl, abs=2), got
        # under the box, or over it near the bottom of the window
        assert pt == pytest.approx(bb, abs=4) or pb == pytest.approx(bt, abs=4), got
    finally:
        page.close()


def test_the_settings_control_sets_the_size_and_this_computer_keeps_it(
    browser, company, books
):
    page, handled = _open(browser, company, "#/settings", "100")
    try:
        page.click("#settings-tab-preferences")  # its group (#720)
        page.wait_for_selector("#pref-text-size")
        assert page.input_value("#pref-text-size") == "100"
        assert (
            page.evaluate("document.documentElement.hasAttribute('data-text-size')")
            is False
        )
        page.select_option("#pref-text-size", "150")
        assert page.evaluate("getComputedStyle(document.documentElement).zoom") == "1.5"
        assert page.evaluate("localStorage.getItem('slowbooks.textsize')") == "150"
        # a fresh load on this computer comes back at 150, the control with it
        page.reload()
        page.wait_for_selector("#pref-text-size")
        assert page.evaluate("getComputedStyle(document.documentElement).zoom") == "1.5"
        assert page.input_value("#pref-text-size") == "150"
        _check_frame(page.evaluate(FRAME), ("settings", "reloaded"))
        # back to 100: nothing kept, nothing zoomed
        page.select_option("#pref-text-size", "100")
        assert page.evaluate("getComputedStyle(document.documentElement).zoom") == "1"
        assert page.evaluate("localStorage.getItem('slowbooks.textsize')") is None
    finally:
        page.close()


def test_the_desktop_keys_and_wheel_step_the_same_setting(browser, company, books):
    """In the desktop window (pywebview's bridge present) Ctrl + / Ctrl − /
    Ctrl 0 and Ctrl + wheel step through the four sizes and write the one
    setting the Settings control shows."""
    page, handled = _open(browser, company, "#/settings", "100")
    try:
        page.evaluate("() => { window.pywebview = { api: {} }; }")
        page.click("#settings-tab-preferences")  # its group (#720)
        page.wait_for_selector("#pref-text-size")
        page.click("#page-content h2, #page-content .page-header")
        seen = []
        for key in ["Control+Equal", "Control+Equal", "Control+Equal", "Control+Equal"]:
            page.keyboard.press(key)
            seen.append(page.evaluate("localStorage.getItem('slowbooks.textsize')"))
        assert seen == ["115", "125", "150", "150"]  # it stops at 150
        assert page.input_value("#pref-text-size") == "150"
        page.keyboard.press("Control+Minus")
        assert (
            page.evaluate("getComputedStyle(document.documentElement).zoom") == "1.25"
        )
        page.keyboard.down("Control")
        page.mouse.move(700, 400)
        page.mouse.wheel(0, 100)  # a notch down
        page.keyboard.up("Control")
        page.wait_for_function(
            "() => localStorage.getItem('slowbooks.textsize') === '115'"
        )
        assert page.input_value("#pref-text-size") == "115"
        page.keyboard.press("Control+Digit0")
        assert page.evaluate("localStorage.getItem('slowbooks.textsize')") is None
        assert page.input_value("#pref-text-size") == "100"
    finally:
        page.close()


FIRST_FRAME = """
    localStorage.setItem('slowbooks.textsize', '125');
    requestAnimationFrame(() => {
        window.__firstFrame = getComputedStyle(document.documentElement).zoom;
    });
"""


def test_the_size_is_on_the_sign_in_screen_from_the_first_frame(browser):
    """Set from <head>: the first frame the page draws is already at the
    size, so nothing jumps, and the sign-in screen has it too."""
    from tests.test_browser_ui import _open_raw  # the stubbed API

    page = _open_raw(browser, WIDTH, HEIGHT)
    try:
        page.add_init_script(FIRST_FRAME)
        page.route(
            "**/api/auth/status",
            lambda r: r.fulfill(
                json={
                    "authenticated": False,
                    "setup_needed": False,
                    "multi_user": False,
                }
            ),
        )
        page.goto(f"{ORIGIN}/")
        page.wait_for_selector("#auth-overlay #auth-form")
        assert page.evaluate("window.__firstFrame") == "1.25"
        got = page.evaluate("""() => {
            const f = document.getElementById('auth-submit').getBoundingClientRect();
            return { zoom: getComputedStyle(document.documentElement).zoom,
                     submit: [f.top, f.bottom], h: innerHeight }; }""")
        assert got["zoom"] == "1.25"
        # the sign-in button is in the window
        assert 0 <= got["submit"][0] and got["submit"][1] <= got["h"], got
    finally:
        page.close()


# --- the smallest window (W-126) ------------------------------------------
#
# The desktop window is never smaller than 900 x 600 (desktop_launcher.py
# MIN_WINDOW_SIZE), and a browser can be narrower still. At 125% Sign Out
# ran past the window's right edge, at 150% About and Contact support too,
# on a Mac from 115% with a long sign-in name, and the page scrolled
# sideways; at 100% a 700px window did the same. And at 150% Company
# Settings showed its list and an empty strip of its panel. The bar is
# measured and folds to fit (topbar_fit.js): nothing in it is past the
# window's edge, and whatever it folds away More lists.

LONG_NAME = {
    "display_name": "Second Administrator of Harbor Light",
    "username": "second.admin",
    "role": "admin",
}
LONG_COMPANY = "Riverbend Construction Services LLC"

BAR = """() => {
    const bar = document.getElementById('topbar');
    const shown = el => !!el && el.offsetWidth > 0 && el.offsetHeight > 0;
    const box = el => { const r = el.getBoundingClientRect();
        return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
    const controls = [...bar.querySelectorAll('a, button, input')]
        .filter(el => shown(el) && !el.closest('#topbar-more-menu'))
        .map(el => ({ id: el.id || el.textContent.trim(), box: box(el) }));
    const chip = document.getElementById('user-chip');
    return {
        fold: bar.dataset.fold,
        overflow: bar.scrollWidth - bar.clientWidth,
        sideways: document.documentElement.scrollWidth > innerWidth + 1
            || document.body.scrollWidth > document.body.clientWidth + 1,
        width: innerWidth,
        controls,
        signOut: shown(document.getElementById('logout-btn')),
        back: shown(document.getElementById('back-btn')),
        search: shown(document.getElementById('global-search')),
        more: shown(document.getElementById('topbar-more')),
        chip: chip ? { shown: shown(chip), lines: chip.getClientRects().length,
                       height: chip.offsetHeight, title: chip.title } : null,
        // the bar's own buttons, shown in it or not
        folded: ['about-btn', 'support-btn', 'theme-toggle']
            .concat([...bar.querySelectorAll('.tb-btn[data-nav], .tb-btn[data-action]')]
                .map(b => b.textContent.trim()))
            .filter(key => { const el = document.getElementById(key)
                || [...bar.querySelectorAll('.tb-btn')].find(b => b.textContent.trim() === key);
                return el && !el.classList.contains('hidden') && !shown(el); }),
    };
}"""

MORE_LIST = """() => [...document.querySelectorAll('#topbar-more-menu > *')]
    .map(el => el.textContent.trim())"""

LABELS = {"about-btn": "About", "support-btn": "Contact support"}


def _check_bar(page, where):
    """Nothing in the bar past the window's edge, the page not sideways, and
    every control the bar folded away listed under More."""
    got = page.evaluate(BAR)
    assert got["overflow"] <= 0, (where, got)
    assert not got["sideways"], (where, got)
    for c in got["controls"]:
        assert 0 <= c["box"]["left"] and c["box"]["right"] <= got["width"] + 0.5, (
            where,
            c,
        )
    assert got["signOut"] and got["back"] and got["search"], (where, got)
    if got["chip"] and got["chip"]["shown"]:
        assert got["chip"]["height"] <= 20, (where, got["chip"])  # one line
        assert got["chip"]["title"].endswith(LONG_NAME["display_name"] + " · admin")
    if got["folded"]:
        assert got["more"], (where, got)
        page.click("#topbar-more")
        listed = page.evaluate(MORE_LIST)
        for key in got["folded"]:
            label = LABELS.get(key, key)
            if key == "theme-toggle":
                assert "Dark mode" in listed or "Light mode" in listed, (where, listed)
            else:
                assert label in listed, (where, key, listed)
        if got["chip"] and not got["chip"]["shown"]:
            assert any(LONG_NAME["display_name"] in t for t in listed), listed
        page.keyboard.press("Escape")
        assert page.evaluate("document.getElementById('topbar-more-menu').hidden")
        assert page.evaluate("document.activeElement.id") == "topbar-more"
    return got


SETTINGS_IN_VIEW = """() => {
    const box = el => { const r = el.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; };
    const content = document.getElementById('content').getBoundingClientRect();
    const active = document.activeElement;
    const field = active && active.closest('#settings-panel') ? active
        : [...document.querySelectorAll('#settings-panel input, #settings-panel select')]
            .find(el => el.offsetParent !== null);
    return {
        window: { width: innerWidth, height: innerHeight },
        content: box(document.getElementById('content')),
        tabs: [...document.querySelectorAll('#settings-groups [role="tab"]')]
            .filter(t => t.offsetParent !== null).map(box),
        field: field ? box(field) : null,
        save: box(document.getElementById('settings-save-btn')),
        sideways: document.documentElement.scrollWidth > innerWidth + 1,
    };
}"""


def _check_settings(page, where):
    got = page.evaluate(SETTINGS_IN_VIEW)
    win = _window(got)
    # the list, a group's first field and Save Settings, all in view at once
    assert got["tabs"], where
    for tab in got["tabs"]:
        assert tab["left"] >= 0 and tab["right"] <= win["right"], (where, tab)
    assert got["field"] and _inside(got["field"], got["content"]), (where, got)
    assert _inside(got["save"], win), (where, got["save"])
    assert got["field"]["bottom"] <= got["save"]["top"], (where, got)
    assert not got["sideways"], where


SMALL = [(900, 600), (700, 900)]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("width, height", SMALL)
def test_the_top_bar_and_settings_fit_the_smallest_window(
    browser, company, books, size, width, height
):
    page, handled = _open(
        browser, company, "#/", size, width=width, height=height, user=LONG_NAME
    )
    try:
        page.evaluate(
            "(n) => { document.getElementById('topbar-company').textContent = n; }",
            LONG_COMPANY,
        )
        page.wait_for_function("() => !!document.getElementById('user-chip')")
        page.evaluate("() => new Promise((done) => requestAnimationFrame(done))")
        for name, route, ready in [
            ("dashboard", "#/", "#page-content"),
            ("register", f"#/banking/{books['checking']}", "#reg-start"),
            ("invoices", "#/invoices", "#page-content table"),
            ("settings", "#/settings", "#settings-groups [role='tab']"),
        ]:
            _go(page, handled, route)
            page.wait_for_selector(ready)
            _check_bar(page, (size, width, name))
            _check_frame(page.evaluate(FRAME), (size, width, name))
            if name == "settings":
                _check_settings(page, (size, width))
                # another group's: Tab from its choice in the list goes to
                # its first field, which comes into view above Save Settings
                page.click("#settings-tab-preferences")
                page.keyboard.press("Tab")
                assert page.evaluate(
                    "() => !!document.activeElement.closest('#settings-panel')"
                )
                _check_settings(page, (size, width, "preferences"))
        if SHOTS:
            page.screenshot(
                path=os.path.join(SHOTS, f"topbar-{width}x{height}-{size}.png"),
                clip={"x": 0, "y": 0, "width": width, "height": 70},
            )
    finally:
        page.close()


def test_more_opens_what_the_bar_folded_away(browser, company, books):
    """At 150% in a 900 x 600 window About and Contact support are in More,
    and choosing one there does what the bar's button does."""
    page, handled = _open(
        browser, company, "#/", "150", width=900, height=600, user=LONG_NAME
    )
    try:
        page.wait_for_function("() => !!document.getElementById('user-chip')")
        assert page.evaluate("document.getElementById('topbar').dataset.fold") >= "4"
        page.click("#topbar-more")
        assert page.get_attribute("#topbar-more", "aria-expanded") == "true"
        # the first choice has focus; the list is inside the window
        assert page.evaluate(
            "() => document.activeElement.closest('#topbar-more-menu') !== null"
        )
        got = page.evaluate(
            """() => { const r = document.getElementById('topbar-more-menu').getBoundingClientRect();
                return { left: r.left, right: r.right, bottom: r.bottom, w: innerWidth, h: innerHeight }; }"""
        )
        assert (
            got["left"] >= 0 and got["right"] <= got["w"] and got["bottom"] <= got["h"]
        )
        page.click("#topbar-more-menu >> text=Contact support")
        page.wait_for_selector("#support-form")
        assert page.evaluate("document.getElementById('topbar-more-menu').hidden")
    finally:
        page.close()
