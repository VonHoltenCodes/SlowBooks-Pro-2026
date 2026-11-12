"""Window polish for 2.32.0 (#813): the small things the release gates noted,
the ones read from the stylesheets and the page sources."""

import re
from pathlib import Path

from tests.test_css_hidden_and_contrast import _hex, _ratio, _token

ROOT = Path(__file__).resolve().parents[1]
DARK = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")


def test_dark_placeholders_are_dimmer_than_typed_text_and_still_read():
    """A dark field's placeholder read almost as bright as typed text: it is
    the light text token now, AA on the field at rest and in focus, and well
    short of the typed text's contrast."""
    m = re.search(r"\[data-theme=\"dark\"\] ::placeholder\s*\{([^}]*)\}", DARK)
    assert m, "no dark placeholder rule"
    assert "var(--text-light)" in m.group(1)
    assert re.search(r"opacity:\s*1", m.group(1))
    hint, typed = _token(DARK, "--text-light"), _token(DARK, "--text-primary")
    for field in ("#14161c", "#1a1e28"):
        assert _ratio(hint, _hex(field)) >= 4.5, field
        assert _ratio(typed, _hex(field)) - _ratio(hint, _hex(field)) > 5, field


STYLE = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")


def test_the_opening_ytd_cells_have_no_stepper_over_the_figure():
    """A seven-figure amount (1234567.89) lost its last digit under the
    number box's stepper in the opening-YTD dialog's narrow cells, and
    WITHHOLDING ran into SUTA WAGES."""
    m = re.search(r'\.opening-states input\[type="number"\]\s*\{([^}]*)\}', STYLE)
    assert m and "appearance: textfield" in m.group(1)
    assert re.search(
        r'\.opening-states input\[type="number"\]::-webkit-inner-spin-button', STYLE
    )
    head = re.search(r"\.opening-states thead th\s*\{([^}]*)\}", STYLE).group(1)
    assert "overflow-wrap: anywhere" in head
