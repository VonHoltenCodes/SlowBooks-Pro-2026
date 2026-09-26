"""Every view of receivables and payables reads the same figures: the A/R and
A/P Aging reports. A/P Aging summed raw document-currency bill balances and
ignored bill-payment money not yet applied; analytics' A/P chart, DSO and
cash forecast, and the assistant's aging tool, did their own raw sums (found
integrating the 2.17.3 exploratory fixes). Home currency, credits netted,
and each report's total equals its control account."""

from datetime import date, timedelta
from decimal import Decimal

from app.models.contacts import Customer, Vendor


def _tb(client):
    tb = client.get(
        "/api/reports/trial-balance?start_date=2000-01-01&end_date=2099-12-31"
    ).json()
    return {i["account_number"]: Decimal(str(i["net_balance"])) for i in tb["items"]}


def _setup(client, db_session, seed_accounts):
    v = Vendor(
        name="Lumen Supply GmbH",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    c = Customer(name="Tidewater Cafe", is_active=True)
    db_session.add_all([v, c])
    db_session.commit()
    today = date.today()
    # a EUR bill booked at 1.10 and a USD bill, plus a bill payment with 50 left over
    for cur, rate, amt in (("EUR", "1.10", 1000), ("USD", None, 200)):
        body = {
            "vendor_id": v.id,
            "date": (today - timedelta(days=5)).isoformat(),
            "lines": [{"description": "Panels", "quantity": 1, "rate": amt}],
        }
        if cur != "USD":
            body.update(currency=cur, exchange_rate=rate)
        r = client.post("/api/bills", json=body)
        assert r.status_code == 201, r.text
    usd_bill = client.get("/api/bills").json()
    usd_bill = next(b for b in usd_bill if Decimal(str(b["total"])) == Decimal("200"))
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": today.isoformat(),
            "amount": 250,
            "allocations": [{"bill_id": usd_bill["id"], "amount": 200}],
        },
    )
    assert r.status_code == 201, r.text
    # a EUR invoice booked at 1.10
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": c.id,
            "date": today.isoformat(),
            "currency": "EUR",
            "exchange_rate": "1.10",
            "lines": [{"description": "Sign", "quantity": 1, "rate": 850}],
        },
    )
    assert r.status_code == 201, r.text
    return v, c


def test_ap_aging_is_in_home_currency_and_ties_to_2000(
    client, db_session, seed_accounts
):
    _setup(client, db_session, seed_accounts)
    ap = client.get("/api/reports/ap-aging").json()["totals"]
    # EUR 1000 at 1.10 = 1100 owed; the 50 paid ahead is a credit
    assert Decimal(str(ap["total"])) == Decimal("1050.00")
    assert Decimal(str(ap["unapplied_credits"])) == Decimal("50.00")
    assert Decimal(str(ap["total"])) == -_tb(client)["2000"]


def test_analytics_and_the_assistant_read_the_reports(
    client, db_session, seed_accounts
):
    from app.services.ai_tools import get_aging_report
    from app.services.analytics import AnalyticsEngine

    _setup(client, db_session, seed_accounts)
    engine = AnalyticsEngine(db_session)
    ap_chart = engine.ap_aging()
    assert sum(sum(b.values()) for b in ap_chart.values()) == 1050.0
    tool = get_aging_report(db_session)
    assert tool["total_ap_outstanding"] == 1050.0
    assert tool["total_ar_outstanding"] == 935.0  # EUR 850 at 1.10
    forecast = engine.cash_forecast(days=60)
    last = forecast[-1] if isinstance(forecast, list) else forecast
    text = str(last)
    assert "935" in text  # the EUR invoice comes in at its booked amount
