"""A read-only sign-in is not offered what it cannot do (2.17.3
exploratory test, W-L17).

Every "+ New" button showed for the readonly role, and a whole form could
be filled in before the server's 403 arrived. The server's refusal stays
the enforcement (pinned below); the page now learns the role from
/api/auth/status, hides the create buttons, and shows every dialog's form
locked with a sentence — except a form that only opens a document.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_the_page_learns_the_role_from_auth_status():
    app = _js("app.js")
    status = app.index("fetch('/api/auth/status'")
    assert (
        "if (a.user) App.setRole(a.user.role, a.user.username);"
        in app[status : status + 400]
    )
    assert "isReadOnly() { return App.role === 'readonly'; }" in app


def test_create_buttons_are_hidden_for_a_read_only_sign_in():
    app = _js("app.js")
    body = app[app.index("hideWriteControls(root) {") :][:2200]
    assert "label.startsWith('+')" in body
    assert "el.classList.contains('btn-primary') && el.closest('.page-header')" in body
    assert "el.classList.add('hidden')" in body
    # pages that re-render in place stay clean, and so do dialogs that fill
    # in after they open (2.18.0 round 4: AR Aging's Apply Late Fees); the
    # pass for any role but admin includes the read-only one
    assert "new MutationObserver(() => roots.forEach(App.rolePass))" in app
    assert "App.readOnlyPass(root);" in app[app.index("rolePass(root) {") :][:200]
    assert "[page, document.getElementById('modal-body')]" in app
    # the toolbar's New Customer / Create Invoice / Receive Payment / Quick Entry
    assert (
        '#topbar .tb-btn[data-action], #topbar .tb-btn[data-nav="#/quick-entry"]' in app
    )


def test_every_dialog_form_is_locked_except_a_document_picker():
    app = _js("app.js")
    body = app[app.index("lockForms(root) {") :][:1200]
    assert "form:not([data-readonly-ok])" in body
    assert "el.disabled = true" in body and "form.onsubmit" in body
    # the sentence is the read-only one for a read-only sign-in (and the
    # accountant's for an accountant, #340)
    assert "App.lockedMessage()" in body
    assert "if (App.inCopy) return App.COPY_MESSAGE();" in app
    assert (
        "return App.isAccountant() ? App.ACCOUNTANT_MESSAGE : App.READ_ONLY_MESSAGE;"
        in app
    )
    utils = _js("utils.js")
    modal = utils[utils.index("function openModal(") :][:900]
    assert "window.App.lockForms($('#modal-body'))" in modal
    reports = _js("reports.js")
    assert 'onsubmit="ReportsPage.openStatement(event)" data-readonly-ok' in reports


def test_the_server_still_refuses_a_read_only_write(client):
    from app.main import _role_allows

    assert _role_allows("readonly", "GET", "/api/items")
    assert not _role_allows("readonly", "POST", "/api/items")
    assert not _role_allows("readonly", "PUT", "/api/accounts/1")


# What skytech's 52-route sweep and macbase1 still found offered to a
# read-only sign-in at 2.18.0 round 4 (W-L17 leftovers), each marked
# data-write where it is built. tests/test_readonly_browser.py sweeps every
# page in a browser; this holds the marks where no browser is installed.
MARKED = {
    "app.js": [
        r'data-write onclick="App\.showChartImport\(\)">Import…',
        r'data-write onclick="App\.setAccountActive\(\$\{a\.id\}, false\)">Deactivate',
        r'data-write onclick="App\.setAccountActive\(\$\{a\.id\}, true\)">Reactivate',
        r'data-write onclick="App\.deleteAccount\(\$\{a\.id\}\)">Delete',
        r'<div class="settings-section" data-write>\s*<h3>Import</h3>',
        r"data-write>Save & Next \(Ctrl\+Enter\)",
    ],
    "settings.js": [
        r'id="settings-savebar" data-write',
        r'data-write onclick="SettingsPage\.testEmail\(\)"',
        r"data-write>\s*<button[^>]*SettingsPage\.seedTemplates\(\)",
        r'data-write>\s*<input type="text" id="new-class-name"',
        r'data-write>\s*<input type="text" id="new-ct-code"',
        r'data-write>\s*<input type="text" id="new-cc-code"',
        r'data-write>\s*<input type="text" id="new-eq-code"',
        r"data-write>\s*<button[^>]*SettingsPage\.createBackup\(\)",
        r"data-write data-filename=",
        r'data-write data-admin id="ai-settings-test"',
        r'data-write data-admin id="ai-settings-save"',
        r'data-write onclick="SettingsPage\.renameCostCode\(',
        r'data-write onclick="SettingsPage\.toggleCostCode\(',
        r'data-write onclick="SettingsPage\.toggleCostType\(',
    ],
    "batch_payments.js": [
        r"data-write><button[^>]*BatchPaymentsPage\.selectAll\(\)",
        r'data-write>\s*<button type="submit" class="btn btn-primary">Apply Batch Payment',
    ],
    "banking.js": [
        # Connect is an administrator's since 2.29.0 (data-admin hides it
        # for a read-only sign-in as data-write did, and for a bookkeeper too)
        r'<form onsubmit="BankingPage\.connectSimpleFIN\(event\)" data-admin>',
        r'class="review-cat" data-write',
        r"data-write>\s*<button[^>]*BankingPage\.addLine\(",
    ],
}


def test_each_leftover_the_gate_found_is_marked_where_it_is_built():
    missing = {
        name: [p for p in patterns if not re.search(p, _js(name))]
        for name, patterns in MARKED.items()
    }
    assert {k: v for k, v in missing.items() if v} == {}
    # the Attachments "Choose File": every file chooser, not one by one
    app = _js("app.js")
    body = app[app.index("hideWriteControls(root) {") :][:2500]
    assert """'button, a.btn, [data-write], input[type="file"]'""" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_read_only_sign_in_on_a_fake_page():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "readonly_marks_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)

    # an admin is offered everything
    admin = got["admin"]
    assert not admin["delete_hidden"] and not admin["import_panel_hidden"]
    assert (
        admin["budget_cell"]
        == admin["chooser"]
        == {
            "hidden": False,
            "disabled": False,
        }
    )
    assert admin["settings_form"]["notes"] == 0
    assert not admin["settings_form"]["fields_disabled"]
    assert admin["sidebar"] == {"batch_hidden": False, "reports_hidden": False}

    ro = got["readonly"]
    # a write is hidden; a field marked so shows its value, locked
    assert ro["delete_hidden"] and ro["import_panel_hidden"]
    assert ro["budget_cell"] == {"hidden": False, "disabled": True}
    # a file chooser is an upload
    assert ro["chooser"] == {"hidden": True, "disabled": True}
    # a form on the page (Settings) is locked, with one sentence saying why;
    # the button that opens an email template (its only view) still opens it
    assert ro["settings_form"] == {
        "fields_disabled": True,
        "save_disabled": True,
        "template_opens": True,
        "notes": 1,
    }
    assert got["notes_after_second_pass"] == 1
    # reads stay: View, an export, a filter, the statement picker, Cancel
    assert ro["reads_untouched"] and ro["statement_usable"] and ro["cancel_enabled"]
    # the sidebar's pages that only enter things are not offered
    assert ro["sidebar"] == {"batch_hidden": True, "reports_hidden": False}
    # the page and the dialog are watched: what a dialog draws after it
    # opens is cleaned too
    assert got["observed"] == ["page-content", "modal-body"]
    assert got["late_dialog"] == {"late_fees_hidden": True, "letters_hidden": True}
    # Payroll by its address says whose page it is, and loads nothing
    assert got["payroll_readonly"] == {"says": True, "rendered": []}
    assert got["payroll_admin"] == {"rendered": ["payroll"], "page": "<payroll>"}
    # and when the role arrives while it is still loading, as on a first page
    assert got["payroll_role_arrives_late"] is True
    # the Audit Log by its address says it isn't open to a read-only sign-in
    # (skytech, 2.18.0 round 6: "Couldn't load this page"); a bookkeeper's opens
    assert got["audit_readonly"] == {"says": True, "rendered": []}
    assert got["audit_bookkeeper"] == {"rendered": ["audit"], "page": "<audit>"}
