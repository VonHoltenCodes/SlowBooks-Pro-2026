"""A customer statement reads in date order and describes each document.

Explore 2.17.3 (skytech W-L9): the statement listed every invoice, then
every payment, so the running balance followed no order a customer could
check against their records, and the Description column printed the
invoice's notes (internal text, or the "Thank you for your business"
footer). It also subtracted voided payments and left credit memos out, so
its Balance Due could disagree with what the customer owes.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.contacts import Customer


@pytest.fixture
def acme(db_session):
    c = Customer(name="Acme Diner", is_active=True, bill_city="Port Alder")
    db_session.add(c)
    db_session.commit()
    return c


def _invoice(client, cid, amount, day, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "tax_rate": 0,
            "notes": "INTERNAL: slow payer, chase weekly",
            "lines": [{"description": "Sign", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, cid, amount, day, allocations=(), check=None):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "amount": amount,
            "method": "Check",
            "check_number": check,
            "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _scenario(client, acme):
    first = _invoice(client, acme.id, 100, 1, po_number="PO-77")
    _pay(client, acme.id, 60, 2, [(first["id"], 60)], check="4420")
    second = _invoice(client, acme.id, 50, 3)
    dead = _pay(client, acme.id, 999, 4, check="9999")
    assert client.post(f"/api/payments/{dead['id']}/void").status_code == 200
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": acme.id,
            "date": "2026-09-05",
            "lines": [{"description": "Return", "quantity": 1, "rate": 10}],
        },
    )
    assert r.status_code == 201, r.text
    _pay(client, acme.id, 25, 6)  # nothing applied
    return first, second


def test_lines_are_in_date_order_with_a_running_balance(
    client, db_session, seed_accounts, acme
):
    from app.routes.reports.receivables import statement_activity

    first, second = _scenario(client, acme)
    act = statement_activity(db_session, acme, date(2026, 9, 30))
    got = [
        (ln["type"], ln["number"], ln["amount"], ln["balance"]) for ln in act["lines"]
    ]
    assert got == [
        ("Invoice", first["invoice_number"], Decimal("100.00"), Decimal("100.00")),
        ("Payment", "4420", Decimal("-60.00"), Decimal("40.00")),
        ("Invoice", second["invoice_number"], Decimal("50.00"), Decimal("90.00")),
        # the voided payment of the 4th is not on it
        ("Credit Memo", "CM-0001", Decimal("-10.00"), Decimal("80.00")),
        ("Payment", "", Decimal("-25.00"), Decimal("55.00")),
    ]
    assert act["balance_due"] == Decimal("55.00")
    # ...which is what the customer owes
    owed = Decimal(str(client.get(f"/api/customers/{acme.id}").json()["balance"]))
    assert owed == act["balance_due"]


def test_each_line_describes_its_document_not_the_notes(
    client, db_session, seed_accounts, acme
):
    from app.routes.reports.receivables import statement_activity

    first, _ = _scenario(client, acme)
    lines = statement_activity(db_session, acme, date(2026, 9, 30))["lines"]
    desc = [ln["description"] for ln in lines]
    assert not any("INTERNAL" in d for d in desc)
    assert desc[0].startswith("PO PO-77 · Due ")
    assert desc[1] == f"Check · applied to #{first['invoice_number']}"
    assert desc[4] == "Check · not applied to an invoice yet"


def test_the_pdf_prints_the_lines_in_order(client, db_session, seed_accounts, acme):
    from app.routes.reports.receivables import statement_activity
    from app.services.pdf_service import _render
    from app.services.settings_service import get_all_settings

    first, second = _scenario(client, acme)
    html = _render(
        "statement_pdf.html",
        get_all_settings(db_session),
        customer=acme,
        activity=statement_activity(db_session, acme, date(2026, 9, 30)),
        as_of_date=date(2026, 9, 30),
    )
    assert "INTERNAL" not in html
    body = html[html.index("<tbody>") :]
    assert (
        body.index(first["invoice_number"])
        < body.index("4420")
        < body.index(second["invoice_number"])
    )
    assert "-$60.00" in body and "$55.00" in html
    r = client.get(
        f"/api/reports/customer-statement/{acme.id}/pdf?as_of_date=2026-09-30"
    )
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
