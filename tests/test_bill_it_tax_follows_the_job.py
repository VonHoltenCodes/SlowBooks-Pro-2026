"""A field ticket's Bill it invoice is taxed as the job is (owner, 2.32.0
round 23).

#1129 started a sales document sent without a tax_rate at the company's
Default Tax Rate, as the forms start one, and Bill it's invoice with it
(it had been an accidental 0%). Taxing it is right, but a job can be tax
exempt (#864), and the rate must follow the job. The invoice form's rule
is the rule here: a new invoice for an exempt job starts with its "Tax
exempt" switch on (the job's, else the customer's), every line untaxed, the
job's certificate copied onto it (noted expired on the PDF when it is), and
the company rate kept. Bill it makes exactly what that form would; so does
the API when a job_id comes with no tax_rate; a rate sent is kept."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.invoices import Invoice
from app.models.transactions import TransactionLine
from tests.test_field_tickets import LABOR, _job, _signed
from tests.test_void_revenue_cost_codes_new262 import books_agree

CERT = {
    "is_taxable": False,
    "exempt_certificate_number": "EX-4410",
    "exempt_certificate_state": "IL",
    "exempt_certificate_kind": "public_entity",
}


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


@pytest.fixture
def con(client, seed_accounts):
    _ok(client.put("/api/settings", json={"company_type": "construction"}))
    _ok(client.put("/api/settings", json={"default_tax_rate": "8.25"}))
    return client


def _customer(client, name, taxable=True):
    return _ok(
        client.post("/api/customers", json={"name": name, "is_taxable": taxable}),
        201,
    )["id"]


JOBS = {
    "exempt job": (True, CERT),
    "taxable job": (True, {}),
    "exempt customer, taxable job": (False, {}),
    "expired certificate": (
        True,
        {**CERT, "exempt_certificate_expires": "2026-01-31"},
    ),
}


def _sales_tax(db_session):
    acct = db_session.query(Account).filter(Account.account_number == "2200").one()
    return sum(
        (
            Decimal(str(r.credit or 0)) - Decimal(str(r.debit or 0))
            for r in db_session.query(TransactionLine).filter_by(account_id=acct.id)
        ),
        Decimal("0"),
    )


def _expect(inv, case):
    taxed = case == "taxable job"
    assert float(inv["tax_rate"]) == 0.0825, inv["tax_rate"]
    assert float(inv["tax_amount"]) == (49.50 if taxed else 0.0), case
    assert inv["tax_exempt"] is (not taxed), case
    assert all(ln["is_taxable"] is taxed for ln in inv["lines"]), case
    if case in ("exempt job", "expired certificate"):
        assert inv["exempt_certificate_number"] == "EX-4410"


@pytest.mark.parametrize("case", list(JOBS))
def test_bill_it_is_taxed_as_the_job(con, db_session, case):
    taxable_customer, job_fields = JOBS[case]
    cid = _customer(con, f"Owner {case}", taxable_customer)
    job = _job(con, cid, name=f"Job {case}", **job_fields)
    a = _signed(con, job["id"], [LABOR])  # 600.00
    billed = _ok(
        con.post(
            "/api/field-tickets/bill",
            json={"ticket_ids": [a["id"]], "date": "2026-06-30"},
        )
    )
    inv = _ok(con.get(f"/api/invoices/{billed['invoice_id']}"))
    _expect({**inv, "lines": inv["lines"]}, case)
    assert _sales_tax(db_session) == (Decimal("49.50") if case == "taxable job" else 0)
    if case == "expired certificate":
        from app.services.tax_exemption import exemption_line

        line = exemption_line(db_session.get(Invoice, inv["id"]))
        assert line.endswith("(expired)"), line
    books_agree(con)


@pytest.mark.parametrize("case", list(JOBS))
def test_the_api_with_a_job_and_no_rate_is_taxed_as_the_job(con, db_session, case):
    taxable_customer, job_fields = JOBS[case]
    cid = _customer(con, f"Buyer {case}", taxable_customer)
    job = _job(con, cid, name=f"Site {case}", **job_fields)
    inv = _ok(
        con.post(
            "/api/invoices",
            json={
                "customer_id": cid,
                "job_id": job["id"],
                "date": "2026-06-30",
                "lines": [{"description": "Work", "quantity": 8, "rate": 75}],
            },
        ),
        201,
    )
    _expect(inv, case)
    # a rate sent is kept as given; the job's exemption still unticks lines
    sent = _ok(
        con.post(
            "/api/invoices",
            json={
                "customer_id": cid,
                "job_id": job["id"],
                "date": "2026-06-30",
                "tax_rate": 0.05,
                "lines": [{"description": "Work", "quantity": 8, "rate": 75}],
            },
        ),
        201,
    )
    assert float(sent["tax_rate"]) == 0.05
    assert float(sent["tax_amount"]) == (30.0 if case == "taxable job" else 0.0)
    books_agree(con)


def test_the_sales_tax_report_names_the_exempt_sale(con, db_session):
    cid = _customer(con, "Village Board")
    job = _job(con, cid, name="Village Hall", **CERT)
    a = _signed(con, job["id"], [LABOR])
    _ok(
        con.post(
            "/api/field-tickets/bill",
            json={"ticket_ids": [a["id"]], "date": date.today().isoformat()},
        )
    )
    report = _ok(con.get("/api/reports/sales-tax"))
    text = str(report)
    assert "Public entity" in text or "public_entity" in text, text[:2000]
