"""A read-only sign-in isn't offered what it can't do (skytech W-L17, 2.18.0
gate). It still saw Edit, Mark Sent, Void, Duplicate and Upload on an
invoice, each refused by the server ("Your role doesn't allow this
action"). Buttons that call a write action are hidden for it, on pages and
in dialogs; reads (View, Print, Save PDF, exports) and opening a record's
form (which opens locked) stay."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def test_every_write_action_named_is_a_real_button_call():
    app = (JS / "app.js").read_text(encoding="utf-8")
    block = app[app.index("WRITE_ACTIONS:") : app.index("hideWriteControls(root)")]
    names = re.findall(r"'(\w+Page\.\w+)'", block)
    assert len(names) > 100
    spa = "".join(p.read_text(encoding="utf-8") for p in JS.glob("*.js"))
    assert [n for n in names if not re.search(re.escape(n) + r"\(", spa)] == []
    # dialogs get the same treatment as pages
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert "window.App.hideWriteControls($('#modal-body'))" in utils


PROBE = r"""
const fs = require('fs'), vm = require('vm');
function button(label, onclick, cls = 'btn btn-secondary') {
  const classes = new Set(cls.split(' '));
  return { textContent: label, hidden: false,
    classList: { contains: (c) => classes.has(c), add: (c) => { if (c === 'hidden') this_hidden(); classes.add(c); } },
    getAttribute: (k) => (k === 'onclick' ? onclick : null), closest: () => null };
}
function this_hidden() {}
const buttons = [
  ['Upload', 'InvoicesPage.uploadAttachment(7)'], ['Save PDF', "window.open('/api/invoices/7/pdf','_blank')"],
  ['Print', "window.open('/api/invoices/7/print-preview','_blank')"], ['Duplicate', 'InvoicesPage.duplicate(7)'],
  ['Mark Sent', 'InvoicesPage.markSent(7)'], ['Void Invoice', 'InvoicesPage.void(7)'],
  ['Edit', 'InvoicesPage.showForm(7)'], ['Close', 'closeModal()'], ['View', 'InvoicesPage.view(7)'],
  ['Export All', 'IIFPage.exportAll()'], ['Import', 'IIFPage.importFile()'],
].map(([l, o]) => button(l, o));
// an invoice row: View and Edit side by side; a vendor row: Edit alone
const row = { querySelectorAll: () => [buttons[8], buttons[6]] };
buttons[6].parentElement = row;
const lone = button('Edit', 'VendorsPage.showForm(3)');
lone.parentElement = { querySelectorAll: () => [lone] };
buttons.push(lone);
// a lone Edit that calls a write action (the register's journal and
// deposit edits, #388) is hidden, unlike one that opens a form locked
const loneJe = button('Edit JE', 'JournalPage.editEntry(7)');
loneJe.parentElement = { querySelectorAll: () => [loneJe] };
buttons.push(loneJe);
const loneDep = button('Edit Dep', 'DepositsPage.editDeposit(7)');
loneDep.parentElement = { querySelectorAll: () => [loneDep] };
buttons.push(loneDep);
const root = { querySelectorAll: () => buttons };
const ctx = { console, window: {}, document: { addEventListener: () => {}, body: { classList: { toggle() {} } },
  getElementById: () => null, querySelectorAll: () => [] } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/app.js', 'utf8') + '\nthis.App = App;', ctx);
const App = ctx.App;
App.role = 'readonly';
App.hideWriteControls(root);
const shown = buttons.filter((b) => !b.classList.contains('hidden')).map((b) => b.textContent);
App.role = 'admin';
const again = buttons.map((b) => button(b.textContent, b.getAttribute('onclick')));
App.hideWriteControls({ querySelectorAll: () => again });
console.log(JSON.stringify({ shown, adminHidden: again.filter((b) => b.classList.contains('hidden')).length }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_read_only_sign_in_sees_only_what_it_can_do():
    out = subprocess.run(
        ["node", "-e", PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # Edit beside View is hidden (View shows it); a lone Edit opens the
    # record's form locked, and stays
    assert got["shown"] == ["Save PDF", "Print", "Close", "View", "Export All", "Edit"]
    # the register's journal/deposit Edit (a write action) is hidden
    assert "Edit JE" not in got["shown"] and "Edit Dep" not in got["shown"]
    assert got["adminHidden"] == 0
