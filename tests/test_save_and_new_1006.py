"""Receive Payments: a Save & New button (#1006).

Record Payment's form gets Save & New beside Record Payment: the payment is
recorded as Record Payment records it, then a blank form opens with the date
kept; Ctrl+Enter presses it, as on Quick Entry. A read-only sign-in has it
hidden (data-write), and an edit (void and replace) has none. The behaviour
runs under node in tests/js/payment_save_and_new_1006.test.cjs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/payments.js").read_text()


def _form_html():
    start = JS.index("openModal('Record Payment'")
    return JS[start : JS.index("</form>`", start)]


def _actions():
    html = _form_html()
    return html[html.index('<div class="form-actions">') :]


def test_save_and_new_sits_after_record_payment():
    actions = _actions()
    assert "Save &amp; New</button>" in actions
    # Record Payment stays the first submit: Enter in a field and Replace's
    # "Void and Replace" relabel both go to it
    assert actions.index(">Record Payment</button>") < actions.index(
        "Save &amp; New</button>"
    )


def test_save_and_new_is_a_write_control_with_its_shortcut():
    tag = re.search(r"<button[^>]*data-save-new[^>]*>", _actions(), re.S).group(0)
    assert 'type="submit"' in tag
    assert "btn btn-primary" in tag
    assert "data-write" in tag
    assert 'aria-keyshortcuts="Control+Enter"' in tag
    assert "Ctrl+Enter" in tag


def test_ctrl_enter_is_wired_on_the_form():
    assert 'onkeydown="PaymentsPage._keys(event)"' in _form_html()
    keys = JS[JS.index("    _keys(e) {") :]
    keys = keys[: keys.index("\n    },")]
    assert "e.ctrlKey && e.key === 'Enter'" in keys
    assert "requestSubmit(more)" in keys
    assert "classList.contains('hidden')" in keys


def test_save_reads_the_button_pressed_and_reopens_with_the_date():
    save = JS[JS.index("    async save(e) {") : JS.index("    _keys(e) {")]
    assert "e.submitter" in save and "data-save-new" in save
    assert "Replace.replacing(form)" in save
    assert "if (andNew) await PaymentsPage._newAfterSave(data.date);" in save
    assert "else App.refresh();" in save
    after = JS[JS.index("    async _newAfterSave(date) {") :]
    after = after[: after.index("\n    },")]
    assert "PaymentsPage.showForm()" in after
    assert "box.value = date" in after


def test_an_edit_has_no_save_and_new():
    replace = JS[JS.index("    async replace(id) {") : JS.index("    _giveBack: null,")]
    assert "Replace.mark(form, 'payment', id);" in replace
    assert "[data-save-new]')?.remove()" in replace


def test_the_node_suite_is_there():
    assert (ROOT / "tests/js/payment_save_and_new_1006.test.cjs").exists()
