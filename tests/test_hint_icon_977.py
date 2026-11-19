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
