"""The purchase screens: what they show and send (2.17.3 exploratory).

UI-only behaviour pinned by reading the page source, as
test_invoice_edit.py does for the invoice form. The server side of each
item is in test_purchase_postings.py.
"""

from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app/static/js"


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def _method(js, signature):
    """The body of one method of a page object (4-space indented)."""
    body = js[js.index(f"\n    {signature} {{") :]
    return body[: body.index("\n    },")]


# ── A5 / B24: the purchase order form ────────────────────────────────────


def test_a_new_po_starts_with_no_tax_not_the_selling_rate():
    # It pre-filled the company's sales-tax rate (8.25%), and the tax then
    # landed on Sales Tax Payable (W-H5, F9).
    form = _method(_js("purchase_orders.js"), "async showForm(id = null)")
    assert "default_tax_rate" not in form
    assert "tax_rate: 0," in form


def test_the_po_form_shows_line_amounts_and_totals_as_you_type():
    # Every line read $0.00 and there was no subtotal, tax or total (F7).
    js = _js("purchase_orders.js")
    row = _method(js, "lineHtml(idx, line, items)")
    assert row.count('oninput="PurchaseOrdersPage.recalc()"') == 2  # qty, rate
    assert "line-amount" in row
    form = _method(js, "async showForm(id = null)")
    for el in ("po-subtotal", "po-tax", "po-total"):
        assert f'id="{el}"' in form
    assert 'oninput="PurchaseOrdersPage.recalc()"' in form  # the tax rate
    recalc = _method(js, "recalc()")
    assert "PurchaseLines.lineAmount(row)" in recalc
    for el in ("#po-subtotal", "#po-tax", "#po-total"):
        assert el in recalc
    assert "PurchaseOrdersPage.recalc()" in _method(js, "itemSel(idx)")


def test_a_po_can_be_viewed_saved_as_pdf_and_printed():
    # W-L19: a PO could be made and billed but never seen or sent.
    js = _js("purchase_orders.js")
    assert "PurchaseOrdersPage.view(${po.id})" in _method(js, "async render()")
    view = _method(js, "async view(id)")
    assert "/api/purchase-orders/${po.id}/pdf" in view
    assert "/api/purchase-orders/${po.id}/print-preview" in view


def test_to_bill_asks_for_an_account_on_every_line_that_needs_one():
    # A line with no account went to 6000 Advertising (W-H5, F8).
    js = _js("purchase_orders.js")
    dialog = _method(js, "async convertToBill(id)")
    assert "item.expense_account_id" in dialog
    assert "vendor.default_expense_account_id" in dialog
    assert "track_inventory" in dialog
    send = _method(js, "async doConvert(id)")
    assert "line_id:" in send and "account_id:" in send
    assert "/convert-to-bill`, { lines }" in send


def test_the_po_vendor_picker_hides_inactive_vendors_but_keeps_the_pos_own():
    form = _method(_js("purchase_orders.js"), "async showForm(id = null)")
    assert "v.is_active !== false || v.id == po.vendor_id" in form


# ── A6 / B1 / B23: Enter Bill ────────────────────────────────────────────


def test_enter_bill_has_an_account_column_and_sends_each_lines_account():
    js = _js("bills.js")
    form = _method(js, "async showForm()")
    assert ">Account</th>" in form
    row = _method(js, "lineHtml(idx)")
    assert 'class="line-account"' in row
    assert "PurchaseAccounts.options(BillsPage._accounts" in row
    save = _method(js, "async save(e)")
    assert "account_id: accountId" in save
    assert "Choose an account for line" in save


def test_picking_an_item_on_a_bill_fills_its_cost_and_account():
    # W-M1: the rate stayed 0 and a $0 bill was saved without a word.
    js = _js("bills.js")
    assert 'onchange="BillsPage.itemSelected(this)"' in _method(js, "lineHtml(idx)")
    pick = _method(js, "itemSelected(select)")
    assert "PurchaseLines.price(item)" in pick
    assert "item.expense_account_id" in pick
    assert "BillsPage.recalc()" in pick


def test_enter_bill_shows_a_running_total_and_refuses_zero():
    js = _js("bills.js")
    assert 'id="bill-total"' in _method(js, "async showForm()")
    assert "#bill-total" in _method(js, "recalc()")
    save = _method(js, "async save(e)")
    assert "BillsPage.recalc() <= 0" in save
    assert save.index("BillsPage.recalc() <= 0") < save.index("API.post('/bills'")


def test_a_vendor_brings_its_terms_and_default_account_to_the_bill():
    # F11: Enter Bill always said Net 30.
    picked = _method(_js("bills.js"), "vendorSelected(vendorId)")
    assert "vendor.terms" in picked and "terms.value = vendor.terms" in picked
    assert "default_expense_account_id" in picked and ".line-account" in picked


def test_every_bill_offers_save_pdf_and_print():
    # W-M8: the button pointed at a route that didn't exist, and was shown
    # only on paid bills.
    view = _method(_js("bills.js"), "async view(id)")
    assert "window.open('/api/bills/${bill.id}/pdf','_blank')" in view
    assert "bill.status === 'paid' ?" not in view
    assert "/api/bills/${bill.id}/print-preview" in view


def test_a_bill_lists_its_payments_and_each_can_be_voided():
    # W-L19: a paid bill's payment couldn't be voided on screen.
    js = _js("bills.js")
    view = _method(js, "async view(id)")
    assert "/bill-payments?bill_id=${id}" in view
    rows = _method(js, "_paymentsHtml(bill, payments)")
    assert "BillsPage.voidBillPayment(${p.id}, ${bill.id})" in rows
    assert "p.is_voided" in rows


# ── B1: the vendor credit form ───────────────────────────────────────────


def test_the_vendor_credit_form_fills_the_items_cost_and_totals_up():
    js = _js("vendor_credits.js")
    row = _method(js, "lineHtml(idx)")
    assert 'onchange="VendorCreditsPage.itemSelected(this)"' in row
    assert row.count('oninput="VendorCreditsPage.recalc()"') == 2
    assert "PurchaseLines.price(item)" in _method(js, "itemSelected(select)")
    form = _method(js, "async showForm()")
    for el in ("vc-subtotal", "vc-tax", "vc-total"):
        assert f'id="{el}"' in form
    save = _method(js, "async save(e)")
    assert "VendorCreditsPage.recalc() <= 0" in save


# ── A6: cost of goods in every purchase account picker ───────────────────


def test_purchase_account_pickers_offer_cost_of_goods_sold():
    # W-L18 / F8: only 6000+ accounts were listed, so a flour supplier could
    # not be pointed at 5100 Materials Cost.
    helper = _js("vendors.js")
    pick = helper[helper.index("const PurchaseAccounts = {") :]
    pick = pick[: pick.index("\n};")]
    assert "a.account_type === 'expense' || a.account_type === 'cogs'" in pick

    vendor_form = _method(helper, "async showForm(id = null)")
    assert "account_type=expense" not in vendor_form
    assert (
        "PurchaseAccounts.filter(accounts, v.default_expense_account_id)" in vendor_form
    )
    expense_form = _method(_js("expenses.js"), "async showForm()")
    assert "PurchaseAccounts.filter(accounts)" in expense_form
    card_form = _method(_js("cc_charges.js"), "async showForm()")
    assert "account_type=expense" not in card_form
    assert "PurchaseAccounts.filter(allAccounts)" in card_form


# ── B16: a vendor can be made inactive ───────────────────────────────────


def test_the_vendor_form_can_make_a_vendor_inactive():
    js = _js("vendors.js")
    form = _method(js, "async showForm(id = null)")
    assert 'name="is_active"' in form
    save = _method(js, "async save(e, id, force)")
    assert "data.is_active = data.is_active === 'true'" in save
    assert "inactive</span>" in _method(js, "async render()")


def test_an_inactive_vendor_leaves_the_pickers(client):
    r = client.post("/api/vendors", json={"name": "Old Supplier"})
    vid = r.json()["id"]
    assert (
        client.put(f"/api/vendors/{vid}", json={"is_active": False}).status_code == 200
    )
    active = client.get("/api/vendors?active_only=true").json()
    assert vid not in [v["id"] for v in active]
    for page in ("bills.js", "expenses.js", "vendor_credits.js"):
        assert "/vendors?active_only=true" in _js(page), page


# ── D1: overdraft warning ────────────────────────────────────────────────


def test_money_leaving_a_bank_account_warns_before_it_overdraws():
    # macbase1 S-a: Checking went to -$2,986.90 without a word.
    helper = _js("expenses.js")
    od = helper[helper.index("const Overdraft = {") :]
    od = od[: od.index("\n};")]
    assert "API.get('/banking/overview')" in od
    assert "acct.bank_kind !== 'bank'" in od
    assert "will be overdrawn by" in od and "Save anyway?" in od
    expense_save = _method(helper, "async save(e)")
    assert "await Overdraft.confirm(paidFrom" in expense_save
    assert expense_save.index("Overdraft.confirm") < expense_save.index(
        "API.post('/expenses'"
    )
    pay = _method(_js("bills.js"), "async savePay(e)")
    assert "await Overdraft.confirm(fromId, outgoing, '1000')" in pay
    assert pay.index("Overdraft.confirm") < pay.index("API.post('/bill-payments'")
