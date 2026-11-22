"""Line-level class in business mode and the "warn when a transaction is
saved without a class" setting (#243, R14).

- `class_warn_blank` is a company setting: "false" by default, "true" or
  "false" only (an API token cannot store "banana"), read back with the
  rest; Settings draws it as a named checkbox in the Classes section and
  sends an unticked box as "false".
- A bill with a class on each of two lines lands in two columns of P&L by
  Class (posting honoured line classes before; the form now offers them to
  a business too). An invoice edited through the form keeps its line
  classes: the payload reads the line's select, else the stored id.
- The sources: the line cell is drawn for every company once it has a
  class of its own (a nonprofit's funds always), the function cell in
  nonprofit mode only; the invoice form has the cell; every document form
  asks ClassWarn.ok before it saves; the warning is client-side — a direct
  API post with no class still lands in Uncategorized, the reconciliation
  the by-class reports promise.

What the forms do in a browser is tests/test_browser_class_filters.py.
"""

import re
from pathlib import Path

from app.models.settings import DEFAULT_SETTINGS

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"

FORMS = {
    "bills.js": "ClassWarn.ok(form, { rows: '#bill-lines tr', cls: 'line-function' })",
    "journal.js": "ClassWarn.ok(form, { rows: '#je-lines tr', cls: 'je-function' })",
    "invoices.js": "ClassWarn.ok(form, { rows: '#inv-lines tr', cls: 'line-class' })",
    "expenses.js": "ClassWarn.ok(form, { rows: '#expense-lines tr', cls: 'line-function' })",
    "cc_charges.js": "ClassWarn.ok(form)",
    "sales_receipts.js": "ClassWarn.ok(form)",
    "credit_memos.js": "ClassWarn.ok(form, { rows: '#cm-lines tr', cls: 'line-class' })",
    "vendor_credits.js": "ClassWarn.ok(form)",
    "estimates.js": "ClassWarn.ok(form)",
    "recurring.js": "ClassWarn.ok(form)",
    "in_kind.js": "ClassWarn.ok(form)",
    "banking.js": "ClassWarn.ok(form)",
    "deposits.js": "ClassWarn.okValue($('#deposit-class').value)",
}


def test_the_setting_defaults_off_and_takes_true_or_false_only(client):
    assert DEFAULT_SETTINGS["class_warn_blank"] == "false"
    assert client.get("/api/settings").json()["class_warn_blank"] == "false"
    r = client.put("/api/settings", json={"class_warn_blank": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["class_warn_blank"] == "true"
    assert client.get("/api/settings").json()["class_warn_blank"] == "true"
    r = client.put("/api/settings", json={"class_warn_blank": "banana"})
    assert r.status_code == 422
    assert "class_warn_blank" in r.json()["detail"]
    assert client.get("/api/settings").json()["class_warn_blank"] == "true"
    assert (
        client.put("/api/settings", json={"class_warn_blank": "false"}).status_code
        == 200
    )


def test_a_bill_with_two_line_classes_lands_in_two_columns(client, seed_accounts):
    a = client.post("/api/classes", json={"name": "Division A"}).json()
    b = client.post("/api/classes", json={"name": "Division B"}).json()
    vendor = client.post("/api/vendors", json={"name": "Lumber Yard"}).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-09",
            "terms": "Net 30",
            "bill_number": "LY-77",
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "2x4s for A",
                    "quantity": 1,
                    "rate": 300,
                    "class_id": a["id"],
                },
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "2x4s for B",
                    "quantity": 1,
                    "rate": 200,
                    "class_id": b["id"],
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    report = client.get(
        "/api/reports/profit-loss-by-class",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
    ).json()
    by_name = {c["class_name"]: c["expenses"] for c in report["classes"]}
    assert by_name["Division A"] == 300.0 and by_name["Division B"] == 200.0
    assert "Uncategorized" not in by_name
    # and a direct post with no class still lands in Uncategorized: the
    # warning is the form's, the promise is the report's
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-10",
            "terms": "Net 30",
            "bill_number": "LY-78",
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "nails",
                    "quantity": 1,
                    "rate": 25,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    report = client.get(
        "/api/reports/profit-loss-by-class",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
    ).json()
    by_name = {c["class_name"]: c["expenses"] for c in report["classes"]}
    assert by_name["Uncategorized"] == 25.0


def test_the_line_cell_is_for_every_company_and_the_function_cell_for_nonprofits():
    src = (JS / "utils.js").read_text(encoding="utf-8")
    np = src[src.index("const Nonprofit = {") :]
    # loadFunds no longer returns early outside nonprofit mode
    load = np[np.index("async loadFunds() {") : np.index("lineClassShown() {")]
    assert "if (!Nonprofit.enabled()) return;" not in load
    assert "API.get('/classes?include_archived=true')" in load
    # shown once a class of the company's own exists; a nonprofit's always
    assert (
        "return Nonprofit.enabled() || (Nonprofit._funds || []).some(f => !f.is_system_default && !f.is_archived);"
        in np
    )
    cell = np[np.index("classCellHtml(cls, fundSelected) {") : np.index("headHtml() {")]
    assert "if (!Nonprofit.lineClassShown()) return '';" in cell
    assert 'class="${cls}-fund" data-no-search>' in cell  # named by nameFields
    assert '<option value="">Same as header</option>' in cell
    # an archived class a line carries stays a choice
    assert ".filter(f => !f.is_archived || f.id === fundSelected)" in cell
    both = np[np.index("    cellHtml(cls, selected, fundSelected) {") :]
    both = both[: both.index("\n    },")]
    assert "Nonprofit.classCellHtml(cls, fundSelected) + (Nonprofit.enabled()" in both
    # the Function picker: no name of its own (nameFields names it by its
    # column and line), sized to share its cell with Split, not to push it
    # out over the next column
    assert '<select class="${cls}" style="flex:1 1 5em;min-width:5em;">' in both
    assert '">${Nonprofit.optionsHtml(selected' in both
    assert "aria-label" not in both
    head = np[np.index("    headHtml() {") :]
    head = head[: head.index("\n")]
    assert "Nonprofit.classHeadHtml() + (Nonprofit.enabled() ?" in head


def test_the_invoice_form_has_the_cell_and_keeps_a_stored_class():
    src = (JS / "invoices.js").read_text(encoding="utf-8")
    # the cell comes from the shared helper, behind a guard: the form still
    # renders where utils.js is not loaded (the node probes of
    # test_js_node_suites.py and test_discount_item_in_form.py build a bare
    # page from invoices.js alone)
    assert (
        "lineClasses() { return typeof Nonprofit !== 'undefined' "
        "&& !!Nonprofit.classCellHtml; }" in src
    )
    assert "if (InvoicesPage.lineClasses()) await Nonprofit.loadFunds();" in src
    assert (
        "${InvoicesPage.lineClasses() ? Nonprofit.classHeadHtml() : ''}"
        # the cost code and cost type heads follow in construction mode (#1055)
        "${InvoicesPage.costCells() ? SalesCost.headHtml() : ''}"
        '<th scope="col" class="col-qty">Qty</th>' in src
    )
    assert (
        "${InvoicesPage.lineClasses() ? "
        "Nonprofit.classCellHtml('line-class', line.class_id) : ''}" in src
    )
    # the payload: the cell's choice where one is drawn, else the stored class
    assert "class_id: InvoicesPage.lineClassFromRow(row)," in src
    assert (
        "if (row.querySelector('.line-class-fund')) "
        "return Nonprofit.fundFromRow(row, 'line-class');\n"
        "        return row.dataset.classId ? parseInt(row.dataset.classId) : null;"
        in src
    )


def test_every_document_form_asks_before_saving_without_a_class():
    for name, call in FORMS.items():
        src = (JS / name).read_text(encoding="utf-8")
        assert call in src, name
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    warn = utils[utils.index("const ClassWarn = {") :]
    warn = warn[: warn.index("\n};")]
    assert "App.settings.class_warn_blank === 'true'" in warn
    assert "if (!form || !form.class_id) return true;" in warn
    assert "return confirm(ClassWarn.message());" in warn
    # the header picker starts blank only with the setting on
    picker = utils[utils.index("async function classFormGroupHtml(selectedId) {") :]
    picker = picker[: picker.index("\n}")]
    assert "const blank = !selectedId && ClassWarn.enabled();" in picker
    assert re.search(r'<option value="" selected>— choose a \$\{T\(\'class\'\)', picker)
    assert "c.is_system_default && !blank ? 'selected'" in picker


def test_settings_draws_the_checkbox_named_and_sends_it_either_way():
    src = (JS / "settings.js").read_text(encoding="utf-8")
    assert '<label for="class-warn-blank"' in src
    assert (
        'type="checkbox" id="class-warn-blank" name="class_warn_blank" value="true"'
        in src
    )
    assert (
        "Warn when a transaction is saved without a ${T('class').toLowerCase()}" in src
    )
    assert (
        "if (classWarn) data.class_warn_blank = classWarn.checked ? 'true' : 'false';"
        in src
    )
