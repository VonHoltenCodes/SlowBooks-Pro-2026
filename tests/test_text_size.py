"""Text size (#717): what the page's files must hold for the size to be on
the page before its first paint, on screen only, with a frame that fits.

The behaviour in a browser is tests/test_browser_text_size.py; the keys,
the wheel and the carry-over of the old desktop zoom are
tests/js/desktop_zoom.test.cjs.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
JS = ROOT / "app" / "static" / "js"
TEXT_SIZE = (JS / "text_size.js").read_text(encoding="utf-8")
SCRIPT = '<script src="/static/js/text_size.js"></script>'


def test_the_size_is_set_from_head_before_the_stylesheet_and_the_body():
    head = INDEX[: INDEX.index("</head>")]
    assert SCRIPT in head
    at = head.index(SCRIPT)
    # boot_check.js stays first (tests/test_boot_check.py); this is next
    assert head.index('<script src="/static/js/boot_check.js"></script>') < at
    assert at < head.index('<link rel="stylesheet" href="/static/css/style.css">')
    # a plain script: run as it is parsed, not deferred to after a paint
    assert not re.search(r"<script[^>]*text_size\.js[^>]*\b(defer|async)\b", INDEX)
    # and before the shim whose keys step it
    assert at < INDEX.index('<script src="/static/js/desktop_shim.js"></script>')


def test_text_size_js_is_es5_like_boot_check():
    code = _code(TEXT_SIZE)
    assert "=>" not in code
    assert "`" not in code
    assert not re.search(r"\b(let|const|class)\s", code)
    assert "?." not in code


def _code(text):
    """The text with its comments taken out."""
    return re.sub(r"/\*.*?\*/|(?<![:'\"])//[^\n]*", "", text, flags=re.S)


def _rules_outside_screen(css, needle):
    """The lines holding `needle` that are not inside an @media screen block."""
    out, depth, screen_at = [], 0, None
    for line in css.splitlines():
        if screen_at is None and re.match(r"\s*@media screen\b", line):
            screen_at = depth
        if needle in line and screen_at is None:
            out.append(line.strip())
        depth += line.count("{") - line.count("}")
        if screen_at is not None and depth <= screen_at:
            screen_at = None
    return out


def test_the_zoom_is_on_screen_only_so_printing_is_never_enlarged():
    zooms = re.findall(r'html\[data-text-size="(\d+)"\]\s*\{\s*zoom:\s*([\d.]+);', CSS)
    assert sorted(zooms) == [("115", "1.15"), ("125", "1.25"), ("150", "1.5")]
    assert _rules_outside_screen(CSS, "zoom:") == []
    # the print and PDF templates are documents of their own, without it
    for tpl in (ROOT / "app" / "templates").rglob("*.html"):
        assert "text_size" not in tpl.read_text(encoding="utf-8"), tpl.name


def test_the_text_size_block_follows_the_root_rules():
    root_end = CSS.index("}", CSS.index(":root {"))
    block = CSS.index("TEXT SIZE (#717)")
    assert root_end < block < CSS.index("* { margin: 0; padding: 0;")


def test_no_height_is_taken_from_the_viewport_unscaled():
    """Under zoom a viewport unit grows with the page: a height or width
    taken from the window is a percentage of it, or divided by --ui-scale."""
    sources = {"style.css": _code(CSS)}
    for f in JS.glob("*.js"):
        if f.name != "chart.umd.js":
            sources[f.name] = _code(f.read_text(encoding="utf-8"))
    bare = []
    for name, text in sources.items():
        for m in re.finditer(r"\b\d+(?:\.\d+)?v[hw]\b(?!\s*/\s*var\(--ui-scale)", text):
            bare.append(f"{name}: {text[max(0, m.start() - 40) : m.end()]!r}")
    assert bare == []


def test_the_shell_is_the_windows_height_at_any_size():
    app = re.search(r"#app \{[^}]*\}", _code(CSS)).group(0)
    assert "height: 100%;" in app and "100vh" not in app
    assert re.search(r"html, body \{ height: 100%; \}", CSS)
    modal = re.search(r"\n\.modal \{\s+background[^}]*\}", CSS).group(0)
    assert "max-height: calc(100% - 40px);" in modal


def test_the_control_is_in_my_preferences():
    settings = (JS / "settings.js").read_text(encoding="utf-8")
    block = settings[
        settings.index("_myPreferencesHtml() {") : settings.index(
            "loadMyPreferences() {"
        )
    ]
    assert "SettingsPage._textSizeHtml()" in block
    assert 'id="pref-text-size"' in block and "data-text-size-control" in block
    for n in ("100", "115", "125", "150"):
        assert n in block
    assert "SlowbooksTextSize.apply(this.value)" in block
