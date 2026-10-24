"""Recent screens (#336, #462): the sources. The session's screens are kept
in sessionStorage; a page lists when it renders, a dialog report or document
when its address is set, and the control redraws when a dialog closes. Since
#462 it is one "Recent ▾" disclosure button over a dropdown of the last
seven, most recent first, with Clear at the foot and no × per line. The
browser run is tests/test_browser_open_tabs.py."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_hooks_and_the_control_are_in_place():
    src = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    # kept for the session, by address, seven at most
    assert "OPEN_TABS_KEY: 'slowbooks-open-screens'" in src
    assert "sessionStorage.getItem(App.OPEN_TABS_KEY)" in src
    assert "OPEN_TABS_MAX: 7," in src
    # a page shown, a dialog addressed, a dialog closed
    assert "App.screenShown(route);" in src
    assert "App.screenShown(null);" in src
    assert src.count("App.renderOpenTabs()") >= 3
    # the Dashboard and the Report Center are not listed
    assert "if (key === '/' || key === '/reports')" in src
    # a disclosure button over the list, newest first; each line a link back
    assert 'aria-controls="recent-menu"' in src and "aria-expanded=" in src
    assert "tabs.slice().reverse()" in src
    assert 'class="recent-screens__link"' in src
    # no × per line; Clear at the foot
    assert "open-screens__close" not in src and "closeTab(" not in src
    assert 'class="recent-screens__clear" data-recent="clear">Clear<' in src
    # the keyboard: arrows, Esc back to the button, Tab closes it behind
    for key in ("'ArrowDown'", "'ArrowUp'", "'Escape'", "'Home'", "'End'"):
        assert key in src
    assert "focusout" in src
    # a report lists with its period
    for words in ("this year to date", "last quarter", "as of ${formatDate("):
        assert words in src


def test_the_styles_cover_both_themes():
    light = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    assert ".recent-screens__toggle" in light and ".recent-screens__clear" in light
    assert ".recent-screens__toggle" in dark and ".recent-screens__link" in dark
    assert ".open-screens" not in light + dark


def test_the_feature_is_documented():
    features = (ROOT / "docs/features.md").read_text(encoding="utf-8")
    assert "Recent screens" in features and "last seven" in features
