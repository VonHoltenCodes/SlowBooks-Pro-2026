"""Two CSS defects found by the marketing agent building the training videos
against the installed 2.10.3 bundle, and reproduced here behaviourally.

Both are guarded at the stylesheet, because both were invisible to every
test we had: the JS was correct in each case, and no assertion looked at
what the browser actually computed.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STYLE = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
DARK = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")


def test_a_generic_hidden_rule_exists():
    """The JS hides elements by adding `.hidden` in ten places across three
    files. There was no generic rule — only `.modal-overlay.hidden` and
    `.splash-overlay.hidden` — so everything else kept the class and stayed
    on screen. The global search dropdown never closed, and at rest it
    painted a 2 px sliver under the toolbar in every session."""
    import re

    assert re.search(r"^\.hidden\s*\{[^}]*display:\s*none", STYLE, re.M), (
        "no generic `.hidden` rule — an element hidden from JS will stay "
        "visible unless it happens to have its own .x.hidden rule"
    )


def test_every_element_hidden_from_js_is_actually_hideable():
    """The tripwire for the class, not the instance: whatever the JS hides
    must be covered by a rule that hides it."""
    import re

    js = ""
    for f in (ROOT / "app/static/js").glob("*.js"):
        js += f.read_text(encoding="utf-8")
    uses = re.findall(r"classList\.add\(['\"]hidden['\"]\)", js)
    assert uses, "no JS hides anything with .hidden any more — update this test"
    # a generic rule covers all of them at once
    assert re.search(r"^\.hidden\s*\{", STYLE, re.M)


def test_the_hidden_class_wins_over_a_component_display():
    """`.search-dropdown` sets its own box; without !important the utility
    loses to it and the trap is simply reset for the next component."""
    import re

    m = re.search(r"^\.hidden\s*\{([^}]*)\}", STYLE, re.M)
    assert m and "!important" in m.group(1)


def test_quick_entry_log_uses_a_theme_token_not_a_hardcoded_white():
    """It was `background: white`, which the dark theme could not override.
    The log text is near-white there, so the running confirmation of what you
    just saved was invisible — about 1.1:1 against a documented AA product."""
    import re

    m = re.search(r"#qe-log\s*\{([^}]*)\}", STYLE)
    assert m, "#qe-log rule is gone — update this test"
    body = m.group(1)
    assert "background: white" not in body and "background:#fff" not in body.replace(
        " ", ""
    )
    assert "var(--panel-bg)" in body, "the log background must follow the theme"
    assert "var(--text-primary)" in body, "and so must its text"


@pytest.mark.parametrize("token", ["--panel-bg", "--text-primary"])
def test_the_tokens_the_log_relies_on_are_themed(token):
    assert (
        token in STYLE and token in DARK
    ), f"{token} must be defined in both themes for #qe-log to follow them"


# ---------------------------------------------------------------------------
# Dark-theme contrast (issue #41, macbase1's sweep)
# ---------------------------------------------------------------------------


def _ratio(fg, bg):
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])

    hi, lo = max(lum(fg), lum(bg)), min(lum(fg), lum(bg))
    return (hi + 0.05) / (lo + 0.05)


def _hex(s):
    s = s.lstrip("#")
    return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))


def _token(css, name):
    import re

    m = re.search(rf"{name}:\s*(#[0-9a-fA-F]{{6}})", css)
    assert m, f"{name} is not defined"
    return _hex(m.group(1))


def test_a_bare_link_has_a_rule_at_all():
    """There was no rule for a bare `<a>` anywhere, so the browser chose —
    and its default #0000EE is 1.73:1 on the dark panel. Attachment
    filenames are bare anchors, which made this release's own feature hard
    to read in dark theme."""
    import re

    assert re.search(
        r"^a\s*\{[^}]*color:\s*var\(--text-link\)", STYLE, re.M
    ), "no bare `a` rule — the user agent picks the link colour again"


def test_link_and_success_tokens_pass_AA_in_both_themes():
    """The product documents WCAG AA. These are the colours #41 measured
    below it; pin the ratios so a future palette change cannot quietly undo
    the fix."""
    checks = [
        ("--text-link", STYLE, (255, 255, 255), "light"),
        ("--text-link", DARK, (30, 32, 40), "dark"),
        ("--text-success", STYLE, (255, 255, 255), "light"),
        ("--text-success", DARK, (30, 32, 40), "dark"),
    ]
    for name, css, bg, theme in checks:
        r = _ratio(_token(css, name), bg)
        assert r >= 4.5, f"{name} in {theme} is {r:.2f}:1, below AA's 4.5"


def test_a_green_amount_passes_AA_on_every_row_it_sits_on():
    """2.22.1 gate, NEW-50: Analytics' A/P aging TOTAL is a green amount on
    the totals row's tint, and #2b8055 was 4.36:1 there in the light theme.
    The rows a green amount sits on, read from the stylesheet: white, the
    striped row, the hovered row, and the totals row's tint over the striped
    and the hovered row."""
    import re

    green = _token(STYLE, "--qb-green")
    striped = _hex(
        re.search(
            r"tbody tr:nth-child\(even\) \{ background: (#[0-9a-f]{6})", STYLE
        ).group(1)
    )
    hovered = _hex(
        re.search(r"tbody tr:hover \{ background: (#[0-9a-f]{6})", STYLE).group(1)
    )
    tint = re.search(
        r"\.totals-row td \{[^}]*background: rgba\((\d+), (\d+), (\d+), ([\d.]+)\)",
        STYLE,
    )
    r, g, b, a = (*map(int, tint.groups()[:3]), float(tint.group(4)))

    def under_tint(base):
        return tuple(round(a * c + (1 - a) * x) for c, x in zip((r, g, b), base))

    for name, bg in (
        ("white", (255, 255, 255)),
        ("a striped row", striped),
        ("a hovered row", hovered),
        ("a totals row", under_tint(striped)),
        ("a hovered totals row", under_tint(hovered)),
    ):
        ratio = _ratio(green, bg)
        assert ratio >= 4.5, f"--qb-green on {name} is {ratio:.2f}:1, below AA's 4.5"


def test_the_dark_sidebar_footer_is_readable():
    """It carries the running version and the feedback link at 2.53:1."""
    import re

    m = re.search(
        r'\[data-theme="dark"\]\s*\.sidebar-footer\s*\{[^}]*color:\s*(#[0-9a-fA-F]{6})',
        DARK,
    )
    assert m, "the dark sidebar-footer rule is gone — update this test"
    r = _ratio(_hex(m.group(1)), (20, 22, 28))
    assert r >= 4.5, f"the dark sidebar footer is {r:.2f}:1, below AA"


def test_no_inline_style_uses_the_low_contrast_blue_for_a_link():
    """`style="color:var(--qb-blue)"` beat the stylesheet's link rule and is
    3.86:1 in dark. Inline styles win, so they have to use the token too."""
    js = ""
    for f in (ROOT / "app/static/js").glob("*.js"):
        js += f.read_text(encoding="utf-8")
    assert 'style="color:var(--qb-blue)' not in js.replace(" ", "")
