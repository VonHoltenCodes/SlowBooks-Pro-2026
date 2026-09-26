"""Settings -> Closing Date says whether one is set, and clears in one click
(explore 2.17.3, macbase1 S-i).

An empty date field shows today's date in grey on macOS, which reads like
"closed through today", and clearing a set date meant emptying three date
segments by hand (WebKit could leave them half-empty and silently invalid).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_clear_empties_the_date_and_says_there_is_no_closing_date():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "settings_closing_date_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["before"] == {
        "value": "2026-06-30",
        "state": "Closed through Jun 30, 2026: changes dated on or before it are refused.",
        "clear_disabled": False,
    }
    assert result["after"] == {
        "value": "",
        "state": "No closing date: every period is open.",
        "clear_disabled": True,
    }


def test_the_field_carries_the_state_line_and_the_clear_button():
    section = JS[
        JS.index('id="settings-closing-date"') : JS.index(
            'name="closing_date_password"'
        )
    ]
    assert 'id="closing-date" name="closing_date" type="date"' in section
    assert 'aria-describedby="closing-date-state"' in section
    assert 'onclick="SettingsPage.clearClosingDate()"' in section
    # disabled when there is nothing to clear; the state line is filled at render
    assert "${s.closing_date ? '' : 'disabled'}>Clear</button>" in section
    assert "SettingsPage._closingStateText(s.closing_date)" in section
    assert 'oninput="SettingsPage.showClosingState()"' in section


def test_a_cleared_closing_date_saves_as_no_closing_date(client):
    assert client.put("/api/settings", json={"closing_date": "2026-06-30"}).is_success
    r = client.put("/api/settings", json={"closing_date": ""})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["closing_date"] == ""
