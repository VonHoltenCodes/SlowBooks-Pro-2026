"""Settings -> Closing Date says whether one is set, and clears in one click
(explore 2.17.3, macbase1 S-i).

An empty date field shows today's date in grey on macOS, which reads like
"closed through today", and clearing a set date meant emptying three date
segments by hand (WebKit could leave them half-empty and silently invalid).

2.18.0 gate, macbase1 NEW-1: Clear set `value = ''`, which on macOS leaves
WebKit's date segments holding the old date, the field invalid with an empty
message and the form unable to submit: Save Settings sent nothing, for any
setting, until a reload. And the line under the field described the field,
not the books, so it said "No closing date: every period is open." while the
closing date was still in force. Clear now rebuilds the field empty, and the
line says what is saved, then what Save Settings would change.
"""

import functools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")


CLOSED = "Closed through Sep 28, 2026: changes dated on or before it are refused."
OPEN = "No closing date: every period is open."


@functools.lru_cache(maxsize=None)
def _closing_date_steps():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "settings_closing_date_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",  # node writes UTF-8; Windows would read cp1252
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return {s["step"]: s for s in map(json.loads, out.stdout.splitlines())}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_clear_leaves_a_date_field_webkit_will_submit():
    steps = _closing_date_steps()
    assert steps["loaded"]["state"] == CLOSED and steps["loaded"]["rendered_state"]
    cleared = steps["cleared"]
    # valid: the form can submit, so Save Settings sends the clear
    assert (cleared["value"], cleared["type"], cleared["valid"]) == ("", "date", True)
    assert cleared["clear_disabled"] is True
    assert cleared["dirty"] == "Unsaved changes"
    assert steps["saved"]["sent"]["closing_date"] == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_state_line_says_what_is_saved_until_the_save_goes_through():
    steps = _closing_date_steps()
    # cleared but not saved: the books are still closed, and the line says so
    assert steps["cleared"]["state"] == (
        CLOSED + " Save Settings to remove the closing date."
    )
    failed = steps["save-failed"]
    assert failed["state"] == steps["cleared"]["state"]
    assert failed["toast"][1] == "error" and failed["dirty"] == "Unsaved changes"
    saved = steps["saved"]
    assert saved["state"] == OPEN and saved["dirty"] == ""
    assert steps["typed-a-date"]["state"] == (
        OPEN + " Save Settings to close the books through Oct 31, 2026."
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_half_typed_date_is_named_and_can_still_be_cleared():
    steps = _closing_date_steps()
    half = steps["half-typed"]
    assert half["valid"] is False
    assert half["state"] == (
        OPEN + " The date typed is not complete: finish it, or press Clear."
    )
    assert half["clear_disabled"] is False  # the way out stays open
    refused = steps["save-refused-by-the-browser"]
    assert refused["toast"] == [
        "Nothing was saved: the closing date is not complete. Finish it, or press Clear.",
        "error",
    ]


def test_the_field_carries_the_state_line_and_the_clear_button():
    section = JS[
        JS.index('id="settings-closing-date"') : JS.index(
            'name="closing_date_password"'
        )
    ]
    assert 'id="closing-date" name="closing_date" type="date"' in section
    # the state line, then the section's lead in its note (#977)
    assert 'aria-describedby="closing-date-state closing-date-help"' in section
    assert 'onclick="SettingsPage.clearClosingDate()"' in section
    # disabled when there is nothing to clear; the state line is filled at render
    assert "${s.closing_date ? '' : 'disabled'}>Clear</button>" in section
    assert "SettingsPage._closingStateText(s.closing_date)" in section
    assert 'oninput="SettingsPage.showClosingState()"' in section
    # a save the browser refuses (a half-typed date) is said on the page
    assert 'oninvalid="SettingsPage.closingDateInvalid()"' in section


def test_a_cleared_closing_date_saves_as_no_closing_date(client):
    assert client.put("/api/settings", json={"closing_date": "2026-06-30"}).is_success
    r = client.put("/api/settings", json={"closing_date": ""})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["closing_date"] == ""
