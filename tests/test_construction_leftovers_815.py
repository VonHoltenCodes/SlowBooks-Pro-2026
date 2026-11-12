"""Construction leftovers from the #731 and #752 reviews (#815).

Four of the six (a billed deduct line pinned on the schedule of values, no
"Unassigned" heading on the bid PDF, no delivery ticket created in a closed
period, a PO edit re-running the receipt status) shipped in #731 with their
tests; see the PR. These pin the two built here:

- rev134-r2-holdrelease: the pay page the emailed link opens showed a pay
  application invoice's total and its balance with nothing between them. It
  now shows the retainage withheld and released and the current payment due,
  the rows the invoice PDF shows.
- rev134-r1-w2: a pay application invoice with no lines that is not a pure
  release (retainage given back by a lower percent, with or without a deposit
  applied) printed an empty table under a $0.00 total. It now names what it
  bills, as a release-only invoice does (#751). On the way: a negative hold
  printed as "--$1,500.00"; it reads as a retainage reduction.
"""

import re

from app.models.invoices import Invoice
from tests.test_pay_applications import (
    _acct,
    _approve,
    _deposit,
    _edit,
    _new_app,
    _two_line_job,
)


def _first_30k(client, job, sov):
    """Bill 30,000 on line 1 at 10%: 3,000 held."""
    a1 = _new_app(client, job["id"])
    r = _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert r.status_code == 200, r.text
    assert _approve(client, a1["id"]).status_code == 200


def _second(client, job, **body):
    a2 = _new_app(client, job["id"])
    r = _edit(client, a2["id"], **body)
    assert r.status_code == 200, r.text
    r = _approve(client, a2["id"])
    assert r.status_code == 200, r.text
    return r.json()["invoice_id"], a2["number"], a2["id"]


def _text(html):
    """The page's words, tags dropped and whitespace collapsed."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def _print(client, inv_id):
    r = client.get(f"/api/invoices/{inv_id}/print-preview")
    assert r.status_code == 200, r.text[:200]
    return _text(r.text)


def _pay_page(client, db_session, inv_id):
    db_session.expire_all()
    token = db_session.get(Invoice, inv_id).payment_token
    r = client.get(f"/pay/{token}")
    assert r.status_code == 200, r.text[:200]
    return _text(r.text)


def _reduction_only(client, customer_id, **extra):
    """App 2 lowers retainage to 5% with no new work: 1,500 of the 3,000 held
    is given back, and the invoice has no lines."""
    job, sov = _two_line_job(client, customer_id)
    _first_30k(client, job, sov)
    return _second(client, job, retainage_pct=5, **extra)


# ── rev134-r1-w2: a line-less pay application invoice names what it bills ──


def test_a_reduction_only_invoice_names_the_reduction(
    client, seed_accounts, seed_customer
):
    inv_id, number, _ = _reduction_only(client, seed_customer.id)
    inv = client.get(f"/api/invoices/{inv_id}").json()
    assert inv["lines"] == [] and inv["total"] == "0.00"
    assert inv["retainage_held"] == "-1500.00"
    assert inv["release_only"] is False
    assert inv["retainage_lines"] == [
        {"description": f"Retainage reduction, {number}", "amount": "1500.00"}
    ]
    text = _print(client, inv_id)
    assert f"Retainage reduction, {number} 1 $1,500.00 $1,500.00" in text
    assert "Current payment due $1,500.00" in text
    # no $0.00 subtotal or total over it, and no "--$" double minus
    assert "Subtotal" not in text and "Total $0.00" not in text
    assert "--$" not in text and "Retainage withheld" not in text


def test_a_reduction_with_a_deposit_applied_names_it_and_shows_the_deposit(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    r = _deposit(client, job["id"], 1000, _acct(db_session, "1000").id)
    assert r.status_code in (200, 201), r.text
    _first_30k(client, job, sov)
    inv_id, number, _ = _second(client, job, retainage_pct=5, deposit_applied=1000)
    text = _print(client, inv_id)
    assert f"Retainage reduction, {number}" in text
    assert "Current payment due $1,500.00" in text
    assert "Paid $1,000.00" in text and "Balance Due $500.00" in text
    assert "Subtotal" not in text


def test_a_deposit_with_nothing_billed_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    """A deposit-only application cannot exist: a deposit is applied to what
    the application bills, so one with nothing billed is refused on the edit
    and never prints. A deposit with no lines rides on a reduction (above) or
    a release (#751)."""
    job, sov = _two_line_job(client, seed_customer.id)
    r = _deposit(client, job["id"], 1000, _acct(db_session, "1000").id)
    assert r.status_code in (200, 201), r.text
    _first_30k(client, job, sov)
    a2 = _new_app(client, job["id"])
    r = _edit(client, a2["id"], deposit_applied=1000)
    assert r.status_code == 422
    assert "more than the current payment due 0.00" in r.json()["detail"]
    r = _approve(client, a2["id"])
    assert r.status_code == 422
    assert "nothing to bill, hold, release or apply" in r.json()["detail"]


def test_a_release_and_a_reduction_print_as_two_lines(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    _first_30k(client, job, sov)
    # 3,000 held; at 5% only 1,500 is held, and 1,000 of that is released:
    # 500 stays held, 2,500 comes back (the release and a 1,500 reduction)
    inv_id, number, _ = _second(client, job, retainage_pct=5, release_retainage=1000)
    inv = client.get(f"/api/invoices/{inv_id}").json()
    assert inv["release_only"] is False
    assert [r["description"] for r in inv["retainage_lines"]] == [
        f"Retainage release, {number}",
        f"Retainage reduction, {number}",
    ]
    total = sum(float(r["amount"]) for r in inv["retainage_lines"])
    assert total == float(inv["balance_due"]) == 2500.0
    text = _print(client, inv_id)
    assert f"Retainage release, {number} 1 $1,000.00" in text
    assert f"Retainage reduction, {number} 1 $1,500.00" in text
    assert "Current payment due $2,500.00" in text
    assert "Retainage released" not in text  # the line carries it


def test_a_voided_reduction_keeps_its_line_and_shows_nothing_due(
    client, seed_accounts, seed_customer
):
    inv_id, number, app_id = _reduction_only(client, seed_customer.id)
    assert client.post(f"/api/pay-applications/{app_id}/void").status_code == 200
    text = _print(client, inv_id)
    assert f"Retainage reduction, {number}" in text
    assert "Current payment due" not in text


def test_a_lined_invoice_with_a_negative_hold_reads_as_a_reduction(
    client, seed_accounts, seed_customer
):
    """App 2 bills 10,000 and drops to 5%: 2,000 held to date against 3,000,
    so this period's hold is -1,000. It printed "--$1,000.00"."""
    job, sov = _two_line_job(client, seed_customer.id)
    _first_30k(client, job, sov)
    inv_id, _, _ = _second(
        client,
        job,
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
        retainage_pct=5,
    )
    inv = client.get(f"/api/invoices/{inv_id}").json()
    assert inv["retainage_lines"] == []  # its lines say what it bills
    text = _print(client, inv_id)
    assert "Total $10,000.00" in text
    assert "Retainage reduction this period $1,000.00" in text
    assert "Current payment due $11,000.00" in text
    assert "--$" not in text


def test_an_invoice_with_lines_has_no_retainage_lines(db_session, seed_customer):
    from decimal import Decimal

    from app.models.invoices import InvoiceLine
    from app.services.pay_applications import invoice_retainage_lines

    row = Invoice(
        invoice_number="T-815",
        customer_id=seed_customer.id,
        total=Decimal("100"),
        retainage_held=Decimal("10"),
        retainage_released=Decimal("5"),
    )
    row.lines = [InvoiceLine(description="Work", quantity=1, rate=100, amount=100)]
    assert invoice_retainage_lines(row, "PA-1-01") == []
    row.lines = []
    assert invoice_retainage_lines(row, None) == [
        {"description": "Retainage release", "amount": Decimal("5.00")},
        {"description": "Retainage withheld", "amount": Decimal("-10.00")},
    ]
    row.retainage_held = row.retainage_released = Decimal("0")
    assert invoice_retainage_lines(row, "PA-1-01") == []


# ── rev134-r2-holdrelease: the pay page shows the retainage rows ───────────


def test_a_hold_and_release_pay_page_shows_the_retainage_rows(
    client, db_session, seed_accounts, seed_customer
):
    """App 2 bills 10,000 (1,000 held) and releases 1,000: the page read
    Invoice Total $10,000.00 and Balance Due $10,000.00 with nothing between.
    It now shows what the PDF shows."""
    job, sov = _two_line_job(client, seed_customer.id)
    _first_30k(client, job, sov)
    inv_id, _, _ = _second(
        client,
        job,
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
        release_retainage=1000,
    )
    page = _pay_page(client, db_session, inv_id)
    assert "Invoice Total $10,000.00" in page
    assert "Retainage withheld this period -$1,000.00" in page
    assert "Retainage released $1,000.00" in page
    assert "Current payment due $10,000.00" in page
    assert "Balance Due $10,000.00" in page
    pdf = _print(client, inv_id)
    for row in (
        "Retainage withheld this period -$1,000.00",
        "Retainage released $1,000.00",
        "Current payment due $10,000.00",
    ):
        assert row in pdf


def test_a_hold_only_pay_page_shows_the_current_payment_due(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    r = _approve(client, a1["id"])
    assert r.status_code == 200, r.text
    page = _pay_page(client, db_session, r.json()["invoice_id"])
    assert "Invoice Total $30,000.00" in page
    assert "Retainage withheld this period -$3,000.00" in page
    assert "Current payment due $27,000.00" in page
    assert "Balance Due $27,000.00" in page
    assert "Retainage released" not in page


def test_a_reduction_only_pay_page_names_the_reduction(
    client, db_session, seed_accounts, seed_customer
):
    inv_id, number, _ = _reduction_only(client, seed_customer.id)
    page = _pay_page(client, db_session, inv_id)
    assert f"Retainage reduction, {number} $1,500.00" in page
    assert "Invoice Total" not in page
    assert "Balance Due $1,500.00" in page


def test_an_ordinary_pay_page_has_no_retainage_rows(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "lines": [{"description": "Banner", "quantity": 1, "rate": 120}],
        },
    )
    assert r.status_code == 201, r.text
    page = _pay_page(client, db_session, r.json()["id"])
    assert "Invoice Total $120.00" in page
    assert "Retainage" not in page and "Current payment due" not in page
