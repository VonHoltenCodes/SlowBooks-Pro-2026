"""Past Customers (#436): a mailing list built from the books.

One row per customer the books know (an invoice or a job), with the service
address (job site_address, else ship-to, else bill-to), the first/last job
dates, lifetime sales, and the last job's type and name — plus a CSV in a
mail-merge shape and an Avery 5160 labels PDF. A Do Not Contact note marks a
row and keeps it out of the export unless asked.
"""

from datetime import date
from pathlib import Path

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.jobs import Job
from app.services import pdf_service

ROOT = Path(__file__).resolve().parents[1]


def _invoice(db, customer_id, when, subtotal, *, sales_receipt=False, number=None):
    db.add(
        Invoice(
            invoice_number=number or f"INV-{customer_id}-{when.isoformat()}",
            customer_id=customer_id,
            date=when,
            status=InvoiceStatus.PAID,
            subtotal=subtotal,
            total=subtotal,
            is_sales_receipt=sales_receipt,
        )
    )


@pytest.fixture
def seed_past(db_session):
    """A, B, C, D, E as the brief lays them out."""
    a = Customer(
        name="Acme Signs", company="Acme Signs LLC", phone="555-1000", is_active=True
    )
    b = Customer(
        name="Belle Bakery",
        is_active=True,
        ship_address1="88 Harbor Rd",
        ship_city="Portland",
        ship_state="OR",
        ship_zip="97201",
    )
    c = Customer(
        name="Crane Co",
        is_active=True,
        notes="Do not contact — moved out of state",
        bill_address1="5 Pine Ave",
        bill_city="Reno",
        bill_state="NV",
        bill_zip="89501",
        donor_type="organization",
    )
    d = Customer(name="Dormant LLC", is_active=True)  # no invoice, no job
    e = Customer(name="Echo Shop", is_active=False)
    db_session.add_all([a, b, c, d, e])
    db_session.flush()

    db_session.add(
        Job(
            customer_id=a.id,
            name="Storefront sign",
            job_type="Signage",
            site_address="12 Oak St\nAustin, TX 78701",
            end_date=date(2024, 6, 1),
        )
    )
    _invoice(db_session, a.id, date(2023, 5, 1), 100)
    _invoice(db_session, a.id, date(2024, 9, 1), 200, sales_receipt=True)
    _invoice(db_session, b.id, date(2021, 3, 15), 400)
    _invoice(db_session, c.id, date(2019, 7, 20), 50)
    _invoice(db_session, e.id, date(2020, 4, 10), 75)
    db_session.commit()
    return {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "e": e.id}


def _rows(client, qs=""):
    r = client.get(f"/api/customers/past{qs}")
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {row["customer_id"]: row for row in body["rows"]}


# ── rows and addresses ─────────────────────────────────────────────────────


def test_only_customers_with_books_are_listed(client, seed_past):
    body, rows = _rows(client)
    assert set(rows) == {seed_past["a"], seed_past["b"], seed_past["c"], seed_past["e"]}
    assert seed_past["d"] not in rows  # no invoice and no job
    assert body["total_rows"] == 4


def test_the_address_comes_from_the_job_then_ship_then_bill(client, seed_past):
    _, rows = _rows(client)
    a = rows[seed_past["a"]]
    assert a["address_source"] == "job"
    assert a["address1"] == "12 Oak St"
    assert (a["city"], a["state"], a["zip"]) == ("Austin", "TX", "78701")
    assert a["service_address"] == "12 Oak St, Austin, TX 78701"

    b = rows[seed_past["b"]]
    assert b["address_source"] == "ship"
    assert (b["city"], b["state"], b["zip"]) == ("Portland", "OR", "97201")

    c = rows[seed_past["c"]]
    assert c["address_source"] == "bill"
    assert c["address1"] == "5 Pine Ave"


def test_first_and_last_job_and_counts(client, seed_past):
    _, rows = _rows(client)
    a = rows[seed_past["a"]]
    # first = earliest invoice; last = the later of the 2024 invoice and the
    # job's end_date (2024-09-01 > 2024-06-01)
    assert a["first_job"] == "2023-05-01"
    assert a["last_job"] == "2024-09-01"
    assert a["job_count"] == 1  # one job record
    assert a["lifetime_sales"] == 300.0
    assert a["last_job_type"] == "Signage"
    assert a["last_job_name"] == "Storefront sign"
    # a customer with only invoices counts each invoice as a job
    assert rows[seed_past["b"]]["job_count"] == 1


# ── filters ────────────────────────────────────────────────────────────────


def test_last_job_date_range(client, seed_past):
    _, rows = _rows(client, "?last_job_from=2020-01-01&last_job_to=2022-12-31")
    assert set(rows) == {seed_past["b"], seed_past["e"]}


def test_active_filter_drops_the_inactive_customer(client, seed_past):
    _, rows = _rows(
        client, "?last_job_from=2020-01-01&last_job_to=2022-12-31&active=active"
    )
    assert set(rows) == {seed_past["b"]}


def test_city_and_zip_filters(client, seed_past):
    _, by_city = _rows(client, "?city=austin")
    assert set(by_city) == {seed_past["a"]}
    _, by_zip = _rows(client, "?zip=787")
    assert set(by_zip) == {seed_past["a"]}


def test_min_sales_and_customer_type(client, seed_past):
    _, rich = _rows(client, "?min_sales=250")
    assert set(rich) == {seed_past["a"], seed_past["b"]}  # 300 and 400
    _, orgs = _rows(client, "?customer_type=organization")
    assert set(orgs) == {seed_past["c"]}


# ── Do Not Contact ─────────────────────────────────────────────────────────


def test_do_not_contact_is_flagged_but_still_returned(client, seed_past):
    _, rows = _rows(client)
    assert rows[seed_past["c"]]["do_not_contact"] is True
    assert rows[seed_past["a"]]["do_not_contact"] is False


def test_the_csv_omits_do_not_contact_unless_asked(client, seed_past):
    default = client.get("/api/customers/past.csv").text
    assert "Crane Co" not in default
    opted = client.get("/api/customers/past.csv?include_do_not_contact=1").text
    assert "Crane Co" in opted


# ── CSV shape ──────────────────────────────────────────────────────────────


def test_csv_header_guard_bom_and_filename(client, db_session, seed_past):
    # a formula-shaped company name is neutralised on export
    db_session.add(
        Customer(name="Hacky", company="=cmd|'/c calc'", is_active=True, phone="x")
    )
    db_session.flush()
    hid = db_session.query(Customer).filter_by(name="Hacky").one().id
    _invoice(db_session, hid, date(2024, 1, 1), 10)
    db_session.commit()

    r = client.get(
        "/api/customers/past.csv?last_job_from=2020-01-01&last_job_to=2024-12-31"
    )
    body = r.text
    assert body.startswith("﻿")  # BOM
    header = body.lstrip("﻿").splitlines()[0]
    assert header == (
        "Name,Company,Address 1,Address 2,City,State,Zip,Service address,"
        "Phone,Email,First job,Last job,Jobs,Lifetime sales,"
        "Last job type,Last job name"
    )
    assert "'=cmd" in body  # the formula guard's leading apostrophe
    assert (
        r.headers["content-disposition"]
        == "attachment; filename=Past-Customers_2020-01-01_2024-12-31.csv"
    )


# ── labels PDF (HTML through the template; renderer monkeypatched) ──────────


@pytest.fixture
def html_pdf(monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


def test_labels_omit_do_not_contact_unless_asked(client, seed_past, html_pdf):
    html = client.get("/api/customers/past/labels.pdf").content.decode()
    assert "Crane Co" not in html
    opted = client.get(
        "/api/customers/past/labels.pdf?include_do_not_contact=1"
    ).content.decode()
    assert "Crane Co" in opted


def test_labels_break_every_thirty(client, db_session, html_pdf):
    for i in range(31):
        cust = Customer(
            name=f"Mailer {i:02d}",
            is_active=True,
            bill_address1=f"{i} Main St",
            bill_city="Tampa",
            bill_state="FL",
            bill_zip="33601",
        )
        db_session.add(cust)
        db_session.flush()
        _invoice(db_session, cust.id, date(2024, 1, 1), 10, number=f"M{i}")
    db_session.commit()

    html = client.get("/api/customers/past/labels.pdf").content.decode()
    assert html.count('class="label"') == 31
    assert "page-break" in html
    first_sheet = html[
        html.index('class="sheet') : html.index(
            'class="sheet', html.index('class="sheet') + 1
        )
    ]
    assert first_sheet.count('class="label"') == 30


# ── the SPA wiring exists ──────────────────────────────────────────────────


def test_the_report_center_card_and_view_and_customers_link_exist():
    reports = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert "card('past-customers'," in reports
    assert "'past-customers':" in reports  # the _VIEWS registry entry
    assert "pastCustomers" in reports
    customers = (ROOT / "app/static/js/customers.js").read_text(encoding="utf-8")
    assert "#/reports/past-customers" in customers
