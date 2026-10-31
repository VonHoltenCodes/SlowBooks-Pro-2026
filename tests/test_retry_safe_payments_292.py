"""Retry-safe request identity for manual payment recording (#292).

A POST that commits and then loses its response is retried and books the
payment twice; the over-allocation guard cannot tell a replay from a
second payment while balance remains. An Idempotency-Key header names
the intended payment: the same key with the same body answers with the
first response, the same key with another body is refused, a first
delivery still running is refused until it finishes, and no header is an
ordinary POST, as before.
"""

import json
from pathlib import Path

from app.models.payments import Payment
from app.models.request_keys import RequestKey
from app.models.transactions import Transaction

ROOT = Path(__file__).resolve().parents[1]
KEY = {"Idempotency-Key": "pay-2026-10-13-001"}


def _invoice(client, customer_id, rate="1000.00"):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-09-10",
            "lines": [{"description": "work", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _body(customer_id, inv, amount="100.00"):
    return {
        "customer_id": customer_id,
        "date": "2026-09-12",
        "amount": amount,
        "allocations": [{"invoice_id": inv["id"], "amount": amount}],
    }


def test_the_same_key_and_body_books_one_payment(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    first = client.post("/api/payments", json=_body(seed_customer.id, inv), headers=KEY)
    assert first.status_code == 201, first.text
    again = client.post("/api/payments", json=_body(seed_customer.id, inv), headers=KEY)
    assert again.status_code == 201, again.text
    assert again.json() == first.json()
    assert (
        again.headers.get("X-Replayed") == "true" and "X-Replayed" not in first.headers
    )
    assert db_session.query(Payment).count() == 1
    assert db_session.query(Transaction).filter_by(source_type="payment").count() == 1
    assert client.get(f"/api/invoices/{inv['id']}").json()["balance_due"] == "900.00"
    row = db_session.query(RequestKey).one()
    assert row.route == "POST /api/payments" and row.key == KEY["Idempotency-Key"]
    assert row.status_code == 201 and row.actor  # who sent it


def test_the_same_key_with_another_body_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    assert (
        client.post(
            "/api/payments", json=_body(seed_customer.id, inv), headers=KEY
        ).status_code
        == 201
    )
    other = client.post(
        "/api/payments", json=_body(seed_customer.id, inv, "250.00"), headers=KEY
    )
    assert other.status_code == 409, other.text
    assert "already used for a different request" in other.json()["detail"]
    assert db_session.query(Payment).count() == 1


def test_without_a_key_a_second_post_is_a_second_payment(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    for _ in range(2):
        assert (
            client.post("/api/payments", json=_body(seed_customer.id, inv)).status_code
            == 201
        )
    assert db_session.query(Payment).count() == 2
    assert db_session.query(RequestKey).count() == 0


def test_two_keys_are_two_payments_of_the_same_amount(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    for key in ("k-1", "k-2"):
        r = client.post(
            "/api/payments",
            json=_body(seed_customer.id, inv),
            headers={"Idempotency-Key": key},
        )
        assert r.status_code == 201, r.text
    assert db_session.query(Payment).count() == 2


def test_a_first_delivery_still_running_is_refused_until_it_finishes(
    client, db_session, seed_accounts, seed_customer
):
    from app.schemas.payments import PaymentCreate
    from app.services.request_keys import payload_hash

    inv = _invoice(client, seed_customer.id)
    body = _body(seed_customer.id, inv)
    # the twin's reservation, response not yet stored (as during its write);
    # hashed the way the route hashes: the validated body, every field
    digest = payload_hash(PaymentCreate(**body).model_dump())
    db_session.add(
        RequestKey(
            route="POST /api/payments", key=KEY["Idempotency-Key"], payload_hash=digest
        )
    )
    db_session.commit()
    r = client.post("/api/payments", json=body, headers=KEY)
    assert r.status_code == 409 and "being processed" in r.json()["detail"]
    assert db_session.query(Payment).count() == 0


def test_a_failed_write_releases_the_key(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    body = _body(seed_customer.id, inv, "2000.00")  # over the invoice: refused
    r = client.post("/api/payments", json=body, headers=KEY)
    assert r.status_code == 400, r.text
    assert db_session.query(RequestKey).count() == 0  # rolled back with the write
    good = client.post("/api/payments", json=_body(seed_customer.id, inv), headers=KEY)
    assert good.status_code == 201, good.text


def test_a_bad_key_is_a_400(client, seed_accounts, seed_customer):
    inv = _invoice(client, seed_customer.id)
    r = client.post(
        "/api/payments",
        json=_body(seed_customer.id, inv),
        headers={"Idempotency-Key": "no spaces here"},
    )
    assert r.status_code == 400 and "Idempotency-Key must be" in r.json()["detail"]


def test_bill_payments_too(client, db_session, seed_accounts):
    from app.models.bills import BillPayment

    v = client.post("/api/vendors", json={"name": "Pier Lumber"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-10",
            "lines": [
                {
                    "description": "Lumber",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 1,
                    "rate": "300.00",
                    "line_order": 0,
                }
            ],
        },
    ).json()
    body = {
        "vendor_id": v["id"],
        "date": "2026-09-12",
        "amount": "100.00",
        "pay_from_account_id": seed_accounts["1000"].id,
        "allocations": [{"bill_id": bill["id"], "amount": "100.00"}],
    }
    key = {"Idempotency-Key": "bp-7"}
    first = client.post("/api/bill-payments", json=body, headers=key)
    again = client.post("/api/bill-payments", json=body, headers=key)
    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json() and again.headers.get("X-Replayed") == "true"
    assert db_session.query(BillPayment).count() == 1
    assert client.get(f"/api/bills/{bill['id']}").json()["balance_due"] == "200.00"
    other = client.post(
        "/api/bill-payments",
        json={
            **body,
            "amount": "50.00",
            "allocations": [{"bill_id": bill["id"], "amount": "50.00"}],
        },
        headers=key,
    )
    assert other.status_code == 409


def test_old_keys_are_swept(client, db_session, seed_accounts, seed_customer):
    from datetime import datetime, timedelta

    from app.services.request_keys import RETENTION_DAYS

    stale = RequestKey(
        route="POST /api/payments",
        key="old",
        payload_hash="x" * 64,
        status_code=201,
        response_json="{}",
    )
    db_session.add(stale)
    db_session.flush()
    stale.created_at = datetime.utcnow() - timedelta(days=RETENTION_DAYS + 1)
    db_session.commit()
    inv = _invoice(client, seed_customer.id)
    assert (
        client.post(
            "/api/payments", json=_body(seed_customer.id, inv), headers=KEY
        ).status_code
        == 201
    )
    assert [r.key for r in db_session.query(RequestKey).all()] == [
        KEY["Idempotency-Key"]
    ]


def test_the_keys_table_is_not_audited_and_the_migration_is_the_head():
    audit = (ROOT / "app" / "services" / "audit.py").read_text(encoding="utf-8")
    assert '"request_keys",' in audit
    mig = (ROOT / "migrations" / "versions" / "d6f7a8b9c0e1_request_keys.py").read_text(
        encoding="utf-8"
    )
    assert 'down_revision: Union[str, None] = "c5e7a9b1d3f5"' in mig
    assert "uq_request_keys_route_key" in mig


def test_the_forms_send_a_key_and_guard_the_submit():
    api = (ROOT / "app" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert (
        "post(path, data, opts, headers) { return this.request('POST', path + _qs(opts), data, headers); }"
        in api
    )
    pay = (ROOT / "app" / "static" / "js" / "payments.js").read_text(encoding="utf-8")
    assert "{ 'Idempotency-Key': PaymentsPage._requestKey }" in pay
    assert "if (PaymentsPage._saving) return;" in pay
    assert "PaymentsPage._requestKey = null; // a new form, a new key (#292)" in pay
    bills = (ROOT / "app" / "static" / "js" / "bills.js").read_text(encoding="utf-8")
    # one key per vendor; a discount sent again with the override (#664) is
    # another request, so it takes its own
    assert (
        "{ 'Idempotency-Key': `${BillsPage._payKey}:${vendorId}"
        "${body.allow_discount_override ? ':override' : ''}` }" in bills
    )
    assert "if (BillsPage._paying) return;" in bills
    app = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "requestKey() {" in app and "crypto.randomUUID()" in app


def test_the_same_amount_spelled_differently_is_the_same_request(
    client, db_session, seed_accounts, seed_customer
):
    """A retry that re-serialised 100.00 as 100 was refused as "a different
    request" and, taking a new key as told, would have booked twice
    (2.23.0 gate, NEW-86): the hash reads the numbers, not the spelling."""
    from decimal import Decimal

    from app.services.request_keys import payload_hash

    inv = _invoice(client, seed_customer.id)
    body = _body(seed_customer.id, inv)
    first = client.post("/api/payments", json=body, headers=KEY)
    assert first.status_code == 201, first.text
    respelled = json.loads(
        json.dumps(body).replace('"100.00"', '"100"').replace("100.00", "100")
    )
    assert respelled != body
    again = client.post("/api/payments", json=respelled, headers=KEY)
    assert again.status_code == 201 and again.headers.get("X-Replayed") == "true"
    assert again.json() == first.json()
    assert db_session.query(Payment).count() == 1
    assert payload_hash({"a": Decimal("100.00")}) == payload_hash({"a": Decimal("100")})
    assert payload_hash({"a": Decimal("1.2345")}) != payload_hash(
        {"a": Decimal("1.23")}
    )


def test_a_keyed_answer_names_its_key(client, db_session, seed_accounts, seed_customer):
    """One keyed POST on Windows answered 201 with no replay and booked a
    second payment, once in 22 tries, and nothing in the answer said whether
    the header had arrived (2.23.0 gate, W-8). Every keyed answer, booked or
    replayed, now carries the key; an unkeyed one carries none."""
    inv = _invoice(client, seed_customer.id)
    body = _body(seed_customer.id, inv)
    first = client.post("/api/payments", json=body, headers=KEY)
    assert (
        first.status_code == 201
        and first.headers.get("Idempotency-Key") == KEY["Idempotency-Key"]
    )
    again = client.post("/api/payments", json=body, headers=KEY)
    assert again.headers.get("X-Replayed") == "true"
    assert again.headers.get("Idempotency-Key") == KEY["Idempotency-Key"]
    plain = client.post("/api/payments", json=body)
    assert plain.status_code == 201 and "Idempotency-Key" not in plain.headers
    assert db_session.query(Payment).count() == 2
