"""The review of sub-customers (#638): what the probes found, fixed.

A bare name in an import is a top-level customer, never a sub-customer
that shares it; a statement's payment and credit lines are what relieved
the invoices on it, whoever paid; the IIF export spells a sub-customer
"Parent:Sub" on every row; a parent is not made inactive over a
sub-customer's open balance billed to it; a three-level QuickBooks name
is flattened out loud; a second property's Unit 1 is not a duplicate; a
credit memo is routed the way a payment is; an invoice's customer_name is
the same name everywhere; and the printed invoice prints the billed
customer's country.

The 2.31.1 gate's round (the last section): a tenant moves once settled
and every statement keeps its history (W-58); the printed invoice keeps
the Bill To it was made with (W-59); a property's billed balance is what
its aging row shows and the CSV names a tenant in full (W-61); the credit
limit checked is the payer's (W-62); every tenant under its property
shows its own name and Unit 2 beside Unit 1 is not a duplicate (W-69).

Round 3: an invoice moved to another customer by an edit (W-72), a
duplicate (W-73), a converted estimate (W-80), an invoice carried over
from QuickBooks Online, a generated recurring invoice and a pay
application's invoice take the payer's whole Bill To as of then, the
street with the name and country; an edit can correct the addresses; and
a duplicate is checked against the credit limit (W-79).

Round 3's notes: the printed Ship To keeps the country frozen with its
street; an invoice with a payment or credit memo on it stays with its
customer; and an address part sent as "" with a move blanks it.
"""

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.contacts import Customer
from app.models.invoices import Invoice
from app.models.jobs import Job
from app.models.payments import Payment

ROOT = Path(__file__).resolve().parents[1]
BILL_TO_REVISION = "b4d8e2a6c7f1"  # invoices bill_to_* columns, after f3a9c7e1b5d4
SHIP_TO_COUNTRY_REVISION = "d7b3f9a1c5e2"  # invoices.ship_to_country, after b4d8


def _customer(client, name, **extra):
    r = client.post("/api/customers", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _property_and_tenant(client, bill_with_parent=True, **parent_extra):
    maple = _customer(
        client,
        "Maple St",
        bill_address1="12 Maple St",
        bill_city="Springfield",
        bill_state="IL",
        bill_zip="62704",
        **parent_extra,
    )
    unit = _customer(
        client,
        "Unit 4B",
        parent_customer_id=maple["id"],
        bill_with_parent=bill_with_parent,
        bill_address1="12 Maple St, Apt 4B",
        bill_city="Springfield",
    )
    return maple, unit


def _invoice(client, cid, amount, day, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "tax_rate": 0,
            "lines": [{"description": "Rent", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _payment(client, cid, amount, day, allocations=()):
    return client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "amount": amount,
            "method": "Check",
            "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
        },
    )


def _credit_memo(client, cid, amount, day, **extra):
    return client.post(
        "/api/credit-memos",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "lines": [{"description": "Credit", "quantity": 1, "rate": amount}],
            **extra,
        },
    )


def _import(client, text, **data):
    return client.post(
        "/api/iif/import",
        files={"file": ("books.iif", text.encode("utf-8"), "text/plain")},
        data=data,
    )


def _balance(client, cid) -> Decimal:
    return Decimal(str(client.get(f"/api/customers/{cid}").json()["balance"]))


def _statement(db_session, cid, day=30):
    from app.routes.reports.receivables import statement_activity

    return statement_activity(
        db_session, db_session.get(Customer, cid), date(2026, 9, day)
    )


# ── 1. a bare name is a top-level customer ───────────────────────────────

SMITH_IIF = (
    "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
    "CUST\tSmith\t\t555-0100\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tSmith\t500\t3001\n"
    "SPL\tINVOICE\t09/01/2026\tSales\tSmith\t-500\t3001\n"
    "ENDTRNS\n"
    "TRNS\tPAYMENT\t09/05/2026\tUndeposited Funds\tSmith\t500\t\n"
    "SPL\tPAYMENT\t09/05/2026\tAccounts Receivable\tSmith\t-500\t3001\n"
    "ENDTRNS\n"
)

UNIT_2_IIF = (
    "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
    "CUST\tSmith:Unit 2\t\t555-0102\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tSmith:Unit 2\t700\t3002\n"
    "SPL\tINVOICE\t09/01/2026\tSales\tSmith:Unit 2\t-700\t3002\n"
    "ENDTRNS\n"
)


def _oak_st_with_tenant_smith(client):
    oak = _customer(client, "Oak St")
    smith = _customer(
        client, "Smith", parent_customer_id=oak["id"], bill_with_parent=True
    )
    return oak, smith


def test_an_imported_bare_name_is_a_top_level_customer_not_the_tenant(
    client, db_session, seed_accounts
):
    """A file's "Smith" posted its invoice and payment to the tenant
    "Oak St:Smith". A bare name is a top-level customer: it is made, and
    the documents are its."""
    oak, tenant = _oak_st_with_tenant_smith(client)
    r = _import(client, SMITH_IIF)
    assert r.status_code == 200, r.text
    assert r.json()["customers"] == 1
    assert r.json()["invoices"] == 1 and r.json()["payments"] == 1
    smiths = db_session.query(Customer).filter(Customer.name == "Smith").all()
    assert len(smiths) == 2
    top = next(c for c in smiths if c.parent_customer_id is None)
    assert top.phone == "555-0100"
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "3001").one()
    assert inv.customer_id == top.id
    pay = db_session.query(Payment).one()
    assert pay.customer_id == top.id
    # the tenant has nothing, and the property's roll-up says so
    assert _balance(client, tenant["id"]) == 0
    assert client.get(f"/api/customers/{oak['id']}/subs").json()["rollup_balance"] == 0
    assert _balance(client, top.id) == 0
    # a second import makes nothing twice
    r = _import(client, SMITH_IIF)
    assert r.json()["customers"] == 0 and r.json()["invoices"] == 0
    assert db_session.query(Customer).filter(Customer.name == "Smith").count() == 2


def test_a_sub_customer_under_a_bare_name_lands_under_the_top_level_one(
    client, db_session, seed_accounts
):
    """ "Smith:Unit 2", with sub-customers on, made a third level under the
    tenant "Oak St:Smith". It lands under a top-level Smith, made for it;
    never three levels."""
    oak, tenant = _oak_st_with_tenant_smith(client)
    r = _import(client, UNIT_2_IIF, sub_customers="true")
    assert r.status_code == 200, r.text
    assert r.json()["customers"] == 1
    unit = db_session.query(Customer).filter(Customer.name == "Unit 2").one()
    smith = db_session.get(Customer, unit.parent_customer_id)
    assert smith.name == "Smith" and smith.parent_customer_id is None
    assert smith.id != tenant["id"]
    # one level deep, everywhere
    for c in db_session.query(Customer).all():
        if c.parent_customer_id:
            assert (
                db_session.get(Customer, c.parent_customer_id).parent_customer_id
                is None
            )
    assert client.get(f"/api/customers/{tenant['id']}/subs").json()["subs"] == []
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "3002").one()
    assert inv.customer_id == unit.id
    # as jobs (the default) the same: a job under the top-level Smith
    r = _import(client, UNIT_2_IIF.replace("Unit 2", "Roof 2026"))
    assert r.status_code == 200, r.text
    job = db_session.query(Job).filter(Job.name == "Roof 2026").one()
    assert job.customer_id == smith.id


def test_a_bare_name_lookup_never_answers_a_sub_customer(db_session):
    from app.services.jobs_service import find_customer, resolve_customer_and_job

    oak = Customer(name="Oak St", is_active=True)
    db_session.add(oak)
    db_session.flush()
    db_session.add(Customer(name="Smith", parent_customer_id=oak.id, is_active=True))
    db_session.flush()
    assert find_customer(db_session, "Smith") is None
    assert find_customer(db_session, "SMITH") is None
    assert find_customer(db_session, "Oak St").id == oak.id
    assert resolve_customer_and_job(db_session, "Smith", create=False) == (None, None)
    # through its parent it is found, in any case
    customer, job = resolve_customer_and_job(db_session, "Oak St:smith", create=False)
    assert (
        customer is not None and customer.parent_customer_id == oak.id and job is None
    )


# ── 2. the statement follows what relieved its invoices ─────────────────


def test_a_subs_own_statement_shows_the_parents_payment_that_relieved_it(
    client, db_session, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    r = _payment(client, maple["id"], 1200, 5, [(rent["id"], 1200)])
    assert r.status_code == 201, r.text
    # the sub owes nothing, and its statement says so
    assert _balance(client, unit["id"]) == 0
    act = _statement(db_session, unit["id"])
    assert [(ln["type"], ln["amount"], ln["balance"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00"), Decimal("1200.00")),
        ("Payment", Decimal("-1200.00"), Decimal("0.00")),
    ]
    assert act["lines"][1]["description"] == (
        f"from Maple St · Check · applied to #{rent['invoice_number']}"
    )
    assert act["balance_due"] == 0 and act["total_payments"] == Decimal("1200.00")
    # the parent's statement carries the sub's invoice and its own payment
    act = _statement(db_session, maple["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Payment", Decimal("-1200.00")),
    ]
    assert act["lines"][1]["description"] == (
        f"Check · applied to #{rent['invoice_number']}"
    )
    assert act["balance_due"] == 0 == _balance(client, maple["id"])


def test_the_iif_shape_a_parent_payment_on_a_sub_billed_on_its_own(
    client, db_session, seed_accounts
):
    """The IIF import allocates a parent's payment by invoice number while
    an imported sub-customer is billed on its own: the sub's statement
    shows the invoice and the payment that paid it; the parent's statement
    shows what it paid as a pair, its payment and the invoice it paid
    named for the sub (2.31.1 gate, W-58; it showed nothing before), and
    both read what the customers owe."""
    text = (
        "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
        "CUST\tMaple St\tMaple St LLC\t\n"
        "CUST\tMaple St:Unit 4B\t\t555-0104\n"
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!ENDTRNS\n"
        "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tMaple St:Unit 4B\t1200\t2001\n"
        "SPL\tINVOICE\t09/01/2026\tSales\tMaple St:Unit 4B\t-1200\t2001\n"
        "ENDTRNS\n"
        "TRNS\tPAYMENT\t09/05/2026\tUndeposited Funds\tMaple St\t1200\t\n"
        "SPL\tPAYMENT\t09/05/2026\tAccounts Receivable\tMaple St\t-1200\t2001\n"
        "ENDTRNS\n"
    )
    r = _import(client, text, sub_customers="true")
    assert r.status_code == 200, r.text
    assert r.json()["invoices"] == 1 and r.json()["payments"] == 1
    maple = db_session.query(Customer).filter(Customer.name == "Maple St").one()
    unit = db_session.query(Customer).filter(Customer.name == "Unit 4B").one()
    assert unit.bill_with_parent is False
    assert _balance(client, unit.id) == 0 and _balance(client, maple.id) == 0
    act = _statement(db_session, unit.id)
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Payment", Decimal("-1200.00")),
    ]
    assert act["lines"][1]["description"].startswith("from Maple St")
    assert act["balance_due"] == 0
    act = _statement(db_session, maple.id)
    assert [(ln["type"], ln["description"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", "for Unit 4B: #2001", Decimal("1200.00")),
        ("Payment", "applied to #2001", Decimal("-1200.00")),
    ]
    assert act["balance_due"] == 0 and act["total_payments"] == Decimal("1200.00")
    assert act["total_invoiced"] == Decimal("1200.00")
    assert client.get("/api/reports/ar-aging").json()["totals"]["total"] == 0.0


def test_a_sub_stays_billed_with_its_parent_while_the_parents_money_is_on_it(
    client, db_session, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    r = _payment(client, maple["id"], 600, 5, [(rent["id"], 600)])
    assert r.status_code == 201, r.text
    other = _customer(client, "Oak Ave")
    for body in (
        {"bill_with_parent": False},
        {"parent_customer_id": None},
        {"parent_customer_id": other["id"]},
    ):
        r = client.put(f"/api/customers/{unit['id']}", json=body)
        assert r.status_code == 409, (body, r.text)
        detail = r.json()["detail"]
        assert detail["error"] == "parent_money_on_open_invoices"
        assert f"#{rent['invoice_number']} ($600.00)" in detail["message"]
        assert "stays billed with Maple St" in detail["message"]
        assert detail["invoices"] == [
            {
                "invoice_id": rent["id"],
                "invoice_number": rent["invoice_number"],
                "amount": 600.0,
            }
        ]
    got = client.get(f"/api/customers/{unit['id']}").json()
    assert got["bill_with_parent"] is True and got["parent_customer_id"] == maple["id"]
    # a parent's credit on it counts the same way
    cm = _credit_memo(client, maple["id"], 100, 6)
    assert cm.status_code == 201, cm.text
    r = client.post(
        f"/api/credit-memos/{cm.json()['id']}/apply",
        json={"invoice_id": rent["id"], "amount": 100},
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 409
    assert r.json()["detail"]["invoices"][0]["amount"] == 700.0
    # settled, the flag comes off
    r = _payment(client, maple["id"], 500, 7, [(rent["id"], 500)])
    assert r.status_code == 201, r.text
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 200, r.text
    assert r.json()["bill_with_parent"] is False
    # ...and the statements still read what each owes: nothing
    assert _statement(db_session, unit["id"])["balance_due"] == 0
    assert _statement(db_session, maple["id"])["balance_due"] == 0


def test_a_customer_with_no_sub_customers_reads_as_before(
    client, db_session, seed_accounts
):
    acme = _customer(client, "Acme Diner")
    first = _invoice(client, acme["id"], 100, 1)
    r = _payment(client, acme["id"], 60, 2, [(first["id"], 60)])
    assert r.status_code == 201, r.text
    r = _payment(client, acme["id"], 25, 6)  # nothing applied
    assert r.status_code == 201, r.text
    cm = _credit_memo(client, acme["id"], 10, 5)
    assert cm.status_code == 201, cm.text
    act = _statement(db_session, acme["id"])
    assert [(ln["type"], ln["amount"], ln["balance"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("100.00"), Decimal("100.00")),
        ("Payment", Decimal("-60.00"), Decimal("40.00")),
        ("Credit Memo", Decimal("-10.00"), Decimal("30.00")),
        ("Payment", Decimal("-25.00"), Decimal("5.00")),
    ]
    assert (
        act["lines"][1]["description"]
        == f"Check · applied to #{first['invoice_number']}"
    )
    assert act["lines"][2]["description"] == "credit"
    assert act["lines"][3]["description"] == "Check · not applied to an invoice yet"
    assert act["total_payments"] == Decimal("85.00")
    assert act["total_credits"] == Decimal("10.00")
    assert act["balance_due"] == Decimal("5.00") == _balance(client, acme["id"])


# ── 3. the IIF export spells a sub-customer one way ─────────────────────


def _names(text):
    return [
        (row[0], row[1], row[4])
        for row in (line.split("\t") for line in text.splitlines())
        if row[0] in ("TRNS", "SPL")
    ]


def test_the_iif_export_names_a_sub_customer_parent_sub_on_every_row(
    client, seed_accounts
):
    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    rent = _invoice(client, unit["id"], 1200, 2, tax_rate=0.05)
    r = _payment(client, unit["id"], 1260, 5, [(rent["id"], 1260)])
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": unit["id"],
            "date": "2026-09-02",
            "lines": [{"description": "Paint", "quantity": 1, "rate": 50}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": unit["id"],
            "date": "2026-09-03",
            "lines": [{"description": "Key", "quantity": 1, "rate": 20}],
        },
    )
    assert r.status_code == 201, r.text
    for path in ("invoices", "payments", "estimates", "sales-receipts"):
        rows = _names(client.get(f"/api/iif/export/{path}").text)
        assert rows, path
        assert {name for _row, _kind, name in rows} == {"Maple St:Unit 4B"}, (
            path,
            rows,
        )


def test_the_customer_list_keeps_its_order_and_puts_a_sub_after_its_parent(
    client,
):
    """The top-level rows go out in the database's name order, as they
    always have (a company with no sub-customers exports as before); a
    sub-customer follows its parent."""
    for name in ("apple co", "Banana Ltd", "Cherry Inc"):
        _customer(client, name)

    def cust_names():
        body = client.get("/api/iif/export/customers").text
        return [
            line.split("\t")[1]
            for line in body.splitlines()
            if line.startswith("CUST\t")
        ]

    r = client.get("/api/customers").json()  # the database's order
    assert (
        cust_names()
        == [c["name"] for c in r]
        == [
            "Banana Ltd",
            "Cherry Inc",
            "apple co",
        ]
    )
    banana = next(c for c in r if c["name"] == "Banana Ltd")
    _customer(client, "Unit 1", parent_customer_id=banana["id"])
    assert cust_names() == ["Banana Ltd", "Banana Ltd:Unit 1", "Cherry Inc", "apple co"]


# ── 4. inactivation ────────────────────────────────────────────────────


def test_a_parent_is_not_made_inactive_over_a_sub_customers_open_balance(
    client, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    for how in (
        lambda: client.put(f"/api/customers/{maple['id']}", json={"is_active": False}),
        lambda: client.put(
            f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
        ),
        lambda: client.delete(f"/api/customers/{maple['id']}?with_subs=true"),
    ):
        r = how()
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "subs_billed_with_parent_owe"
        assert (
            f"Unit 4B is billed with Maple St and still owes $1,200.00 "
            f"(#{rent['invoice_number']})" in detail["message"]
        )
        assert "Receive the payment or write the balance off" in detail["message"]
        assert "take Bill with parent off" in detail["message"]
        assert detail["subs"] == [
            {
                "id": unit["id"],
                "name": "Unit 4B",
                "balance": 1200.0,
                "invoices": [rent["invoice_number"]],
            }
        ]
    assert client.get(f"/api/customers/{maple['id']}").json()["is_active"] is True
    assert client.get(f"/api/customers/{unit['id']}").json()["is_active"] is True
    # a tenant who moved out still owing is the same case
    r = client.put(f"/api/customers/{unit['id']}", json={"is_active": False})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/customers/{maple['id']}", json={"is_active": False})
    assert r.status_code == 409 and r.json()["detail"]["subs"][0]["balance"] == 1200.0
    # paid, the property can go, with its sub-customers
    r = _payment(client, maple["id"], 1200, 5, [(rent["id"], 1200)])
    assert r.status_code == 201, r.text
    r = client.put(
        f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/reports/ar-aging").json()["items"] == []


def test_the_question_about_active_subs_names_each_ones_open_balance(
    client, seed_accounts
):
    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    attic = _customer(client, "Attic", parent_customer_id=maple["id"])
    _invoice(client, attic["id"], 800, 3)
    r = client.put(f"/api/customers/{maple['id']}", json={"is_active": False})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "active_subs"
    assert "Attic ($800.00 open), Unit 4B" in detail["message"]
    assert detail["subs"] == [
        {"id": attic["id"], "name": "Attic", "balance": 800.0},
        {"id": unit["id"], "name": "Unit 4B", "balance": 0.0},
    ]
    # billed on its own, the tenant's balance is its own: the property can
    # go with its sub-customers, and the balance ages on the tenant's row
    r = client.put(
        f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
    )
    assert r.status_code == 200, r.text
    rows = client.get("/api/reports/ar-aging").json()["items"]
    assert [(i["customer_name"], i["total"]) for i in rows] == [
        ("Maple St: Attic", 800.0)
    ]


def test_a_sub_is_not_made_active_again_under_an_inactive_parent(client):
    maple, unit = _property_and_tenant(client)
    r = client.put(
        f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/customers/{unit['id']}", json={"is_active": True})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "parent_inactive"
    assert detail["parent_id"] == maple["id"]
    assert "Maple St, which is inactive" in detail["message"]
    assert "Make Maple St active first" in detail["message"]
    assert client.get(f"/api/customers/{unit['id']}").json()["is_active"] is False
    # a new sub-customer under it, or one moved under it, the same
    r = client.post(
        "/api/customers", json={"name": "Attic", "parent_customer_id": maple["id"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "parent_inactive"
    loner = _customer(client, "Oak Ave")
    r = client.put(
        f"/api/customers/{loner['id']}", json={"parent_customer_id": maple["id"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "parent_inactive"
    # the parent first, then the tenant; an edit of the inactive tenant
    # that keeps its parent is fine meanwhile
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"parent_customer_id": maple["id"], "phone": "555-0104"},
    )
    assert r.status_code == 200, r.text
    assert (
        client.put(
            f"/api/customers/{maple['id']}", json={"is_active": True}
        ).status_code
        == 200
    )
    r = client.put(f"/api/customers/{unit['id']}", json={"is_active": True})
    assert r.status_code == 200 and r.json()["is_active"] is True


# ── 5. three-level names ───────────────────────────────────────────────

DEEP_IIF = (
    "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
    "CUST\tMaple St\t\t\n"
    "CUST\tMaple St:Unit 4B:Bedroom\t\t555-0104\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tMaple St:Unit 4B:Bedroom\t300\t2001\n"
    "SPL\tINVOICE\t09/01/2026\tSales\tMaple St:Unit 4B:Bedroom\t-300\t2001\n"
    "ENDTRNS\n"
)


def test_a_three_level_name_is_reported_and_flattened_out_loud(
    client, db_session, seed_accounts
):
    r = client.post(
        "/api/iif/validate",
        files={"file": ("deep.iif", DEEP_IIF.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["customer_job_names"] == 1
    assert report["nested_customer_job_names"] == ["Maple St:Unit 4B:Bedroom"]
    assert any(
        "Maple St:Unit 4B:Bedroom' has more than one level" in w
        and "'Maple St' with job or sub-customer 'Unit 4B:Bedroom'" in w
        for w in report["warnings"]
    ), report["warnings"]

    r = _import(client, DEEP_IIF, sub_customers="true")
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [
        "Customer 'Maple St:Unit 4B:Bedroom' has more than one level; imported "
        "as 'Maple St' with sub-customer 'Unit 4B:Bedroom' (one level deep)"
    ]
    maple = db_session.query(Customer).filter(Customer.name == "Maple St").one()
    sub = (
        db_session.query(Customer).filter(Customer.parent_customer_id == maple.id).one()
    )
    assert sub.name == "Unit 4B:Bedroom" and sub.phone == "555-0104"
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "2001").one()
    assert inv.customer_id == sub.id


def test_a_three_level_name_as_a_job_says_so_too(client, db_session, seed_accounts):
    r = _import(client, DEEP_IIF)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [
        "Customer 'Maple St:Unit 4B:Bedroom' has more than one level; imported "
        "as 'Maple St' with job 'Unit 4B:Bedroom' (one level deep)"
    ]
    assert db_session.query(Job).filter(Job.name == "Unit 4B:Bedroom").count() == 1
    # said once per import, however many rows name it, on a re-import too
    r = _import(client, DEEP_IIF.replace("2001", "2002"))
    assert len(r.json()["warnings"]) == 1
    # a transaction alone naming three levels is said as well
    text = "\n".join(
        line for line in DEEP_IIF.splitlines() if not line.startswith("CUST")
    )
    r = _import(client, text.replace("Bedroom", "Closet").replace("2001", "2003"))
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [
        "Customer 'Maple St:Unit 4B:Closet' has more than one level; imported "
        "as 'Maple St' with job 'Unit 4B:Closet' (one level deep)"
    ]


# ── 6. duplicates: siblings and top-level names ─────────────────────────


def test_a_second_propertys_unit_1_is_not_a_duplicate(client):
    oak = _customer(client, "Oak St")
    _customer(client, "Unit 1", parent_customer_id=oak["id"])
    maple = _customer(client, "Maple St")
    # the same name under another parent: fine
    r = client.post(
        "/api/customers", json={"name": "Unit 1", "parent_customer_id": maple["id"]}
    )
    assert r.status_code == 201, r.text
    # a sibling: a likely duplicate, as before
    r = client.post(
        "/api/customers", json={"name": "Unit 1", "parent_customer_id": oak["id"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "possible_duplicate"
    # a sub-customer named like a top-level customer is still caught
    r = client.post(
        "/api/customers", json={"name": "Maple St", "parent_customer_id": oak["id"]}
    )
    assert r.status_code == 409
    # the standalone check takes the parent the same way
    found = lambda qs: [  # noqa: E731
        d["name"]
        for d in client.get(f"/api/customers/check-duplicate?{qs}").json()["duplicates"]
    ]
    assert found(f"name=Unit%201&parent_customer_id={oak['id']}") == ["Unit 1"]
    assert found(f"name=Unit%201&parent_customer_id={maple['id']}") == ["Unit 1"]
    assert found("name=Oak%20St") == ["Oak St"]
    assert found("name=Unit%201") == []
    # a bare name is a top-level customer: checked against those
    assert client.post("/api/customers", json={"name": "Oak St"}).status_code == 409
    assert client.post("/api/customers", json={"name": "Unit 1"}).status_code == 201


# ── 7. credit memos route like payments ─────────────────────────────────


def test_a_parents_credit_memo_applies_to_a_sub_billed_with_it(
    client, db_session, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    cm = _credit_memo(client, maple["id"], 200, 3)
    assert cm.status_code == 201, cm.text
    r = client.post(
        f"/api/credit-memos/{cm.json()['id']}/apply",
        json={"invoice_id": rent["id"], "amount": 200},
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/api/invoices/{rent['id']}").json()
    assert Decimal(got["balance_due"]) == 1000 and got["status"] == "partial"
    # a memo issued against the sub's invoice is the parent's too
    r = _credit_memo(client, maple["id"], 50, 4, original_invoice_id=rent["id"])
    assert r.status_code == 201, r.text
    # the sub's statement shows the credit that relieved its invoice, and
    # ties to what it owes; the parent's carries the memo whole
    act = _statement(db_session, unit["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Credit Memo", Decimal("-200.00")),
    ]
    assert act["lines"][1]["description"] == (
        f"from Maple St · applied to #{rent['invoice_number']}"
    )
    assert act["balance_due"] == Decimal("1000.00") == _balance(client, unit["id"])
    act = _statement(db_session, maple["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Credit Memo", Decimal("-200.00")),
        ("Credit Memo", Decimal("-50.00")),
    ]
    # ...which is the parent's A/R Aging row: the sub's open item, net of
    # the parent's unapplied credit (the parent's own balance is -50)
    assert act["balance_due"] == Decimal("950.00")
    rows = {
        i["customer_name"]: i
        for i in client.get("/api/reports/ar-aging").json()["items"]
    }
    assert (
        rows["Maple St"]["total"] == 950.0
        and rows["Maple St"]["unapplied_credits"] == 50.0
    )
    assert _balance(client, maple["id"]) == Decimal("-50.00")


def test_a_credit_memo_does_not_reach_a_sub_billed_on_its_own(client, seed_accounts):
    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    rent = _invoice(client, unit["id"], 1200, 2)
    cm = _credit_memo(client, maple["id"], 200, 3)
    assert cm.status_code == 201, cm.text
    r = client.post(
        f"/api/credit-memos/{cm.json()['id']}/apply",
        json={"invoice_id": rent["id"], "amount": 200},
    )
    assert r.status_code == 400, r.text
    assert "belongs to a different customer than this credit memo" in r.json()["detail"]
    r = _credit_memo(client, maple["id"], 50, 4, original_invoice_id=rent["id"])
    assert r.status_code == 400, r.text
    # the pages offer what the API takes
    for name in ("payments.js", "credit_memos.js"):
        js = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "js"
            / name
        ).read_text(encoding="utf-8")
        assert "open_only=true&billed_to=true" in js, name
        assert "kind === 'payment' ? '&billed_to=true'" not in js, name


# ── 8. customer_name, one name everywhere ───────────────────────────────


def test_every_invoice_response_names_the_customer_the_same_way(client, seed_accounts):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    full = "Maple St: Unit 4B"
    assert rent["customer_name"] == full
    r = client.put(f"/api/invoices/{rent['id']}", json={"notes": "September"})
    assert r.status_code == 200 and r.json()["customer_name"] == full, r.text
    r = client.post(f"/api/invoices/{rent['id']}/send")
    assert r.status_code == 200 and r.json()["customer_name"] == full, r.text
    r = client.post(f"/api/invoices/{rent['id']}/duplicate")
    assert r.status_code == 201 and r.json()["customer_name"] == full, r.text
    copy_id = r.json()["id"]
    r = client.post(
        f"/api/invoices/{copy_id}/write-off", json={"date": "2026-09-20", "amount": 10}
    )
    assert r.status_code == 201 and r.json()["customer_name"] == full, r.text
    r = client.post(f"/api/invoices/{rent['id']}/void")
    assert r.status_code == 200 and r.json()["customer_name"] == full, r.text
    # the small-balance list from A/R Aging says the same
    rows = client.get("/api/invoices/write-offs/candidates?under=5000").json()
    assert rows and {row["customer_name"] for row in rows} == {full}


# ── 9. the printed country is the billed customer's ─────────────────────


def test_the_printed_invoice_prints_the_billed_customers_country(
    client, db_session, seed_accounts
):
    from app.services.donor_documents import invoice_pdf_context
    from app.services.pdf_service import render_invoice_html
    from app.services.settings_service import get_all_settings

    maple, unit = _property_and_tenant(client, bill_country="CA")
    rent = _invoice(client, unit["id"], 1200, 2)
    settings = get_all_settings(db_session)
    inv = db_session.get(Invoice, rent["id"])
    ctx = invoice_pdf_context(inv, settings)
    assert ctx["bill_to_country"] == "CA"
    assert (ctx["bill_to_name"], ctx["bill_to_for"]) == ("Maple St", "Unit 4B")
    html = render_invoice_html(inv, settings)
    block = html[html.index("Bill To") : html.index('<table class="line-items">')]
    assert "12 Maple St" in block and "Canada" in block and "For: Unit 4B" in block
    # billed on its own, the sub-customer's own country prints
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"bill_with_parent": False, "bill_country": "DE"},
    )
    assert r.status_code == 200, r.text
    own = _invoice(client, unit["id"], 50, 3)
    inv = db_session.get(Invoice, own["id"])
    assert invoice_pdf_context(inv, settings)["bill_to_country"] == "DE"
    html = render_invoice_html(inv, settings)
    block = html[html.index("Bill To") : html.index('<table class="line-items">')]
    assert "Germany" in block and "Canada" not in block and "For:" not in block


# ── the 2.31.1 gate: W-58, W-59, W-61, W-62, W-69 ───────────────────────


def _js(name: str) -> str:
    return (ROOT / "app" / "static" / "js" / name).read_text(encoding="utf-8")


def _lines(act):
    return [
        (ln["type"], ln["description"], ln["amount"], ln["balance"])
        for ln in act["lines"]
    ]


def test_a_tenant_moves_once_settled_and_the_former_parents_statement_keeps_what_it_paid(
    client, db_session, seed_accounts
):
    """W-58: while the property's money sits on the tenant's open invoice
    the flag stays on and the tenant stays put (409, as before); once the
    invoice is settled the tenant may be billed on its own or moved, and
    the property's statement keeps the invoice it paid and the payment as
    a pair, so it still shows what it paid and its balance is unchanged.
    The new property's statement carries neither."""
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1000, 2)
    n = rent["invoice_number"]
    r = _payment(client, maple["id"], 1000, 5, [(rent["id"], 1000)])
    assert r.status_code == 201, r.text
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 200, r.text
    assert r.json()["bill_with_parent"] is False

    act = _statement(db_session, maple["id"])
    assert _lines(act) == [
        ("Invoice", f"for Unit 4B: #{n}", Decimal("1000.00"), Decimal("1000.00")),
        ("Payment", f"Check · applied to #{n}", Decimal("-1000.00"), Decimal("0.00")),
    ]
    assert act["total_invoiced"] == Decimal("1000.00")
    assert act["total_payments"] == Decimal("1000.00")
    assert act["balance_due"] == 0 == _balance(client, maple["id"])
    # the tenant's own statement is its own: the invoice, and who paid it
    act = _statement(db_session, unit["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1000.00")),
        ("Payment", Decimal("-1000.00")),
    ]
    assert act["lines"][1]["description"].startswith("from Maple St")

    # moved to another property and billed with it: that property's
    # statement does not carry the old property's payment, nor the
    # invoice it relieved
    elm = _customer(client, "Elm St")
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"parent_customer_id": elm["id"], "bill_with_parent": True},
    )
    assert r.status_code == 200, r.text
    act = _statement(db_session, elm["id"])
    assert act["lines"] == [] and act["balance_due"] == 0
    assert act["total_invoiced"] == 0 and act["total_payments"] == 0
    # the former parent's statement is as it was
    assert [ln["amount"] for ln in _statement(db_session, maple["id"])["lines"]] == [
        Decimal("1000.00"),
        Decimal("-1000.00"),
    ]
    # a new invoice to the tenant is the new property's business
    second = _invoice(client, unit["id"], 700, 8)
    act = _statement(db_session, elm["id"])
    assert [(ln["number"], ln["amount"]) for ln in act["lines"]] == [
        (second["invoice_number"], Decimal("700.00"))
    ]
    assert act["lines"][0]["description"].startswith("Unit 4B · Due ")
    assert act["balance_due"] == Decimal("700.00")
    rows = {
        i["customer_name"]: i["total"]
        for i in client.get("/api/reports/ar-aging").json()["items"]
    }
    assert rows == {"Elm St": 700.0}
    # the sub's statement is the whole story
    act = _statement(db_session, unit["id"])
    assert [ln["amount"] for ln in act["lines"]] == [
        Decimal("1000.00"),
        Decimal("-1000.00"),
        Decimal("700.00"),
    ]
    assert act["balance_due"] == Decimal("700.00") == _balance(client, unit["id"])


def test_a_new_parents_statement_carries_only_what_is_still_its_business(
    client, db_session, seed_accounts
):
    """W-58, the invoice two customers relieved: the old property's
    payment and credit are paired on its own statement at what they
    relieved; the new property's statement carries the invoice net of
    that money, with the tenant's own payment that covered the rest."""
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1000, 2)
    n = rent["invoice_number"]
    assert _payment(client, maple["id"], 500, 3, [(rent["id"], 500)]).status_code == 201
    cm = _credit_memo(client, maple["id"], 100, 4)
    assert cm.status_code == 201, cm.text
    r = client.post(
        f"/api/credit-memos/{cm.json()['id']}/apply",
        json={"invoice_id": rent["id"], "amount": 100},
    )
    assert r.status_code == 200, r.text
    # still open with the property's money on it: no move
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "parent_money_on_open_invoices"
    assert _payment(client, unit["id"], 400, 6, [(rent["id"], 400)]).status_code == 201
    elm = _customer(client, "Elm St")
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"parent_customer_id": elm["id"], "bill_with_parent": True},
    )
    assert r.status_code == 200, r.text

    act = _statement(db_session, maple["id"])
    assert _lines(act) == [
        ("Invoice", f"for Unit 4B: #{n}", Decimal("600.00"), Decimal("600.00")),
        ("Payment", f"Check · applied to #{n}", Decimal("-500.00"), Decimal("100.00")),
        ("Credit Memo", f"applied to #{n}", Decimal("-100.00"), Decimal("0.00")),
    ]
    assert act["total_invoiced"] == Decimal("600.00")
    assert act["total_payments"] == Decimal("500.00")
    assert act["total_credits"] == Decimal("100.00")
    assert act["balance_due"] == 0 == _balance(client, maple["id"])
    act = _statement(db_session, elm["id"])
    assert [(ln["type"], ln["amount"], ln["balance"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("400.00"), Decimal("400.00")),
        ("Payment", Decimal("-400.00"), Decimal("0.00")),
    ]
    assert act["lines"][0]["description"].startswith("Unit 4B · Due ")
    assert act["lines"][1]["description"] == f"Unit 4B · Check · applied to #{n}"
    assert act["balance_due"] == 0 == _balance(client, elm["id"])
    act = _statement(db_session, unit["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1000.00")),
        ("Payment", Decimal("-500.00")),
        ("Credit Memo", Decimal("-100.00")),
        ("Payment", Decimal("-400.00")),
    ]
    assert act["lines"][1]["description"].startswith("from Maple St")
    assert act["balance_due"] == 0


def test_the_printed_invoice_keeps_the_bill_to_it_was_made_with(
    client, db_session, seed_accounts
):
    """W-59: the Bill To block comes from the invoice alone. The name and
    country are frozen with the street when the invoice is made, so a
    tenant moved to another property, or billed on its own, afterwards
    prints the same invoice it was sent. A duplicate is a new invoice,
    billed as of now (W-73)."""
    from app.services.donor_documents import invoice_pdf_context
    from app.services.pdf_service import render_invoice_html
    from app.services.settings_service import get_all_settings

    settings = get_all_settings(db_session)

    def block(inv_id):
        db_session.expire_all()
        html = render_invoice_html(db_session.get(Invoice, inv_id), settings)
        return html[html.index("Bill To") : html.index('<table class="line-items">')]

    maple, unit = _property_and_tenant(client, bill_country="CA")
    rent = _invoice(client, unit["id"], 1000, 2)
    assert (
        rent["bill_to_customer_id"],
        rent["bill_to_name"],
        rent["bill_to_country"],
    ) == (
        maple["id"],
        "Maple St",
        "CA",
    )
    assert (
        _payment(client, maple["id"], 1000, 5, [(rent["id"], 1000)]).status_code == 201
    )
    elm = _customer(
        client,
        "Elm St",
        bill_address1="3 Elm St",
        bill_city="Berlin",
        bill_country="DE",
    )
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"parent_customer_id": elm["id"], "bill_with_parent": True},
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    ctx = invoice_pdf_context(db_session.get(Invoice, rent["id"]), settings)
    assert (ctx["bill_to_name"], ctx["bill_to_for"], ctx["bill_to_country"]) == (
        "Maple St",
        "Unit 4B",
        "CA",
    )
    first = block(rent["id"])
    assert "<strong>Maple St</strong>" in first
    assert "12 Maple St" in first and "Canada" in first and "For: Unit 4B" in first
    assert "Elm St" not in first and "Germany" not in first

    # an invoice made under the new property is billed to it; the flag
    # turned off afterwards (and the tenant's own country changed) alters
    # nothing on it
    second = _invoice(client, unit["id"], 50, 8)
    assert (second["bill_to_name"], second["bill_to_country"]) == ("Elm St", "DE")
    assert second["bill_address1"] == "3 Elm St"
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"bill_with_parent": False, "bill_country": "MX"},
    )
    assert r.status_code == 200, r.text
    printed = block(second["id"])
    assert "<strong>Elm St</strong>" in printed and "3 Elm St" in printed
    assert "Germany" in printed and "For: Unit 4B" in printed
    assert "Mexico" not in printed
    # the tenant billed on its own: its own name and country, no For: line
    third = _invoice(client, unit["id"], 20, 9)
    assert (
        third["bill_to_customer_id"],
        third["bill_to_name"],
        third["bill_to_country"],
    ) == (
        unit["id"],
        "Unit 4B",
        "MX",
    )
    own = block(third["id"])
    assert "<strong>Unit 4B</strong>" in own and "Mexico" in own and "For:" not in own
    # a duplicate is a new invoice billed as of now (W-73): the tenant is
    # billed on its own today, so the copy is too, its own street and all;
    # the original keeps the block it was sent with
    r = client.post(f"/api/invoices/{rent['id']}/duplicate")
    assert r.status_code == 201, r.text
    copy = r.json()
    assert (
        copy["bill_to_customer_id"],
        copy["bill_to_name"],
        copy["bill_to_country"],
        copy["bill_address1"],
    ) == (unit["id"], "Unit 4B", "MX", "12 Maple St, Apt 4B")
    dup = block(copy["id"])
    assert "<strong>Unit 4B</strong>" in dup and "Mexico" in dup and "For:" not in dup
    assert "For: Unit 4B" in block(rent["id"])


def test_the_bill_to_migration_backfills_from_the_customer_billed_to(tmp_path):
    """Books from before the columns: every invoice takes the name and
    country of the customer it is billed to at the upgrade, the parent's
    for a sub-customer billed with it."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text

    db = tmp_path / "billto.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision("b4d8e2a6c7f1").down_revision == "f3a9c7e1b5d4"

    command.upgrade(cfg, "f3a9c7e1b5d4")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        assert "bill_to_name" not in {
            c["name"] for c in inspect(conn).get_columns("invoices")
        }
        conn.execute(
            text(
                "INSERT INTO customers (id, name, bill_country, parent_customer_id, "
                "bill_with_parent, is_active) VALUES "
                "(1, 'Maple St', 'CA', NULL, 0, 1), "
                "(2, 'Unit 4B', 'US', 1, 1, 1), "
                "(3, 'Attic', 'MX', 1, 0, 1), "
                "(4, 'Oak Ave', 'DE', NULL, 0, 1)"
            )
        )
        for inv_id, cid in ((10, 2), (11, 3), (12, 4), (13, 1)):
            conn.execute(
                text(
                    "INSERT INTO invoices (id, invoice_number, customer_id, date, "
                    "status, retainage_held, retainage_released, is_sales_receipt, "
                    "is_pledge) VALUES (:id, :num, :cid, '2026-09-01', 'SENT', 0, 0, "
                    "0, 0)"
                ),
                {"id": inv_id, "num": str(inv_id), "cid": cid},
            )
    engine.dispose()

    command.upgrade(cfg, BILL_TO_REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, bill_to_customer_id, bill_to_name, bill_to_country "
                "FROM invoices ORDER BY id"
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        (10, 1, "Maple St", "CA"),  # billed with the parent: the parent's
        (11, 3, "Attic", "MX"),  # a sub billed on its own: its own
        (12, 4, "Oak Ave", "DE"),
        (13, 1, "Maple St", "CA"),
    ]
    engine.dispose()

    command.downgrade(cfg, "f3a9c7e1b5d4")
    engine = create_engine(f"sqlite:///{db}")
    names = {c["name"] for c in inspect(engine).get_columns("invoices")}
    assert not names & {
        "bill_to_customer_id",
        "bill_to_name",
        "bill_to_country",
        "ship_to_country",
    }
    assert "bill_address1" in names
    engine.dispose()


def test_a_propertys_billed_balance_is_what_its_aging_row_shows(client, seed_accounts):
    """W-61: balance is the customer's own open documents, billed_balance
    what sits on its A/R (its own plus the sub-customers billed with it,
    the aging row), rollup_balance everything under it; and the customer
    CSV names a sub-customer in full."""
    maple, unit = _property_and_tenant(client)
    attic = _customer(client, "Attic", parent_customer_id=maple["id"])
    rent = _invoice(client, unit["id"], 1000, 2)
    _invoice(client, attic["id"], 700, 3)

    got = client.get(f"/api/customers/{maple['id']}").json()
    assert Decimal(got["balance"]) == 0
    assert Decimal(got["billed_balance"]) == Decimal("1000.00")
    assert Decimal(got["rollup_balance"]) == Decimal("1700.00")
    rows = {
        i["customer_name"]: i["total"]
        for i in client.get("/api/reports/ar-aging").json()["items"]
    }
    assert rows == {"Maple St": 1000.0, "Maple St: Attic": 700.0}
    # a customer with nothing billed with it: the two are the same
    for cid, owed in ((unit["id"], "1000.00"), (attic["id"], "700.00")):
        got = client.get(f"/api/customers/{cid}").json()
        assert (
            Decimal(got["balance"]) == Decimal(got["billed_balance"]) == Decimal(owed)
        )
        assert got["rollup_balance"] is None
    # the list and the page carry the same figures
    listed = {c["id"]: c for c in client.get("/api/customers?tree=1").json()}
    assert Decimal(listed[maple["id"]]["billed_balance"]) == Decimal("1000.00")
    assert Decimal(listed[maple["id"]]["balance"]) == 0
    page = client.get(f"/api/customers/{maple['id']}/subs").json()
    assert (page["own_balance"], page["billed_balance"], page["rollup_balance"]) == (
        0.0,
        1000.0,
        1700.0,
    )
    # the property's own invoice and the tenant's payment move it
    _invoice(client, maple["id"], 300, 4)
    assert (
        _payment(client, maple["id"], 1000, 5, [(rent["id"], 1000)]).status_code == 201
    )
    got = client.get(f"/api/customers/{maple['id']}").json()
    assert (Decimal(got["balance"]), Decimal(got["billed_balance"])) == (
        Decimal("300.00"),
        Decimal("300.00"),
    )
    assert Decimal(got["rollup_balance"]) == Decimal("1000.00")

    body = client.get("/api/csv/export/customers").text.lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0][1] == "Name"
    assert [r[1] for r in rows[1:]] == [
        "Maple St",
        "Maple St: Unit 4B",
        "Maple St: Attic",
    ]


def test_the_customer_center_shows_the_billed_balance_and_names_every_tenant():
    """W-61 and W-69 on the page: the figure shown is billed_balance, and
    each sub-customer right under its parent shows its own name (the
    last top-level row is what a sub is matched against, not the row
    above it)."""
    js = _js("customers.js")
    page = js[js.index("async render()") : js.index("filter(query)")]
    assert "CustomersPage._balanceCell(c)" in page
    assert "c.billed_balance" in js and "CustomersPage._owed(customer)" in js
    assert "if (!sub) parentAbove = c.id;" in page
    assert "prevId" not in js


def test_the_credit_limit_checked_is_the_payers(client, seed_accounts):
    """W-62: an invoice to a tenant billed with its property lands on the
    property's A/R, so the property's limit is checked against what sits
    there, its own open invoices and its billed tenants', and the 409
    names the property. Billed on its own, the tenant's own limit."""

    def post(cid, rate, **extra):
        return client.post(
            "/api/invoices",
            json={
                "customer_id": cid,
                "date": "2026-09-02",
                "tax_rate": 0,
                "lines": [{"description": "Rent", "quantity": 1, "rate": rate}],
                **extra,
            },
        )

    maple = _customer(client, "Maple St", credit_limit="500")
    unit = _customer(
        client,
        "Unit 4B",
        parent_customer_id=maple["id"],
        bill_with_parent=True,
        credit_limit="9000",
    )
    # control: the property's own $600 invoice
    r = post(maple["id"], 600)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "credit_limit"
    # the same invoice to the billed tenant: the property pays
    r = post(unit["id"], 600)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "credit_limit"
    assert d["message"] == (
        "Maple St's credit limit is $500.00. With this invoice they would owe $600.00."
    )
    assert (d["limit"], d["would_owe"]) == ("500.00", "600.00")
    # what the property owes counts its billed tenants, from either side
    first = post(unit["id"], 300)
    assert first.status_code == 201, first.text
    r = post(maple["id"], 300)
    assert r.status_code == 409 and r.json()["detail"]["would_owe"] == "600.00"
    r = post(unit["id"], 150)
    assert r.status_code == 201, r.text
    r = post(unit["id"], 100)
    assert r.status_code == 409 and r.json()["detail"]["would_owe"] == "550.00"
    r = post(unit["id"], 100, allow_over_credit_limit=True)
    assert r.status_code == 201, r.text
    # an edit that raises the tenant's invoice asks the same way
    r = client.put(
        f"/api/invoices/{first.json()['id']}",
        json={"lines": [{"description": "Rent", "quantity": 1, "rate": 400}]},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["message"].startswith("Maple St's credit limit")
    # billed on its own, the tenant's own limit is the one checked
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 200, r.text
    assert post(unit["id"], 600).status_code == 201
    r = post(unit["id"], 9000)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["message"].startswith("Unit 4B's credit limit")


def test_unit_102_beside_unit_101_is_not_a_duplicate_but_the_same_name_is(client):
    """W-69: among the sub-customers under one parent only the same name
    (in any case) is a duplicate; the likely-match rule stays for the
    top-level names, which a sub-customer's name is checked against too.
    "Unit 101" and "Unit 102" are 0.875 alike, over the 0.85 threshold
    that refused the second unit of a property before."""
    from app.services.duplicate_detection import similarity

    assert similarity("Unit 101", "Unit 102") >= 0.85
    maple = _customer(client, "Maple St")
    first = _customer(client, "Unit 101", parent_customer_id=maple["id"])
    r = client.post(
        "/api/customers", json={"name": "Unit 102", "parent_customer_id": maple["id"]}
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/customers", json={"name": "unit 101", "parent_customer_id": maple["id"]}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "possible_duplicate"
    assert r.json()["detail"]["duplicates"] == [
        {"id": first["id"], "name": "Unit 101", "similarity": 1.0}
    ]
    # a sub-customer named like a top-level customer is still caught
    r = client.post(
        "/api/customers", json={"name": "Maple St.", "parent_customer_id": maple["id"]}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["duplicates"][0]["name"] == "Maple St"
    # the standalone check says the same
    found = lambda qs: [  # noqa: E731
        d["name"]
        for d in client.get(f"/api/customers/check-duplicate?{qs}").json()["duplicates"]
    ]
    assert found(f"name=Unit%20103&parent_customer_id={maple['id']}") == []
    assert found(f"name=UNIT%20101&parent_customer_id={maple['id']}") == ["Unit 101"]
    # a top-level name is matched the likely way, as before, against the
    # top-level names only
    assert client.post("/api/customers", json={"name": "Unit 101"}).status_code == 201
    r = client.post("/api/customers", json={"name": "Unit 102"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["duplicates"][0]["name"] == "Unit 101"
    assert found("name=Unit%20102") == ["Unit 101"]


def test_an_edit_to_another_customer_takes_its_bill_to_and_a_rename_keeps_the_old(
    client, db_session, seed_accounts
):
    """W-59, the edit side: an invoice moved to another customer prints the
    new customer's Bill To; a customer renamed afterwards leaves the
    invoice printing the name it was sent with, as its street always did."""
    from app.services.donor_documents import invoice_pdf_context
    from app.services.settings_service import get_all_settings

    settings = get_all_settings(db_session)
    first = _customer(client, "First Co", bill_country="US")
    second = _customer(client, "Second Co", bill_country="CA")
    inv = _invoice(client, first["id"], 250, 1)
    assert inv["bill_to_name"] == "First Co"

    r = client.put(f"/api/invoices/{inv['id']}", json={"customer_id": second["id"]})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    ctx = invoice_pdf_context(db_session.get(Invoice, inv["id"]), settings)
    assert (ctx["bill_to_name"], ctx["bill_to_country"]) == ("Second Co", "CA")

    r = client.put(f"/api/customers/{second['id']}", json={"name": "Second Company"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    ctx = invoice_pdf_context(db_session.get(Invoice, inv["id"]), settings)
    assert ctx["bill_to_name"] == "Second Co"
    # a header-only edit keeps it
    r = client.put(f"/api/invoices/{inv['id']}", json={"notes": "thanks"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(Invoice, inv["id"]).bill_to_name == "Second Co"


# ── the 2.31.1 gate, round 3: W-72, W-73, W-79, W-80 ────────────────────


def _printed(db_session, inv_id):
    """The printed invoice's address blocks, each as the lines it prints:
    ``{"Bill To": [...], "Ship To": [...]}`` (no Ship To block when the
    invoice has no ship-to)."""
    import re

    from app.services.pdf_service import render_invoice_html
    from app.services.settings_service import get_all_settings

    db_session.expire_all()
    html = render_invoice_html(
        db_session.get(Invoice, inv_id), get_all_settings(db_session)
    )
    blocks = {}
    for label, text in re.findall(
        r'<div class="address-label">(.*?)</div>\s*'
        r'<div class="address-text">(.*?)</div>',
        html,
        re.S,
    ):
        lines = (re.sub(r"<[^>]+>", "", part).strip() for part in text.split("<br>"))
        blocks[label] = [line for line in lines if line]
    return blocks


def test_an_invoice_edited_onto_another_customer_prints_that_customers_whole_bill_to(
    client, db_session, seed_accounts
):
    """W-72: an edit that moves an invoice to another customer takes the
    whole Bill To from the new customer's payer as create does, the street
    with the name and country, and the ship-to from the new customer. The
    edit takes the addresses too, so a street can be corrected, and one
    sent with the move wins over the one copied. An edit that keeps the
    customer leaves the Bill To as the invoice was made unless it sends an
    address."""
    alpha = _customer(
        client,
        "Alpha",
        bill_address1="1 Solo Way",
        bill_city="Austin",
        bill_state="TX",
        bill_zip="78701",
        ship_address1="Dock 3",
        ship_city="Austin",
    )
    beta = _customer(
        client,
        "Beta SARL",
        bill_address1="8 Rue des Lilas",
        bill_city="Lyon",
        bill_zip="69001",
        bill_country="FR",
    )
    inv = _invoice(client, alpha["id"], 300, 1)
    assert _printed(db_session, inv["id"]) == {
        "Bill To": ["Alpha", "1 Solo Way", "Austin, TX 78701"],
        "Ship To": ["Dock 3", "Austin"],
    }

    r = client.put(f"/api/invoices/{inv['id']}", json={"customer_id": beta["id"]})
    assert r.status_code == 200, r.text
    moved = r.json()
    assert (
        moved["bill_address1"],
        moved["bill_city"],
        moved["bill_state"],
        moved["bill_zip"],
    ) == ("8 Rue des Lilas", "Lyon", None, "69001")
    assert (
        moved["bill_to_customer_id"],
        moved["bill_to_name"],
        moved["bill_to_country"],
    ) == (beta["id"], "Beta SARL", "FR")
    # Alpha's dock is not Beta's: Beta has no ship-to, so none prints
    assert moved["ship_address1"] is None
    assert _printed(db_session, inv["id"]) == {
        "Bill To": ["Beta SARL", "8 Rue des Lilas", "Lyon, 69001", "France"]
    }

    # onto a tenant billed with its property: the property's street under
    # the property's name, "For:" the tenant, and the tenant's ship-to
    maple, unit = _property_and_tenant(client, bill_country="CA")
    r = client.put(f"/api/customers/{unit['id']}", json={"ship_address1": "Apt 4B"})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/invoices/{inv['id']}", json={"customer_id": unit["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["bill_to_customer_id"] == maple["id"]
    assert _printed(db_session, inv["id"]) == {
        "Bill To": [
            "Maple St",
            "12 Maple St",
            "Springfield, IL 62704",
            "Canada",
            "For: Unit 4B",
        ],
        "Ship To": ["Apt 4B"],
    }

    # an address sent with the move wins over the one copied, part by part
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"customer_id": beta["id"], "bill_address1": "8 Rue des Lilas, Bat. B"},
    )
    assert r.status_code == 200, r.text
    assert _printed(db_session, inv["id"]) == {
        "Bill To": ["Beta SARL", "8 Rue des Lilas, Bat. B", "Lyon, 69001", "France"]
    }

    # an edit that keeps the customer: Beta moving changes nothing on it...
    r = client.put(
        f"/api/customers/{beta['id']}",
        json={"bill_address1": "2 Quai Perrache", "bill_country": "BE"},
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"customer_id": beta["id"], "notes": "second notice"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["bill_address1"] == "8 Rue des Lilas, Bat. B"
    # ...and an address it sends corrects the street; the name and country
    # stay the ones it was billed with
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "bill_address1": "10 Rue des Lilas",
            "bill_zip": "69002",
            "ship_address1": "Quai 2",
        },
    )
    assert r.status_code == 200, r.text
    fixed = r.json()
    assert (fixed["bill_to_name"], fixed["bill_to_country"]) == ("Beta SARL", "FR")
    assert _printed(db_session, inv["id"]) == {
        "Bill To": ["Beta SARL", "10 Rue des Lilas", "Lyon, 69002", "France"],
        "Ship To": ["Quai 2"],
    }

    # a customer that does not exist is refused, as on create
    r = client.put(f"/api/invoices/{inv['id']}", json={"customer_id": 99999})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Customer not found"


def test_a_duplicate_is_billed_as_of_now(client, db_session, seed_accounts):
    """W-73: a duplicate is a new invoice billed as of now. A tenant moved
    from Maple St to Elm St since its invoice was made has the copy billed
    to Elm St's account, and the copy prints Elm St's street under Elm
    St's name with "For:" the tenant, and the tenant's ship-to as it is
    now; the original still prints what it was sent with."""
    maple = _customer(
        client,
        "Maple St",
        bill_address1="12 Maple St",
        bill_city="Springfield",
        bill_state="IL",
        bill_zip="62704",
    )
    unit = _customer(
        client,
        "Unit 7",
        parent_customer_id=maple["id"],
        bill_with_parent=True,
        bill_address1="12 Maple St, Unit 7",
        bill_city="Springfield",
        ship_address1="12 Maple St, Unit 7",
    )
    rent = _invoice(client, unit["id"], 900, 2)
    # settled by the property, so the tenant may move
    assert _payment(client, maple["id"], 900, 4, [(rent["id"], 900)]).status_code == 201
    elm = _customer(
        client,
        "Elm St",
        bill_address1="40 Elm St",
        bill_city="Riverton",
        bill_state="WY",
        bill_zip="82501",
    )
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={
            "parent_customer_id": elm["id"],
            "bill_with_parent": True,
            "ship_address1": "40 Elm St, Unit 7",
        },
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/invoices/{rent['id']}/duplicate")
    assert r.status_code == 201, r.text
    copy = r.json()
    assert (copy["customer_id"], copy["bill_to_customer_id"], copy["bill_to_name"]) == (
        unit["id"],
        elm["id"],
        "Elm St",
    )
    assert (copy["bill_address1"], copy["bill_city"]) == ("40 Elm St", "Riverton")
    assert _printed(db_session, copy["id"]) == {
        "Bill To": ["Elm St", "40 Elm St", "Riverton, WY 82501", "For: Unit 7"],
        "Ship To": ["40 Elm St, Unit 7"],
    }
    # on Elm St's A/R: what Receive Payment offers Elm St
    offered = client.get(
        f"/api/invoices?customer_id={elm['id']}&billed_to=true&open_only=true"
    ).json()
    assert [i["id"] for i in offered] == [copy["id"]]
    # the original prints what it was sent with
    assert _printed(db_session, rent["id"]) == {
        "Bill To": ["Maple St", "12 Maple St", "Springfield, IL 62704", "For: Unit 7"],
        "Ship To": ["12 Maple St, Unit 7"],
    }


def test_a_duplicate_is_checked_against_the_payers_credit_limit(
    client, db_session, seed_accounts
):
    """W-79: Duplicate asks about the credit limit as a new invoice does
    (its schema always said so): past the payer's limit it is refused with
    the same 409, nothing written, and made with allow_over_credit_limit,
    the say-so create takes. A tenant billed with its property is checked
    against the property's limit; within it nothing is asked."""
    maple = _customer(client, "Maple St", credit_limit="1000")
    unit = _customer(
        client,
        "Unit 4B",
        parent_customer_id=maple["id"],
        bill_with_parent=True,
        credit_limit="9000",
    )
    rent = _invoice(client, unit["id"], 600, 2)
    db_session.expire_all()
    before = db_session.query(Invoice).count()
    r = client.post(f"/api/invoices/{rent['id']}/duplicate")
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "credit_limit"
    assert d["message"] == (
        "Maple St's credit limit is $1,000.00. "
        "With this invoice they would owe $1,200.00."
    )
    assert d["question"] == d["message"] + " Duplicate it anyway?"
    assert (d["limit"], d["would_owe"]) == ("1000.00", "1200.00")
    db_session.expire_all()
    assert db_session.query(Invoice).count() == before  # nothing written
    r = client.post(
        f"/api/invoices/{rent['id']}/duplicate",
        json={"allow_over_credit_limit": True},
    )
    assert r.status_code == 201, r.text
    # within the limit nothing is asked
    small = _invoice(client, maple["id"], 100, 3, allow_over_credit_limit=True)
    r = client.put(f"/api/customers/{maple['id']}", json={"credit_limit": "5000"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/invoices/{small['id']}/duplicate").status_code == 201


def test_an_estimate_converted_for_a_billed_tenant_bills_the_property_street_and_all(
    client, db_session, seed_accounts
):
    """W-80: Convert bills the payer's street with the payer's name, as a
    new invoice is billed. A tenant billed with its property (the estimate
    itself is addressed to the tenant) gets the property's street under the
    property's name and country, "For:" the tenant, and its own ship-to; a
    tenant billed on its own gets its own street."""

    def convert(cid):
        r = client.post(
            "/api/estimates",
            json={
                "customer_id": cid,
                "date": "2026-09-01",
                "tax_rate": 0,
                "lines": [{"description": "Repaint", "quantity": 1, "rate": 400}],
            },
        )
        assert r.status_code == 201, r.text
        r = client.post(f"/api/estimates/{r.json()['id']}/convert")
        assert r.status_code == 200, r.text
        return r.json()

    maple, unit = _property_and_tenant(client, bill_country="CA")
    r = client.put(f"/api/customers/{unit['id']}", json={"ship_address1": "Apt 4B"})
    assert r.status_code == 200, r.text
    inv = convert(unit["id"])
    assert (
        inv["bill_to_customer_id"],
        inv["bill_to_name"],
        inv["bill_to_country"],
        inv["bill_address1"],
        inv["ship_address1"],
    ) == (maple["id"], "Maple St", "CA", "12 Maple St", "Apt 4B")
    assert _printed(db_session, inv["id"]) == {
        "Bill To": [
            "Maple St",
            "12 Maple St",
            "Springfield, IL 62704",
            "Canada",
            "For: Unit 4B",
        ],
        "Ship To": ["Apt 4B"],
    }
    # billed on its own: the tenant's own street under its own name
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 200, r.text
    own = convert(unit["id"])
    assert (own["bill_to_customer_id"], own["bill_to_name"]) == (unit["id"], "Unit 4B")
    assert _printed(db_session, own["id"])["Bill To"] == [
        "Unit 4B",
        "12 Maple St, Apt 4B",
        "Springfield",
    ]


def test_an_open_invoice_carried_from_quickbooks_online_bills_the_payer(
    client, db_session, seed_accounts
):
    """The QuickBooks Online export's carried-over open invoices copied the
    customer's own street; for a tenant already on file billed with its
    property, the name and country frozen were the property's, so the
    invoice printed the property over the tenant's street. They take the
    payer's Bill To through the same helper as every new invoice."""
    from app.models.accounts import Account, AccountType
    from app.services import qbo_export_import as qbo

    maple, unit = _property_and_tenant(client, bill_country="CA")
    db_session.add(
        Account(
            name="Accounts Receivable (A/R)",
            account_type=AccountType.ASSET,
            balance=Decimal("0"),
        )
    )
    db_session.commit()
    plan = {
        "receivable": "Accounts Receivable (A/R)",
        "invoices": [
            {
                "customer": "Maple St:Unit 4B",
                "num": "1044",
                "date": date(2026, 9, 1),
                "due_date": date(2026, 10, 1),
                "open": Decimal("250.00"),
                "amount": Decimal("250.00"),
            }
        ],
        "payments": [],
    }
    assert qbo._import_carried(db_session, plan) == (1, 0)
    db_session.commit()
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "1044").one()
    assert (inv.customer_id, inv.bill_to_customer_id) == (unit["id"], maple["id"])
    assert _printed(db_session, inv.id) == {
        "Bill To": [
            "Maple St",
            "12 Maple St",
            "Springfield, IL 62704",
            "Canada",
            "For: Unit 4B",
        ]
    }


def test_a_generated_recurring_invoice_prints_the_payers_whole_bill_to(
    client, db_session, seed_accounts
):
    """The scheduler's invoices carried no street, so a recurring invoice
    printed a name alone. It takes the payer's whole Bill To as of the day
    it is generated, through the helper every new invoice uses: for a
    tenant billed with its property, the property's street under its name
    and country, "For:" the tenant, and the tenant's own ship-to; billed on
    its own, its own street."""
    maple, unit = _property_and_tenant(client, bill_country="CA")
    r = client.put(f"/api/customers/{unit['id']}", json={"ship_address1": "Apt 4B"})
    assert r.status_code == 200, r.text

    def generated(cid, day):
        r = client.post(
            "/api/recurring",
            json={
                "customer_id": cid,
                "frequency": "monthly",
                "start_date": day,
                "lines": [{"description": "Rent", "quantity": 1, "rate": 900}],
            },
        )
        assert r.status_code == 201, r.text
        out = client.post(f"/api/recurring/generate?as_of={day}").json()
        assert out["invoices_created"] == 1, out
        return db_session.get(Invoice, out["invoice_ids"][0])

    inv = generated(unit["id"], "2026-09-01")
    assert (inv.bill_to_customer_id, inv.bill_address1, inv.ship_address1) == (
        maple["id"],
        "12 Maple St",
        "Apt 4B",
    )
    assert _printed(db_session, inv.id) == {
        "Bill To": [
            "Maple St",
            "12 Maple St",
            "Springfield, IL 62704",
            "Canada",
            "For: Unit 4B",
        ],
        "Ship To": ["Apt 4B"],
    }
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 200, r.text
    own = generated(unit["id"], "2026-09-02")
    assert _printed(db_session, own.id)["Bill To"] == [
        "Unit 4B",
        "12 Maple St, Apt 4B",
        "Springfield",
    ]


def test_a_pay_applications_invoice_prints_the_payers_whole_bill_to(
    client, db_session, seed_accounts
):
    """A pay application's invoice carried no street either; approving one
    for a tenant billed with its property bills the property's street,
    name and country, "For:" the tenant."""
    maple, unit = _property_and_tenant(client, bill_country="CA")
    r = client.post("/api/jobs", json={"customer_id": unit["id"], "name": "Lobby"})
    assert r.status_code == 201, r.text
    job = r.json()
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": 10000,
            "retainage_pct": 0,
        },
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [{"description": "Tile", "kind": "work", "scheduled_value": 10000}]
        },
    )
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-06-01"}
    )
    assert r.status_code == 201, r.text
    app_id = r.json()["id"]
    r = client.put(
        f"/api/pay-applications/{app_id}",
        json={
            "lines": [{"sov_line_id": sov[0]["id"], "this_period": 2500, "stored": 0}]
        },
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{app_id}/approve")
    assert r.status_code == 200, r.text
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert (inv.bill_to_customer_id, inv.bill_address1) == (maple["id"], "12 Maple St")
    assert _printed(db_session, inv.id)["Bill To"] == [
        "Maple St",
        "12 Maple St",
        "Springfield, IL 62704",
        "Canada",
        "For: Unit 4B",
    ]


# ── the 2.31.1 gate, round 3's notes: the Ship To's country, a paid
# invoice's move, and a part sent empty with a move ─────────────────────


def _preview_blocks(client, inv_id):
    """The print preview's address blocks, as _printed reads the PDF's."""
    import re

    r = client.get(f"/api/invoices/{inv_id}/print-preview")
    assert r.status_code == 200, r.text
    blocks = {}
    for label, text in re.findall(
        r'<div class="address-label">(.*?)</div>\s*'
        r'<div class="address-text">(.*?)</div>',
        r.text,
        re.S,
    ):
        lines = (re.sub(r"<[^>]+>", "", part).strip() for part in text.split("<br>"))
        blocks[label] = [line for line in lines if line]
    return blocks


def test_the_printed_ship_to_keeps_the_country_it_was_made_with(
    client, db_session, seed_accounts
):
    """The printed Ship To read its country off the customer at print time,
    under the ship-to street frozen on the invoice, so an old invoice
    printed "2 Dock Rd, Austin, TX 78702, Canada" once the customer's ship
    country changed. The country is frozen with the street
    (ship_to_country on every response): the customer's own, never the
    payer's, taken again by a move, and a request may give it as it gives
    the ship street. The PDF and the print preview print the same."""
    alpha = _customer(
        client,
        "Alpha",
        ship_address1="2 Dock Rd",
        ship_city="Austin",
        ship_state="TX",
        ship_zip="78702",
    )
    dock = ["2 Dock Rd", "Austin, TX 78702"]
    old = _invoice(client, alpha["id"], 300, 1)
    assert _printed(db_session, old["id"])["Ship To"] == dock

    r = client.put(f"/api/customers/{alpha['id']}", json={"ship_country": "CA"})
    assert r.status_code == 200, r.text
    assert _printed(db_session, old["id"])["Ship To"] == dock
    assert _preview_blocks(client, old["id"])["Ship To"] == dock
    assert old["ship_to_country"] == "US"
    assert client.get(f"/api/invoices/{old['id']}").json()["ship_to_country"] == "US"
    # an invoice made now, and a duplicate (a new invoice), take the
    # country the customer has now
    new = _invoice(client, alpha["id"], 100, 2)
    assert new["ship_to_country"] == "CA"
    assert _printed(db_session, new["id"])["Ship To"] == dock + ["Canada"]
    r = client.post(f"/api/invoices/{old['id']}/duplicate")
    assert r.status_code == 201, r.text
    assert r.json()["ship_to_country"] == "CA"
    assert _preview_blocks(client, r.json()["id"])["Ship To"] == dock + ["Canada"]

    # the Ship To is the customer's own: a tenant billed with its property
    # is billed the property's way and shipped its own
    maple, unit = _property_and_tenant(client, ship_country="DE")
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"ship_address1": "Apt 4B", "ship_country": "MX"},
    )
    assert r.status_code == 200, r.text
    rent = _invoice(client, unit["id"], 900, 3)
    assert (rent["bill_to_customer_id"], rent["ship_to_country"]) == (
        maple["id"],
        "MX",
    )
    assert _printed(db_session, rent["id"])["Ship To"] == ["Apt 4B", "Mexico"]

    # a move takes the new customer's ship-to, its country with it
    beta = _customer(
        client,
        "Beta SARL",
        ship_address1="9 Rue de Paris",
        ship_city="Paris",
        ship_zip="75001",
        ship_country="FR",
    )
    r = client.put(f"/api/invoices/{old['id']}", json={"customer_id": beta["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["ship_to_country"] == "FR"
    paris = ["9 Rue de Paris", "Paris, 75001"]
    assert _printed(db_session, old["id"])["Ship To"] == paris + ["France"]
    # an edit corrects it, as it corrects the ship street...
    r = client.put(f"/api/invoices/{old['id']}", json={"ship_to_country": "BE"})
    assert r.status_code == 200, r.text
    assert r.json()["ship_to_country"] == "BE"
    assert _preview_blocks(client, old["id"])["Ship To"] == paris + ["Belgium"]
    # ...and one sent with a move wins over the one copied
    r = client.put(
        f"/api/invoices/{old['id']}",
        json={"customer_id": alpha["id"], "ship_to_country": "US"},
    )
    assert r.status_code == 200, r.text
    assert _printed(db_session, old["id"])["Ship To"] == dock
    # create takes one too, over the customer's
    given = _invoice(client, alpha["id"], 50, 4, ship_to_country="MX")
    assert given["ship_to_country"] == "MX"
    assert _printed(db_session, given["id"])["Ship To"] == dock + ["Mexico"]


def test_every_way_an_invoice_is_made_freezes_its_customers_ship_country(
    client, db_session, seed_accounts
):
    """Convert and the scheduler's recurring invoices freeze the Ship To's
    country with the ship-to through the one helper, the customer's own
    and not the payer's; an invoice made by any other path (an importer
    that writes no street) takes it on insert, and one made with a country
    keeps it."""
    from app.models.invoices import InvoiceStatus

    maple, unit = _property_and_tenant(client, ship_country="DE")
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"ship_address1": "Apt 4B", "ship_country": "MX"},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/estimates",
        json={
            "customer_id": unit["id"],
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Repaint", "quantity": 1, "rate": 400}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(f"/api/estimates/{r.json()['id']}/convert")
    assert r.status_code == 200, r.text
    assert r.json()["ship_to_country"] == "MX"

    r = client.post(
        "/api/recurring",
        json={
            "customer_id": unit["id"],
            "frequency": "monthly",
            "start_date": "2026-09-01",
            "lines": [{"description": "Rent", "quantity": 1, "rate": 900}],
        },
    )
    assert r.status_code == 201, r.text
    out = client.post("/api/recurring/generate?as_of=2026-09-01").json()
    assert out["invoices_created"] == 1, out
    db_session.expire_all()
    generated = db_session.get(Invoice, out["invoice_ids"][0])
    assert (generated.bill_to_customer_id, generated.ship_to_country) == (
        maple["id"],
        "MX",
    )

    def made(**extra):
        inv = Invoice(
            invoice_number=f"SHIP-{len(extra)}",
            customer_id=unit["id"],
            date=date(2026, 9, 1),
            status=InvoiceStatus.SENT,
            **extra,
        )
        db_session.add(inv)
        db_session.flush()
        return inv

    bare = made()
    assert (bare.bill_to_name, bare.ship_to_country) == ("Maple St", "MX")
    kept = made(ship_to_country="CA")
    assert (kept.bill_to_name, kept.ship_to_country) == ("Maple St", "CA")


def test_the_ship_to_country_migration_backfills_from_each_invoices_own_customer(
    tmp_path,
):
    """Books from before the column: every invoice takes its own customer's
    ship country at the upgrade, which is what it printed until now; a
    tenant billed with its property takes the tenant's, not the
    property's. downgrade drops the column and leaves the Bill To."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text

    db = tmp_path / "shipto.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision("d7b3f9a1c5e2").down_revision == "b4d8e2a6c7f1"

    command.upgrade(cfg, "b4d8e2a6c7f1")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        assert "ship_to_country" not in {
            c["name"] for c in inspect(conn).get_columns("invoices")
        }
        conn.execute(
            text(
                "INSERT INTO customers (id, name, bill_country, ship_country, "
                "parent_customer_id, bill_with_parent, is_active) VALUES "
                "(1, 'Maple St', 'CA', 'DE', NULL, 0, 1), "
                "(2, 'Unit 4B', 'US', 'MX', 1, 1, 1), "
                "(3, 'Oak Ave', 'US', NULL, NULL, 0, 1)"
            )
        )
        # each with the Bill To frozen at that revision: the property's
        # for the tenant billed with it
        for inv_id, cid, billed, name in (
            (10, 2, 1, "Maple St"),
            (11, 1, 1, "Maple St"),
            (12, 3, 3, "Oak Ave"),
        ):
            conn.execute(
                text(
                    "INSERT INTO invoices (id, invoice_number, customer_id, date, "
                    "status, retainage_held, retainage_released, is_sales_receipt, "
                    "is_pledge, bill_to_customer_id, bill_to_name) VALUES (:id, "
                    ":num, :cid, '2026-09-01', 'SENT', 0, 0, 0, 0, :billed, :name)"
                ),
                {
                    "id": inv_id,
                    "num": str(inv_id),
                    "cid": cid,
                    "billed": billed,
                    "name": name,
                },
            )
    engine.dispose()

    command.upgrade(cfg, SHIP_TO_COUNTRY_REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, bill_to_name, ship_to_country FROM invoices ORDER BY id")
        ).all()
    assert [tuple(r) for r in rows] == [
        (10, "Maple St", "MX"),  # billed to the property, shipped the tenant's
        (11, "Maple St", "DE"),
        (12, "Oak Ave", None),
    ]
    engine.dispose()

    command.downgrade(cfg, "b4d8e2a6c7f1")
    engine = create_engine(f"sqlite:///{db}")
    names = {c["name"] for c in inspect(engine).get_columns("invoices")}
    assert "ship_to_country" not in names
    assert {"bill_to_name", "bill_to_country", "ship_address1"} <= names
    engine.dispose()


def test_an_invoice_with_money_on_it_stays_with_its_customer(
    client, db_session, seed_accounts
):
    """An invoice Alpha paid could be edited onto Beta, and Alpha's payment
    then showed on both customers' statements. While a payment or credit
    memo (a write-off is one) is applied to the invoice, a move is refused
    (409 money_applied), the sentence naming each, whose it is and what to
    do, and nothing changes; an edit that keeps the customer goes through.
    Once the money is off it, voided, the invoice moves."""
    alpha = _customer(client, "Alpha")
    beta = _customer(client, "Beta SARL")
    inv = _invoice(client, alpha["id"], 300, 1)
    n = inv["invoice_number"]
    pay = _payment(client, alpha["id"], 120, 5, [(inv["id"], 120)])
    assert pay.status_code == 201, pay.text

    def move(**extra):
        return client.put(
            f"/api/invoices/{inv['id']}", json={"customer_id": beta["id"], **extra}
        )

    r = move()
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "money_applied"
    assert detail["message"] == (
        f"Invoice #{n} cannot move to Beta SARL while money is applied to it: "
        "Alpha's payment of $120.00 (Sep 5, 2026). Void the payment first and "
        "enter it again where it belongs, then move the invoice."
    )
    assert [
        (a["kind"], a["id"], a["customer_id"], a["amount"], a["date"])
        for a in detail["applied"]
    ] == [("payment", pay.json()["id"], alpha["id"], "120.00", "2026-09-05")]
    # with other changes beside it, nothing of the edit is saved
    r = move(notes="moved", lines=[{"description": "Rent", "quantity": 1, "rate": 400}])
    assert r.status_code == 409, r.text
    db_session.expire_all()
    stored = db_session.get(Invoice, inv["id"])
    assert (stored.customer_id, stored.bill_to_name, stored.notes) == (
        alpha["id"],
        "Alpha",
        None,
    )
    assert stored.total == Decimal("300.00")
    assert _statement(db_session, beta["id"])["lines"] == []
    # an edit that keeps the customer (the form sends it as stored) is fine
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"customer_id": alpha["id"], "notes": "thanks"},
    )
    assert r.status_code == 200, r.text

    # a credit memo and a write-off are named too, oldest first
    cm = _credit_memo(client, alpha["id"], 30, 6)
    assert cm.status_code == 201, cm.text
    cm = cm.json()
    r = client.post(
        f"/api/credit-memos/{cm['id']}/apply",
        json={"invoice_id": inv["id"], "amount": 30},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/invoices/{inv['id']}/write-off",
        json={"date": "2026-09-07", "amount": 20},
    )
    assert r.status_code == 201, r.text
    wo = r.json()
    r = move()
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["message"] == (
        f"Invoice #{n} cannot move to Beta SARL while money is applied to it: "
        f"Alpha's payment of $120.00 (Sep 5, 2026), Alpha's credit memo "
        f"{cm['memo_number']} for $30.00 and Alpha's write-off "
        f"{wo['memo_number']} for $20.00. Void them first and enter them again "
        "where they belong, then move the invoice."
    )
    assert [a["kind"] for a in r.json()["detail"]["applied"]] == [
        "payment",
        "credit_memo",
        "write_off",
    ]

    # the way out: off the invoice, it moves, billed as Beta is
    assert client.post(f"/api/payments/{pay.json()['id']}/void").status_code == 200
    for memo in (cm, wo):
        r = client.post(f"/api/credit-memos/{memo['id']}/void")
        assert r.status_code == 200, r.text
    r = move()
    assert r.status_code == 200, r.text
    assert (r.json()["customer_id"], r.json()["bill_to_name"]) == (
        beta["id"],
        "Beta SARL",
    )

    # a tenant's invoice its property paid names the property's payment
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1000, 2)
    assert _payment(client, maple["id"], 1000, 5, [(rent["id"], 1000)]).status_code == (
        201
    )
    r = client.put(f"/api/invoices/{rent['id']}", json={"customer_id": beta["id"]})
    assert r.status_code == 409, r.text
    assert (
        "while money is applied to it: Maple St's payment of $1,000.00 (Sep 5, 2026)."
        in r.json()["detail"]["message"]
    )


def test_a_part_sent_empty_with_a_move_blanks_it(client, db_session, seed_accounts):
    """On a move, an address part sent as "" took the new customer's anyway,
    so the copied street could not be blanked. An edit takes a part as it
    is sent, "" or null blanking it, with a move as without one; a part
    left out keeps what is stored, or with a move is copied. Create keeps
    its rule: a part left out or sent empty is the customer's. The invoice
    form sends no address part, so its save never blanks one."""
    alpha = _customer(client, "Alpha", bill_address1="1 Solo Way", bill_city="Austin")
    beta = _customer(
        client,
        "Beta SARL",
        bill_address1="8 Rue des Lilas",
        bill_address2="Bat. B",
        bill_city="Lyon",
        bill_zip="69001",
        bill_country="FR",
        ship_address1="Quai 2",
        ship_city="Lyon",
        ship_country="FR",
    )
    inv = _invoice(client, alpha["id"], 300, 1)
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"customer_id": beta["id"], "bill_address2": "", "ship_address1": ""},
    )
    assert r.status_code == 200, r.text
    moved = r.json()
    assert (
        moved["bill_address1"],
        moved["bill_address2"],
        moved["bill_city"],
        moved["bill_zip"],
        moved["ship_address1"],
        moved["ship_city"],
    ) == ("8 Rue des Lilas", "", "Lyon", "69001", "", "Lyon")
    assert moved["ship_to_country"] == "FR"
    # the Ship To's street is blank, so no Ship To prints
    assert _printed(db_session, inv["id"]) == {
        "Bill To": ["Beta SARL", "8 Rue des Lilas", "Lyon, 69001", "France"]
    }
    # null blanks a part too, as it does on an edit that keeps the customer
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"customer_id": alpha["id"], "bill_city": None, "ship_to_country": ""},
    )
    assert r.status_code == 200, r.text
    back = r.json()
    assert (back["bill_address1"], back["bill_city"], back["ship_to_country"]) == (
        "1 Solo Way",
        None,
        "",
    )
    r = client.put(f"/api/invoices/{inv['id']}", json={"bill_address1": ""})
    assert r.status_code == 200, r.text
    assert r.json()["bill_address1"] == ""

    # create: left out or sent empty, a part is the customer's
    made = _invoice(
        client, beta["id"], 50, 2, bill_address2="", ship_city="", ship_to_country=""
    )
    assert (
        made["bill_address2"],
        made["ship_address1"],
        made["ship_city"],
        made["ship_to_country"],
    ) == ("Bat. B", "Quai 2", "Lyon", "FR")

    # the invoice form's save sends no address part: its edit keeps the
    # addresses, and its change of customer takes the new customer's whole
    js = _js("invoices.js")
    save = js[js.index("    async save(e, id) {") : js.index("async creditLimitOk(")]
    from app.services.sub_customers import INVOICE_ADDRESS_FIELDS

    assert "const data = {" in save
    assert not [f for f in INVOICE_ADDRESS_FIELDS + ("bill_to_",) if f in save]
    form = {
        "customer_id": beta["id"],
        "date": "2026-09-01",
        "due_date": None,
        "terms": "Net 30",
        "po_number": None,
        "is_pledge": False,
        "class_id": None,
        "job_id": None,
        "currency": None,
        "exchange_rate": None,
        "tax_rate": 0,
        "notes": None,
        "lines": [{"description": "Rent", "quantity": 1, "rate": 300}],
    }
    other = _invoice(client, alpha["id"], 300, 3)
    r = client.put(f"/api/invoices/{other['id']}", json=form)
    assert r.status_code == 200, r.text
    assert _printed(db_session, other["id"]) == {
        "Bill To": [
            "Beta SARL",
            "8 Rue des Lilas",
            "Bat. B",
            "Lyon, 69001",
            "France",
        ],
        "Ship To": ["Quai 2", "Lyon", "France"],
    }
    r = client.put(f"/api/invoices/{other['id']}", json={**form, "notes": "again"})
    assert r.status_code == 200, r.text
    assert (
        r.json()["bill_address2"] == "Bat. B" and r.json()["ship_address1"] == "Quai 2"
    )
