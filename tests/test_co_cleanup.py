"""Construction-overhaul leftovers (workstream L): each small fix, pinned."""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_estimates_commitments_buyout import (
    _estimate,
    _job as _plain_job,
    _section,
    _set_construction,
)
from tests.test_co_effective_date_allowance import (
    _app,
    _approve,
    _contract,
    _owner_co,
    _setup,
)
from tests.test_cash_basis_construction import (
    _approved_app,
    _commitment,
    _job,
    _release,
    _sub,
    _sub_bill,
)

D = Decimal


def test_a_retainage_release_bill_cannot_be_re_lined(
    client, db_session, seed_accounts, seed_customer
):
    """Editing the release bill's line re-posted it to an expense account
    instead of debiting 2050; it is refused, and its note still saves."""
    sub = _sub(client, "Framer")
    job, _sov = _job(client, seed_customer.id)
    po = _commitment(client, sub["id"], job["id"], 8000, None)
    _sub_bill(client, po, "2026-07-01", 8000, seed_accounts["5100"].id)
    bill_id, amount = _release(client, po, "2026-08-01")
    assert amount > 0

    bill = client.get(f"/api/bills/{bill_id}").json()
    r = client.put(
        f"/api/bills/{bill_id}",
        json={
            "lines": [
                {
                    "account_id": seed_accounts["5100"].id,
                    "description": "x",
                    "quantity": 1,
                    "rate": float(amount),
                }
            ]
        },
    )
    assert r.status_code == 409, r.text
    assert "can't be re-lined" in r.json()["detail"]

    r = client.put(f"/api/bills/{bill_id}", json={"notes": "per the sub"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/bills/{bill_id}").json()["total"] == bill["total"]


def test_voiding_a_change_order_in_a_closed_period_is_refused(client, seed_customer):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    co = _owner_co(client, job["id"], 1000, date="2026-08-10")
    assert _approve(client, co["id"]).status_code == 200
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code
        == 200
    )
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 403, r.text
    assert "on or before the closing date" in r.json()["detail"]
    assert client.get(f"/api/change-orders/{co['id']}").json()["status"] == "approved"
    assert _contract(client, job["id"])["contract"]["contract_amount"] == 101000.0
    # an open period still voids
    client.put("/api/settings", json={"closing_date": ""})
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200


def test_a_pay_application_before_a_change_order_leaves_its_line_off(
    client, db_session, seed_accounts, seed_customer
):
    """The schedule on an application dated before the change order takes
    effect equals the contract sum to date on that application, on screen and
    on the PDF; one dated after lists the change order's line."""
    from app.models.pay_applications import PayApplication
    from app.routes.pay_applications import _out
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings

    job, sov = _setup(
        client,
        seed_customer.id,
        [{"description": "Base", "scheduled_value": 100000}],
        100000,
    )
    co = _owner_co(client, job["id"], 13640, date="2026-08-10")
    assert _approve(client, co["id"]).status_code == 200

    july = _app(client, job["id"], "2026-07-31")
    got = client.get(f"/api/pay-applications/{july['id']}").json()
    assert [ln["description"] for ln in got["lines"]] == ["Base"]
    assert got["totals"]["scheduled_total"] == 100000.0
    # a refresh (the tab calls it on opening a draft) does not add it back
    assert (
        client.post(f"/api/pay-applications/{july['id']}/refresh-lines").status_code
        == 200
    )
    got = client.get(f"/api/pay-applications/{july['id']}").json()
    assert got["totals"]["scheduled_total"] == 100000.0

    db_session.expire_all()
    pa = db_session.get(PayApplication, july["id"])
    html = render_pay_application_html(
        pa, _out(db_session, pa), pa.job, get_all_settings(db_session)
    )
    assert "Deck" not in html and "113,640.00" not in html

    # moving the date past the change order's brings its line in
    r = client.put(f"/api/pay-applications/{july['id']}", json={"date": "2026-08-31"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert len(got["lines"]) == 2
    assert got["totals"]["scheduled_total"] == 113640.0

    assert client.delete(f"/api/pay-applications/{july['id']}").status_code == 200
    august = _app(client, job["id"], "2026-08-31")
    got = client.get(f"/api/pay-applications/{august['id']}").json()
    assert got["totals"]["scheduled_total"] == 113640.0


def test_accept_says_how_many_lines_have_no_cost_code(client, seed_customer):
    """Accept still goes ahead; the API names the lines whose budget lands on
    no code, and the estimate carries the count for the confirm."""
    _set_construction(client)
    code = _section(client, "26 05 00", "Electrical")
    job = _plain_job(client, seed_customer.id)
    lines = [
        {
            "description": "Wire",
            "quantity": 1,
            "unit_cost": 100,
            "cost_code_id": code["id"],
        },
        {"description": "Misc", "quantity": 1, "unit_cost": 50},
        {"description": "Cleanup", "quantity": 1, "unit_cost": 25},
    ]
    est = _estimate(client, seed_customer.id, lines, job_id=job["id"])
    assert client.get(f"/api/estimates/{est['id']}").json()["uncoded_lines"] == 2
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert r.json()["warnings"] == [
        "2 lines have no cost code, so their budget lands on no code."
    ]

    # every line coded: no warning
    clean = _estimate(
        client,
        seed_customer.id,
        [{**lines[0]}],
        job_id=_plain_job(client, seed_customer.id, name="Clean")["id"],
    )
    r = client.post(f"/api/estimates/{clean['id']}/accept", json={})
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []


def test_accept_confirm_names_the_lines_with_no_cost_code():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = (
        Path(__file__).resolve().parents[1] / "app/static/js/estimates.js"
    ).read_text()
    body = js[js.index("acceptConfirmText(est) {") : js.index("async accept(id)")]
    script = (
        "const formatCurrency = v => '$' + Number(v).toFixed(2);"
        "const formatDate = d => d; const todayISO = () => '2026-11-07';"
        f"const P = {{ {body.rstrip().rstrip(',')} }};"
        "console.log(JSON.stringify([P.acceptConfirmText({uncoded_lines:1}),"
        "P.acceptConfirmText({uncoded_lines:3}), P.acceptConfirmText({})]));"
    )
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    )
    one, three, none = json.loads(out.stdout)
    assert "1 line has no cost code, so its budget lands on no code." in one
    assert "3 lines have no cost code, so their budget lands on no code." in three
    assert "no cost code" not in none


def test_the_construction_estimate_pdf_has_no_status(client, db_session, seed_customer):
    """The owner is never told the estimate is "Pending"; a Business
    estimate keeps its Status."""
    from tests.test_pdf_estimating import _estimate as _est, _html

    line = [{"description": "Slab", "quantity": 1, "rate": 100, "unit_cost": 80}]
    est = _est(client, seed_customer.id, line)
    assert "Status:" not in _html(db_session, est["id"], "construction")
    assert "Status:" in _html(db_session, est["id"], "business")


def test_a_late_fee_stays_in_the_aging_and_ties_to_1100(
    client, db_session, seed_accounts, seed_customer
):
    """A fee posts its own A/R debit; an edit of the invoice used to drop it
    from the balance, and an aging dated before the fee still counted it."""
    from datetime import date, timedelta

    from tests.test_aging_everywhere_ties import _tb
    from tests.test_late_fee_job_and_void import (
        _apply_late_fees,
        _overdue_job_invoice,
    )

    _job_id, inv_id = _overdue_job_invoice(client, seed_customer)
    _apply_late_fees(client)  # 5% of 100

    def aging(**params):
        r = client.get("/api/reports/ar-aging", params=params)
        return D(str(r.json()["totals"]["total"]))

    assert aging() == D("105.00") == _tb(client)["1100"]
    # before the fee posted the invoice owed 100, as 1100 did
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert aging(as_of_date=yesterday) == D("100.00")

    # re-lining the invoice keeps the fee on it
    r = client.put(
        f"/api/invoices/{inv_id}",
        json={"lines": [{"description": "Service", "quantity": 1, "rate": 100}]},
    )
    assert r.status_code == 200, r.text
    assert D(str(r.json()["balance_due"])) == D("105.00")
    assert aging() == D("105.00") == _tb(client)["1100"]
    assert aging(as_of_date=yesterday) == D("100.00")


def test_a_pay_app_invoice_links_to_its_term_on_an_unseeded_list(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice
    from app.models.payment_terms import PaymentTerm

    job, sov = _job(client, seed_customer.id)
    db_session.query(PaymentTerm).delete()
    db_session.commit()
    inv_id = _approved_app(client, job["id"], "2026-07-31", sov, (1000, 0))
    db_session.expire_all()
    inv = db_session.get(Invoice, inv_id)
    term = db_session.get(PaymentTerm, inv.payment_term_id)
    assert term is not None and term.name == inv.terms
