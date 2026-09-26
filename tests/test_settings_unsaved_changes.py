"""Settings saves from anywhere on the page and warns before edits are lost
(explore 2.17.3, skytech L16).

Company info saved only via "Save Settings" at the very bottom, after 25
other buttons — the first "Save" on the page was AI Insights' — and leaving
the page discarded the edits without a word. The save bar now stays at the
bottom of the window and says when there are unsaved changes; leaving the
page (a link, a toolbar button, a shortcut, a reload) asks first.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_unsaved_changes_are_tracked_and_leaving_asks_first():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "settings_unsaved_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    steps = {s["step"]: s for s in map(json.loads, out.stdout.splitlines())}

    assert steps["loaded"]["dirty"] is False and steps["loaded"]["note"] == ""
    assert steps["edited"]["dirty"] is True
    assert steps["edited"]["note"] == "Unsaved changes"

    cancelled = steps["leave-cancelled"]
    assert cancelled["confirms"] == 1 and cancelled["navigated"] == []
    assert cancelled["hash"] == "#/settings"  # the address goes back, too
    assert cancelled["dirty"] is True  # the edits are still there

    same = steps["same-page"]
    assert same["navigated"] == ["#/settings"] and same["confirms"] == 1

    unload = steps["unload-while-dirty"]
    assert unload["prevented"] is True and unload["returnValue"] == ""

    assert steps["type-only"]["others"] is False

    confirmed = steps["leave-confirmed"]
    assert confirmed["navigated"] == ["#/settings", "#/customers"]
    assert confirmed["confirms"] == 2

    assert steps["unload-when-clean"]["prevented"] is False
    assert steps["unload-when-leaving-on-purpose"]["prevented"] is False
    assert steps["installed-twice"]["wrapped_once"] is True


def test_the_save_bar_stays_in_reach_and_is_the_forms_own_submit():
    bar = JS[JS.index('id="settings-savebar"') - 40 : JS.index("</form>`;")]
    assert "position:sticky; bottom:0" in bar
    assert (
        '<button type="submit" class="btn btn-primary" id="settings-save-btn">Save Settings</button>'
        in bar
    )
    assert 'id="settings-dirty-note"' in bar
    # and AI Insights' own button no longer reads as the page's "Save"
    assert 'id="ai-settings-save">Save AI settings</button>' in JS


def test_saving_marks_the_page_clean_and_the_page_is_watched():
    save = JS[JS.index("async save(e) {") : JS.index("async uploadLogo(input) {")]
    assert save.index("await API.put('/settings', data)") < save.index(
        "SettingsPage._markClean()"
    )
    assert 'oninput="SettingsPage._updateDirty()"' in JS
    render_hooks = JS[: JS.index("return `")]
    assert "SettingsPage._installLeaveGuard();" in render_hooks
    assert "SettingsPage._markClean();" in render_hooks


def test_actions_that_reload_or_rerender_do_not_drop_other_edits():
    logo = JS[JS.index("async uploadLogo(input) {") : JS.index("async testEmail() {")]
    # a logo upload re-renders only when nothing else is unsaved
    assert (
        "if (!SettingsPage.isDirty()) { App.navigate('#/settings'); return; }" in logo
    )
    kind = JS[
        JS.index("async changeCompanyType(sel) {") : JS.index(
            "async saveOcrEngine(value) {"
        )
    ]
    # switching company type saves the other changes with it, then reloads
    assert "const others = SettingsPage.isDirty('company_type');" in kind
    assert "const payload = others ? SettingsPage._formData() : {};" in kind
    assert kind.index("SettingsPage._leaving = true;") < kind.index("location.reload()")
