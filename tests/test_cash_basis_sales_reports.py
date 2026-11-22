"""The sales reports honour the cash basis (#1062).

Reported by a bookkeeper testing 2.31.14: on a company set to cash basis
the sales by customer reports ran on the accrual basis only. Income by
Customer and Sales by Item took no basis at all and counted every invoice
dated in the period in full, while the P&L (#483) counted a sale when it
was paid. Both now take `basis` (the company default when left out) on
screen, and Sales by Item in its lines, PDF and CSV too. On the cash basis a
sale counts when it is paid and in the proportion paid, read from the cash
lines the payments wrote, so the reports tie to the cash P&L."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.models.items import Item, ItemType
from tests.test_cash_basis import _invoice, _payment
from tests.test_inventory import _create_bill_for_inventory, _seed_vendor

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
OCT = "start_date=2026-10-01&end_date=2026-10-31"
NOV = "start_date=2026-11-01&end_date=2026-11-30"


def _get(client, path, qs, basis=None):
    url = f"/api/reports/{path}?{qs}" + (f"&basis={basis}" if basis else "")
    r = client.get(url)
    assert r.status_code == 200, r.text
    return r.json()


def _cash_company(client):
    assert client.put("/api/settings", json={"report_basis": "cash"}).status_code == 200


@pytest.fixture
def sale(client, seed_accounts, seed_customer):
    """An October invoice: 3 of an item at $200 and a $400 line with no
    item, 10% tax ($1,100), half paid in October and half in November."""
    item = client.post(
        "/api/items",
        json={"name": "Gutter cleaning", "item_type": "service", "rate": 200},
    ).json()
    inv = _invoice(
        client,
        seed_customer.id,
        "2026-10-01",
        [
            {"item_id": item["id"], "quantity": 3, "rate": 200, "is_taxable": True},
            {"rate": 400, "is_taxable": True},
        ],
        tax_rate=0.1,
    )
    assert Decimal(str(inv["total"])) == Decimal("1100.00")
    _payment(client, seed_customer.id, "2026-10-20", 550, [(inv["id"], 550)])
    _payment(client, seed_customer.id, "2026-11-05", 550, [(inv["id"], 550)])
    return {"item": item, "invoice": inv}


def test_a_cash_basis_company_counts_a_sale_when_it_is_paid(client, sale):
    _cash_company(client)
    pl = _get(client, "profit-loss", OCT)
    assert pl["basis"] == "cash" and pl["total_income"] == 500.0

    ibc = _get(client, "income-by-customer", OCT)
    assert ibc["basis"] == "cash"
    (row,) = ibc["items"]
    assert (row["invoice_count"], row["total_sales"], row["total_tax"]) == (
        1,
        500.0,
        50.0,
    )
    assert row["total_paid"] == 550.0
    assert ibc["total_sales"] == pl["total_income"]

    sbi = _get(client, "sales-by-item", OCT)
    assert sbi["basis"] == "cash"
    rows = {r["item_id"]: r for r in sbi["items"]}
    assert (
        rows[sale["item"]["id"]]["amount"],
        rows[sale["item"]["id"]]["quantity"],
    ) == (
        300.0,
        1.5,
    )
    assert rows[None]["amount"] == 200.0
    assert sbi["total_amount"] == pl["total_income"]

    # the rest in November, when it was paid
    nov = _get(client, "sales-by-item", NOV)
    assert (
        nov["total_amount"] == 500.0 == _get(client, "profit-loss", NOV)["total_income"]
    )
    assert _get(client, "income-by-customer", NOV)["total_sales"] == 500.0


def test_the_per_report_switch_overrides_the_company_default(client, sale):
    # an accrual company: the reports as they were, unless asked for cash
    assert _get(client, "income-by-customer", OCT)["total_sales"] == 1000.0
    assert _get(client, "sales-by-item", OCT)["total_amount"] == 1000.0
    assert _get(client, "sales-by-item", OCT)["basis"] == "accrual"
    assert _get(client, "income-by-customer", OCT, "cash")["total_sales"] == 500.0
    assert _get(client, "sales-by-item", OCT, "cash")["total_amount"] == 500.0
    # a cash company asked for accrual
    _cash_company(client)
    accrual = _get(client, "income-by-customer", OCT, "accrual")
    assert (accrual["basis"], accrual["total_sales"], accrual["total_tax"]) == (
        "accrual",
        1000.0,
        100.0,
    )
    assert _get(client, "sales-by-item", OCT, "accrual")["total_amount"] == 1000.0


def test_the_lines_pdf_and_csv_follow_the_basis(client, sale):
    from app.services.ledger_exports import sales_by_item_sections

    _cash_company(client)
    item_id = sale["item"]["id"]
    lines = _get(client, "sales-by-item/lines", f"{OCT}&item_id={item_id}")
    assert lines["basis"] == "cash"
    assert [(ln["quantity"], ln["amount"]) for ln in lines["lines"]] == [(1.5, 300.0)]
    accrual = _get(client, "sales-by-item/lines", f"{OCT}&item_id={item_id}", "accrual")
    assert [ln["amount"] for ln in accrual["lines"]] == [600.0]

    csv = client.get(f"/api/reports/sales-by-item/csv?{OCT}")
    assert csv.status_code == 200
    assert "(cash basis)" in csv.text and "TOTAL,,1.5,500.00" in csv.text
    assert (
        "(cash basis)"
        not in client.get(f"/api/reports/sales-by-item/csv?{OCT}&basis=accrual").text
    )

    pdf = client.get(f"/api/reports/sales-by-item/pdf?{OCT}")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    data = _get(client, "sales-by-item", OCT)
    assert sales_by_item_sections(data)[0]["period"].endswith("(cash basis)")


def test_a_voided_payment_and_a_credit_memo_count_as_on_the_cash_pl(
    client, seed_accounts, seed_customer
):
    _cash_company(client)
    inv = _invoice(client, seed_customer.id, "2026-10-01", [{"rate": 800}])
    pay = _payment(client, seed_customer.id, "2026-10-10", 800, [(inv["id"], 800)])
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-12",
            "tax_rate": 0,
            "lines": [{"description": "Refund", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    pl = _get(client, "profit-loss", OCT)
    sbi = _get(client, "sales-by-item", OCT)
    ibc = _get(client, "income-by-customer", OCT)
    assert pl["total_income"] == 0.0
    assert sbi["total_amount"] == 0.0 and sbi["items"] == []
    assert ibc["total_sales"] == 0.0


def test_money_not_yet_applied_is_the_customers_income_on_the_cash_basis(
    client, seed_accounts, seed_customer
):
    _cash_company(client)
    _payment(client, seed_customer.id, "2026-10-10", 250)
    pl = _get(client, "profit-loss", OCT)
    ibc = _get(client, "income-by-customer", OCT)
    assert pl["total_income"] == 250.0
    (row,) = ibc["items"]
    assert (row["total_sales"], row["total_paid"], row["total_balance"]) == (
        250.0,
        250.0,
        -250.0,
    )
    # on the accrual basis it is money paid, not a sale, as before
    accrual = _get(client, "income-by-customer", OCT, "accrual")
    assert (accrual["total_sales"], accrual["total_paid"]) == (0.0, 250.0)


def test_a_stock_items_cost_counts_in_the_part_paid(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _seed_vendor(db_session)
    widget = Item(
        name="Widget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("25"),
        cost=Decimal("10"),
        income_account_id=seed_accounts["4000"].id,
        asset_account_id=seed_accounts["1300"].id,
        track_inventory=True,
        is_taxable=False,
    )
    db_session.add(widget)
    db_session.commit()
    _create_bill_for_inventory(client, vendor.id, widget.id, 10, 10)
    inv = _invoice(
        client,
        seed_customer.id,
        "2026-04-15",
        [{"item_id": widget.id, "quantity": 4, "rate": 25}],
    )
    _payment(client, seed_customer.id, "2026-04-20", 50, [(inv["id"], 50)])
    _cash_company(client)
    april = "start_date=2026-04-01&end_date=2026-04-30"
    pl = _get(client, "profit-loss", april)
    sbi = _get(client, "sales-by-item", april)
    (row,) = sbi["items"]
    assert (row["quantity"], row["amount"], row["cogs"]) == (2.0, 50.0, 20.0)
    assert sbi["total_amount"] == pl["total_income"]
    assert sbi["total_cogs"] == pl["total_cogs"]
    accrual = _get(client, "sales-by-item", april, "accrual")
    assert (accrual["items"][0]["amount"], accrual["items"][0]["cogs"]) == (100.0, 40.0)


def test_an_unknown_basis_is_refused_in_the_pls_words(client, seed_accounts):
    for path in (
        "income-by-customer",
        "sales-by-item",
        "sales-by-item/csv",
        "sales-by-item/pdf",
    ):
        r = client.get(f"/api/reports/{path}?{OCT}&basis=modified")
        assert r.status_code == 422, path
        assert r.json()["detail"] == "basis must be accrual, cash or left out"


def test_the_reports_page_offers_the_basis_switch_on_both():
    src = (JS / "reports.js").read_text(encoding="utf-8")
    ibc = src[src.index("async incomeByCustomer(prefill) {") :]
    ibc = ibc[: ibc.index("\n    },")]
    assert "const basisOpts = ReportsPage._basisOpts(prefill);" in ibc
    assert "${ReportsPage._basisQs(p)}" in ibc
    assert "toolbar: basisOpts.toolbar" in ibc and "...basisOpts.params" in ibc
    assert "ReportsPage._basisWords(data)" in ibc
    sbi = src[src.index("async salesByItem(prefill) {") :]
    sbi = sbi[: sbi.index("\n    },")]
    assert "const basisOpts = ReportsPage._basisOpts(prefill);" in sbi
    assert "+ basisOpts.toolbar;" in sbi
    # one query string for the screen, the lines, the PDF and the CSV
    assert (
        "ReportsPage._itemQs(range, p, 'customer_id') + ReportsPage._basisQs(p);" in sbi
    )
    assert "ReportsPage._exportButtons('sales-by-item', qs)" in sbi
    assert "ReportsPage._basisWords(data)" in sbi
