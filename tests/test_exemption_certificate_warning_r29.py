"""A tax-exempt customer whose exemption certificate is missing or expired
is warned about and never blocked.

A customer marked tax exempt is charged no sales tax whatever is on file,
and an expired certificate exempted the sale without a word. One function,
services/tax_exemption.certificate_warning, now says when the paper behind
the exemption is not there: nothing on file, or everything on file expired
as of the document's date. The sales forms and the customer's page ask the
endpoint that reads it, and a create or an update of a sales document
answers it in tax_note. No figure changes and nothing is refused."""

from datetime import date

from app.models.contacts import Customer
from app.services.tax_exemption import certificate_warning

MISSING = "No exemption certificate is on file for Cedar Hill School."
EXPIRED = "Cedar Hill School's exemption certificate expired on June 30, 2026."
DAY = "2026-09-21"
LINES = [{"description": "Benches", "quantity": 2, "rate": 150}]
CERT = {
    "exempt_certificate_number": "EX-4410",
    "exempt_certificate_state": "IL",
    "exempt_certificate_kind": "public_entity",
}


def _customer(db_session, name="Cedar Hill School", taxable=False):
    customer = Customer(name=name, is_active=True, is_taxable=taxable)
    db_session.add(customer)
    db_session.commit()
    return customer


def _permit(client, customer_id, expires=None, **more):
    r = client.post(
        "/api/reseller-permits",
        json={
            "entity_type": "customer",
            "entity_id": customer_id,
            "jurisdiction": "IL",
            "permit_number": "RP-2210",
            "expires_at": expires,
            **more,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _asked(client, customer_id, **query):
    r = client.get(f"/api/customers/{customer_id}/exemption-warning", params=query)
    assert r.status_code == 200, r.text
    return r.json()["warning"]


# ── the one function ─────────────────────────────────────────────────────


def test_a_taxable_customer_is_never_warned_about(client, db_session):
    customer = _customer(db_session, taxable=True)
    assert certificate_warning(db_session, customer, date(2026, 9, 21)) is None
    assert certificate_warning(db_session, None, date(2026, 9, 21)) is None


def test_no_certificate_on_file_says_so(client, db_session):
    customer = _customer(db_session)
    assert certificate_warning(db_session, customer, date(2026, 9, 21)) == MISSING


def test_an_expired_certificate_names_the_day_it_expired(client, db_session):
    customer = _customer(db_session)
    _permit(client, customer.id, "2026-06-30")
    assert certificate_warning(db_session, customer, date(2026, 9, 21)) == EXPIRED
    # as of the document's date: a sale dated while it still ran is quiet,
    # its last day included
    assert certificate_warning(db_session, customer, date(2026, 6, 30)) is None
    assert certificate_warning(db_session, customer, date(2026, 7, 1)) == EXPIRED
    # no date given is today, which is after it
    assert certificate_warning(db_session, customer) == EXPIRED


def test_one_current_certificate_is_enough(client, db_session):
    customer = _customer(db_session)
    _permit(client, customer.id, "2025-12-31")
    _permit(client, customer.id, "2026-06-30", permit_number="RP-2211")
    # every one expired: the latest expiry is the one named
    assert certificate_warning(db_session, customer, date(2026, 9, 21)) == EXPIRED
    _permit(client, customer.id, "2027-06-30", permit_number="RP-2212")
    assert certificate_warning(db_session, customer, date(2026, 9, 21)) is None


def test_a_certificate_with_no_expiry_date_never_expires(client, db_session):
    customer = _customer(db_session)
    _permit(client, customer.id, None)
    assert certificate_warning(db_session, customer, date(2031, 1, 1)) is None


def test_a_permit_set_inactive_or_another_customers_does_not_count(client, db_session):
    customer = _customer(db_session)
    other = _customer(db_session, name="Alder Street Church")
    _permit(client, other.id, "2027-06-30")
    _permit(client, customer.id, "2027-06-30", is_active=False)
    assert certificate_warning(db_session, customer, date(2026, 9, 21)) == MISSING


def test_the_documents_job_certificate_counts(client, db_session):
    customer = _customer(db_session)
    r = client.post(
        "/api/jobs",
        json={
            "customer_id": customer.id,
            "name": "Gym floor",
            "is_taxable": False,
            **CERT,
            "exempt_certificate_expires": "2026-06-30",
        },
    )
    assert r.status_code == 201, r.text
    job = r.json()["id"]
    # without the job, nothing is on file for the customer
    assert certificate_warning(db_session, customer, date(2026, 6, 1)) == MISSING
    assert certificate_warning(db_session, customer, date(2026, 6, 1), job) is None
    assert certificate_warning(db_session, customer, date(2026, 9, 21), job) == EXPIRED


# ── what the forms and the customer's page ask ───────────────────────────


def test_the_endpoint_answers_the_same_sentence(client, db_session):
    customer = _customer(db_session)
    assert _asked(client, customer.id, as_of=DAY) == MISSING
    _permit(client, customer.id, "2026-06-30")
    assert _asked(client, customer.id, as_of=DAY) == EXPIRED
    assert _asked(client, customer.id, as_of="2026-06-30") is None
    assert _asked(client, customer.id) == EXPIRED  # today
    taxable = _customer(db_session, name="Salt & Pine", taxable=True)
    assert _asked(client, taxable.id, as_of=DAY) is None
    assert client.get("/api/customers/999999/exemption-warning").status_code == 404


# ── tax_note on a create and an update: warned, never blocked ────────────


def _sale(customer_id, **more):
    return {
        "customer_id": customer_id,
        "date": DAY,
        "tax_rate": 0.0825,
        "lines": LINES,
        **more,
    }


def _exempt_figures(doc):
    assert float(doc["subtotal"]) == 300.0
    assert float(doc["tax_amount"]) == 0.0
    assert float(doc["total"]) == 300.0


def test_an_invoice_is_saved_exempt_and_says_what_is_missing(
    client, db_session, seed_accounts
):
    customer = _customer(db_session)
    r = client.post("/api/invoices", json=_sale(customer.id))
    assert r.status_code == 201, r.text
    inv = r.json()
    _exempt_figures(inv)
    assert inv["tax_exempt"] is True
    assert inv["tax_note"] == MISSING

    # an expired one: the update says so, and still charges nothing
    _permit(client, customer.id, "2026-06-30")
    r = client.put(f"/api/invoices/{inv['id']}", json={"notes": "Second floor"})
    assert r.status_code == 200, r.text
    _exempt_figures(r.json())
    assert r.json()["tax_note"] == EXPIRED

    # dated while the certificate ran: nothing to say
    r = client.put(f"/api/invoices/{inv['id']}", json={"date": "2026-06-15"})
    assert r.status_code == 200, r.text
    assert r.json()["tax_note"] is None

    # a read is not a create or an update: it carries no note
    assert client.get(f"/api/invoices/{inv['id']}").json()["tax_note"] is None


def test_an_estimate_says_it_on_create_and_update(client, db_session, seed_accounts):
    customer = _customer(db_session)
    r = client.post("/api/estimates", json=_sale(customer.id))
    assert r.status_code == 201, r.text
    est = r.json()
    _exempt_figures(est)
    assert est["tax_note"] == MISSING
    _permit(client, customer.id, "2026-06-30")
    r = client.put(f"/api/estimates/{est['id']}", json={"notes": "Revised"})
    assert r.status_code == 200, r.text
    _exempt_figures(r.json())
    assert r.json()["tax_note"] == EXPIRED


def test_a_sales_receipt_says_it_beside_its_warnings(client, db_session, seed_accounts):
    customer = _customer(db_session)
    r = client.post("/api/sales-receipts", json=_sale(customer.id))
    assert r.status_code == 201, r.text
    receipt = r.json()
    _exempt_figures(receipt["invoice"])
    assert receipt["tax_note"] == MISSING


def test_a_credit_memo_says_it(client, db_session, seed_accounts):
    customer = _customer(db_session)
    _permit(client, customer.id, "2026-06-30")
    r = client.post("/api/credit-memos", json=_sale(customer.id))
    assert r.status_code == 201, r.text
    memo = r.json()
    _exempt_figures(memo)
    assert memo["tax_note"] == EXPIRED


def test_a_recurring_invoice_says_it_as_of_its_next_invoice(
    client, db_session, seed_accounts
):
    customer = _customer(db_session)
    _permit(client, customer.id, "2026-06-30")
    body = {
        "customer_id": customer.id,
        "start_date": "2026-06-01",
        "frequency": "monthly",
        "tax_rate": 0.0825,
        "lines": LINES,
    }
    r = client.post("/api/recurring", json=body)
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["next_due"] == "2026-06-01" and first["tax_note"] is None
    r = client.post("/api/recurring", json={**body, "start_date": "2026-08-01"})
    assert r.status_code == 201, r.text
    assert r.json()["tax_note"] == EXPIRED
    r = client.put(f"/api/recurring/{r.json()['id']}", json={"notes": "Monthly"})
    assert r.status_code == 200, r.text
    assert r.json()["tax_note"] == EXPIRED


def test_the_jobs_certificate_counts_on_every_sales_kind(
    client, db_session, seed_accounts
):
    """A customer whose only certificate is the job's: every sales kind on
    that job is judged with it, the credit memo and the recurring invoice
    as the invoice is."""
    customer = _customer(db_session)
    r = client.post(
        "/api/jobs",
        json={
            "customer_id": customer.id,
            "name": "Gym floor",
            "is_taxable": False,
            **CERT,
            "exempt_certificate_expires": "2027-12-31",
        },
    )
    assert r.status_code == 201, r.text
    job = r.json()["id"]
    sale = {**_sale(customer.id), "job_id": job}
    schedule = {
        "customer_id": customer.id,
        "job_id": job,
        "start_date": DAY,
        "frequency": "monthly",
        "tax_rate": 0.0825,
        "lines": LINES,
    }
    notes = {}
    for path, body in (
        ("/api/invoices", sale),
        ("/api/estimates", sale),
        ("/api/sales-receipts", sale),
        ("/api/credit-memos", sale),
        ("/api/recurring", schedule),
    ):
        r = client.post(path, json=body)
        assert r.status_code == 201, (path, r.text)
        notes[path] = r.json()["tax_note"]
        if path == "/api/recurring":
            assert r.json()["job_id"] == job
            r = client.put(f"{path}/{r.json()['id']}", json={"notes": "Monthly"})
            assert r.status_code == 200, r.text
            notes[path + " update"] = r.json()["tax_note"]
    assert notes == dict.fromkeys(notes), notes
    # and off the job, the same customer has nothing on file
    for path, body in (
        ("/api/credit-memos", _sale(customer.id)),
        ("/api/recurring", {**schedule, "job_id": None}),
    ):
        r = client.post(path, json=body)
        assert r.status_code == 201, (path, r.text)
        assert r.json()["tax_note"] == MISSING, path


def test_a_current_certificate_and_a_taxable_customer_carry_no_note(
    client, db_session, seed_accounts
):
    exempt = _customer(db_session)
    _permit(client, exempt.id, "2027-06-30")
    r = client.post("/api/invoices", json=_sale(exempt.id))
    assert r.status_code == 201, r.text
    _exempt_figures(r.json())
    assert r.json()["tax_note"] is None

    taxable = _customer(db_session, name="Salt & Pine", taxable=True)
    r = client.post("/api/invoices", json=_sale(taxable.id))
    assert r.status_code == 201, r.text
    inv = r.json()
    # charged as before: the note is only ever about an exempt customer
    assert float(inv["tax_amount"]) == 24.75 and float(inv["total"]) == 324.75
    assert inv["tax_note"] is None


def test_the_warning_is_on_no_printed_document(client, db_session, seed_accounts):
    """The preview is the page the PDF is made from: the bookkeeper's
    warning is not on it."""
    customer = _customer(db_session)
    r = client.post("/api/invoices", json=_sale(customer.id))
    assert r.status_code == 201 and r.json()["tax_note"] == MISSING
    page = client.get(f"/api/invoices/{r.json()['id']}/print-preview")
    assert page.status_code == 200, page.text
    assert "No exemption certificate" not in page.text
    assert "certificate expired" not in page.text
