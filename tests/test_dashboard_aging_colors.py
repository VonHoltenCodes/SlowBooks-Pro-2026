"""The dashboard's A/R Aging bar prints a wide-enough slice's figure in the
app's ink or white, whichever contrasts more, and only at AA (4.5:1). The
light theme's 31-60 orange, #f36706, took ink at 5.53:1, which WCAG 2's
arithmetic clears and the eye did not: black on a bright orange (2.23.4
gate, NEW-129, the owner's report). The slice is a deeper orange now, and its
figure prints white. The key beside each label is a graphic: 3:1 on the card
(WCAG 1.4.11), which every band keeps.

The arithmetic here is utils.js's figureTextOn, in Python.
"""

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "style.css"
INK = "#1a1a28"
BANDS = ("--aging-current", "--aging-30", "--aging-60", "--aging-90")


def _var(css: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\s*([^;]+);", css)
    assert match, name
    value = match.group(1).strip()
    ref = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return _var(css, ref.group(1)) if ref else value


def _luminance(hex_color: str) -> float:
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", hex_color), hex_color
    channels = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(l1: float, l2: float) -> float:
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def test_the_31_60_slice_prints_its_figure_white_at_aa():
    css = CSS.read_text(encoding="utf-8")
    orange = _luminance(_var(css, "--aging-60"))
    on_white = _contrast(orange, 1.0)
    on_ink = _contrast(orange, _luminance(INK))
    # figureTextOn takes the higher ratio, and prints only at 4.5 or more
    assert on_white > on_ink, (on_white, on_ink)
    assert on_white >= 4.5, on_white


def test_every_band_reads_on_the_card_and_carries_a_figure():
    css = CSS.read_text(encoding="utf-8")
    for name in BANDS:
        band = _luminance(_var(css, name))
        assert _contrast(band, 1.0) >= 3.0, name  # the key, a graphic
        best = max(_contrast(band, 1.0), _contrast(band, _luminance(INK)))
        assert best >= 4.5, (name, best)  # the figure, text
