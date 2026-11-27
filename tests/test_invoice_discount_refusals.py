"""A discount that breaks the sale is refused in a sentence, never a 500.

A discount line (a negative price on an item marked "Discount or
concession", #639) could break a sale two ways, and both ended in an
unhandled DataProblem from the posting (a 500):

- discounts that come to more than the other lines (a total below zero),
  which was first asked about as "This invoice adds up to $0.00";
- with a tax rate, a taxable discount more than the taxable lines above it
  (a negative tax, which the posting skips, so the entry was out of
  balance), on a total above zero too.

Each is now a 422 with a sentence at every place an invoice is priced:
create, edit (before the amount-paid check, which told an unpaid invoice
it was "less than the amount already paid"), duplicate, estimate
conversion (an accepted deduct alternate is a negative line), sales
receipts, and the recurring run, which skips the schedule with the
sentence. The other refusals on the way: a negative price on any other
item says where a discount and a refund belong, and credit applied to a
voided invoice says the invoice is voided."""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

OVER = (
    "The discounts on this {noun} come to more than its other lines. "
    "A refund belongs on a credit memo."
)
BASE = (
    "A taxable discount is more than the taxable lines above it. Untick "
    "Tax on the discount line, or discount the taxable lines."
)
DAY = "2026-10-01"


def _item(client, name, **extra):
    body = {"name": name, "item_type": "service", "rate": 0, **extra}
    r = client.post("/api/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def discount(client, seed_accounts):
    return _item(client, "Loyalty discount", allows_negative_price=True)


def _line(description, rate, taxable, item_id=None, order=0):
    line = {
        "description": description,
        "quantity": 1,
        "rate": rate,
        "is_taxable": taxable,
        "line_order": order,
    }
    if item_id is not None:
        line["item_id"] = item_id
    return line


def _over_lines(discount_id):
    """100 of work and a 150 discount: a total of -50."""
    return [
        _line("Work", 100, False),
        _line("Discount", -150, False, discount_id, 1),
    ]


def _base_lines(discount_id):
    """100 of untaxed labor and a 10 taxable discount: the taxable base is
    -10, the total is above zero."""
    return [
        _line("Labor", 100, False),
        _line("Discount", -10, True, discount_id, 1),
    ]


def _invoice(client, customer_id, lines, tax_rate=0, **extra):
    return client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": DAY,
            "tax_rate": tax_rate,
            "lines": lines,
            **extra,
        },
    )


def _refused(r, sentence):
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == sentence


def _no_invoices(client):
    assert client.get("/api/invoices").json() == []


# -- create -----------------------------------------------------------------


@pytest.mark.parametrize("allow", [False, True])
def test_create_past_the_other_lines_is_refused(client, seed_customer, discount, allow):
    r = _invoice(
        client,
        seed_customer.id,
        _over_lines(discount["id"]),
        allow_zero_total=allow,
    )
    _refused(r, OVER.format(noun="invoice"))
    _no_invoices(client)


@pytest.mark.parametrize("allow", [False, True])
def test_create_taxable_discount_past_the_taxed_lines_is_refused(
    client, seed_customer, discount, allow
):
    r = _invoice(
        client,
        seed_customer.id,
        _base_lines(discount["id"]),
        tax_rate=0.1,
        allow_zero_total=allow,
    )
    _refused(r, BASE)
    _no_invoices(client)


def test_a_positive_total_with_a_negative_taxable_base_is_refused(
    client, seed_customer, discount
):
    # part 33.33 taxed, labor 50 untaxed, a 40 taxable discount at 10%:
    # the total is 42.66, the posting was out of balance by $0.67
    r = _invoice(
        client,
        seed_customer.id,
        [
            _line("Part", 33.33, True),
            _line("Labor", 50, False, order=1),
            _line("Discount", -40, True, discount["id"], 2),
        ],
        tax_rate=0.1,
    )
    _refused(r, BASE)
    _no_invoices(client)


def test_the_same_lines_untaxed_or_with_no_rate_still_post(
    client, seed_customer, discount
):
    # no rate: a negative taxable base charges no tax, so nothing is wrong
    r = _invoice(client, seed_customer.id, _base_lines(discount["id"]), tax_rate=0)
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["total"])) == Decimal("90")
    # the discount untaxed: fine at any rate
    lines = _base_lines(discount["id"])
    lines[1]["is_taxable"] = False
    r = _invoice(client, seed_customer.id, lines, tax_rate=0.1)
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["total"])) == Decimal("90")


def test_a_discount_to_exactly_zero_is_still_the_zero_question(
    client, seed_customer, discount
):
    lines = [
        _line("Work", 100, False),
        _line("Discount", -100, False, discount["id"], 1),
    ]
    r = _invoice(client, seed_customer.id, lines)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "zero_total"
    r = _invoice(client, seed_customer.id, lines, allow_zero_total=True)
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["total"])) == Decimal("0")


# -- edit -------------------------------------------------------------------


def _good_invoice(client, customer_id, discount_id):
    r = _invoice(
        client,
        customer_id,
        [
            _line("Work", 100, False),
            _line("Discount", -10, False, discount_id, 1),
        ],
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize("allow", [False, True])
def test_edit_of_an_unpaid_invoice_past_its_lines_is_refused(
    client, seed_customer, discount, allow
):
    inv = _good_invoice(client, seed_customer.id, discount["id"])
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"lines": _over_lines(discount["id"]), "allow_zero_total": allow},
    )
    # not "less than the amount already paid": nothing was paid
    _refused(r, OVER.format(noun="invoice"))
    after = client.get(f"/api/invoices/{inv['id']}").json()
    assert Decimal(str(after["total"])) == Decimal("90")


def test_edit_of_a_partly_paid_invoice_past_its_lines_says_so_first(
    client, seed_customer, discount
):
    inv = _good_invoice(client, seed_customer.id, discount["id"])
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": DAY,
            "amount": 20,
            "method": "Cash",
            "allocations": [{"invoice_id": inv["id"], "amount": 20}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"lines": _over_lines(discount["id"]), "allow_zero_total": True},
    )
    _refused(r, OVER.format(noun="invoice"))


def test_edit_to_a_taxable_discount_past_the_taxed_lines_is_refused(
    client, seed_customer, discount
):
    inv = _good_invoice(client, seed_customer.id, discount["id"])
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"lines": _base_lines(discount["id"]), "tax_rate": 0.1},
    )
    _refused(r, BASE)
    # a rate alone, on lines that already are so, the same
    lines = _base_lines(discount["id"])
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_rate": 0.1})
    _refused(r, BASE)


# -- duplicate --------------------------------------------------------------


def test_duplicate_of_lines_that_cannot_be_priced_is_refused(
    client, db_session, seed_customer, discount
):
    """An invoice whose lines can't be priced now (one an import brought,
    or saved before this check) is refused when duplicated, not a 500."""
    from app.models.invoices import Invoice

    r = _invoice(client, seed_customer.id, _base_lines(discount["id"]), tax_rate=0)
    assert r.status_code == 201, r.text
    inv = r.json()
    stored = db_session.get(Invoice, inv["id"])
    stored.tax_rate = Decimal("0.1")
    db_session.commit()

    r = client.post(f"/api/invoices/{inv['id']}/duplicate")
    _refused(r, BASE)
    r = client.post(
        f"/api/invoices/{inv['id']}/duplicate", json={"allow_zero_total": True}
    )
    _refused(r, BASE)
    assert len(client.get("/api/invoices").json()) == 1


# -- estimate conversion ----------------------------------------------------


def _estimate(client, customer_id, lines, tax_rate):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": DAY,
            "tax_rate": tax_rate,
            "lines": lines,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _deduct(rate, taxable, order=1):
    return {
        "description": "Deduct alternate",
        "quantity": 1,
        "rate": rate,
        "is_taxable": taxable,
        "line_kind": "alternate",
        "alternate_accepted": True,
        "line_order": order,
    }


@pytest.mark.parametrize("allow", [False, True])
def test_convert_with_a_taxable_deduct_over_untaxed_labor_is_refused(
    client, seed_accounts, seed_customer, allow
):
    est = _estimate(
        client,
        seed_customer.id,
        [_line("Labor", 100, False), _deduct(-10, True)],
        0.1,
    )
    r = client.post(
        f"/api/estimates/{est['id']}/convert", json={"allow_zero_total": allow}
    )
    _refused(r, BASE)
    _no_invoices(client)


@pytest.mark.parametrize("allow", [False, True])
def test_convert_with_a_deduct_past_the_scope_is_refused(
    client, seed_accounts, seed_customer, allow
):
    est = _estimate(
        client,
        seed_customer.id,
        [_line("Work", 100, False), _deduct(-150, False)],
        0,
    )
    r = client.post(
        f"/api/estimates/{est['id']}/convert", json={"allow_zero_total": allow}
    )
    _refused(r, OVER.format(noun="estimate"))
    _no_invoices(client)


# -- sales receipt ----------------------------------------------------------


def _receipt(client, customer_id, lines, tax_rate):
    return client.post(
        "/api/sales-receipts",
        json={
            "customer_id": customer_id,
            "date": DAY,
            "tax_rate": tax_rate,
            "method": "Cash",
            "lines": lines,
        },
    )


def test_sales_receipt_past_its_lines_is_refused(client, seed_customer, discount):
    r = _receipt(client, seed_customer.id, _over_lines(discount["id"]), 0)
    _refused(r, OVER.format(noun="sales receipt"))
    _no_invoices(client)


def test_sales_receipt_taxable_discount_past_the_taxed_lines_is_refused(
    client, seed_customer, discount
):
    r = _receipt(client, seed_customer.id, _base_lines(discount["id"]), 0.1)
    _refused(r, BASE)
    _no_invoices(client)
    # and with the flags left to the items: the labor item untaxed, the
    # discount item taxed, as the item form makes them
    labor = _item(client, "Labor", rate=100, is_taxable=False)
    lines = [
        {"item_id": labor["id"], "description": "Labor", "quantity": 1, "rate": 100},
        {
            "item_id": discount["id"],
            "description": "Discount",
            "quantity": 1,
            "rate": -10,
            "line_order": 1,
        },
    ]
    r = _receipt(client, seed_customer.id, lines, 0.1)
    _refused(r, BASE)
    _no_invoices(client)


# -- recurring run ----------------------------------------------------------


def test_recurring_run_skips_a_schedule_that_cannot_be_priced(
    db_session, seed_accounts, seed_customer
):
    """A template's lines are kept non-negative by its own schema, but one
    stored otherwise (an import, an older version) was posted mid-run and
    raised. The run skips it with the sentence, as it skips a $0.00 one."""
    from app.models.invoices import Invoice
    from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
    from app.services.recurring_service import generate_due_invoices

    due = date(2026, 10, 1)
    rec = RecurringInvoice(
        customer_id=seed_customer.id,
        frequency="monthly",
        start_date=due,
        next_due=due,
        is_active=True,
        tax_rate=Decimal("0.1"),
    )
    db_session.add(rec)
    db_session.flush()
    for order, (description, rate, taxable) in enumerate(
        [("Labor", 100, False), ("Discount", -10, True)]
    ):
        db_session.add(
            RecurringInvoiceLine(
                recurring_invoice_id=rec.id,
                description=description,
                quantity=Decimal("1"),
                rate=Decimal(rate),
                is_taxable=taxable,
                line_order=order,
            )
        )
    db_session.commit()

    skipped: list = []
    made = generate_due_invoices(db_session, as_of=due, skipped=skipped)
    assert made == []
    [skip] = skipped
    assert skip["recurring_id"] == rec.id
    assert BASE in skip["message"]
    assert db_session.query(Invoice).count() == 0
    db_session.refresh(rec)
    assert rec.next_due == due  # kept, as a $0.00 one keeps it


# -- the refusals on the way --------------------------------------------------


def test_a_negative_price_on_another_item_says_where_a_discount_goes(
    client, seed_accounts, seed_customer
):
    plain = _item(client, "Sourdough", rate=8.5)
    r = _invoice(
        client,
        seed_customer.id,
        [_line("Loaf", 100, False), _line("Off", -10, False, plain["id"], 1)],
    )
    assert r.status_code == 422, r.text
    [problem] = r.json()["detail"]
    assert problem["message"] == (
        "Line 2: A negative price is a discount, and a discount goes on an "
        'item marked "Discount or concession"; a refund belongs on a credit '
        "memo."
    )
    assert "allows_negative_price" in problem["msg"]


def test_credit_applied_to_a_voided_invoice_says_it_is_voided(
    client, seed_accounts, seed_customer
):
    r = _invoice(client, seed_customer.id, [_line("Work", 30, False)])
    assert r.status_code == 201, r.text
    inv = r.json()
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": DAY,
            "tax_rate": 0,
            "lines": [{"description": "Allowance", "quantity": 1, "rate": 30}],
        },
    )
    assert r.status_code == 201, r.text
    memo = r.json()
    r = client.post(
        f"/api/credit-memos/{memo['id']}/apply",
        json={"invoice_id": inv["id"], "amount": 30},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"Invoice {inv['invoice_number']} is voided, so no credit can be "
        "applied to it."
    )
    after = client.get(f"/api/credit-memos/{memo['id']}").json()
    assert Decimal(str(after["balance_remaining"])) == Decimal("30")


def test_the_line_schema_says_how_a_discount_is_sent():
    from app.schemas.invoices import InvoiceLineCreate

    fields = InvoiceLineCreate.model_fields
    assert "allows_negative_price" in fields["rate"].description
    assert "credit memo" in fields["rate"].description
    assert "allows_negative_price" in fields["item_id"].description


# -- the forms ----------------------------------------------------------------


def test_forms_without_discount_lines_keep_the_price_floor_in_their_source():
    """The estimate, credit memo and recurring forms take no discount line on
    the server, so they ask fillFromItem to keep the floor."""
    for name, page in (
        ("estimates.js", "EstimatesPage"),
        ("credit_memos.js", "CreditMemosPage"),
        ("recurring.js", "RecurringPage"),
    ):
        text = (ROOT / "app" / "static" / "js" / name).read_text(encoding="utf-8")
        assert (
            f"SalesLines.fillFromItem(row, {page}._items, {{ allowNegative: false }})"
            in text
        ), name
    # the invoice, sales receipt and Quick Entry forms still lift it
    for name, call in (
        ("invoices.js", "SalesLines.fillFromItem(row, InvoicesPage._items)"),
        ("sales_receipts.js", "SalesLines.fillFromItem(row, SalesReceiptsPage._items)"),
        ("app.js", "SalesLines.fillFromItem(row, App._qeItems)"),
    ):
        text = (ROOT / "app" / "static" / "js" / name).read_text(encoding="utf-8")
        assert call in text, name


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_fill_from_item_keeps_the_floor_when_asked():
    probe = r"""
const fs = require('fs'), vm = require('vm');
function input(attrs) {
  const a = Object.assign({}, attrs);
  return { value: '', placeholder: '', dataset: {},
    getAttribute: (k) => (k in a ? a[k] : null), setAttribute: (k, v) => { a[k] = String(v); },
    removeAttribute: (k) => { delete a[k]; }, attrs: a };
}
function row(itemId) {
  const rate = input({ min: '0' }), desc = input({}), tax = { checked: true, disabled: false, dataset: {} };
  return { rate, querySelector: (sel) => ({ '.line-item': { value: itemId }, '.line-desc': desc,
    '.line-rate': rate, '.line-taxable': tax })[sel] || null };
}
const items = [{ id: 5, name: 'Discount', rate: '0.00', is_discount: true }];
const ctx = { console, window: {}, document: { addEventListener: () => {} }, App: { settings: {} },
  escapeHtml: (s) => String(s), formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/item_quick_add.js', 'utf8'), ctx);
vm.runInContext(fs.readFileSync('app/static/js/invoices.js', 'utf8')
  + '\nthis.SalesLines = SalesLines;', ctx);
const kept = row('5'), lifted = row('5');
const found = !!ctx.SalesLines.fillFromItem(kept, items, { allowNegative: false });
ctx.SalesLines.fillFromItem(lifted, items);
console.log(JSON.stringify({ found,
  kept: { min: kept.rate.getAttribute('min'), placeholder: kept.rate.placeholder },
  lifted: { min: lifted.rate.getAttribute('min'), placeholder: lifted.rate.placeholder } }));
"""
    out = subprocess.run(
        ["node", "-e", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["found"] is True
    assert got["kept"] == {"min": "0", "placeholder": ""}
    assert got["lifted"] == {"min": None, "placeholder": "-10.00"}
