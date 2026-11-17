"""Quick Enter CC charges: pick the card once for the batch, and Save & New (#1011).

The charge form keeps the card picked for the session (CCChargesPage._lastCard)
and starts its picker on it; Save & New beside Save Charge posts the charge
and opens a blank form with the card and the date kept; Ctrl+Enter presses
it, as on Quick Entry. A read-only sign-in has it hidden (data-write); an
edit (void and replace) keeps its own card and has none. The behaviour runs
under node in tests/js/cc_charge_save_and_new_1011.test.cjs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/cc_charges.js").read_text()


def _form_html():
    start = JS.index("openModal('Enter Credit Card Charge'")
    return JS[start : JS.index("</form>`", start)]


def _actions():
    html = _form_html()
    return html[html.index('<div class="form-actions">') :]


def _method(name, end="\n    },"):
    body = JS[JS.index(f"    {name}") :]
    return body[: body.index(end)]


def test_save_and_new_sits_after_save_charge():
    actions = _actions()
    assert "Save &amp; New</button>" in actions
    # Save Charge stays the first submit: Enter in a field and Replace's
    # "Void and Replace" relabel both go to it
    assert actions.index(">Save Charge</button>") < actions.index(
        "Save &amp; New</button>"
    )


def test_save_and_new_is_a_write_control_with_its_shortcut():
    tag = re.search(r"<button[^>]*data-save-new[^>]*>", _actions(), re.S).group(0)
    assert 'type="submit"' in tag
    assert "btn btn-primary" in tag
    assert "data-write" in tag
    assert 'aria-keyshortcuts="Control+Enter"' in tag
    assert "Ctrl+Enter" in tag


def test_the_card_picker_starts_on_the_last_card():
    show = _method("async showForm(")
    assert "CCChargesPage._cardStart(cardList, CCChargesPage._lastCard)" in show
    assert "String(a.id) === startCard ? 'selected'" in show
    assert "_lastCard: null," in JS
    start = _method("_cardStart(cards, last) {")
    assert "account_number === '2100'" in start


def test_save_remembers_the_card_and_reopens_on_save_and_new():
    save = _method("async save(e) {", "\n    _saving: false,")
    assert "e.submitter" in save and "data-save-new" in save
    assert "CCChargesPage._lastCard = form.card_account_id.value;" in save
    # the card is remembered for a new charge only, never from an edit
    assert save.index("if (replacing)") < save.index("CCChargesPage._lastCard =")
    assert "await CCChargesPage.showForm();" in save
    assert "box.value = date" in save
    assert "if (CCChargesPage._saving) return;" in save


def test_ctrl_enter_is_wired_on_the_form():
    assert 'onkeydown="CCChargesPage._keys(event)"' in _form_html()
    keys = _method("_keys(e) {")
    assert "e.ctrlKey && e.key === 'Enter'" in keys
    assert "requestSubmit(more)" in keys
    assert "classList.contains('hidden')" in keys


def test_an_edit_keeps_its_card_and_has_no_save_and_new():
    replace = _method("async replace(id) {")
    assert "Replace.set(form, 'card_account_id', card.account_id);" in replace
    assert "[data-save-new]')?.remove()" in replace


def test_the_node_suite_is_there():
    assert (ROOT / "tests/js/cc_charge_save_and_new_1011.test.cjs").exists()
