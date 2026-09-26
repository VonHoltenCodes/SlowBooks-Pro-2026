"""Settings -> Invoice prefix, Next invoice # and Invoice footer are used
(2.17.3 exploratory: skytech W-M3, macbase1 F6 / F20).

All three were saved and never read: with a prefix of "INV-" invoices were
still numbered 1001..., Next invoice # showed 1001 while invoices ran to
1005, and the footer appeared on no PDF. Estimates' prefix and counter
always worked; invoices now follow the same rule — the prefix plus the
larger of the counter and one past the highest number used — and the
counter moves on as invoices are made. Existing numbers never change.
"""

from datetime import date
from decimal import Decimal

from app.services import pdf_service


def _settings(client, **values):
    r = client.put("/api/settings", json=values)
    assert r.status_code == 200, r.text


def _invoice(client, customer_id):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Banner", "quantity": 1, "rate": 120}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _numbers(client, customer_id, n):
    return [_invoice(client, customer_id)["invoice_number"] for _ in range(n)]


def test_the_invoice_prefix_is_used(client, seed_accounts, seed_customer):
    _settings(client, invoice_prefix="INV-")
    assert _numbers(client, seed_customer.id, 2) == ["INV-1001", "INV-1002"]


def test_the_next_number_is_honoured_and_moves_on(client, seed_accounts, seed_customer):
    _settings(client, invoice_next_number="5000")
    assert _numbers(client, seed_customer.id, 2) == ["5000", "5001"]
    # Settings shows the number the next invoice will get
    assert client.get("/api/settings").json()["invoice_next_number"] == "5002"


def test_the_counter_follows_the_invoices(client, seed_accounts, seed_customer):
    assert _numbers(client, seed_customer.id, 3) == ["1001", "1002", "1003"]
    assert client.get("/api/settings").json()["invoice_next_number"] == "1004"


def test_a_lower_counter_never_reuses_a_number(client, seed_accounts, seed_customer):
    _numbers(client, seed_customer.id, 2)
    _settings(client, invoice_next_number="500")
    assert _numbers(client, seed_customer.id, 1) == ["1003"]


def test_turning_a_prefix_on_continues_and_keeps_existing_numbers(
    client, seed_accounts, seed_customer
):
    first = _numbers(client, seed_customer.id, 2)
    _settings(client, invoice_prefix="HLB-")
    assert _numbers(client, seed_customer.id, 1) == ["HLB-1003"]
    listed = {i["invoice_number"] for i in client.get("/api/invoices").json()}
    assert listed == {*first, "HLB-1003"}


def test_a_typed_counter_can_start_a_new_prefix_over(
    client, seed_accounts, seed_customer
):
    _numbers(client, seed_customer.id, 2)
    _settings(client, invoice_prefix="2026-", invoice_next_number="0001")
    assert _numbers(client, seed_customer.id, 2) == ["2026-0001", "2026-0002"]


def test_an_imported_series_below_the_default_carries_on(
    client, db_session, seed_accounts, seed_customer
):
    """The counter's untouched default is not a choice: a file whose
    imported invoices run to 0099 goes on to 0100, as it always did, rather
    than jumping to 1001 on upgrade."""
    from app.models.invoices import Invoice

    for number in ("0098", "0099"):
        db_session.add(
            Invoice(
                invoice_number=number,
                customer_id=seed_customer.id,
                date=date(2026, 1, 5),
                total=Decimal("10"),
                balance_due=Decimal("10"),
            )
        )
    db_session.commit()
    assert _numbers(client, seed_customer.id, 1) == ["0100"]


def test_the_invoice_footer_prints_on_the_invoice(
    client, seed_accounts, seed_customer, monkeypatch
):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    footer = "Harbor Light Bakery · 418 Wharf Street · Port Alder"
    _settings(client, invoice_footer=footer)
    inv = _invoice(client, seed_customer.id)
    for page in ("pdf", "print-preview"):
        html = client.get(f"/api/invoices/{inv['id']}/{page}").text
        assert f'<div class="invoice-footer">{footer}</div>' in html, page
