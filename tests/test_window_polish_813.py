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
