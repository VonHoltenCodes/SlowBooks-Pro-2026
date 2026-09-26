"""Pledges: generated installments link to their template, the pledge
report reconciles, a write-off posts to Bad Debt Expense and applies
itself, and voiding the credit memo puts the balance back."""

from decimal import Decimal

from app.models.transactions import Transaction, TransactionLine


def _nonprofit(client):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    client.post("/api/nonprofit/setup-accounts")


def _monthly_pledge(client, customer_id, amount="100", class_id=None):
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": customer_id,
            "frequency": "monthly",
            "start_date": "2026-01-01",
            "class_id": class_id,
            "lines": [{"description": "Monthly pledge", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _generate(client, as_of):
    r = client.post(f"/api/recurring/generate?as_of={as_of}")
    assert r.status_code in (200, 201), r.text
    return r.json()["invoice_ids"]


def test_pledge_report_reconciles_through_payment_write_off_and_void(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    campaign = client.post("/api/classes", json={"name": "Annual Fund"}).json()
    rec = _monthly_pledge(client, seed_customer.id, class_id=campaign["id"])
    jan = _generate(client, "2026-01-15")
    feb = _generate(client, "2026-02-15")
    assert len(jan) == 1 and len(feb) == 1
    jan_inv, feb_inv = jan[0], feb[0]
    got = client.get(f"/api/invoices/{jan_inv}").json()
    assert got["recurring_invoice_id"] == rec["id"] and got["is_pledge"] is True

    # pay January
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-01-20",
            "amount": "100",
            "allocations": [{"invoice_id": jan_inv, "amount": "100"}],
        },
    )
    assert r.status_code in (200, 201), r.text

    qs = "start_date=2026-01-01&end_date=2026-02-28"
    rep = client.get(f"/api/reports/pledges?{qs}").json()
    t = rep["totals"]
    assert t == {
        "pledged": 200.0,
        "invoiced": 200.0,
        "not_yet_invoiced": 0.0,
        "received": 100.0,
        "written_off": 0.0,
        "outstanding": 100.0,
    }
    # a longer window promises more than was invoiced
    year = client.get(
        "/api/reports/pledges?start_date=2026-01-01&end_date=2026-12-31"
    ).json()["totals"]
    assert year["pledged"] == 1200.0 and year["not_yet_invoiced"] == 1000.0
    assert rep["by_donor"][0]["customer_name"] == "Test Customer"
    assert rep["by_class"][0]["class_name"] == "Annual Fund"
    for c in ("pledged", "received", "outstanding"):
        assert (
            sum(g[c] for g in rep["by_class"])
            == sum(g[c] for g in rep["by_donor"])
            == t[c]
        )

    # write off February: credit memo to Bad Debt, applied at once
    r = client.post(f"/api/invoices/{feb_inv}/write-off", json={"date": "2026-03-01"})
    assert r.status_code == 201, r.text
    cm = r.json()
    assert cm["is_write_off"] is True and cm["status"] == "applied"
    assert (
        Decimal(cm["total"]) == Decimal("100") and Decimal(cm["balance_remaining"]) == 0
    )
    inv = client.get(f"/api/invoices/{feb_inv}").json()
    assert inv["status"] == "paid" and Decimal(inv["balance_due"]) == 0
    txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "credit_memo", Transaction.source_id == cm["id"]
        )
        .one()
    )
    from app.models.accounts import Account

    bad_debt = db_session.query(Account).filter_by(name="Bad Debt Expense").one()
    by_acct = {ln.account_id: ln for ln in txn.lines}
    assert by_acct[bad_debt.id].debit == Decimal("100")
    assert sum(ln.debit for ln in txn.lines) == sum(ln.credit for ln in txn.lines)

    rep = client.get(f"/api/reports/pledges?{qs}").json()["totals"]
    assert (
        rep["written_off"] == 100.0
        and rep["outstanding"] == 0.0
        and rep["received"] == 100.0
    )

    # nothing left to write off; a paid invoice refuses
    assert (
        client.post(
            f"/api/invoices/{feb_inv}/write-off", json={"date": "2026-03-02"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/invoices/{jan_inv}/write-off", json={"date": "2026-03-02"}
        ).status_code
        == 400
    )

    # void the write-off: balance comes back, reversal balanced
    r = client.post(f"/api/credit-memos/{cm['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    inv = client.get(f"/api/invoices/{feb_inv}").json()
    assert inv["status"] == "sent" and Decimal(inv["balance_due"]) == Decimal("100")
    rev = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "credit_memo_void",
            Transaction.source_id == cm["id"],
        )
        .one()
    )
    assert {ln.account_id: ln.credit for ln in rev.lines}[bad_debt.id] == Decimal("100")
    dr = sum(Decimal(str(x[0])) for x in db_session.query(TransactionLine.debit).all())
    cr = sum(Decimal(str(x[0])) for x in db_session.query(TransactionLine.credit).all())
    assert dr == cr
    rep = client.get(f"/api/reports/pledges?{qs}").json()["totals"]
    assert rep["written_off"] == 0.0 and rep["outstanding"] == 100.0
    assert client.post(f"/api/credit-memos/{cm['id']}/void").status_code == 400
    # partial write-off is allowed and the amount is capped
    assert (
        client.post(
            f"/api/invoices/{feb_inv}/write-off",
            json={"date": "2026-03-03", "amount": "150"},
        ).status_code
        == 400
    )
    r = client.post(
        f"/api/invoices/{feb_inv}/write-off",
        json={"date": "2026-03-03", "amount": "40"},
    )
    assert (
        r.status_code == 201
        and client.get(f"/api/invoices/{feb_inv}").json()["status"] == "partial"
    )


def test_one_off_pledge_and_exports(client, seed_accounts, seed_customer):
    _nonprofit(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": "0",
            "is_pledge": True,
            "lines": [
                {
                    "description": "Capital campaign pledge",
                    "quantity": 1,
                    "rate": "5000",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    qs = "start_date=2026-01-01&end_date=2026-12-31"
    rep = client.get(f"/api/reports/pledges?{qs}").json()
    assert rep["totals"]["pledged"] == 5000.0 and rep["totals"]["outstanding"] == 5000.0
    assert rep["by_donor"][0]["pledges"][0]["label"].startswith("Pledge ")
    pdf = client.get(f"/api/reports/pledges/pdf?{qs}")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    csv_r = client.get(f"/api/reports/pledges/csv?{qs}")
    # utf-8-sig: every CSV opens with the byte-order mark Excel needs
    assert csv_r.status_code == 200 and csv_r.content.decode("utf-8-sig").startswith(
        "Donor,Pledge,Campaign,Pledged"
    )
    assert client.post(
        "/api/saved-reports",
        json={"name": "p", "report_type": "pledges", "parameters": {}},
    ).status_code in (200, 201)
    # a plain invoice is not a pledge
    client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-02",
            "tax_rate": "0",
            "is_pledge": False,
            "lines": [{"description": "Hall rental", "quantity": 1, "rate": "300"}],
        },
    )
    assert (
        client.get(f"/api/reports/pledges?{qs}").json()["totals"]["pledged"] == 5000.0
    )
