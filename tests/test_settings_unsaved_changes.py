"""Settings saves from anywhere on the page and warns before edits are lost
(explore 2.17.3, skytech L16).

Company info saved only via "Save Settings" at the very bottom, after 25
other buttons — the first "Save" on the page was AI Insights' — and leaving
the page discarded the edits without a word. The save bar now stays at the
bottom of the window and says when there are unsaved changes; leaving the
page (a link, a toolbar button, a shortcut, a reload) asks first.
"""

import json
import re
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
        encoding="utf-8",
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
    # by going back to the Settings entry itself (App.stayPut), not by
    # rewriting the link's new entry over it
    assert cancelled["stayed"] == ["#/invoices"] and cancelled["replaced"] == []
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

    # a read-only sign-in's locked page holds no edits: it leaves unasked
    # (2.18.0 round 4, W-L17 leftovers: Settings is locked for it now)
    locked = steps["read-only"]
    assert locked["dirty"] is False and locked["note"] == ""
    assert locked["confirms"] == 0 and locked["left_to"] == "#/reports"
    assert locked["prevented"] is False


def test_the_save_bar_stays_in_reach_and_is_the_forms_own_submit():
    # the bar sits after the form, the panel's last child (NEW-210), and its
    # button is the form's own submit by the form attribute
    at = JS.index('id="settings-savebar"')
    assert at > JS.index("</form>`;")
    bar = JS[at - 40 : JS.index("</div>`;", at)]
    assert "position:sticky; bottom:0" in bar
    assert (
        '<button type="submit" form="settings-form" class="btn btn-primary" id="settings-save-btn">Save Settings</button>'
        in bar
    )
    assert "${form}\n                    ${savebar}" in JS
    assert 'id="settings-dirty-note"' in bar
    # and AI Insights' own button no longer reads as the page's "Save"
    assert 'id="ai-settings-save">Save AI settings</button>' in JS


def test_no_other_button_submits_the_settings_form():
    # A button with no type submits the form it is in. The email templates'
    # Edit had none: opening a template saved every setting on the page
    # behind the editor (and told a bookkeeper "Your role doesn't allow this
    # action"). Every button this page builds says what it is.
    untyped = [b for b in re.findall(r"<button\b[^>]*>", JS) if "type=" not in b]
    assert untyped == []


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
