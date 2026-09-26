"""A read-only sign-in is not offered what it cannot do (2.17.3
exploratory test, W-L17).

Every "+ New" button showed for the readonly role, and a whole form could
be filled in before the server's 403 arrived. The server's refusal stays
the enforcement (pinned below); the page now learns the role from
/api/auth/status, hides the create buttons, and shows every dialog's form
locked with a sentence — except a form that only opens a document.
"""

from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_the_page_learns_the_role_from_auth_status():
    app = _js("app.js")
    status = app.index("fetch('/api/auth/status'")
    assert "if (a.user) App.setRole(a.user.role);" in app[status : status + 400]
    assert "isReadOnly() { return App.role === 'readonly'; }" in app


def test_create_buttons_are_hidden_for_a_read_only_sign_in():
    app = _js("app.js")
    body = app[app.index("hideWriteControls(root) {") :][:700]
    assert "label.startsWith('+')" in body
    assert "el.classList.contains('btn-primary') && el.closest('.page-header')" in body
    assert "el.classList.add('hidden')" in body
    # pages that re-render in place stay clean
    assert "new MutationObserver(() => App.hideWriteControls(page))" in app
    # the toolbar's New Customer / Create Invoice / Receive Payment / Quick Entry
    assert (
        '#topbar .tb-btn[data-action], #topbar .tb-btn[data-nav="#/quick-entry"]' in app
    )


def test_every_dialog_form_is_locked_except_a_document_picker():
    app = _js("app.js")
    body = app[app.index("lockForms(root) {") :][:1200]
    assert "form:not([data-readonly-ok])" in body
    assert "el.disabled = true" in body and "form.onsubmit" in body
    assert "App.READ_ONLY_MESSAGE" in body
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
