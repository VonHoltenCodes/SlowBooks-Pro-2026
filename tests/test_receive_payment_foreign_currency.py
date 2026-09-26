"""A foreign-currency invoice can be paid from Receive Payment. The server
took a payment's currency and exchange rate, and refused mixing currencies,
but the form never sent either, so a EUR invoice could only be paid through
the API (found integrating the 2.17.3 exploratory fixes). The form now
offers the customer's invoice currencies, lists one currency's invoices at a
time, and sends that currency with the rate for the payment date."""

from decimal import Decimal
from pathlib import Path

from app.models.contacts import Customer

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/payments.js").read_text(encoding="utf-8")


def test_the_form_sends_the_currency_and_rate_it_shows():
    save = JS[JS.index("    async save(e) {") :]
    save = save[: save.index("\n    },")]
    assert "currency: PaymentsPage._currency" in save
    assert "exchange_rate:" in save


def test_the_form_offers_the_customers_invoice_currencies():
    load = JS[JS.index("    async loadInvoices(customerId) {") :]
    load = load[: load.index("\n    },")]
    assert "PaymentsPage._currencies" in load
    assert 'name="pay_currency"' in JS
    assert 'name="pay_exchange_rate"' in JS
    # one currency's invoices at a time: the server refuses a mix
    assert "(i.currency || PaymentsPage._home) === PaymentsPage._currency" in JS


def test_a_eur_invoice_is_paid_with_the_payload_the_form_sends(
    client, db_session, seed_accounts
):
    cust = Customer(name="Bäckerei Müller", is_active=True)
    db_session.add(cust)
    db_session.commit()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cust.id,
            "date": "2026-09-01",
            "currency": "EUR",
            "exchange_rate": "1.10",
            "lines": [{"description": "Sign", "quantity": 1, "rate": 850}],
        },
    ).json()
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cust.id,
            "date": "2026-09-20",
            "amount": 850,
            "currency": "EUR",
            "exchange_rate": 1.12,
            "allocations": [{"invoice_id": inv["id"], "amount": 850}],
        },
    )
    assert r.status_code == 201, r.text
    paid = client.get(f"/api/invoices/{inv['id']}").json()
    assert Decimal(str(paid["balance_due"])) == Decimal("0")
    assert paid["status"] == "paid"
