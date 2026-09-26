"""Customer and vendor balances are what the documents say is owed.

Explore 2.17.3 (skytech W-H3, macbase1 F13): the Customer Center list, the
customer page, the vendor list and the Balance column of both CSV exports
read $0.00 for every contact — Acme owed $10,825,419.34 — because they
rendered ``Customer.balance`` / ``Vendor.balance``, stored columns that no
posting path ever wrote. The balance is now summed from the open documents
on every read, in home currency, net of credits not yet applied, so it ties
to the control account.
"""

import csv
import io
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import event, func

from app.models.contacts import Customer, Vendor
from app.models.transactions import TransactionLine


def _customer(db_session, name):
    c = Customer(name=name, is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _vendor(db_session, name):
    v = Vendor(name=name, is_active=True)
    db_session.add(v)
    db_session.commit()
    return v.id


def _invoice(client, customer_id, amount, date="2026-09-01", **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": date,
            "tax_rate": 0,
            "lines": [{"description": "Work", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, customer_id, amount, allocations=(), date="2026-09-10"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": date,
            "amount": amount,
            "method": "Check",
            "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _gl(db_session, account):
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == account.id)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


def _customer_scenario(client, db_session):
    """Owes 1,220.00: a never-sent 1,000 invoice, 300 left on a 500 invoice,
    less a 50 payment nobody applied and a 30 credit memo, while a voided
    999 invoice counts for nothing."""
    cid = _customer(db_session, "Acme Diner")
    _invoice(client, cid, 1000)  # a draft still posts to A/R
    part = _invoice(client, cid, 500)
    _pay(client, cid, 200, [(part["id"], 200)])
    _pay(client, cid, 50)  # recorded without applying it
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": cid,
            "date": "2026-09-12",
            "lines": [{"description": "Return", "quantity": 1, "rate": 30}],
        },
    )
    assert r.status_code == 201, r.text
    void = _invoice(client, cid, 999)
    assert client.post(f"/api/invoices/{void['id']}/void").status_code == 200
    return cid


def test_customer_list_and_page_show_what_the_customer_owes(
    client, db_session, seed_accounts
):
    cid = _customer_scenario(client, db_session)
    other = _customer(db_session, "Nobody Owes")

    rows = {c["id"]: c for c in client.get("/api/customers").json()}
    assert Decimal(str(rows[cid]["balance"])) == Decimal("1220.00")
    assert Decimal(str(rows[other]["balance"])) == Decimal("0")
    page = client.get(f"/api/customers/{cid}").json()
    assert Decimal(str(page["balance"])) == Decimal("1220.00")

    # ...which is exactly what account 1100 carries for this customer.
    assert _gl(db_session, seed_accounts["1100"]) == Decimal("1220.00")


def test_a_customer_in_credit_shows_a_negative_balance(
    client, db_session, seed_accounts
):
    cid = _customer(db_session, "Prepaid Co")
    inv = _invoice(client, cid, 100)
    _pay(client, cid, 130, [(inv["id"], 100)])  # 30 over
    page = client.get(f"/api/customers/{cid}").json()
    assert Decimal(str(page["balance"])) == Decimal("-30.00")


def test_a_foreign_invoice_counts_at_its_booked_home_amount(
    client, db_session, seed_accounts
):
    cid = _customer(db_session, "Bäckerei Müller")
    _invoice(client, cid, 850, currency="EUR", exchange_rate="1.10")
    page = client.get(f"/api/customers/{cid}").json()
    assert Decimal(str(page["balance"])) == Decimal("935.00")
    assert _gl(db_session, seed_accounts["1100"]) == Decimal("935.00")


def test_vendor_list_shows_what_is_owed_to_the_vendor(
    client, db_session, seed_accounts
):
    vid = _vendor(db_session, "Cascade Flour Mill")
    expense = seed_accounts["6000"].id

    def bill(amount, number):
        r = client.post(
            "/api/bills",
            json={
                "vendor_id": vid,
                "bill_number": number,
                "date": "2026-09-01",
                "lines": [{"account_id": expense, "description": "x", "rate": amount}],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    bill(777.24, "B-1")
    b2 = bill(90, "B-2")
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vid,
            "date": "2026-09-05",
            "amount": 60,  # 40 to B-2, 20 not applied to any bill
            "pay_from_account_id": seed_accounts["1000"].id,
            "allocations": [{"bill_id": b2["id"], "amount": 40}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vid,
            "date": "2026-09-06",
            "lines": [{"account_id": expense, "description": "short", "rate": 10}],
        },
    )
    assert r.status_code == 201, r.text

    # 777.24 + 50 − 20 − 10
    rows = {v["id"]: v for v in client.get("/api/vendors").json()}
    assert Decimal(str(rows[vid]["balance"])) == Decimal("797.24")
    page = client.get(f"/api/vendors/{vid}").json()
    assert Decimal(str(page["balance"])) == Decimal("797.24")
    # A/P (a credit-normal account) carries the same figure
    assert -_gl(db_session, seed_accounts["2000"]) == Decimal("797.24")


def test_csv_exports_carry_the_computed_balance(client, db_session, seed_accounts):
    cid = _customer_scenario(client, db_session)
    body = client.get("/api/csv/export/customers").text
    rows = {r["ID"]: r for r in csv.DictReader(io.StringIO(body.lstrip("﻿")))}
    assert Decimal(rows[str(cid)]["Balance"]) == Decimal("1220.00")

    vid = _vendor(db_session, "Blue Heron Packaging")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vid,
            "bill_number": "BH-1",
            "date": "2026-09-01",
            "lines": [
                {"account_id": seed_accounts["6000"].id, "description": "x", "rate": 90}
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = client.get("/api/csv/export/vendors").text
    rows = {r["ID"]: r for r in csv.DictReader(io.StringIO(body.lstrip("﻿")))}
    assert Decimal(rows[str(vid)]["Balance"]) == Decimal("90.00")


@contextmanager
def _count_selects(engine):
    statements = []

    def _hook(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _hook)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _hook)


def test_the_balance_costs_a_fixed_number_of_queries_per_page(
    client, db_session, db_engine, seed_accounts
):
    """One grouped read per document kind for the whole list, never one per
    row — the Customer Center loads every customer at once."""

    def count(n):
        for i in range(n):
            cid = _customer(db_session, f"Customer {n}-{i}")
            _invoice(client, cid, 10 + i)
            vid = _vendor(db_session, f"Vendor {n}-{i}")
            r = client.post(
                "/api/bills",
                json={
                    "vendor_id": vid,
                    "bill_number": f"N-{n}-{i}",
                    "date": "2026-09-01",
                    "lines": [
                        {
                            "account_id": seed_accounts["6000"].id,
                            "description": "x",
                            "rate": 5,
                        }
                    ],
                },
            )
            assert r.status_code == 201, r.text
        with _count_selects(db_engine) as custs:
            assert client.get("/api/customers").status_code == 200
        with _count_selects(db_engine) as vends:
            assert client.get("/api/vendors").status_code == 200
        return len(custs), len(vends)

    assert count(2) == count(6)


def test_the_ai_tools_read_the_same_balance(client, db_session, seed_accounts):
    """The assistant's customer and vendor lookups used the same dead column,
    so it told the owner every customer owed nothing."""
    from app.services.ai_tools import list_customers, list_vendors

    cid = _customer_scenario(client, db_session)
    found = {r["id"]: r for r in list_customers(db_session)["results"]}
    assert found[cid]["balance"] == 1220.0

    vid = _vendor(db_session, "Sign Supply")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vid,
            "bill_number": "SS-1",
            "date": "2026-09-01",
            "lines": [
                {"account_id": seed_accounts["6000"].id, "description": "x", "rate": 45}
            ],
        },
    )
    assert r.status_code == 201, r.text
    found = {r["id"]: r for r in list_vendors(db_session)["results"]}
    assert found[vid]["balance"] == 45.0
