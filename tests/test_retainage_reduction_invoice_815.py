"""Pay documents name the retainage a pay application gives back (#815).

A pay application that lowers the retainage percent and bills no new work
posts no lines and a negative hold: the PDF printed an empty table, a $0.00
total and "Retainage withheld this period --$1,500.00". A hold-and-release
pay page showed the total and the balance with no retainage rows between.
"""

import re

from app.models.invoices import Invoice
from app.services import pdf_service
from app.services.settings_service import get_all_settings
from tests.test_pay_applications import _approve, _edit, _new_app, _two_line_job


def _first_application(client, customer_id):
    """Bill 30,000 with 10% held (3,000)."""
    job, sov = _two_line_job(client, customer_id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert _approve(client, a1["id"]).status_code == 200
    return job, sov


def _approved(client, job_id, **body):
    app = _new_app(client, job_id)
    assert _edit(client, app["id"], **body).status_code == 200
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    return r.json()["invoice_id"], app["number"]


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _pdf_text(db_session, inv_id):
    inv = db_session.get(Invoice, inv_id)
    return _text(pdf_service.render_invoice_html(inv, get_all_settings(db_session)))


def test_a_lower_percent_with_no_work_names_the_reduction(
    client, db_session, seed_accounts, seed_customer
):
    job, _ = _first_application(client, seed_customer.id)
    inv_id, number = _approved(client, job["id"], retainage_pct=5)
    inv = client.get(f"/api/invoices/{inv_id}").json()
    assert inv["lines"] == [] and inv["retainage_held"] == "-1500.00"
    assert inv["release_only"] is True

    text = _pdf_text(db_session, inv_id)
    assert f"Retainage reduction, {number}" in text
    assert "Subtotal" not in text and "--" not in text
    assert "Current payment due $1,500.00" in text

    token = db_session.get(Invoice, inv_id).payment_token
    page = _text(client.get(f"/pay/{token}").text)
    assert f"Retainage reduction, {number} $1,500.00" in page
    assert "Invoice Total" not in page


def test_work_billed_at_a_lower_percent_reads_as_a_reduction(
    client, db_session, seed_accounts, seed_customer
):
    """10,000 more at 5%: 2,000 to date less the 3,000 held is 1,000 back."""
    job, sov = _first_application(client, seed_customer.id)
    inv_id, _ = _approved(
        client,
        job["id"],
        retainage_pct=5,
        lines=[{"sov_line_id": sov[1]["id"], "this_period": 10000, "stored": 0}],
    )
    text = _pdf_text(db_session, inv_id)
    assert "Retainage reduction this period $1,000.00" in text
    assert "--" not in text and "Retainage withheld" not in text
    assert "Current payment due $11,000.00" in text


def test_the_pay_page_shows_a_hold_and_release(
    client, db_session, seed_accounts, seed_customer
):
    """10,000 more with 1,000 held and 2,000 of the earlier 3,000 released."""
    job, sov = _first_application(client, seed_customer.id)
    inv_id, _ = _approved(
        client,
        job["id"],
        release_retainage=2000,
        lines=[{"sov_line_id": sov[1]["id"], "this_period": 10000, "stored": 0}],
    )
    token = db_session.get(Invoice, inv_id).payment_token
    page = _text(client.get(f"/pay/{token}").text)
    assert "Invoice Total $10,000.00" in page
    assert "Retainage withheld this period -$1,000.00" in page
    assert "Retainage released $2,000.00" in page
    assert "Current payment due $11,000.00" in page


def test_retainage_going_out_is_never_retainage_only(db_session, seed_customer):
    from decimal import Decimal

    from app.services.pay_applications import invoice_is_release_only

    def inv(held, released):
        row = Invoice(
            invoice_number=f"T-{held}-{released}",
            customer_id=seed_customer.id,
            total=Decimal("0"),
            tax_amount=Decimal("0"),
            retainage_held=Decimal(held),
            retainage_released=Decimal(released),
        )
        row.lines = []
        return row

    assert invoice_is_release_only(inv("-1500", "0"))
    assert invoice_is_release_only(inv("-1500", "500"))
    assert not invoice_is_release_only(inv("-1500", "-500"))
    assert not invoice_is_release_only(inv("0", "0"))
