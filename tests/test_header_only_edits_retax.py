"""An edit that sends no lines but changes what the tax depends on — the rate,
or the customer — re-totals the document. An estimate kept its old tax and
total after a rate-only edit, and moving an invoice or an estimate to a
non-taxable customer without resending the lines left the lines taxed
(found integrating the 2.17.3 exploratory fixes)."""

from decimal import Decimal

from app.models.contacts import Customer


def _customers(db_session):
    shop = Customer(name="Main Street Cafe", is_active=True, is_taxable=True)
    church = Customer(name="Grace Chapel", is_active=True, is_taxable=False)
    db_session.add_all([shop, church])
    db_session.commit()
    return shop.id, church.id


LINES = [{"description": "Sign", "quantity": 1, "rate": 200, "is_taxable": True}]


def test_an_estimate_rate_only_edit_retotals(client, db_session, seed_accounts):
    shop, _ = _customers(db_session)
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": shop,
            "date": "2026-09-01",
            "tax_rate": 0.05,
            "lines": LINES,
        },
    ).json()
    assert Decimal(str(est["total"])) == Decimal("210.00")
    r = client.put(f"/api/estimates/{est['id']}", json={"tax_rate": 0.10})
    assert r.status_code == 200, r.text
    assert Decimal(str(r.json()["tax_amount"])) == Decimal("20.00")
    assert Decimal(str(r.json()["total"])) == Decimal("220.00")


def test_an_estimate_moved_to_an_exempt_customer_loses_its_tax(
    client, db_session, seed_accounts
):
    shop, church = _customers(db_session)
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": shop,
            "date": "2026-09-01",
            "tax_rate": 0.05,
            "lines": LINES,
        },
    ).json()
    r = client.put(f"/api/estimates/{est['id']}", json={"customer_id": church})
    assert r.status_code == 200, r.text
    assert Decimal(str(r.json()["tax_amount"])) == Decimal("0")
    assert Decimal(str(r.json()["total"])) == Decimal("200.00")


def test_an_invoice_moved_to_an_exempt_customer_loses_its_tax(
    client, db_session, seed_accounts
):
    shop, church = _customers(db_session)
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": shop,
            "date": "2026-09-01",
            "tax_rate": 0.05,
            "lines": LINES,
        },
    ).json()
    assert Decimal(str(inv["total"])) == Decimal("210.00")
    r = client.put(f"/api/invoices/{inv['id']}", json={"customer_id": church})
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(str(body["tax_amount"])) == Decimal("0")
    assert Decimal(str(body["total"])) == Decimal("200.00")
    assert all(line["is_taxable"] is False for line in body["lines"])
    tb = client.get(
        "/api/reports/trial-balance?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    net = {i["account_number"]: Decimal(str(i["net_balance"])) for i in tb["items"]}
    assert net.get("2200", Decimal("0")) == Decimal(
        "0"
    )  # the reposted entry has no tax
    assert net["1100"] == Decimal("200.00")
