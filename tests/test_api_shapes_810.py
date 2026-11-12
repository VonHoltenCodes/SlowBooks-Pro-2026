"""The response shapes settled before the 2.32.0 API freeze (#810).

Each answers the way its siblings do: an invoice and a bill name the term
they are tied to, a new sales receipt names its customer at the top as the
list does, a recurring invoice's Generate reads the body as memorized Enter
now does, the field-ticket purchase-order picker is construction mode's as
the tickets are, a tax form's quarter is refused as its year is, the older
Form 940 route carries the credit reduction the newer one does, and a 422
echoes at most 200 characters of what was sent.
"""

from decimal import Decimal

import pytest

from app.models.invoices import Invoice
from app.services.settings_service import set_setting

LINE = {"description": "Service", "quantity": 1, "rate": "100.00"}


@pytest.fixture(autouse=True)
def _chart(seed_accounts):
    return seed_accounts


def _term(client, name="Net 21", days=21):
    r = client.post("/api/payment-terms", json={"name": name, "net_days": days})
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(client):
    expense = next(
        a["id"]
        for a in client.get("/api/accounts").json()
        if a["account_number"] == "6000"
    )
    r = client.post(
        "/api/vendors",
        json={"name": "Gulf Supply", "default_expense_account_id": expense},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ── 1. payment_term_id on the invoice and the bill ──────────────────────


def test_an_invoice_answers_the_term_it_is_tied_to(client, seed_customer):
    term = _term(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "terms": "Net 21",
            "payment_term_id": term["id"],
            "lines": [LINE],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["payment_term_id"] == term["id"]
    inv_id = r.json()["id"]
    assert client.get(f"/api/invoices/{inv_id}").json()["payment_term_id"] == term["id"]
    listed = [i for i in client.get("/api/invoices").json() if i["id"] == inv_id]
    assert listed[0]["payment_term_id"] == term["id"]


def test_an_invoice_tied_to_no_term_answers_null(client, seed_customer):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "terms": "15th of following month",
            "lines": [LINE],
        },
    )
    assert r.status_code == 201, r.text
    assert "payment_term_id" in r.json()
    assert r.json()["payment_term_id"] is None


def test_a_bill_answers_the_term_it_is_tied_to(client):
    term = _term(client)
    vendor = _vendor(client)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-810",
            "date": "2026-10-05",
            "terms": "Net 21",
            "payment_term_id": term["id"],
            "lines": [{"description": "Lumber", "quantity": 1, "rate": "500.00"}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["payment_term_id"] == term["id"]
    bill_id = r.json()["id"]
    assert client.get(f"/api/bills/{bill_id}").json()["payment_term_id"] == term["id"]


def test_the_openapi_schema_describes_payment_term_id(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("InvoiceResponse", "BillResponse"):
        field = schemas[name]["properties"]["payment_term_id"]
        assert "tied to" in field["description"]


# ── 2. a new sales receipt names its customer ───────────────────────────


def _receipt(client, **extra):
    body = {"date": "2026-04-01", "method": "Cash", "lines": [LINE], **extra}
    r = client.post("/api/sales-receipts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_new_sales_receipt_answers_its_customer_name_as_the_list_does(
    client, seed_customer
):
    data = _receipt(client, customer_id=seed_customer.id)
    assert data["customer_name"] == "Test Customer"
    assert data["invoice"]["customer_name"] == "Test Customer"
    listed = client.get("/api/sales-receipts").json()
    assert listed[0]["customer_name"] == data["customer_name"]


def test_a_counter_sale_answers_the_walk_in_customers_name(client):
    data = _receipt(client)
    assert data["customer_name"] == "Walk-in Customer"


# ── 3. Generate reads occurrence from the body too ──────────────────────


def _schedule(client, customer_id, start="2026-01-01"):
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": customer_id,
            "frequency": "monthly",
            "start_date": start,
            "lines": [LINE],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_generate_reads_the_occurrence_sent_in_the_body(client, seed_customer):
    rec_id = _schedule(client, seed_customer.id)
    # not the next installment: refused, where the body used to be ignored
    # and the next one made
    r = client.post(
        f"/api/recurring/{rec_id}/generate", json={"occurrence": "2026-02-01"}
    )
    assert r.status_code == 409, r.text
    r = client.post(
        f"/api/recurring/{rec_id}/generate", json={"occurrence": "2026-01-01"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["occurrence"] == "2026-01-01"
    assert r.json()["next_due"] == "2026-02-01"


def test_generate_reads_dated_from_the_body(client, db_session, seed_customer):
    rec_id = _schedule(client, seed_customer.id)
    r = client.post(
        f"/api/recurring/{rec_id}/generate",
        json={"occurrence": "2026-01-01", "dated": "2026-01-05"},
    )
    assert r.status_code == 200, r.text
    inv = db_session.get(Invoice, r.json()["invoice_ids"][0])
    assert inv.date.isoformat() == "2026-01-05"


def test_generate_still_reads_the_query(client, seed_customer):
    rec_id = _schedule(client, seed_customer.id)
    r = client.post(f"/api/recurring/{rec_id}/generate?occurrence=2026-01-01")
    assert r.status_code == 200, r.text
    assert r.json()["occurrence"] == "2026-01-01"


def test_the_same_occurrence_in_the_query_and_the_body_is_entered(
    client, seed_customer
):
    rec_id = _schedule(client, seed_customer.id)
    r = client.post(
        f"/api/recurring/{rec_id}/generate?occurrence=2026-01-01",
        json={"occurrence": "2026-01-01"},
    )
    assert r.status_code == 200, r.text


def test_two_different_occurrences_are_a_422_and_nothing_is_made(
    client, db_session, seed_customer
):
    rec_id = _schedule(client, seed_customer.id)
    r = client.post(
        f"/api/recurring/{rec_id}/generate?occurrence=2026-01-01",
        json={"occurrence": "2026-02-01"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "The occurrence date was sent twice, as 2026-01-01 in the query and "
        "2026-02-01 in the body. Send it once, or the same date both ways."
    )
    assert db_session.query(Invoice).count() == 0


def test_an_unknown_field_in_the_generate_body_is_refused_by_name(
    client, seed_customer
):
    rec_id = _schedule(client, seed_customer.id)
    r = client.post(f"/api/recurring/{rec_id}/generate", json={"when": "2026-01-01"})
    assert r.status_code == 422
    assert "'when'" in r.json()["detail"][0]["message"]


# ── 4. the purchase-order picker is construction mode's ─────────────────


@pytest.mark.parametrize("company_type", ["business", "nonprofit"])
def test_the_field_ticket_purchase_orders_are_a_409_outside_construction(
    client, company_type
):
    r = client.put("/api/settings", json={"company_type": company_type})
    assert r.status_code == 200, r.text
    tickets = client.get("/api/field-tickets")
    orders = client.get("/api/field-tickets/purchase-orders?job_id=1")
    assert tickets.status_code == orders.status_code == 409
    assert orders.json()["detail"] == tickets.json()["detail"]


def test_the_field_ticket_purchase_orders_answer_in_construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    r = client.get("/api/field-tickets/purchase-orders?job_id=1")
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── 5. a tax form's quarter is refused as its year is ───────────────────


@pytest.mark.parametrize("quarter", [0, 5])
def test_a_liability_quarter_outside_one_to_four_is_a_422_naming_them(client, quarter):
    r = client.get(f"/api/tax-forms/liability?year=2026&quarter={quarter}")
    assert r.status_code == 422
    (entry,) = r.json()["detail"]
    assert entry["loc"] == ["query", "quarter"]
    assert entry["message"] == "Quarter must be 1, 2, 3 or 4."


def test_a_liability_year_past_9999_is_the_same_shape(client):
    r = client.get("/api/tax-forms/liability?year=10000&quarter=4")
    assert r.status_code == 422
    assert r.json()["detail"][0]["message"] == "Year must be 9999 or less."


@pytest.mark.parametrize(
    "path",
    [
        "/api/tax-forms/941?year=2026&quarter=5",
        "/api/tax-forms/941/pdf?year=2026&quarter=5",
        "/api/tax-forms/sui?year=2026&quarter=5",
    ],
)
def test_the_other_quarterly_forms_refuse_quarter_five_the_same_way(client, path):
    r = client.get(path)
    assert r.status_code == 422
    assert r.json()["detail"][0]["message"] == "Quarter must be 1, 2, 3 or 4."


def test_a_liability_quarter_in_range_still_answers(client):
    r = client.get("/api/tax-forms/liability?year=2026&quarter=4")
    assert r.status_code == 200, r.text


# ── 6. the older Form 940 route carries the credit reduction ────────────


def _employee(client, state):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Ines",
            "last_name": "Moreno",
            "pay_type": "salary",
            "pay_rate": 104000,
            "pay_frequency": "biweekly",
            "filing_status": "single",
            "work_state": state,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, emp_id, pay, gross):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": f"{pay[:8]}01",
            "period_end": pay,
            "pay_date": pay,
            "stubs": [{"employee_id": emp_id, "gross_override": gross}],
        },
    )
    assert r.status_code == 201, r.text
    p = client.post(f"/api/payroll/{r.json()['id']}/process")
    assert p.status_code == 200, p.text


def test_the_older_940_route_carries_the_newer_routes_credit_reduction(
    client, db_session
):
    set_setting(db_session, "company_state", "CA")
    db_session.commit()
    emp = _employee(client, "CA")
    _run(client, emp["id"], "2025-11-14", 4000)
    _run(client, emp["id"], "2025-11-28", 4000)

    old = client.post("/api/payroll/forms/940/2025")
    assert old.status_code == 200, old.text
    new = client.get("/api/tax-forms/940?year=2025")
    assert new.status_code == 200, new.text
    old, new = old.json(), new.json()
    assert old["credit_reduction_state"] == new["credit_reduction_state"] == "CA"
    assert Decimal(old["credit_reduction_rate"]) == Decimal(
        str(new["credit_reduction_rate"])
    )
    assert old["credit_reduction"] == "84.00"
    assert Decimal(old["credit_reduction"]) == Decimal(str(new["credit_reduction"]))
    assert old["total_futa_tax_after_adjustments"] == "126.00"


def test_the_older_940_route_says_zero_for_a_state_with_no_reduction(
    client, db_session
):
    set_setting(db_session, "company_state", "WA")
    db_session.commit()
    emp = _employee(client, "WA")
    _run(client, emp["id"], "2025-11-14", 4000)
    r = client.post("/api/payroll/forms/940/2025")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["credit_reduction_state"] is None
    assert data["credit_reduction_rate"] == "0"
    assert data["credit_reduction"] == "0.00"
    assert data["total_futa_tax_after_adjustments"] == data["box_2"]


# ── 7. a 422 echoes at most 200 characters ──────────────────────────────


def test_a_422_trims_a_long_echoed_value_and_keeps_the_message(client):
    long_name = "N" * 5000
    r = client.post("/api/customers", json={"name": long_name})
    assert r.status_code == 422, r.text
    (entry,) = [e for e in r.json()["detail"] if e["loc"][-1] == "name"]
    assert entry["input"] == "N" * 200 + "…"
    assert entry["message"].startswith("Name must be")
    assert long_name not in r.text


def test_a_422_trims_text_inside_an_echoed_list_or_object(client):
    r = client.post("/api/customers", json={"name": {"first": "x" * 5000}})
    assert r.status_code == 422, r.text
    (entry,) = [e for e in r.json()["detail"] if e["loc"][-1] == "name"]
    assert entry["input"] == {"first": "x" * 200 + "…"}
    r = client.post("/api/customers", json=["y" * 5000])
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["input"] == ["y" * 200 + "…"]


def test_a_short_echoed_value_is_left_whole(client):
    r = client.get("/api/tax-forms/liability?year=10000&quarter=4")
    assert r.json()["detail"][0]["input"] == "10000"
