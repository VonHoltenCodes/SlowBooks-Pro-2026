"""Progress billing and retainage (#555): the owner pay application from the
schedule of values (previous / this period / stored / retainage, with a
release and a deposit), the invoice and balanced journal approve creates,
owner deposits, the sub pay-app schedule and retainage release, and the
seams it fills (build_sov, PUT /sov shrink, change-order void).

WIP / cost-to-complete / the change-order log / buyout report / job cost by
vendor are #556; this one leaves those figures reachable.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.models.bills import Bill
from app.models.change_orders import ChangeOrder
from app.models.invoices import Invoice
from app.models.pay_applications import PayApplication
from app.models.transactions import TransactionLine
from app.services.pay_applications import _Row, application_totals, line_computed
from app.services.pdf_service import _render
from tests.test_change_orders import _co, _sub_line, _submit_approve
from tests.test_change_orders import _job as _change_job

ROOT = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _contract(client, job_id, **terms):
    r = client.put(f"/api/jobs/{job_id}/contract", json=terms)
    assert r.status_code == 200, r.text
    return r.json()


def _sov(client, job_id, lines):
    r = client.put(f"/api/jobs/{job_id}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text
    return client.get(f"/api/jobs/{job_id}/contract").json()["sov"]


def _two_line_job(client, customer_id, retainage_pct=10, name="Tower"):
    job = _job(client, customer_id, name=name)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=100000,
        retainage_pct=retainage_pct,
    )
    sov = _sov(
        client,
        job["id"],
        [
            {"description": "Sitework", "kind": "work", "scheduled_value": 60000},
            {"description": "Structure", "kind": "work", "scheduled_value": 40000},
        ],
    )
    return job, sov


def _acct(db, number):
    from app.models.accounts import Account

    return db.query(Account).filter(Account.account_number == number).first()


def _gl(db, number):
    acct = _acct(db, number)
    if acct is None:
        return Decimal("0")
    rows = db.query(TransactionLine).filter_by(account_id=acct.id).all()
    return sum(
        (Decimal(str(r.debit)) - Decimal(str(r.credit)) for r in rows), Decimal("0")
    )


# ── Pure math over two applications ─────────────────────────────────────


def _rows(pairs):
    return [
        _Row(scheduled_value=s, previous=p, this_period=t, stored=st)
        for (s, p, t, st) in pairs
    ]


def test_application_math_two_periods_and_release():
    # app 1: 30,000 this period + 5,000 stored on line 1, 10% retainage
    rows = _rows([(50000, 0, 30000, 5000), (50000, 0, 0, 0)])
    t1 = application_totals(
        rows, retainage_pct=10, previous_retainage=0, previous_applications=0
    )
    assert t1["completed_to_date"] == Decimal("35000.00")
    assert t1["retainage_to_date"] == Decimal("3500.00")
    assert t1["retainage_this_period"] == Decimal("3500.00")
    assert t1["total_earned_less_retainage"] == Decimal("31500.00")
    assert t1["current_due"] == Decimal("31500.00")

    # app 2: 20,000 more this period on line 1; 5,000 still stored → 55,000
    rows = _rows([(50000, 30000, 20000, 5000), (50000, 0, 0, 0)])
    t2 = application_totals(
        rows,
        retainage_pct=10,
        previous_retainage=t1["retainage_to_date"],
        previous_applications=t1["current_due"],
    )
    assert t2["completed_to_date"] == Decimal("55000.00")
    assert t2["retainage_to_date"] == Decimal("5500.00")
    assert t2["retainage_this_period"] == Decimal("2000.00")
    assert t2["previous_applications"] == Decimal("31500.00")
    assert t2["current_due"] == Decimal("18000.00")

    # a final application that releases the 5,500 held (previous now 50,000)
    rows = _rows([(50000, 50000, 0, 5000), (50000, 0, 0, 0)])
    t3 = application_totals(
        rows,
        retainage_pct=10,
        previous_retainage=t2["retainage_to_date"],
        previous_applications=Decimal("49500.00"),
        release_retainage=5500,
    )
    assert t3["retainage_this_period"] == Decimal("0.00")
    assert t3["current_due"] == Decimal("5500.00")


def test_line_pct_and_balance():
    row = line_computed(_Row(50000, 10000, 15000, 0), 10)
    assert row["completed_to_date"] == Decimal("25000.00")
    assert row["balance_to_finish"] == Decimal("25000.00")
    assert row["retainage"] == Decimal("2500.00")
    assert row["pct_complete"] == 50.0


# ── Lifecycle ─────────────────────────────────────────────────────────────


def _new_app(client, job_id):
    r = client.post(
        "/api/pay-applications", json={"job_id": job_id, "date": "2026-06-01"}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _edit(client, app_id, **body):
    r = client.put(f"/api/pay-applications/{app_id}", json=body)
    return r


def _approve(client, app_id):
    r = client.post(f"/api/pay-applications/{app_id}/approve")
    return r


def test_one_open_application_per_job(client, seed_accounts, seed_customer):
    job, _sovl = _two_line_job(client, seed_customer.id)
    _new_app(client, job["id"])
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-07-01"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "application_open"


NO_SOV = (
    "This job has no schedule of values yet: set the contract on the job's "
    "Contract tab (Build from accepted estimate, or add the lines by hand), "
    "then bill against it here."
)


def test_a_job_with_no_schedule_of_values_makes_no_application(
    client, db_session, seed_accounts, seed_customer
):
    """#714: New pay application on a job with no SOV made an application
    with no lines, and the form opened on an empty grid. It is refused with
    the step that is missing, and nothing is made."""
    job = _job(client, seed_customer.id, name="Bare Job")
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=50000)
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-06-01"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == NO_SOV
    assert db_session.query(PayApplication).count() == 0


def test_a_job_with_a_schedule_of_values_still_makes_one_line_each(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    assert app["status"] == "draft" and app["number"] == f"PA-{job['id']}-01"
    assert [ln["sov_line_id"] for ln in app["lines"]] == [s["id"] for s in sov]
    assert [ln["description"] for ln in app["lines"]] == ["Sitework", "Structure"]


def test_the_pay_apps_tab_says_the_same_in_place_of_new():
    js = (ROOT / "app/static/js/jobs.js").read_text(encoding="utf-8")
    hint = js.split("paNoSovHtml() {")[1].split("\n    },")[0]
    assert " ".join(hint.split()).count("then bill against it here.") == 1
    assert "JobsPage.setTab('contract')\">Contract tab</a>" in hint
    assert 'hasSov ? `<div class="form-actions" data-write' in js


def test_line_over_scheduled_422(client, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 70000, "stored": 0}],
    )
    assert r.status_code == 422


def test_approve_creates_invoice_journal_and_retainage(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["totals"]["current_due"] == 31500.0
    inv = db_session.query(Invoice).filter_by(id=body["invoice_id"]).first()
    assert inv.subtotal == Decimal("35000.00")
    assert inv.total == Decimal("35000.00")
    assert inv.retainage_held == Decimal("3500.00")
    assert inv.balance_due == Decimal("31500.00")
    # balanced journal: A/R 31,500 + 1150 3,500 debit; income 35,000 credit
    rows = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=inv.transaction_id)
        .all()
    )
    dr = sum((Decimal(str(r.debit)) for r in rows), Decimal("0"))
    cr = sum((Decimal(str(r.credit)) for r in rows), Decimal("0"))
    assert dr == cr == Decimal("35000.00")
    assert _gl(db_session, "1150") == Decimal("3500.00")  # retainage receivable
    assert _gl(db_session, "1100") == Decimal("31500.00")  # A/R


def test_two_periods_then_release(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    # app 1
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    assert _approve(client, a1["id"]).status_code == 200
    # app 2
    a2 = _new_app(client, job["id"])
    _edit(
        client,
        a2["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 20000, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    r2 = _approve(client, a2["id"])
    assert r2.status_code == 200, r2.text
    t2 = r2.json()["totals"]
    assert t2["completed_to_date"] == 55000.0
    assert t2["retainage_to_date"] == 5500.0
    assert t2["retainage_this_period"] == 2000.0
    # billed to date is the latest application's cumulative figure, not a
    # sum across applications (35,000 + 55,000 would count period 1 twice)
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["billed_to_date"] == 55000.0
    assert t2["current_due"] == 18000.0
    inv2 = db_session.query(Invoice).filter_by(id=r2.json()["invoice_id"]).first()
    assert inv2.subtotal == Decimal("20000.00")
    assert inv2.retainage_held == Decimal("2000.00")
    assert inv2.balance_due == Decimal("18000.00")
    # retainage receivable now holds 5,500
    assert _gl(db_session, "1150") == Decimal("5500.00")

    # app 3 releases the 5,500
    a3 = _new_app(client, job["id"])
    _edit(
        client,
        a3["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 0, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
        release_retainage=5500,
    )
    r3 = _approve(client, a3["id"])
    assert r3.status_code == 200, r3.text
    assert r3.json()["totals"]["current_due"] == 5500.0
    inv3 = db_session.query(Invoice).filter_by(id=r3.json()["invoice_id"]).first()
    assert inv3.retainage_released == Decimal("5500.00")
    assert inv3.balance_due == Decimal("5500.00")
    assert _gl(db_session, "1150") == Decimal("0.00")  # released back to zero


def test_release_over_held_422(client, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert _approve(client, a1["id"]).status_code == 200
    a2 = _new_app(client, job["id"])
    r = _edit(client, a2["id"], release_retainage=9999)
    assert r.status_code == 422


# ── Deposits ────────────────────────────────────────────────────────────


def _deposit(client, job_id, amount, bank_id, date="2026-05-01"):
    return client.post(
        f"/api/jobs/{job_id}/deposits",
        json={"date": date, "amount": amount, "deposit_to_account_id": bank_id},
    )


def test_deposit_received_and_applied(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    r = _deposit(client, job["id"], 10000, bank.id)
    assert r.status_code == 201, r.text
    # DR bank / CR Customer Deposits (2210)
    assert _gl(db_session, "2210") == Decimal("-10000.00")  # a liability credit
    listing = client.get(f"/api/jobs/{job['id']}/deposits").json()
    assert listing["received"] == 10000.0
    assert listing["unapplied"] == 10000.0

    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv.amount_paid == Decimal("10000.00")
    # the deposit payment relieved A/R and drew down 2210
    assert _gl(db_session, "2210") == Decimal("0.00")
    listing = client.get(f"/api/jobs/{job['id']}/deposits").json()
    assert listing["applied"] == 10000.0
    assert listing["unapplied"] == 0.0


def test_deposit_over_unapplied_422(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 5000, bank.id)
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=9000,
    )
    assert r.status_code == 422


# ── Void ──────────────────────────────────────────────────────────────────


def test_void_without_payment(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    inv_id = _approve(client, app["id"]).json()["invoice_id"]
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    inv = db_session.query(Invoice).filter_by(id=inv_id).first()
    assert inv.status.value == "void"
    assert _gl(db_session, "1150") == Decimal("0.00")  # retainage reversed


def test_void_restores_deposit(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 10000, bank.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    _approve(client, app["id"])
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 200, r.text
    listing = client.get(f"/api/jobs/{job['id']}/deposits").json()
    assert listing["unapplied"] == 10000.0  # deposit back to unapplied


def test_void_refused_leaves_the_deposit_applied(
    client, db_session, seed_accounts, seed_customer, monkeypatch
):
    """The deposit payment, the invoice and the application void together:
    when the invoice's void is refused, the deposit is not left voided under
    an application that still stands (#576)."""
    from app.models.payments import Payment
    from app.routes import pay_applications as pa_routes

    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 10000, bank.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    approved = _approve(client, app["id"]).json()
    inv_id = approved["invoice_id"]
    # a real receipt on the invoice, with the application's own up-front
    # guard out of the way so the invoice void itself is what refuses
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-06-05",
            "amount": 1000,
            "method": "Check",
            "allocations": [{"invoice_id": inv_id, "amount": 1000}],
        },
    )
    assert pay.status_code == 201, pay.text
    monkeypatch.setattr(pa_routes, "_refuse_if_truly_paid", lambda db, a, i: None)
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 400, r.text
    assert "payments applied" in r.json()["detail"]
    db_session.expire_all()
    dep_id = db_session.get(PayApplication, app["id"]).deposit_payment_id
    assert dep_id is not None
    assert db_session.get(Payment, dep_id).is_voided is False
    inv = db_session.query(Invoice).filter_by(id=inv_id).first()
    assert inv.status.value != "void"
    assert inv.amount_paid == Decimal("11000.00")
    assert client.get(f"/api/pay-applications/{app['id']}").json()["status"] == (
        "approved"
    )
    assert client.get(f"/api/jobs/{job['id']}/deposits").json()["unapplied"] == 0.0


def test_void_history_recomputed(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, a1["id"])
    a2 = _new_app(client, job["id"])
    _edit(
        client,
        a2["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
    )
    _approve(client, a2["id"])
    # void app 2 then app 1 (latest first): a fresh app 2's `previous`
    # recomputes from approved ones only
    assert client.post(f"/api/pay-applications/{a2['id']}/void").status_code == 200
    assert client.post(f"/api/pay-applications/{a1['id']}/void").status_code == 200
    body = client.get(f"/api/pay-applications/{a2['id']}").json()
    line = next(l for l in body["lines"] if l["sov_line_id"] == sov[0]["id"])
    assert float(line["previous"]) == 0.0  # the only prior approved app is gone


# ── Contract summary figures ────────────────────────────────────────────


def test_contract_summary_billed_retainage_deposits(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 4000, bank.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000}],
    )
    _approve(client, app["id"])
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["billed_to_date"] == 35000.0
    assert c["retainage"]["held_to_date"] == 3500.0
    assert c["retainage"]["outstanding"] == 3500.0
    assert c["deposits"]["received"] == 4000.0
    assert c["deposits"]["unapplied"] == 4000.0


# ── PDF ───────────────────────────────────────────────────────────────────


def test_pdf_renders_both_sheets(client, db_session, seed_accounts, seed_customer):
    from app.models.jobs import Job
    from app.routes.pay_applications import _out
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings

    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000}],
    )
    _approve(client, app["id"])
    pa = db_session.query(PayApplication).filter_by(id=app["id"]).first()
    out = _out(db_session, pa)
    html = render_pay_application_html(
        pa, out, db_session.query(Job).get(job["id"]), get_all_settings(db_session)
    )
    assert "APPLICATION FOR PAYMENT" in html
    assert "Line detail" in html
    assert "Current payment due" in html


# ── Seams ────────────────────────────────────────────────────────────────


def test_build_sov_blocked_after_application(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_contracts import _build_estimate_with_shape

    est, _pkg_c, _pkg_e = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id, name="FromEstimate")
    client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]})
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
    )
    assert _approve(client, app["id"]).status_code == 200
    # rebuilding the schedule from the estimate is now refused
    again = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "sov_has_billings"


def test_put_sov_shrink_below_billed_422(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, app["id"])
    # shrinking line 1 below its billed 30,000 is refused
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "id": sov[0]["id"],
                    "description": "Sitework",
                    "kind": "work",
                    "scheduled_value": 10000,
                },
                {
                    "id": sov[1]["id"],
                    "description": "Structure",
                    "kind": "work",
                    "scheduled_value": 50000,
                },
            ]
        },
    )
    assert r.status_code == 422


# ── Sub pay-app schedule and retainage release ───────────────────────────


def _vendor(client, name="Ace Concrete", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _sent_po(client, vendor_id, lines, **extra):
    body = {"vendor_id": vendor_id, "date": "2026-05-01", "lines": lines}
    body.update(extra)
    po = client.post("/api/purchase-orders", json=body).json()
    up = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert up.status_code == 200
    return up.json()


def test_sub_pay_app_schedule_and_release(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(
        client,
        vendor_type="sub",
        default_expense_account_id=_acct(db_session, "5100").id,
    )
    po = _sent_po(
        client,
        v["id"],
        [{"description": "Foundations", "quantity": 1, "rate": 10000}],
        retainage_pct=10,
    )
    # bill half the line, holding 10% retainage
    line_id = po["lines"][0]["id"]
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": line_id, "amount": 5000}]},
    )
    assert r.status_code == 200, r.text
    sched = client.get(f"/api/purchase-orders/{po['id']}/pay-app").json()
    assert sched["lines"][0]["previous_billed"] == 5000.0
    assert sched["lines"][0]["remaining"] == 5000.0
    assert sched["retainage_held_to_date"] == 500.0
    assert sched["retainage_outstanding"] == 500.0

    # release the retainage — a bill DR 2050 / CR A/P
    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage", json={"amount": 500}
    )
    assert rel.status_code == 200, rel.text
    bill = db_session.query(Bill).filter_by(id=rel.json()["bill_id"]).first()
    assert bill.retainage_released == Decimal("500.00")
    assert bill.balance_due == Decimal("500.00")
    assert _gl(db_session, "2050") == Decimal("0.00")  # held 500, released 500

    # over the held amount is refused
    over = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage", json={"amount": 100}
    )
    assert over.status_code == 422

    # voiding the release restores the outstanding
    assert client.post(f"/api/bills/{bill.id}/void").status_code == 200
    sched = client.get(f"/api/purchase-orders/{po['id']}/pay-app").json()
    assert sched["retainage_released_to_date"] == 0.0
    assert sched["retainage_outstanding"] == 500.0


# ── Change-order seam ──────────────────────────────────────────────────────


def test_co_void_blocked_once_its_sov_line_is_billed(
    client, db_session, seed_accounts, seed_customer
):
    job, _sov = _two_line_job(client, seed_customer.id)
    co = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "Extra",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 5000,
                }
            ],
        },
    ).json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    assert client.post(f"/api/change-orders/{co['id']}/approve").status_code == 200
    # the approved CO appended a change_order SOV line — bill it
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    co_line = next(l for l in sov if l["kind"] == "change_order")
    # dated today: an application before the CO takes effect has no line for it
    app = client.post(
        "/api/pay-applications",
        json={"job_id": job["id"], "date": date.today().isoformat()},
    ).json()
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": co_line["id"], "this_period": 5000, "stored": 0}],
    )
    assert _approve(client, app["id"]).status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_billed"


def test_sub_waivers_warning(client, db_session, seed_accounts, seed_customer):
    v = _vendor(
        client,
        vendor_type="sub",
        default_expense_account_id=_acct(db_session, "5100").id,
    )
    job, _sov = _two_line_job(client, seed_customer.id)
    # a bill on the job with no waiver, dated before the period
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-05-15",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "Work",
                    "amount": 1000,
                    "account_id": _acct(db_session, "5100").id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    app = client.post(
        "/api/pay-applications",
        json={"job_id": job["id"], "date": "2026-06-01", "period_to": "2026-06-01"},
    ).json()
    body = client.get(f"/api/pay-applications/{app['id']}").json()
    assert body["sub_waivers"]["missing"] == 1
    assert body["sub_waivers"]["bills"][0]["vendor_name"] == "Ace Concrete"


# ── Review round: the fixes ─────────────────────────────────────────────


def test_number_keys_on_job_id_not_job_number(client, seed_accounts, seed_customer):
    # two jobs sharing a job_number get distinct application numbers
    j1 = _job(client, seed_customer.id, name="Tower A", job_number="100")
    _contract(client, j1["id"], contract_type="lump_sum", contract_amount=10000)
    _sov(
        client,
        j1["id"],
        [{"description": "x", "kind": "work", "scheduled_value": 10000}],
    )
    j2 = _job(client, seed_customer.id, name="Tower B", job_number="100")
    _contract(client, j2["id"], contract_type="lump_sum", contract_amount=10000)
    _sov(
        client,
        j2["id"],
        [{"description": "y", "kind": "work", "scheduled_value": 10000}],
    )
    a1 = _new_app(client, j1["id"])
    a2 = _new_app(client, j2["id"])
    assert a1["number"] == f"PA-{j1['id']}-01"
    assert a2["number"] == f"PA-{j2['id']}-01"
    assert a1["number"] != a2["number"]
    assert a1["job_number"] == "100"


def _balanced(db_session, txn_id):
    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    dr = sum((Decimal(str(r.debit)) for r in rows), Decimal("0"))
    cr = sum((Decimal(str(r.credit)) for r in rows), Decimal("0"))
    return dr, cr


def test_negative_retainage_this_period_balances(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id, retainage_pct=10)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert _approve(client, a1["id"]).status_code == 200
    assert _gl(db_session, "1150") == Decimal("3000.00")
    # app 2 drops the percent to 5% and bills 10,000 more: retainage to date is
    # 40,000 × 5% = 2,000, below the 3,000 held, so this period is −1,000 and
    # the 1150 leg flips to a credit.
    a2 = _new_app(client, job["id"])
    _edit(
        client,
        a2["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
        retainage_pct=5,
    )
    r = _approve(client, a2["id"])
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["retainage_this_period"] == -1000.0
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    dr, cr = _balanced(db_session, inv.transaction_id)
    assert dr == cr == Decimal("11000.00")  # A/R 11,000 = income 10,000 + 1150 1,000
    assert _gl(db_session, "1150") == Decimal("2000.00")


def test_many_applications_stay_linear(client, seed_accounts, seed_customer):
    import time

    job = _job(client, seed_customer.id)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=1000000,
        retainage_pct=10,
    )
    sov = _sov(
        client,
        job["id"],
        [{"description": "Work", "kind": "work", "scheduled_value": 1000000}],
    )
    for _ in range(15):
        a = _new_app(client, job["id"])
        _edit(
            client,
            a["id"],
            lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
        )
        assert _approve(client, a["id"]).status_code == 200
    t0 = time.perf_counter()
    assert client.get(f"/api/pay-applications?job_id={job['id']}").status_code == 200
    a = _new_app(client, job["id"])
    _edit(
        client,
        a["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
    )
    assert _approve(client, a["id"]).status_code == 200
    assert time.perf_counter() - t0 < 1.0


def _taxed_estimate_job(client, customer_id, rate="0.08"):
    """A job whose SOV came from an accepted estimate carrying a tax rate."""
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-02-01",
            "tax_rate": rate,
            "lines": [
                {"description": "Base", "quantity": 1, "rate": 10000, "unit_cost": 8000}
            ],
        },
    )
    assert r.status_code == 201, r.text
    est = r.json()
    job = _job(client, customer_id, name="Taxed")
    acc = client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]})
    assert acc.status_code == 200, acc.text
    fe = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert fe.status_code == 200, fe.text
    client.put(f"/api/jobs/{job['id']}/contract", json={"retainage_pct": 10})
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    return job, sov


def test_tax_from_accepted_estimate(client, db_session, seed_accounts, seed_customer):
    job, sov = _taxed_estimate_job(client, seed_customer.id, rate="0.08")
    app = _new_app(client, job["id"])
    assert float(app["tax_rate"]) == 0.08  # defaulted from the estimate
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 5000, "stored": 0}],
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv.tax_amount == Decimal("400.00")  # 5,000 × 8%
    assert inv.total == Decimal("5400.00")
    assert _gl(db_session, "2200") == Decimal("-400.00")  # sales tax payable
    dr, cr = _balanced(db_session, inv.transaction_id)
    assert dr == cr


def test_deduct_sov_line_bills_down_and_balances(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=9000,
        retainage_pct=0,
    )
    sov = _sov(
        client,
        job["id"],
        [
            {"description": "Work", "kind": "work", "scheduled_value": 10000},
            {"description": "Deduct alt", "kind": "work", "scheduled_value": -1000},
        ],
    )
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": -1000, "stored": 0},
        ],
    )
    assert r.status_code == 200, r.text
    rr = _approve(client, app["id"])
    assert rr.status_code == 200, rr.text
    inv = db_session.query(Invoice).filter_by(id=rr.json()["invoice_id"]).first()
    assert inv.subtotal == Decimal("9000.00")
    # the deduct line debits income, so the journal's gross is 10,000 both
    # sides — balanced, with A/R taking the 9,000 net.
    dr, cr = _balanced(db_session, inv.transaction_id)
    assert dr == cr
    assert inv.balance_due == Decimal("9000.00")


def test_deduct_line_cannot_bill_past_its_value(client, seed_accounts, seed_customer):
    job = _job(client, seed_customer.id)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=9000)
    sov = _sov(
        client,
        job["id"],
        [
            {"description": "Work", "kind": "work", "scheduled_value": 10000},
            {"description": "Deduct", "kind": "work", "scheduled_value": -1000},
        ],
    )
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[1]["id"], "this_period": -2000, "stored": 0}],
    )
    assert r.status_code == 422  # -2,000 is past the -1,000 deduct


def test_approve_with_nothing_to_bill_422(client, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])  # every line zero, no release, no deposit
    r = _approve(client, app["id"])
    assert r.status_code == 422


def test_deposit_over_current_due_422(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 50000, bank.id)
    app = _new_app(client, job["id"])
    # bill 1,000 at 10% → current due 900; a 5,000 deposit is more than that
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
        deposit_applied=5000,
    )
    assert r.status_code == 422


def test_void_with_a_real_payment_409(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    inv_id = _approve(client, app["id"]).json()["invoice_id"]
    # a real customer payment on the invoice
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-06-05",
            "amount": 1000,
            "method": "Check",
            "allocations": [{"invoice_id": inv_id, "amount": 1000}],
        },
    )
    assert pay.status_code == 201, pay.text
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "invoice_paid"


def test_void_with_an_unrelated_deposit_method_payment_409(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])  # no deposit applied
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    inv_id = _approve(client, app["id"]).json()["invoice_id"]
    bank = _acct(db_session, "1000")
    # an unrelated payment flagged method="deposit" — not this app's own
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-06-05",
            "amount": 500,
            "method": "deposit",
            "deposit_to_account_id": bank.id,
            "allocations": [{"invoice_id": inv_id, "amount": 500}],
        },
    )
    assert pay.status_code == 201, pay.text
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 409


def test_put_sov_dropping_a_billed_line_422(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, app["id"])
    # resend only line 2 — dropping the billed line 1 is refused
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "id": sov[1]["id"],
                    "description": "Structure",
                    "kind": "work",
                    "scheduled_value": 40000,
                }
            ]
        },
    )
    assert r.status_code == 422


def test_second_application_after_a_void(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, a1["id"])
    assert client.post(f"/api/pay-applications/{a1['id']}/void").status_code == 200
    # a fresh application opens (the open-application guard let go on void)
    a2 = _new_app(client, job["id"])
    body = client.get(f"/api/pay-applications/{a2['id']}").json()
    line = next(line for line in body["lines"] if line["sov_line_id"] == sov[0]["id"])
    assert float(line["previous"]) == 0.0  # the only prior app is void


def test_business_invoice_journal_unchanged_with_zero_retainage(
    client, db_session, seed_accounts, seed_customer
):
    # a plain invoice (no retainage) posts the same two-account A/R + income set
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": "0",
            "lines": [{"description": "Work", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    inv = db_session.query(Invoice).filter_by(id=r.json()["id"]).first()
    assert inv.retainage_held == Decimal("0")
    assert inv.retainage_released == Decimal("0")
    rows = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=inv.transaction_id)
        .all()
    )
    # a plain invoice posts only A/R (1100) and income (4000) — no construction
    # retainage accounts are even created on a business books.
    assert _acct(db_session, "1150") is None
    assert len(rows) == 2
    assert {_acct(db_session, "1100").id, _acct(db_session, "4000").id} == {
        row.account_id for row in rows
    }


# ── Migration ───────────────────────────────────────────────────────────


def test_migration_up_down_reverses(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "pa.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "head")
    eng = create_engine("sqlite:///" + db.as_posix())
    insp = inspect(eng)
    assert "pay_applications" in insp.get_table_names()
    assert "job_deposits" in insp.get_table_names()
    pa_cols = {c["name"] for c in insp.get_columns("pay_applications")}
    assert {"tax_rate", "deposit_payment_id"} <= pa_cols
    assert {"charge_tax", "contract_sum_to_date"} <= pa_cols
    assert "lien_waivers" in insp.get_table_names()
    assert "retainage_held" in {c["name"] for c in insp.get_columns("invoices")}
    eng.dispose()
    # the review-fix migrations (#576) reverse on their own, then the original;
    # named revisions, since later steps in the stack sit above them
    command.downgrade(cfg, "e5c9a1f7b2d4")
    eng = create_engine("sqlite:///" + db.as_posix())
    insp = inspect(eng)
    assert "lien_waivers" not in insp.get_table_names()
    assert "charge_tax" not in {c["name"] for c in insp.get_columns("pay_applications")}
    eng.dispose()
    command.downgrade(cfg, "b8d0f2a4c6e8")
    eng = create_engine("sqlite:///" + db.as_posix())
    insp = inspect(eng)
    assert "pay_applications" not in insp.get_table_names()
    assert "retainage_held" not in {c["name"] for c in insp.get_columns("invoices")}
    eng.dispose()


# ── Review fixes (#576) ──────────────────────────────────────────────────


def _bill(client, app_id, lines, **extra):
    """Edit then approve an application with `lines` = [(sov_idx, this, stored)]."""
    _edit(client, app_id, lines=lines, **extra)
    return _approve(client, app_id)


def test_retainage_to_date_is_net_of_releases(
    client, db_session, seed_accounts, seed_customer
):
    # Trent's repro: bill 30k, release 1,500, bill 10k. The application, the
    # invoice and account 1150 all agree.
    job, sov = _two_line_job(client, seed_customer.id)

    def rows(tp):
        return [
            {"sov_line_id": sov[0]["id"], "this_period": tp, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ]

    a1 = _new_app(client, job["id"])
    assert _bill(client, a1["id"], rows(30000)).status_code == 200
    assert _gl(db_session, "1150") == Decimal("3000.00")

    a2 = _new_app(client, job["id"])
    r2 = _bill(client, a2["id"], rows(0), release_retainage=1500)
    assert r2.status_code == 200, r2.text
    t2 = r2.json()["totals"]
    assert t2["retainage_to_date"] == 1500.0
    assert t2["current_due"] == 1500.0
    assert _gl(db_session, "1150") == Decimal("1500.00")

    a3 = _new_app(client, job["id"])
    r3 = _bill(client, a3["id"], rows(10000))
    assert r3.status_code == 200, r3.text
    body = r3.json()
    t3 = body["totals"]
    assert t3["retainage_to_date"] == 2500.0
    assert t3["current_due"] == 9000.0
    inv = db_session.query(Invoice).filter_by(id=body["invoice_id"]).first()
    assert inv.balance_due == Decimal("9000.00")
    assert _gl(db_session, "1150") == Decimal("2500.00")
    assert body["retainage_outstanding"] == 2500.0


def _approved_30k(client, customer_id):
    job, sov = _two_line_job(client, customer_id)
    a1 = _new_app(client, job["id"])
    r = _bill(
        client,
        a1["id"],
        [
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    assert r.status_code == 200, r.text
    return job, sov, r.json()


def test_pay_app_invoice_refuses_amount_edits_allows_notes(
    client, db_session, seed_accounts, seed_customer
):
    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    inv_id = app["invoice_id"]
    before = _gl(db_session, "1150")

    r = client.put(f"/api/invoices/{inv_id}", json={"tax_rate": 0.07})
    assert r.status_code == 409, r.text
    r = client.put(
        f"/api/invoices/{inv_id}",
        json={
            "lines": [
                {"description": "x", "quantity": 1, "rate": 5, "is_taxable": False}
            ]
        },
    )
    assert r.status_code == 409, r.text

    r = client.put(f"/api/invoices/{inv_id}", json={"notes": "Sent by email"})
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "Sent by email"
    inv = db_session.query(Invoice).filter_by(id=inv_id).first()
    db_session.refresh(inv)
    assert inv.total == Decimal("30000.00")
    assert inv.retainage_held == Decimal("3000.00")
    assert _gl(db_session, "1150") == before == Decimal("3000.00")


def test_only_the_latest_approved_application_can_be_voided(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)

    def rows(tp):
        return [
            {"sov_line_id": sov[0]["id"], "this_period": tp, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ]

    a1 = _new_app(client, job["id"])
    assert _bill(client, a1["id"], rows(30000)).status_code == 200
    a2 = _new_app(client, job["id"])
    assert _bill(client, a2["id"], rows(10000)).status_code == 200

    r = client.post(f"/api/pay-applications/{a1['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "later_application"
    assert _gl(db_session, "1150") == Decimal("4000.00")  # nothing reversed

    assert client.post(f"/api/pay-applications/{a2['id']}/void").status_code == 200
    assert client.post(f"/api/pay-applications/{a1['id']}/void").status_code == 200
    assert _gl(db_session, "1150") == Decimal("0.00")


def test_tax_toggle_and_tax_rate_saved_on_put(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _taxed_estimate_job(client, seed_customer.id, rate="0.08")
    app = _new_app(client, job["id"])
    assert app["charge_tax"] is True  # on by default
    lines = [{"sov_line_id": sov[0]["id"], "this_period": 5000, "stored": 0}]
    # tax_rate on the PUT is saved (it was accepted and dropped)
    r = _edit(client, app["id"], lines=lines, tax_rate="0.06")
    assert r.status_code == 200, r.text
    body = r.json()
    assert float(body["tax_rate"]) == 0.06
    # tax shows on the application, so its current due is the invoice's
    t = body["totals"]
    assert t["tax_amount"] == 300.0
    assert t["current_due"] == 4800.0  # 5,000 - 500 retainage + 300 tax
    r = _approve(client, app["id"])
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv.tax_amount == Decimal("300.00")
    assert inv.balance_due == Decimal(str(r.json()["totals"]["current_due"]))

    # a second application with tax off: no tax on the invoice or the app
    a2 = _new_app(client, job["id"])
    r = _edit(
        client,
        a2["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
        charge_tax=False,
    )
    assert r.json()["charge_tax"] is False
    assert r.json()["totals"]["tax_amount"] == 0.0
    r = _approve(client, a2["id"])
    inv2 = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv2.tax_amount == Decimal("0.00")
    assert inv2.balance_due == Decimal("900.00")
    assert r.json()["totals"]["current_due"] == 900.0


def test_stored_to_installed_only_application_has_no_invoice(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    r = _bill(
        client,
        a1["id"],
        [{"sov_line_id": sov[0]["id"], "this_period": 0, "stored": 5000}],
    )
    assert r.status_code == 200, r.text
    n_invoices = db_session.query(Invoice).count()

    # materials installed: this period takes up the stored amount
    a2 = _new_app(client, job["id"])
    r = _bill(
        client,
        a2["id"],
        [{"sov_line_id": sov[0]["id"], "this_period": 5000, "stored": 0}],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["invoice_id"] is None
    assert body["totals"]["current_due"] == 0.0
    assert db_session.query(Invoice).count() == n_invoices
    # it still voids cleanly
    assert client.post(f"/api/pay-applications/{a2['id']}/void").status_code == 200


def test_empty_application_still_refused(client, seed_accounts, seed_customer):
    job, _sov_ = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    assert _approve(client, app["id"]).status_code == 422


def test_pdf_uses_contract_sums_as_of_approval(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.jobs import Job
    from app.routes.pay_applications import _out
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings

    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    assert (
        _bill(
            client,
            app["id"],
            [{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
        ).status_code
        == 200
    )
    jb = db_session.query(Job).get(job["id"])
    jb.contract_amount = Decimal("123456.00")  # a later change order
    db_session.commit()
    pa = db_session.query(PayApplication).filter_by(id=app["id"]).first()
    html = render_pay_application_html(
        pa, _out(db_session, pa), jb, get_all_settings(db_session)
    )
    assert "100,000.00" in html
    assert "123,456.00" not in html


def test_applied_deposit_payment_cannot_be_voided_from_payments(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 10000, bank.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    assert _approve(client, app["id"]).status_code == 200
    pa = db_session.query(PayApplication).filter_by(id=app["id"]).first()
    r = client.post(f"/api/payments/{pa.deposit_payment_id}/void")
    assert r.status_code == 409
    assert pa.number in r.json()["detail"]
    # the application's own void still reverses it
    assert client.post(f"/api/pay-applications/{app['id']}/void").status_code == 200
    listing = client.get(f"/api/jobs/{job['id']}/deposits").json()
    assert listing["unapplied"] == 10000.0


def test_negative_this_period_or_stored_cannot_go_below_zero(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    # nothing billed yet: a negative this period would leave completed < 0
    r = _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": -100, "stored": 0}],
    )
    assert r.status_code == 422
    # a negative stored is refused the same way
    r = _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 500, "stored": -100}],
    )
    assert r.status_code == 422
    # a correction that keeps completed >= 0 is fine
    assert (
        _bill(
            client,
            a1["id"],
            [{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
        ).status_code
        == 200
    )
    a2 = _new_app(client, job["id"])
    r = _edit(
        client,
        a2["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": -400, "stored": 0}],
    )
    assert r.status_code == 200, r.text


# ── Lien waivers ─────────────────────────────────────────────────────────


def _pay_invoice(client, customer_id, invoice_id, amount):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-06-15",
            "amount": amount,
            "method": "check",
            "allocations": [{"invoice_id": invoice_id, "amount": amount}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_lien_waivers_one_per_payment_and_type(
    client, db_session, seed_accounts, seed_customer
):
    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    pay = _pay_invoice(client, seed_customer.id, app["invoice_id"], 5000)
    made = {}
    for kind in (
        "conditional_progress",
        "unconditional_progress",
        "conditional_final",
        "unconditional_final",
    ):
        r = client.post(
            "/api/lien-waivers",
            json={"payment_id": pay["id"], "waiver_type": kind, "signed_by": "Pat"},
        )
        assert r.status_code == 201, r.text
        w = r.json()
        assert w["waiver_type"] == kind
        assert w["amount"] == "5000.00" or float(w["amount"]) == 5000.0
        assert w["pay_application_id"] == app["id"]
        assert w["job_id"] == app["job_id"]
        made[kind] = w
    # the same type twice for a payment is refused
    r = client.post(
        "/api/lien-waivers",
        json={"payment_id": pay["id"], "waiver_type": "conditional_final"},
    )
    assert r.status_code == 409
    # a type outside the four is a validation error
    r = client.post(
        "/api/lien-waivers", json={"payment_id": pay["id"], "waiver_type": "bogus"}
    )
    assert r.status_code == 422
    listed = client.get(f"/api/lien-waivers?payment_id={pay['id']}").json()
    assert len(listed) == 4
    # the application's Waivers screen reads its payments with their waivers
    rows = client.get(f"/api/pay-applications/{app['id']}/payments").json()
    assert [r["id"] for r in rows] == [pay["id"]]
    assert len(rows[0]["waivers"]) == 4


def test_lien_waiver_wording_follows_the_type(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.jobs import Job
    from app.models.lien_waivers import LienWaiver
    from app.services.pdf_service import render_lien_waiver_html
    from app.services.settings_service import get_all_settings

    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    pay = _pay_invoice(client, seed_customer.id, app["invoice_id"], 5000)
    for kind, title, effect in (
        (
            "conditional_progress",
            "Conditional Waiver and Release on Progress Payment",
            "Upon receipt",
        ),
        (
            "unconditional_progress",
            "Unconditional Waiver and Release on Progress Payment",
            "has been paid",
        ),
        (
            "conditional_final",
            "Conditional Waiver and Release on Final Payment",
            "whole of the project",
        ),
        (
            "unconditional_final",
            "Unconditional Waiver and Release on Final Payment",
            "has been paid",
        ),
    ):
        w = client.post(
            "/api/lien-waivers", json={"payment_id": pay["id"], "waiver_type": kind}
        ).json()
        row = db_session.get(LienWaiver, w["id"])
        html = render_lien_waiver_html(
            row,
            row.payment,
            db_session.get(Job, row.job_id),
            get_all_settings(db_session),
        )
        assert title in html
        assert effect in html
        assert "5,000.00" in html


def test_lien_waiver_pdf_route(client, db_session, seed_accounts, seed_customer):
    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    pay = _pay_invoice(client, seed_customer.id, app["invoice_id"], 5000)
    w = client.post(
        "/api/lien-waivers",
        json={"payment_id": pay["id"], "waiver_type": "conditional_progress"},
    ).json()
    r = client.get(f"/api/lien-waivers/{w['id']}/pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_lien_waiver_refused_on_a_voided_payment(
    client, db_session, seed_accounts, seed_customer
):
    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    pay = _pay_invoice(client, seed_customer.id, app["invoice_id"], 5000)
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    r = client.post(
        "/api/lien-waivers",
        json={"payment_id": pay["id"], "waiver_type": "conditional_progress"},
    )
    assert r.status_code == 409


# ── Review fixes, round two (#576) ───────────────────────────────────────


def _rows30(sov, tp):
    return [
        {"sov_line_id": sov[0]["id"], "this_period": tp, "stored": 0},
        {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
    ]


def test_release_with_percent_dropped_to_zero_refused(
    client, db_session, seed_accounts, seed_customer
):
    job, sov, _a1 = _approved_30k(client, seed_customer.id)
    assert _gl(db_session, "1150") == Decimal("3000.00")
    a2 = _new_app(client, job["id"])
    r = _edit(
        client,
        a2["id"],
        lines=_rows30(sov, 0),
        retainage_pct=0,
        release_retainage=3000,
    )
    assert r.status_code == 422, r.text
    assert "-3000.00" in r.json()["detail"]
    assert _gl(db_session, "1150") == Decimal("3000.00")


def test_full_release_at_the_same_percent_is_allowed(
    client, db_session, seed_accounts, seed_customer
):
    job, sov, _a1 = _approved_30k(client, seed_customer.id)
    a2 = _new_app(client, job["id"])
    r = _bill(client, a2["id"], _rows30(sov, 0), release_retainage=3000)
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["retainage_to_date"] == 0.0
    assert t["current_due"] == 3000.0
    assert _gl(db_session, "1150") == Decimal("0.00")


def test_pay_app_invoice_cannot_be_voided_from_the_invoice_screen(
    client, db_session, seed_accounts, seed_customer
):
    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    r = client.post(f"/api/invoices/{app['invoice_id']}/void")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "invoice_belongs_to_pay_application"
    pa = db_session.query(PayApplication).filter_by(id=app["id"]).first()
    db_session.refresh(pa)
    assert pa.status == "approved"
    assert _gl(db_session, "1150") == Decimal("3000.00")
    # voiding through the application still works
    assert client.post(f"/api/pay-applications/{app['id']}/void").status_code == 200
    inv = db_session.query(Invoice).filter_by(id=app["invoice_id"]).first()
    db_session.refresh(inv)
    assert inv.status.value == "void"
    assert _gl(db_session, "1150") == Decimal("0.00")


def test_tax_rate_keeps_six_decimals(client, db_session, seed_accounts, seed_customer):
    job, sov = _taxed_estimate_job(client, seed_customer.id, rate="0.08875")
    app = _new_app(client, job["id"])
    assert Decimal(str(app["tax_rate"])) == Decimal("0.08875")
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv.tax_amount == Decimal("887.50")


def test_waiver_amount_is_the_part_paid_to_the_jobs_invoices(
    client, db_session, seed_accounts, seed_customer
):
    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    other = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": "0",
            "lines": [{"description": "Other", "quantity": 1, "rate": 700}],
        },
    )
    assert other.status_code == 201, other.text
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-06-15",
            "amount": 1000,
            "method": "check",
            "allocations": [
                {"invoice_id": app["invoice_id"], "amount": 300},
                {"invoice_id": other.json()["id"], "amount": 700},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    w = client.post(
        "/api/lien-waivers",
        json={"payment_id": r.json()["id"], "waiver_type": "conditional_progress"},
    )
    assert w.status_code == 201, w.text
    assert float(w.json()["amount"]) == 300.0
    # the invoice with no job does not make the payment's job ambiguous, and
    # the waiver is the job's whatever the invoices' ids
    assert w.json()["job_id"] == app["job_id"]
    assert w.json()["pay_application_id"] == app["id"]


def test_waiver_per_job_on_a_payment_split_across_two_jobs(
    client, db_session, seed_accounts, seed_customer
):
    """A payment applied to two jobs' invoices gets a waiver per job, each for
    the part applied to that job; the route needs to be told which (#576)."""
    _ja, _sa, app_a = _approved_30k(client, seed_customer.id)
    job_b, sov_b = _two_line_job(client, seed_customer.id, name="Annex")
    b1 = _new_app(client, job_b["id"])
    app_b = _bill(
        client,
        b1["id"],
        [
            {"sov_line_id": sov_b[0]["id"], "this_period": 20000, "stored": 0},
            {"sov_line_id": sov_b[1]["id"], "this_period": 0, "stored": 0},
        ],
    ).json()
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-06-15",
            "amount": 1000,
            "method": "check",
            "allocations": [
                {"invoice_id": app_a["invoice_id"], "amount": 300},
                {"invoice_id": app_b["invoice_id"], "amount": 700},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    pay_id = r.json()["id"]
    # no job named: refused, naming the jobs
    r = client.post(
        "/api/lien-waivers",
        json={"payment_id": pay_id, "waiver_type": "conditional_progress"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "job_required"
    assert r.json()["detail"]["job_ids"] == sorted([app_a["job_id"], job_b["id"]])
    assert "Tower" in r.json()["detail"]["message"]
    assert "Annex" in r.json()["detail"]["message"]
    # a job the payment does not pay
    r = client.post(
        "/api/lien-waivers",
        json={
            "payment_id": pay_id,
            "job_id": 999999,
            "waiver_type": "conditional_progress",
        },
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "job_not_paid"
    # one per job, each for its part
    wa = client.post(
        "/api/lien-waivers",
        json={
            "payment_id": pay_id,
            "job_id": app_a["job_id"],
            "waiver_type": "conditional_progress",
        },
    )
    assert wa.status_code == 201, wa.text
    assert float(wa.json()["amount"]) == 300.0
    assert wa.json()["job_id"] == app_a["job_id"]
    assert wa.json()["pay_application_id"] == app_a["id"]
    wb = client.post(
        "/api/lien-waivers",
        json={
            "payment_id": pay_id,
            "job_id": job_b["id"],
            "waiver_type": "conditional_progress",
        },
    )
    assert wb.status_code == 201, wb.text
    assert float(wb.json()["amount"]) == 700.0
    assert wb.json()["job_id"] == job_b["id"]
    assert wb.json()["pay_application_id"] == app_b["id"]
    # the same type again for the same job is refused; another type is fine
    r = client.post(
        "/api/lien-waivers",
        json={
            "payment_id": pay_id,
            "job_id": job_b["id"],
            "waiver_type": "conditional_progress",
        },
    )
    assert r.status_code == 409
    r = client.post(
        "/api/lien-waivers",
        json={
            "payment_id": pay_id,
            "job_id": job_b["id"],
            "waiver_type": "unconditional_progress",
        },
    )
    assert r.status_code == 201, r.text
    # listed by payment and by job; each application's screen shows its own
    assert len(client.get(f"/api/lien-waivers?payment_id={pay_id}").json()) == 3
    assert len(client.get(f"/api/lien-waivers?job_id={app_a['job_id']}").json()) == 1
    rows = client.get(f"/api/pay-applications/{app_a['id']}/payments").json()
    assert [w["id"] for w in rows[0]["waivers"]] == [wa.json()["id"]]
    rows = client.get(f"/api/pay-applications/{app_b['id']}/payments").json()
    assert {w["waiver_type"] for w in rows[0]["waivers"]} == {
        "conditional_progress",
        "unconditional_progress",
    }


def test_a_pay_application_locks_the_contract_sum_and_contingency(
    client, seed_accounts, seed_customer
):
    """The sum and contingency edit freely until the first pay application;
    from then on they move by change order only (#552, #555). A void
    application frees them again."""
    job, _sov = _two_line_job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    assert client.put(base, json={"contract_amount": 110000}).status_code == 200
    app = _new_app(client, job["id"])
    r = client.put(base, json={"contract_amount": 120000})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "contract_sum_locked"
    r = client.put(base, json={"contingency_amount": 5000})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "contingency_locked"
    assert client.put(base, json={"contract_amount": 110000}).status_code == 200
    # an approved then voided application leaves no billing behind
    r = _bill(
        client,
        app["id"],
        [
            {"sov_line_id": _sov[0]["id"], "this_period": 30000, "stored": 0},
            {"sov_line_id": _sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    assert r.status_code == 200, r.text
    assert client.put(base, json={"contract_amount": 120000}).status_code == 409
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 200, r.text
    assert client.put(base, json={"contract_amount": 120000}).status_code == 200


def _billed_deduct_job(client, customer_id):
    """A job whose deduct line (-1,000) is fully billed on an approved app."""
    job = _job(client, customer_id)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=9000,
        retainage_pct=0,
    )
    sov = _sov(
        client,
        job["id"],
        [
            {"description": "Work", "kind": "work", "scheduled_value": 10000},
            {"description": "Deduct alt", "kind": "work", "scheduled_value": -1000},
        ],
    )
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": -1000, "stored": 0},
        ],
    )
    assert _approve(client, app["id"]).status_code == 200
    return job, sov


def test_put_sov_dropping_a_billed_deduct_line_422(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _billed_deduct_job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "id": sov[0]["id"],
                    "description": "Work",
                    "kind": "work",
                    "scheduled_value": 10000,
                }
            ]
        },
    )
    assert r.status_code == 422
    assert "has been billed" in r.json()["detail"]


def test_put_sov_cannot_cut_a_billed_deduct_line_back(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _billed_deduct_job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "id": sov[0]["id"],
                    "description": "Work",
                    "kind": "work",
                    "scheduled_value": 10000,
                },
                {
                    "id": sov[1]["id"],
                    "description": "Deduct alt",
                    "kind": "work",
                    "scheduled_value": -400,
                },
            ]
        },
    )
    assert r.status_code == 422
    assert "has billed -1000.00" in r.json()["detail"]


def test_an_application_dated_at_the_calendars_end_is_refused_not_a_500(
    client, db_session, seed_accounts, seed_customer
):
    """The invoice's due date is the date plus its terms; 9999-12-31 plus 30
    days runs past the calendar. The request schemas stop such a date, but one
    stored by an import reaches approve: a 422 sentence, not an OverflowError."""
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
    )
    assert r.status_code == 200, r.text
    db_session.query(PayApplication).filter_by(id=app["id"]).update(
        {"date": date(9999, 12, 31)}
    )
    db_session.commit()
    r = _approve(client, app["id"])
    assert r.status_code == 422, r.text
    assert "runs past the calendar" in r.json()["detail"]
    assert db_session.query(Invoice).count() == 0


def _dated_co_job(client, customer_id):
    """A 100,000 job at 10% retainage with one 10,000 change order approved
    effective 2026-08-10; returns (job, base SOV line, CO SOV line)."""
    job = _job(client, customer_id)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=100000,
        retainage_pct=10,
    )
    base = _sov(
        client,
        job["id"],
        [{"description": "Base", "kind": "work", "scheduled_value": 100000}],
    )[0]
    co = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "Extra",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 10000,
                }
            ],
        },
    ).json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(
        f"/api/change-orders/{co['id']}/approve", json={"approved_on": "2026-08-10"}
    )
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    co_line = next(l for l in sov if l["kind"] == "change_order")
    return job, base, co_line


def _dated_app(client, job_id, on, lines):
    app = client.post(
        "/api/pay-applications", json={"job_id": job_id, "date": on}
    ).json()
    assert _edit(client, app["id"], lines=lines).status_code == 200
    return app


def test_a_back_dated_application_keeps_a_line_an_earlier_one_billed(
    client, db_session, seed_accounts, seed_customer
):
    """An application dated before a change order takes effect still carries
    that change order's line when an earlier approved application billed it,
    so completed-to-date never falls and approve posts no retainage release."""
    job, base, co_line = _dated_co_job(client, seed_customer.id)
    a1 = _dated_app(
        client,
        job["id"],
        "2026-09-01",
        [
            {"sov_line_id": base["id"], "this_period": 10000, "stored": 0},
            {"sov_line_id": co_line["id"], "this_period": 10000, "stored": 0},
        ],
    )
    assert _approve(client, a1["id"]).status_code == 200
    a2 = _dated_app(
        client,
        job["id"],
        "2026-08-01",
        [{"sov_line_id": base["id"], "this_period": 5000, "stored": 0}],
    )
    r = _approve(client, a2["id"])
    assert r.status_code == 200, r.text
    totals = r.json()["totals"]
    assert totals["completed_to_date"] == 25000.0
    assert totals["retainage_to_date"] == 2500.0
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv.retainage_held == Decimal("500.00")
    assert inv.retainage_held >= 0
    rows = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=inv.transaction_id)
        .all()
    )
    by_acct = {}
    for row in rows:
        num = row.account.account_number
        net = Decimal(str(row.debit)) - Decimal(str(row.credit))
        by_acct[num] = by_acct.get(num, Decimal("0")) + net
    assert by_acct["1100"] == Decimal("4500.00")
    assert by_acct["1150"] == Decimal("500.00")  # a debit: held, never released
    assert sum(by_acct.values()) == Decimal("0.00")


def test_a_back_dated_application_leaves_off_a_line_nobody_billed(
    client, seed_accounts, seed_customer
):
    """A change order's line nothing has billed yet stays off an application
    dated before the change order takes effect."""
    job, base, co_line = _dated_co_job(client, seed_customer.id)
    a = _dated_app(
        client,
        job["id"],
        "2026-08-01",
        [{"sov_line_id": base["id"], "this_period": 5000, "stored": 0}],
    )
    ids = [
        ln["sov_line_id"]
        for ln in client.get(f"/api/pay-applications/{a['id']}").json()["lines"]
    ]
    assert base["id"] in ids and co_line["id"] not in ids


# ── Companies upgraded from the release before ───────────────────────────


def test_an_existing_schedule_line_keeps_crediting_the_default_income_account(
    client, db_session, seed_accounts, seed_customer
):
    """Lines made before a revenue account existed are pointed at the default
    income account (4000), so a job part-way through bills every application
    to one account and the first approve creates no Contract Revenue."""
    import importlib.util

    from app.models.jobs import SovLine

    job, _lines = _two_line_job(client, seed_customer.id)
    path = next((ROOT / "migrations" / "versions").glob("a4d8e2f6b9c3_*.py"))
    spec = importlib.util.spec_from_file_location("sov_revenue_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    db_session.query(SovLine).update({SovLine.revenue_account_id: None})
    db_session.commit()
    migration._default_existing_lines_to_income(db_session.connection())
    db_session.commit()
    income = _acct(db_session, "4000")
    assert {s.revenue_account_id for s in db_session.query(SovLine).all()} == {
        income.id
    }

    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    for day, amount in (("2026-06-01", 10000), ("2026-07-01", 5000)):
        r = client.post(
            "/api/pay-applications", json={"job_id": job["id"], "date": day}
        )
        assert r.status_code == 201, r.text
        app_id = r.json()["id"]
        _edit(
            client,
            app_id,
            lines=[{"sov_line_id": sov[0]["id"], "this_period": amount, "stored": 0}],
        )
        assert _approve(client, app_id).status_code == 200
    assert _gl(db_session, "4000") == Decimal("-15000")
    assert _acct(db_session, "4050") is None


def _bayview(client, seed_customer):
    """The job: a $100,000 contract, CO-003 dated 8/10 and approved
    effective 9/20, and a pay application on 9/01 (CO line absent)."""
    job = _job(client, seed_customer.id, name="Bayview")
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": 100000,
            "retainage_pct": 10,
        },
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{"description": "Base", "scheduled_value": 100000}]},
    )
    assert r.status_code == 200, r.text
    co = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "date": "2026-08-10",
            "lines": [{"cost_type": "material", "quantity": 1, "unit_cost": 1000}],
        },
    ).json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(
        f"/api/change-orders/{co['id']}/approve", json={"approved_on": "2026-09-20"}
    )
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    co_line = next(s for s in sov if s["kind"] == "change_order")
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-09-01"}
    )
    assert r.status_code == 201, r.text
    app = r.json()
    assert co_line["id"] not in {ln["sov_line_id"] for ln in app["lines"]}
    return job, co, co_line, app


def test_moving_a_billed_change_order_line_before_its_date_is_refused(
    client, seed_accounts, seed_customer
):
    """PUT date 9/25 (the CO line appears), bill 100 on it, PUT
    date 9/01 → was 200 and approve billed CO-003 on a 9/01 invoice."""
    job, co, co_line, app = _bayview(client, seed_customer)
    url = f"/api/pay-applications/{app['id']}"
    assert client.put(url, json={"date": "2026-09-25"}).status_code == 200
    r = client.put(
        url, json={"lines": [{"sov_line_id": co_line["id"], "this_period": 100}]}
    )
    assert r.status_code == 200, r.text
    r = client.put(url, json={"date": "2026-09-01"})
    assert r.status_code == 409, r.text
    message = r.json()["detail"]["message"]
    assert co["number"] in message and "2026-09-20" in message
    assert client.get(url).json()["date"] == "2026-09-25"
    # cleared, the line no longer carries the change, and the date moves
    r = client.put(
        url, json={"lines": [{"sov_line_id": co_line["id"], "this_period": 0}]}
    )
    assert r.status_code == 200, r.text
    assert client.put(url, json={"date": "2026-09-01"}).status_code == 200


def test_approve_re_checks_a_change_order_line_before_its_date(
    client, db_session, seed_accounts, seed_customer
):
    """Approve re-checks what the date PUT does (an application whose
    date reached the row some other way)."""
    job, co, co_line, app = _bayview(client, seed_customer)
    url = f"/api/pay-applications/{app['id']}"
    assert client.put(url, json={"date": "2026-09-25"}).status_code == 200
    r = client.put(
        url, json={"lines": [{"sov_line_id": co_line["id"], "this_period": 100}]}
    )
    assert r.status_code == 200, r.text
    from app.models.pay_applications import PayApplication

    db_session.expire_all()
    db_session.get(PayApplication, app["id"]).date = date(2026, 9, 1)
    db_session.commit()
    r = client.post(f"{url}/approve")
    assert r.status_code == 409, r.text
    assert co["number"] in r.json()["detail"]["message"]
    assert client.get(url).json()["status"] == "draft"


def _pdf_html(current_due, pct):
    """The template over a synthetic application: no database, no WeasyPrint."""
    totals = {
        k: 0
        for k in (
            "completed_to_date",
            "previous_retainage",
            "retainage_this_period",
            "release_retainage",
            "retainage_to_date",
            "total_earned_less_retainage",
            "previous_applications",
            "tax_amount",
            "deposit_applied",
            "net_due",
            "balance_to_finish_total",
            "scheduled_total",
            "previous_total",
            "this_period_total",
            "stored_total",
            "previous_released",
        )
    }
    totals.update(current_due=current_due, this_period_total=current_due)
    line = {
        "item_no": "01",
        "description": "Deduct alternate",
        "scheduled_value": -1000,
        "previous": 0,
        "this_period": 0,
        "stored": 0,
        "completed_to_date": 0,
        "pct_complete": pct,
        "balance_to_finish": -1000,
        "retainage": 0,
    }
    pa = SimpleNamespace(lines=[SimpleNamespace(**line)], totals=totals)
    app = SimpleNamespace(
        number="PA-5-02",
        application_no=2,
        status="draft",
        period_to=date(2026, 9, 30),
        date=date(2026, 9, 30),
    )
    header = {
        "owner": "Owner",
        "job_name": "Tower",
        "net_change": 0,
        "contract_sum_original": 1,
        "contract_sum_to_date": 1,
    }
    return _render(
        "pay_application_pdf.html",
        {"company_name": "Co"},
        app=app,
        pa=pa,
        job=None,
        header=header,
        totals=totals,
    )


def test_a_negative_current_payment_due_keeps_its_sign_on_one_line():
    """PA-5-02 (-10% application) printed "-" on one line and
    "$7,894.13" below; the totals row wrapped "-$8,366.51" the same way. Every
    money cell, in the summary and in the line detail, is nowrap."""
    html = _pdf_html(Decimal("-7894.13"), 0.0)
    assert '<td class="r grand">-$7,894.13</td>' in html
    assert html.count("-$7,894.13") >= 2  # summary and the line-detail totals
    for selector in (".cert td.r", "table.cont td.r"):
        rule = next(
            ln for ln in html.splitlines() if ln.strip().startswith(selector + " {")
        )
        assert "white-space: nowrap" in rule, selector


def test_a_deduct_line_with_nothing_billed_prints_zero_percent_not_minus_zero():
    """A deduct line with nothing billed printed "-0.0%"."""
    html = _pdf_html(Decimal("0"), -0.0)
    assert "-0.0%" not in html
    assert ">0.0%</td>" in html
    # a real percentage still prints as it was
    assert ">12.5%</td>" in _pdf_html(Decimal("0"), 12.5)
    assert ">-12.5%</td>" in _pdf_html(Decimal("0"), -12.5)


WORK = {"description": "Work", "kind": "work", "scheduled_value": 116500}


def _setup(client, customer_id, lines, amount, name="Tower", **terms):
    job = _job(client, customer_id, name=name)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": amount,
            "retainage_pct": 10,
            **terms,
        },
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/jobs/{job['id']}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text
    return job, r.json(), client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]


def test_a_schedule_short_of_the_contract_saves_with_a_warning(client, seed_customer):
    """Contract 118,500, SOV 116,500 saved with sov_balance 2000.0
    and warnings None. It still saves (a job may be mid-setup)."""
    job, saved, _sov = _setup(client, seed_customer.id, [WORK], 118500)
    assert saved["sov_balance"] == 2000.0
    assert len(saved["warnings"]) == 1
    assert "$116,500.00" in saved["warnings"][0]
    assert "$2,000.00 short of" in saved["warnings"][0]
    assert "$118,500.00" in saved["warnings"][0]
    # over the contract says so too
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{**WORK, "scheduled_value": 120000}]},
    )
    assert r.status_code == 200, r.text
    assert "$1,500.00 over" in r.json()["warnings"][0]
    # a schedule that foots has nothing to say
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{**WORK, "scheduled_value": 118500}]},
    )
    assert r.json()["warnings"] == []


def _new_app_2(client, job_id, day="2026-09-30"):
    r = client.post("/api/pay-applications", json={"job_id": job_id, "date": day})
    assert r.status_code == 201, r.text
    return r.json()


def test_the_pay_app_approve_response_warns_of_a_short_schedule(
    client, seed_accounts, seed_customer
):
    job, _saved, sov = _setup(client, seed_customer.id, [WORK], 118500)
    app = _new_app_2(client, job["id"])
    assert any("short of" in w for w in app["warnings"])
    r = client.put(
        f"/api/pay-applications/{app['id']}",
        json={
            "tax_rate": 0.065,
            "lines": [{"sov_line_id": sov[0]["id"], "this_period": 10000}],
        },
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{app['id']}/approve")
    assert r.status_code == 200, r.text
    warnings = r.json()["warnings"]
    assert any("short of" in w for w in warnings), warnings
    assert not any("tax rate of 0" in w for w in warnings), warnings


def test_a_job_with_no_schedule_or_no_sum_has_no_balance_warning(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(f"/api/jobs/{job['id']}/sov", json={"lines": []})
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []


TAXABLE = {"description": "Work", "kind": "work", "scheduled_value": 60000}


def test_taxable_lines_at_a_tax_rate_of_0_warn_where_to_set_the_rate(
    client, seed_accounts, seed_customer
):
    """A job with no accepted estimate defaults its pay apps to
    tax rate 0; lines taxable, app 1 returned tax_amount 0.0 with
    taxable_billed 59000.0 and said nothing."""
    job, _saved, sov = _setup(client, seed_customer.id, [TAXABLE], 60000)
    app = _new_app_2(client, job["id"])
    assert Decimal(app["tax_rate"]) == 0
    tax_warnings = [w for w in app["warnings"] if "tax rate of 0" in w]
    assert len(tax_warnings) == 1
    assert "Tax rate" in tax_warnings[0]
    # setting the rate clears it
    body = {"lines": [{"sov_line_id": sov[0]["id"], "this_period": 59000}]}
    r = client.put(
        f"/api/pay-applications/{app['id']}", json={**body, "tax_rate": 0.065}
    )
    assert not any("tax rate of 0" in w for w in r.json()["warnings"])
    assert r.json()["totals"]["tax_amount"] == 3835.0
    # unticking Charge tax is an answer too
    r = client.put(
        f"/api/pay-applications/{app['id']}", json={"tax_rate": 0, "charge_tax": False}
    )
    assert not any("tax rate of 0" in w for w in r.json()["warnings"])
    # and ticking it back brings the warning back
    r = client.put(f"/api/pay-applications/{app['id']}", json={"charge_tax": True})
    assert any("tax rate of 0" in w for w in r.json()["warnings"])


def test_no_tax_warning_when_no_line_is_taxable(client, seed_accounts, seed_customer):
    line = {**TAXABLE, "is_taxable": False}
    job, _saved, _sov = _setup(client, seed_customer.id, [line], 60000)
    app = _new_app_2(client, job["id"])
    assert not any("tax rate of 0" in w for w in app["warnings"])


def test_a_contractor_pays_pay_app_reads_no_taxable_billing(
    client, seed_accounts, seed_customer
):
    """A contractor-pays pay app returned taxable_billed 5000.0
    beside tax_amount 0.0, which read like a missed tax. It reads 0, and the
    tax-rate-0 warning stays quiet (no tax is due)."""
    job, _saved, sov = _setup(
        client,
        seed_customer.id,
        [{**TAXABLE, "scheduled_value": 60000}],
        60000,
        sales_tax_treatment="contractor_pays",
    )
    app = _new_app_2(client, job["id"])
    r = client.put(
        f"/api/pay-applications/{app['id']}",
        json={"lines": [{"sov_line_id": sov[0]["id"], "this_period": 5000}]},
    )
    assert r.status_code == 200, r.text
    totals = r.json()["totals"]
    assert totals["tax_amount"] == 0.0
    assert totals["taxable_billed"] == 0.0
    assert totals["billed_this_period"] == 5000.0
    assert not any("tax rate of 0" in w for w in r.json()["warnings"])


def test_a_charge_owner_pay_app_still_reports_its_taxable_billing(
    client, seed_accounts, seed_customer
):
    job, _saved, sov = _setup(client, seed_customer.id, [TAXABLE], 60000)
    app = _new_app_2(client, job["id"])
    r = client.put(
        f"/api/pay-applications/{app['id']}",
        json={
            "tax_rate": 0.065,
            "lines": [{"sov_line_id": sov[0]["id"], "this_period": 5000}],
        },
    )
    assert r.json()["totals"]["taxable_billed"] == 5000.0
    assert r.json()["totals"]["tax_amount"] == 325.0


TODAY = date.today()


def _void_later(db, co_id, on):
    """Mark a CO voided on `on` (the void route stamps now)."""
    co = db.get(ChangeOrder, co_id)
    co.voided_at = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc)
    db.commit()


def test_a_draft_pay_app_leaves_out_a_change_order_voided_since(
    client, db_session, seed_customer
):
    """The void took the change order's schedule lines, so the draft's
    contract sum to date reads without it and still foots with them."""
    from app.services.contracts import contract_changes_after

    job = _change_job(client, seed_customer.id)
    co = _co(client, job["id"], [_sub_line("Added", 1000)])
    assert _submit_approve(client, co["id"]).status_code == 200
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    _void_later(db_session, co["id"], TODAY + timedelta(days=5))
    db_session.expire_all()
    from app.models.jobs import Job

    row = db_session.get(Job, job["id"])
    assert contract_changes_after(db_session, row, TODAY) == 0
