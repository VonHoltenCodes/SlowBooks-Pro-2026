"""Income by Customer's row adds up on each basis, and a paid security
deposit is in it on the cash basis (W-204, W-205, 2.32.0 gate round 23).

- W-205: on the cash basis a tenant who paid 2,500.00 (1,500.00 rent and a
  1,000.00 security deposit) showed Sales 1,500.00, Not income 0.00 and
  Paid 1,500.00: the deposit was in no column. The cash lines a payment
  writes onto the deposit's liability are now Not income, and in Paid.
- W-204: on the cash basis Sales + Not income + Tax − Paid was not the
  Balance (a customer billed 300.00 who paid 100.00: 100 / 0 / 0 / 100,
  Balance 200). The columns mean different things on the two bases, and
  the report says which sum a row holds:
    accrual: Sales + Not income + Sales Tax − Paid = Balance;
    cash:    Sales + Not income + Sales Tax = Paid (the money received in
             the dates), and "Balance owed now" stands apart.

The books are round 22's (invoices, partial payments, money not applied,
credit memos open, applied and voided, a voided invoice, a deposit item, a
sales receipt and a register deposit) plus the round's tenant and partly
paid customer and a voided payment. For every period and both bases each
row holds its sum, Sales is the P&L's income, and the trial balance and
Books Check agree."""

import pytest

from app.models.contacts import Customer
from tests.test_cash_basis import _invoice, _payment
from tests.test_customer_item_reports_tie_r22 import (  # noqa: F401 (fixture)
    PERIODS,
    _get,
    _ok,
    books,
)
from tests.test_void_revenue_cost_codes_new262 import books_agree

OCT = PERIODS["oct"]


@pytest.fixture
def more(client, db_session, books):  # noqa: F811
    tenant = Customer(name="Tenant One", is_active=True)
    partly = Customer(name="Partly Paid", is_active=True)
    db_session.add_all([tenant, partly])
    db_session.commit()
    lease = _invoice(
        client,
        tenant.id,
        "2026-10-01",
        [
            {
                "item_id": books["item"],
                "quantity": 1,
                "rate": 1500,
                "is_taxable": False,
            },
            {"item_id": books["deposit"], "quantity": 1, "rate": 1000},
        ],
    )
    _payment(client, tenant.id, "2026-10-05", 2500, [(lease["id"], 2500)])
    owed = _invoice(
        client,
        partly.id,
        "2026-10-02",
        [{"item_id": books["item"], "quantity": 1, "rate": 300, "is_taxable": True}],
        tax_rate=0.1,
    )
    _payment(client, partly.id, "2026-10-10", 110, [(owed["id"], 110)])
    # a payment taken and voided counts for nothing
    gone = _payment(client, partly.id, "2026-10-12", 50, [(owed["id"], 50)])
    _ok(client.post(f"/api/payments/{gone['id']}/void", json={}))
    return {**books, "tenant": tenant.id, "partly": partly.id}


def _rows_hold(data):
    for r in data["items"]:
        parts = r["total_sales"] + r["total_other"] + r["total_tax"]
        if data["basis"] == "cash":
            assert round(parts, 2) == round(r["total_paid"], 2), r
        else:
            assert round(parts - r["total_paid"], 2) == round(r["total_balance"], 2), r
    for k in ("total_sales", "total_other", "total_tax", "total_paid"):
        assert round(sum(r[k] for r in data["items"]), 2) == round(data[k], 2), k


@pytest.mark.parametrize("basis", ["accrual", "cash"])
def test_every_row_holds_its_sum_and_sales_is_the_pl(client, more, basis):
    for name, qs in PERIODS.items():
        data = _get(client, "income-by-customer", qs, basis)
        assert data["basis"] == basis
        _rows_hold(data)
        income = _get(client, "profit-loss", qs, basis)["total_income"]
        assert data["total_sales"] == income, (basis, name)
        if basis == "cash":
            assert data["balance_heading"] == "Balance owed now"
            assert "= Paid" in data["row_rule"] and "− Paid" not in data["row_rule"]
        else:
            assert data["balance_heading"] == "Balance"
            assert "− Paid = Balance" in data["row_rule"]
    books_agree(client)


def test_the_tenant_and_the_partly_paid_customer_in_october(client, more):
    cash = _get(client, "income-by-customer", OCT, "cash")
    rows = {r["customer_id"]: r for r in cash["items"]}
    tenant = rows[more["tenant"]]
    # W-205: the whole 2,500.00 is in the row
    assert (tenant["total_sales"], tenant["total_other"], tenant["total_paid"]) == (
        1500.0,
        1000.0,
        2500.0,
    )
    assert tenant["total_balance"] == 0.0
    # W-204: 110 of 330 paid; the 50 voided counts for nothing
    partly = rows[more["partly"]]
    assert (partly["total_sales"], partly["total_tax"], partly["total_paid"]) == (
        100.0,
        10.0,
        110.0,
    )
    assert partly["total_balance"] == 220.0
    accrual = _get(client, "income-by-customer", OCT, "accrual")
    rows = {r["customer_id"]: r for r in accrual["items"]}
    assert rows[more["tenant"]]["total_other"] == 1000.0
    assert rows[more["tenant"]]["total_paid"] == 2500.0
    assert (
        rows[more["partly"]]["total_paid"],
        rows[more["partly"]]["total_balance"],
    ) == (
        110.0,
        220.0,
    )
    # November's cash: the rent with its deposit (1,500 of it the deposit's
    # 1,000 share by proportion: 500 rent + 1,000 deposit paid in full)
    nov = _get(client, "income-by-customer", PERIODS["nov"], "cash")
    other = {r["customer_id"]: r for r in nov["items"]}[more["other"]]
    assert other["total_other"] == 1000.0 and other["total_paid"] == 1500.0


def test_the_pdf_and_csv_say_which_sum(client, more):
    from app.services.ledger_exports import income_by_customer_sections

    for basis in ("accrual", "cash"):
        data = _get(client, "income-by-customer", OCT, basis)
        (section,) = income_by_customer_sections(data)
        assert section["note"] == data["row_rule"]
        assert section["columns"][-1] == data["balance_heading"]
        r = client.get(f"/api/reports/income-by-customer/csv?{OCT}&basis={basis}")
        assert r.status_code == 200
        lines = r.text.splitlines()
        assert any(ln.startswith("Note,") and "Paid" in ln for ln in lines[:5])
        assert data["balance_heading"] in r.text
