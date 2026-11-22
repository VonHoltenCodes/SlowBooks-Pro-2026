"""#977: the tortoise info icon's shared pieces. hint() and its behaviour are
tested in tests/js/hint_977.test.cjs; this is what the page loads."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_icon_set_is_in_the_apps_images():
    for name, least in (
        ("tortoise-hint.png", 500),
        ("tortoise-hint@2x.png", 1500),
        ("tortoise-info.png", 1000),
        ("tortoise-info@2x.png", 3000),
    ):
        path = ROOT / "app/static/img" / name
        assert path.stat().st_size > least, name
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_page_loads_the_helper_after_the_utilities_it_uses():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    utils = html.index('<script src="/static/js/utils.js"></script>')
    assert html.index('<script src="/static/js/hint.js"></script>') > utils


def test_the_button_is_a_24px_target_with_a_focus_ring_and_a_dark_theme():
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    rule = css[css.index(".hint-icon {") : css.index("}", css.index(".hint-icon {"))]
    assert "width: 24px; height: 24px;" in rule
    assert ".hint-icon:focus-visible { outline: 2px solid var(--focus-ring);" in css
    assert ".hint-pop[hidden] { display: none; }" in css
    assert ":hover .hint-pop" not in css, "a note never opens on hover"
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    assert '[data-theme="dark"] .hint-pop {' in dark


def test_the_mark_is_the_whole_tortoise_on_a_clear_ground():
    """The button's mark was a crop of the shell on a pale block: the head
    was cut off and the block showed on a dark page. All four are the whole
    round mark with nothing behind the tortoise."""
    from PIL import Image

    for name, side in (
        ("tortoise-hint.png", 22),
        ("tortoise-hint@2x.png", 44),
        ("tortoise-info.png", 28),
        ("tortoise-info@2x.png", 56),
    ):
        mark = Image.open(ROOT / "app/static/img" / name).convert("RGBA")
        assert mark.size == (side, side), name
        alpha = mark.getchannel("A")
        # clear at the corners and inside the ring, above the tortoise
        for spot in ((0, 0), (side - 1, 0), (0, side - 1), (side // 2, side // 6)):
            assert alpha.getpixel(spot) < 40, (name, spot)
        # and the tortoise is there
        assert alpha.getpixel((side // 2, side // 2)) == 255, name
    js = (ROOT / "app/static/js/hint.js").read_text(encoding="utf-8")
    assert 'tortoise-hint@2x.png 2x" width="22" height="22"' in js


def test_the_i_on_the_shell_reads_at_the_buttons_size():
    """NEW-264: at 22px the "i" was about 2 x 5 pixels, a dot on the shell.
    The button's two sizes keep the same tortoise with a larger white disc
    behind a darker "i" (main had 4 and 28 white pixels there)."""
    from PIL import Image

    for name, side, white_least, ink_least in (
        ("tortoise-hint.png", 22, 14, 0),
        ("tortoise-hint@2x.png", 44, 70, 30),
    ):
        mark = Image.open(ROOT / "app/static/img" / name).convert("RGBA")
        px = mark.load()
        spots = [(x, y) for x in range(side) for y in range(side) if px[x, y][3] > 200]
        white = sum(1 for s in spots if min(px[s][:3]) > 235)
        ink = sum(1 for s in spots if sum(px[s][:3]) / 3 < 90)
        assert white >= white_least, (name, white)
        assert ink >= ink_least, (name, ink)
