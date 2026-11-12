"""Text on every page meets WCAG AA contrast, in both themes.

The macOS gate measures this off a live window (SlowBooks-Pro-Testing,
mac/macgate/check_css_regressions.py). It sweeps every visible element with
its own short text, works out the ground the text actually sits on, and
scores the pair. Its one standing FAIL at 2.18.0 was that list: 17 rules
below AA. Most were muted text: on the light theme's toolbar and status bar
gradients (2.57:1), on white (4.14), and the dark theme's grey on its panel
(4.32). The rest were the sidebar's section headings, the moon button, the
primary button, the dark skip link and the void badge.

This is the same sweep in playwright's Chromium, so the list is kept at zero
here rather than found again on a gate box:

- the gate's JavaScript: a translucent layer is composited, a gradient is
  scored at its worst stop, a bitmap is left out; and one thing the gate's
  scorer missed. It kept a text colour's r, g and b and dropped its alpha,
  so rgba(255,255,255,0.35) scored as white (skytech R3-1). Here the text
  is painted as the browser paints it: its colour's alpha, and every
  element's opacity, over each ground. A disabled control has no
  requirement (WCAG 1.4.3) and is left out;
- its thresholds: 4.5:1, or 3:1 for text of 24px, or 18.66px bold;
- its pages in its order, in dark and then light, and then the splash with
  the licence terms and What's new showing, in light and then dark.

The gate visits #/dashboard and #/pledges, which are not routes (the
dashboard is #/), so those two measure only the shell around "Page not
found"; and it reads only text of up to 60 characters, which leaves out
every hint paragraph. So every page is swept a second time, whole: the
dashboard with all its cards, a job's own page, the bank register, the
global search's results, the update notice, the notices (toasts) a save
shows. The dialogs are swept in tests/test_dialog_contrast.py.

A colour key, the "■" beside each band of the dashboard's A/R aging bar,
is a graphic rather than text: it is held to 3:1 (WCAG 1.4.11), and the
words beside it carry the band's name and amount (macbase1 NEW-11).

The page is the real index.html, scripts and stylesheets, served by the app
itself through the test client, on a bakery's books entered through the API
(seed_books): every list has rows, every document a status. Skipped, as one
module, where playwright or its Chromium is not installed.
"""

import re
import sys
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.bakery_books import _ok  # noqa: E402

ORIGIN = "http://slowbooks.test"
AA = 4.5

# The gate's pages, in its order.
GATE_ROUTES = (
    "#/dashboard",
    "#/pledges",
    "#/invoices",
    "#/accounts",
    "#/reports",
    "#/settings",
)
# A route that only moves the address to another page
REDIRECTS = {"/check-register"}
# Colour keys, the "■" beside each band of the A/R aging bar: graphics, not
# text, so they are held to 3:1 (WCAG 1.4.11) rather than 4.5.
COLOUR_KEYS = {"■"}

# check_css_regressions.py's SWEEP, verbatim but for its comments.
SWEEP = r"""
(function(){
  function parse(c){
    var m = (c || '').match(/[\d.]+/g);
    if (!m || m.length < 3) return null;
    return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1];
  }
  function over(fg, bg){          // composite fg (with alpha) onto bg
    var a = fg[3];
    return [fg[0]*a + bg[0]*(1-a), fg[1]*a + bg[1]*(1-a),
            fg[2]*a + bg[2]*(1-a), 1];
  }
  // Premultiplied colours, [r*a, g*a, b*a, a]: an element with opacity
  // paints its own content, then lays it over what is behind at that
  // opacity, and source-over in premultiplied form is exactly that.
  function pm(c){ return [c[0]*c[3], c[1]*c[3], c[2]*c[3], c[3]]; }
  function pover(top, under){
    var a = top[3];
    return [top[0] + under[0]*(1-a), top[1] + under[1]*(1-a),
            top[2] + under[2]*(1-a), a + under[3]*(1-a)];
  }
  function fade(c, o){ return [c[0]*o, c[1]*o, c[2]*o, c[3]*o]; }
  // A gradient between known colours is a range: its stops. Only a bitmap
  // (url(...)) is unresolvable from here.
  function stops(bgi){
    if (!bgi || bgi === 'none') return null;
    if (/url\(/.test(bgi)) return null;                 // a real image
    var found = bgi.match(/rgba?\([^)]*\)/g) || [];
    var out = [];
    for (var i = 0; i < found.length; i++) {
      var c = parse(found[i]);
      if (c && c[3] > 0) out.push(c);
    }
    return out.length ? out : null;
  }
  var rgbs = function(c){ return 'rgb(' + Math.round(c[0]) + ', '
                 + Math.round(c[1]) + ', ' + Math.round(c[2]) + ')'; };
  // The text and the ground under it, as painted: the walk up to the
  // ground is the gate's; each element passed keeps its background and its
  // opacity, and the text colour's own alpha is painted on top. A gradient
  // is a range, so one pair per stop.
  function bg(el, text){
    var levels = [], e = el, img = false, grad = null, ground = null;
    while (e && e !== document.documentElement) {
      var cs = getComputedStyle(e), c = parse(cs.backgroundColor);
      var gs = stops(cs.backgroundImage);
      var hasImg = cs.backgroundImage && cs.backgroundImage !== 'none';
      var level = {color: c && c[3] > 0 ? c : null, opacity: +cs.opacity, grad: null};
      levels.push(level);
      if (gs && !grad) { grad = gs; level.grad = gs; }  // the nearest gradient wins
      if (c && c[3] > 0) {
        if (c[3] >= 0.999) { img = img || (!!hasImg && !gs); ground = e; break; }
        if (hasImg && !gs) img = true;
      } else if (hasImg && !gs) {
        img = true;                         // a bitmap: genuinely unresolvable
      }
      if (gs) { ground = e; break; }        // the gradient supplies the paint
      e = e.parentElement;
    }
    // opacity above the ground fades all of it over the page
    var above = 1;
    for (var p = ground && ground.parentElement; p && p !== document.documentElement;
         p = p.parentElement) above *= +getComputedStyle(p).opacity;
    var base = parse(getComputedStyle(document.body).backgroundColor)
               || [255,255,255,1];
    if (base[3] < 0.999) base = [255,255,255,1];
    function paint(withText, stop){
      var content = withText ? pm(text) : [0, 0, 0, 0];
      for (var i = 0; i < levels.length; i++) {
        var L = levels[i];
        if (L.grad && stop) content = pover(content, pm(stop));  // image over colour
        if (L.color) content = pover(content, pm(L.color));
        content = fade(content, L.opacity);
      }
      return pover(fade(content, above), pm(base));
    }
    var pairs = [];
    var each = grad || [null];
    for (var k = 0; k < each.length; k++)
      pairs.push([rgbs(paint(true, each[k])), rgbs(paint(false, each[k]))]);
    return {color: pairs[0][1], image: img, pairs: pairs};
  }
  var out = [], seen = {};
  var nodes = document.querySelectorAll(
    'a,button,label,td,th,h1,h2,h3,h4,h5,p,span,div,li,legend,summary,strong,em');
  for (var i = 0; i < nodes.length && out.length < 400; i++) {
    var el = nodes[i];
    // only elements with their OWN short visible text
    var own = '';
    for (var j = 0; j < el.childNodes.length; j++)
      if (el.childNodes[j].nodeType === 3) own += el.childNodes[j].nodeValue;
    own = own.trim();
    if (!own || own.length > 60) continue;
    var r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    var cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    // a disabled control has no contrast requirement (WCAG 1.4.3)
    if (el.closest('button:disabled, fieldset:disabled')) continue;
    var dim = 1;
    for (var q = el; q && q !== document.documentElement; q = q.parentElement)
      dim *= +getComputedStyle(q).opacity;
    var key = own + '|' + cs.color + '|' + dim;
    if (seen[key]) continue;
    seen[key] = 1;
    var b = bg(el, parse(cs.color));
    out.push({text: own.slice(0, 42), color: cs.color, background: b.color,
              pairs: b.pairs, unmeasurable: b.image,
              size: parseFloat(cs.fontSize) || 0,
              weight: cs.fontWeight, tag: el.tagName.toLowerCase(),
              cls: (el.className || '').toString().slice(0, 40)});
  }
  return out;
})()
"""


def sweep_of(root="document", cap=400):
    """The sweep over `root` (a JavaScript expression for an element, or the
    whole document) with no length limit, as the gate sweeps the splash: a
    What's-new box of long items once went unmeasured at 2.33:1, and the
    page sweep's 60-character limit leaves out every hint paragraph."""
    swept = SWEEP.replace("own.length > 60", "own.length > 100000", 1)
    swept = swept.replace("out.length < 400", f"out.length < {cap}", 1)
    if root != "document":
        swept = swept.replace(
            "document.querySelectorAll(", f"{root}.querySelectorAll(", 1
        )
    assert "100000" in swept and (root == "document" or root in swept)
    return swept


# the gate's splash sweep, exactly
SPLASH_SWEEP = sweep_of("document.getElementById('splash')")
# every other sweep here takes the whole text of everything it finds
PAGE_SWEEP = sweep_of(cap=5000)
TOAST_SWEEP = sweep_of("document.getElementById('toast-container')")


def _rgba(s):
    m = re.findall(r"[\d.]+", s or "")
    if len(m) < 3:
        return None
    return [float(x) for x in m[:3]] + [float(m[3]) if len(m) > 3 else 1.0]


def contrast(fg, bg):
    """WCAG contrast ratio between two computed colours. A translucent text
    colour is seen over its ground, so it is composited there first: the
    gate's scorer kept only r, g and b, and scored rgba(255,255,255,0.35) as
    white (skytech R3-1)."""
    a, b = _rgba(fg), _rgba(bg)
    if not a or not b:
        return None
    a = [a[i] * a[3] + b[i] * (1 - a[3]) for i in range(3)]
    b = b[:3]

    def lum(c):
        out = []
        for v in c:
            v /= 255.0
            out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    l1, l2 = sorted((lum(a), lum(b)), reverse=True)
    return round((l1 + 0.05) / (l2 + 0.05), 2)


# What counts as bold for the large-text rule: the gate's page sweep leaves
# 600 out, its splash sweep counts it.
PAGE_BOLD = ("700", "800", "900", "bold")
SPLASH_BOLD = ("600",) + PAGE_BOLD


def _score(item, bold):
    """(ratio, needed) for one swept element, or None where it cannot be
    measured. A gradient is a range: the stop that reads worst. A colour key
    is a graphic: 3:1 (WCAG 1.4.11)."""
    if item.get("unmeasurable"):
        return None
    ratios = [r for r in (contrast(fg, bg) for fg, bg in item["pairs"]) if r]
    if not ratios:
        return None
    size = item.get("size") or 0
    heavy = str(item.get("weight")) in bold
    large = size >= 24 or (size >= 18.66 and heavy)
    need = 3.0 if large or item["text"] in COLOUR_KEYS else AA
    return min(ratios), need


def below_threshold(sweeps, bold=PAGE_BOLD):
    """The gate's report: one row per rule (tag, first class, colour, ground,
    theme) with its worst ratio and the pages it was on."""
    groups = {}
    for (theme, where), items in sweeps.items():
        for it in items:
            scored = _score(it, bold)
            if not scored or scored[0] >= scored[1]:
                continue
            cls = (it.get("cls") or "").split()
            key = (it["tag"], cls[0] if cls else "", it["color"], it["background"])
            g = groups.setdefault(
                key + (theme,),
                {"ratio": scored[0], "need": scored[1], "text": it["text"]},
            )
            g["ratio"] = min(g["ratio"], scored[0])
            g.setdefault("where", set()).add(where)
    return sorted(
        f"{g['ratio']:>5}:1 (needs {g['need']}) {k[0]}{'.' + k[1] if k[1] else ''}"
        f"  {k[2]} on {k[3]}  [{k[4]}]  {g['text']!r}  on {sorted(g['where'])}"
        for k, g in groups.items()
    )


# What only another kind of install answers, answered as it does there: the
# company list of a Server Edition install (PostgreSQL), which says when each
# company was last opened, and the backups of a company kept in a file (the
# suite's is in memory, so it has none).
SERVED = {
    "/api/companies": [
        {
            "id": None,
            "name": "Harbor Light Bakery",
            "database_name": "harbor_light",
            "description": "the database this server is connected to",
            "last_accessed": None,
            "is_current": True,
        },
        {
            "id": 2,
            "name": "Harbor Light Catering",
            "database_name": "harbor_catering",
            "description": "Events and weddings",
            "last_accessed": "2026-09-21T16:02:11",
            "is_current": False,
        },
    ],
    "/api/backups": [
        {
            "id": 2,
            "filename": "harbor-light-bakery_2026-09-25_2210.db",
            "file_size": 1843200,
            "backup_type": "manual",
            "notes": "Before the September close",
            "created_at": "2026-09-25T22:10:04+00:00",
        },
        {
            "id": 1,
            "filename": "harbor-light-bakery_2026-09-18_0915_pre-restore.db",
            "file_size": 1720320,
            "backup_type": "pre-restore",
            "notes": None,
            "created_at": "2026-09-18T09:15:40+00:00",
        },
    ],
}


@pytest.fixture(name="company")
def company_fixture(client, books):
    return client


# --- the browser ----------------------------------------------------------


@pytest.fixture(scope="module", name="browser")
def browser_fixture():
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # the package without its browser
            pytest.skip(f"playwright's Chromium is not installed: {exc}")
        yield chromium
        chromium.close()


_HOP_BY_HOP = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def _served_by(client, handled, served):
    """Every request the page makes, answered by the app through the test
    client, which is signed in to the company above, except the paths in
    `served`, answered with that body. Anything off the origin (web fonts
    and the like) is refused: not part of what is measured."""

    def serve(route):
        req = route.request
        url = urlsplit(req.url)
        if f"{url.scheme}://{url.netloc}" != ORIGIN:
            return route.abort()
        target = url.path + (f"?{url.query}" if url.query else "")
        if req.method == "GET" and url.path in served:
            handled.append(target)
            return route.fulfill(json=served[url.path])
        headers = {
            k: v
            for k, v in req.headers.items()
            if k.lower() in ("content-type", "accept")
        }
        resp = client.request(
            req.method, target, headers=headers, content=req.post_data_buffer
        )
        handled.append(target)
        route.fulfill(
            status=resp.status_code,
            headers={
                k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP
            },
            body=resp.content,
        )

    return serve


# The suite's browser is a Linux one, whatever machine runs it: the app reads
# navigator.platform for its shortcuts and their labels (Alt+← and "Alt+B" here,
# ⌘[ and "⌥B" on a Mac), and the keyboard tests press and read the Linux ones.
# On a Mac host Playwright's Chromium said MacIntel, and sixteen of them were
# red on macbase1's and Keith's Macs. A test that wants the Mac's keys sets
# the platform itself, as the keyboard tests do, and `HOST_PLATFORM` is what
# the browser really is, for a click the browser itself answers to (a new tab).
# (a statement, not a function: an init script is run as source, not called)
LINUX_PLATFORM = """Object.defineProperty(Navigator.prototype, 'platform',
    { get: () => 'Linux x86_64', configurable: true });"""
HOST_PLATFORM = {"darwin": "MacIntel", "win32": "Win32"}.get(
    sys.platform, "Linux x86_64"
)


def new_page(browser, **kwargs):
    """`browser.new_page(...)`, as a Linux browser."""
    page = browser.new_page(**kwargs)
    page.add_init_script(LINUX_PLATFORM)
    return page


def _platform(page, platform):
    """The platform the page says it is, from here on, with the labels that
    read it redrawn."""
    page.evaluate(
        """(p) => { Object.defineProperty(Navigator.prototype, 'platform',
            { get: () => p, configurable: true });
            if (window.App && App.labelBack) App.labelBack(); }""",
        platform,
    )


def _open(browser, client, served=None):
    handled = []
    # the gate's window: 1500 x 980
    page = new_page(browser, viewport={"width": 1500, "height": 980})
    page.route(
        "**/*", _served_by(client, handled, SERVED if served is None else served)
    )
    page.goto(f"{ORIGIN}/")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    # The lists open on Show: Active since #210. The sweeps look at them on
    # Show: All, so the inactive vendor and item set aside above (dimmed,
    # badged) are still measured.
    page.evaluate("""() => ['customers', 'vendors', 'items', 'employees', 'jobs']
            .forEach(k => localStorage.setItem('slowbooks-show-' + k, 'all'))""")
    return page, handled


def _visit(page, handled, route):
    """The page at `route`, rendered, with whatever it fills in afterwards.
    App.navigate is what the address change runs; awaiting it directly means
    the page is in, rather than guessing how long that takes."""
    page.evaluate("async (h) => { await App.navigate(h); }", route)
    settle(page, handled)


def settle(page, handled):
    """A moment in which the page asks for nothing more: some fill in parts
    after they open."""
    quiet = 0
    for _ in range(125):
        n = len(handled)
        page.wait_for_timeout(40)
        quiet = quiet + 1 if len(handled) == n else 0
        if quiet >= 2:
            return


def _theme(page, theme):
    # as the gate does it: the attribute and the remembered choice
    page.evaluate(
        """(t) => { document.documentElement.setAttribute('data-theme', t);
                    localStorage.setItem('slowbooks-theme', t); }""",
        theme,
    )
    # Buttons fade to the new theme's colours (transition: all 0.1s), and a
    # colour read halfway is neither theme's. The gate waits 1.2 s; here the
    # fades are run to their end, which is the colour the theme sets.
    page.evaluate("""() => document.getAnimations()
                   .filter(a => a instanceof CSSTransition)
                   .forEach(a => a.finish())""")


def _toggle_to(page, handled, theme):
    """The theme switched on a page already drawn, as the toolbar's toggle
    switches it: App.toggleTheme sets the attribute and sends
    slowbooks:themechange, and a page that draws for its theme repaints on
    it (the dashboard's aging bar picks ink or white for each slice's figure
    in the theme it is drawn in, NEW-60). Setting the attribute alone left a
    figure drawn for one theme to be measured in the other (#814): the page
    opens on the dashboard, and a visit to the route it is already on draws
    nothing again."""
    page.evaluate(
        """(t) => { const now = document.documentElement.getAttribute('data-theme')
                                || 'light';
                    if (now !== t) App.toggleTheme(); }""",
        theme,
    )
    _theme(page, theme)
    settle(page, handled)


def test_the_gate_pages_and_the_splash_meet_aa_in_both_themes(browser, company):
    """The gate's sequence: its six pages, and the dashboard, in dark and
    then light, with the splash still up behind them as the gate leaves it;
    then the splash with its terms, in light and then dark."""
    page, handled = _open(browser, company)
    try:
        pages = {}
        for theme in ("dark", "light"):
            _toggle_to(page, handled, theme)
            for route in GATE_ROUTES + ("#/",):
                _visit(page, handled, route)
                pages[(theme, route)] = page.evaluate(SWEEP)
        splash = {}
        for theme in ("light", "dark"):
            _toggle_to(page, handled, theme)
            page.evaluate(
                """() => { document.getElementById('splash').classList.remove('hidden');
                           const t = document.getElementById('splash-terms');
                           if (t) t.hidden = false; }"""
            )
            page.wait_for_function(
                "() => !document.getElementById('splash-whatsnew').hidden"
            )
            splash[(theme, "splash")] = page.evaluate(SPLASH_SWEEP)
    finally:
        page.close()

    # the sweep read the pages, the void badge and the splash's licence block
    texts = {it["text"] for items in pages.values() for it in items}
    assert {"Chart of Accounts", "Reports", "void"} <= texts
    splash_texts = {it["text"] for items in splash.values() for it in items}
    assert {"Before you start", "I understand"} <= splash_texts
    assert all(splash.values())

    # the gate's report, which should be empty: one row per rule below AA
    assert (below_threshold(pages), below_threshold(splash, SPLASH_BOLD)) == ([], [])


def test_every_page_and_notice_meets_aa_in_both_themes(browser, company, books):
    """Every page of the app, a job's and the bank register's own pages
    among them, each swept whole in both themes; and a success, an error and
    an information notice."""
    # the desktop app with an update waiting: the notice at the top of the
    # sidebar shows on every page
    info = _ok(company.get("/api/system"))
    served = dict(SERVED)
    served["/api/system"] = dict(info, update_check_enabled=True)
    served["/api/system/update-check"] = {
        "update_available": True,
        "latest_version": "2.18.1",
        "download_url": "https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases",
    }
    page, handled = _open(browser, company, served)
    try:
        paths = page.evaluate(
            "() => Object.keys(App.routes).filter(k => !k.includes('/:'))"
        )
        # the gate's pages too: the gate sweep reads only text of up to 60
        # characters
        routes = [f"#{p}" for p in paths if p not in REDIRECTS]
        assert len(routes) >= 45, routes
        routes += [f"#/jobs/{books['job']}", f"#/banking/{books['checking']}"]
        pages = {}
        for route in routes:
            _visit(page, handled, route)
            for theme in ("dark", "light"):
                _toggle_to(page, handled, theme)
                pages[(theme, route)] = page.evaluate(PAGE_SWEEP)
        # the global search's dropdown
        page.fill("#global-search", "an")
        page.wait_for_selector("#search-results .search-item")
        for theme in ("dark", "light"):
            _toggle_to(page, handled, theme)
            pages[(theme, "search")] = page.evaluate(
                sweep_of("document.getElementById('search-results')")
            )
        page.fill("#global-search", "")
        notices = {}
        for theme in ("light", "dark"):
            _toggle_to(page, handled, theme)
            page.evaluate("""() => { toast('Invoice saved', 'success');
                           toast('Could not save the invoice', 'error');
                           toast('Opening the report', 'info'); }""")
            # measured once they have faded in (toast-in, 0.2 s)
            page.wait_for_function(
                """() => { const t = [...document.querySelectorAll('#toast-container .toast')];
                           return t.length === 3
                               && t.every(e => getComputedStyle(e).opacity === '1'); }"""
            )
            notices[(theme, "toasts")] = page.evaluate(TOAST_SWEEP)
            page.evaluate(
                "() => document.querySelectorAll('#toast-container .toast')"
                ".forEach(e => e.remove())"
            )
    finally:
        page.close()

    # every page was in, not an error in its place
    failed = [
        where
        for where, items in pages.items()
        if any(
            it["text"] in ("Couldn't load this page", "Page not found") for it in items
        )
    ]
    assert failed == []
    # and they showed what they show with books in them: permits in every
    # state, a job, the register, when another company was last opened
    texts = {it["text"] for items in pages.values() for it in items}
    shown = {"Expired", "Expires soon", "Active", "Inactive", "Never", "Note:"}
    shown |= {"Click to browse", "Waterfront Gala", "Checking", "See what changed →"}
    assert shown <= texts, shown - texts
    assert any(t.startswith("Last accessed:") for t in texts)
    assert [len(items) for items in notices.values()] == [3, 3]

    assert (below_threshold(pages), below_threshold(notices)) == ([], [])
