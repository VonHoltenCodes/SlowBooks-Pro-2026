"""One logo, in the sidebar (#978).

The brand is once in the app: the designer's lockup (the turtle and
"SlowBooks Pro", white lettering, the turtle as the designer drew it, for the
dark ground) in the sidebar's brand block, with the version line under it
as it was. The top bar's left end no longer repeats the name; it keeps the
Hide sidebar control and Back, and leaves the room to the Fast Rack (#912).
The browser half is in tests/test_sidebar_fold_browser.py.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
DARK = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
FIT = (ROOT / "app/static/js/topbar_fit.js").read_text(encoding="utf-8")


def _header():
    return SHELL.split('class="sidebar-header"')[1].split(
        "</div>\n                <ul"
    )[0]


def _topbar():
    return SHELL.split('<div id="topbar">')[1].split('<div id="main-area">')[0]


def _logo():
    m = re.search(r'<h1 class="sidebar-brand">(<svg\b.*?</svg>)</h1>', _header(), re.S)
    assert m, "the sidebar's brand block has no logo"
    return m.group(1)


def test_the_sidebar_brand_is_the_logo_with_its_name():
    svg = _logo()
    assert 'role="img"' in svg
    assert 'aria-label="SlowBooks Pro"' in svg
    assert 'focusable="false"' in svg
    assert re.search(r'viewBox="[\d. ]+"', svg)
    assert 'width="160"' in svg and 'height="46"' in svg
    # the designer's colours, on the sidebar's dark ground: green and gold
    # turtle, white lettering, no navy lettering, and no outline round the
    # turtle (the owner's call: the outline is gone, contrast or no)
    assert "evenodd" not in svg
    fills = set(re.findall(r'fill="(#[0-9a-f]+)"', svg))
    assert fills == {"#257321", "#c93", "#fff"}, fills


def test_the_logo_is_inert_markup():
    """Inline in the shell: nothing in it runs, styles the page or links out,
    and it carries no class rules that could reach the rest of the page."""
    svg = _logo()
    for bad in ("<script", "<style", "href", "<foreignObject", "<image", "on"):
        if bad == "on":
            assert not re.search(r"\son[a-z]+=", svg)
        else:
            assert bad not in svg, bad
    assert "class=" not in svg.split(">", 1)[1], "a class inside the logo"


def test_the_version_line_stays_as_it_was():
    header = _header()
    assert (
        '<div class="sidebar-edition">2026 Edition · <span id="app-version">' in header
    )
    # once, under the logo
    assert header.index("sidebar-brand") < header.index("sidebar-edition")
    assert SHELL.count('class="sidebar-brand"') == 1
    assert (
        "text-transform: uppercase" in CSS.split(".sidebar-edition {")[1].split("}")[0]
    )


def test_the_top_bar_no_longer_names_the_product():
    bar = _topbar()
    text = re.sub(r"<!--.*?-->", "", bar, flags=re.S)
    assert "topbar-brand" not in text
    assert not re.search(r"slow\s*books", re.sub(r"<[^>]+>", " ", text), re.I)
    # the controls at its left end stay: skip link, Hide sidebar, Back
    order = [
        text.index(k)
        for k in ('id="skip-link"', 'id="sidebar-toggle"', 'id="back-btn"')
    ]
    assert order == sorted(order)
    assert text.index('id="back-btn"') < text.index('id="fast-rack"')
    assert ".topbar-brand" not in CSS and ".topbar-brand" not in DARK


def test_the_bar_has_no_brand_fold_step():
    assert "tb-fold-brand" not in FIT and "tb-fold-brand" not in CSS
    assert "return 1 + rackButtons().length + 2;" in FIT
    assert "bar.classList.toggle('tb-fold-theme', n >= r + 3);" in FIT


def test_the_logo_is_sized_in_px_not_viewport_units():
    """The Text size is a zoom: px scale with the sidebar; a viewport unit
    would not (tests/test_text_size*.py)."""
    rule = CSS.split(".sidebar-brand .sidebar-logo {")[1].split("}")[0]
    assert "width: 160px" in rule and "height: 46px" in rule
    assert not re.search(r"\d(vw|vh|vmin|vmax)\b", rule)
    assert "--sidebar-width: 200px" in CSS  # 172px of room inside its padding
