"""Reports → Sales Tax (2.17.3 exploratory test, macbase1 F16).

The report listed invoices and sales receipts but never credit memos, so it
disagreed with Sales Tax Payable (2200) by the tax every return gave back —
"three screens, three sales-tax numbers". An invoice with no taxable line
was listed as "8.25%, $0.00", as if the rate had applied. The report now
nets credit memos, shows a rate only where there is a taxable amount, and
states the tax it totals against what the ledger holds in 2200.
"""

from decimal import Decimal
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
PERIOD = "start_date=2026-07-01&end_date=2026-07-31"


def _item(client, name, rate, taxable):
    r = client.post(
        "/api/items",
        json={
            "name": name,
            "item_type": "service",
            "rate": rate,
            "is_taxable": taxable,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _sales(client, customer_id):
    part = _item(client, "Neon sign", 100, True)
    labor = _item(client, "Install labor", 200, False)
    taxed = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-07-02",
            "tax_rate": 0.0825,
            "lines": [{"item_id": part["id"], "quantity": 1, "rate": 100}],
        },
    )
    assert taxed.status_code == 201, taxed.text
    untaxed = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-07-03",
            "tax_rate": 0.0825,
            "lines": [{"item_id": labor["id"], "quantity": 1, "rate": 200}],
        },
    )
    assert untaxed.status_code == 201, untaxed.text
    cm = client.post(
        "/api/credit-memos",
        json={
            "customer_id": customer_id,
            "date": "2026-07-10",
            "tax_rate": 0.0825,
            "original_invoice_id": taxed.json()["id"],
            "lines": [{"description": "Returned sign", "quantity": 1, "rate": 20}],
        },
    )
    assert cm.status_code == 201, cm.text
    return taxed.json(), untaxed.json(), cm.json()


def test_credit_memos_are_netted(client, seed_accounts, seed_customer):
    taxed, untaxed, cm = _sales(client, seed_customer.id)
    rep = client.get(f"/api/reports/sales-tax?{PERIOD}").json()

    assert Decimal(str(cm["tax_amount"])) == Decimal("1.65")
    assert rep["total_tax"] == 6.60  # 8.25 on the sign, 1.65 back on the return
    assert rep["total_sales"] == 280.0  # 100 + 200 - 20
    assert rep["total_taxable"] == 80.0  # 100 - 20
    assert rep["total_non_taxable"] == 200.0
    assert rep["tax_on_sales"] == 8.25
    assert rep["tax_credited"] == 1.65

    memo = next(i for i in rep["items"] if i["type"] == "credit_memo")
    assert memo["number"] == cm["memo_number"]
    assert memo["subtotal"] == -20.0 and memo["tax_amount"] == -1.65


def test_a_rate_shows_only_where_something_was_taxed(
    client, seed_accounts, seed_customer
):
    taxed, untaxed, _ = _sales(client, seed_customer.id)
    rep = client.get(f"/api/reports/sales-tax?{PERIOD}").json()
    by_number = {i["number"]: i for i in rep["items"]}
    assert by_number[taxed["invoice_number"]]["tax_rate"] == 0.0825
    assert by_number[taxed["invoice_number"]]["taxable"] == 100.0
    labour = by_number[untaxed["invoice_number"]]
    assert labour["tax_rate"] is None and labour["taxable"] == 0.0


def test_the_report_reconciles_to_sales_tax_payable(
    client, seed_accounts, seed_customer
):
    _sales(client, seed_customer.id)
    rep = client.get(f"/api/reports/sales-tax?{PERIOD}").json()
    ledger = rep["ledger"]
    assert ledger["account_number"] == "2200"
    assert ledger["tax_posted"] == 6.60
    assert ledger["difference"] == 0.0
    assert ledger["balance"] == 6.60

    paid = client.post(
        "/api/reports/sales-tax/pay",
        json={
            "date": "2026-07-31",
            "amount": 6.60,
            "pay_from_account_id": seed_accounts["1000"].id,
        },
    )
    assert paid.status_code == 200, paid.text
    ledger = client.get(f"/api/reports/sales-tax?{PERIOD}").json()["ledger"]
    assert ledger["payments"] == 6.60
    assert ledger["tax_posted"] == 6.60  # a payment is not tax collected
    assert ledger["balance"] == 0.0


def test_a_posting_the_report_cannot_see_shows_as_a_difference(
    client, db_session, seed_accounts, seed_customer
):
    from datetime import date

    from app.services.accounting import create_journal_entry

    _sales(client, seed_customer.id)
    # tax debited to 2200 from somewhere other than a sale or a credit memo
    create_journal_entry(
        db_session,
        date(2026, 7, 20),
        "Bill with tax",
        [
            {"account_id": seed_accounts["2200"].id, "debit": 3, "credit": 0},
            {"account_id": seed_accounts["2000"].id, "debit": 0, "credit": 3},
        ],
    )
    db_session.commit()
    ledger = client.get(f"/api/reports/sales-tax?{PERIOD}").json()["ledger"]
    assert ledger["tax_posted"] == 3.60
    assert ledger["difference"] == 3.0


def test_the_screen_shows_credit_memos_the_dash_and_the_reconciliation():
    js = (JS / "reports.js").read_text(encoding="utf-8")
    body = js[js.index("async salesTax(prefill)") :]
    body = body[: body.index("async generalLedger(")]
    assert "i.tax_rate == null ? '—'" in body
    assert "escapeHtml(i.number)" in body
    assert "data.tax_credited" in body
    assert "data.ledger" in body
