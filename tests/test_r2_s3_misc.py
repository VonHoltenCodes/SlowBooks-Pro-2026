"""Skeptic round 2, S3: the pay-app PDF keeps a negative amount's sign with
its figure, a schedule of values short of the contract and a taxable pay
application at a tax rate of 0 each say so, a contractor-pays pay application
reads no taxable billing, and an allowance return takes a date."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget
from app.services.pdf_service import _render


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


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


def _new_app(client, job_id, day="2026-09-30"):
    r = client.post("/api/pay-applications", json={"job_id": job_id, "date": day})
    assert r.status_code == 201, r.text
    return r.json()


# ── 1. The pay-app PDF ───────────────────────────────────────────────────


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
    """M bug 10: PA-5-02 (-10% application) printed "-" on one line and
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
    """L weakness: a deduct line with nothing billed printed "-0.0%"."""
    html = _pdf_html(Decimal("0"), -0.0)
    assert "-0.0%" not in html
    assert ">0.0%</td>" in html
    # a real percentage still prints as it was
    assert ">12.5%</td>" in _pdf_html(Decimal("0"), 12.5)
    assert ">-12.5%</td>" in _pdf_html(Decimal("0"), -12.5)


# ── 2. A schedule of values short of the contract ────────────────────────


WORK = {"description": "Work", "kind": "work", "scheduled_value": 116500}


def test_a_schedule_short_of_the_contract_saves_with_a_warning(client, seed_customer):
    """L weakness: contract 118,500, SOV 116,500 saved with sov_balance 2000.0
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


def test_the_pay_app_approve_response_warns_of_a_short_schedule(
    client, seed_accounts, seed_customer
):
    job, _saved, sov = _setup(client, seed_customer.id, [WORK], 118500)
    app = _new_app(client, job["id"])
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


# ── 3. Tax rate 0 on taxable lines; contractor-pays taxable_billed ───────

TAXABLE = {"description": "Work", "kind": "work", "scheduled_value": 60000}


def test_taxable_lines_at_a_tax_rate_of_0_warn_where_to_set_the_rate(
    client, seed_accounts, seed_customer
):
    """L weakness: a job with no accepted estimate defaults its pay apps to
    tax rate 0; lines taxable, app 1 returned tax_amount 0.0 with
    taxable_billed 59000.0 and said nothing."""
    job, _saved, sov = _setup(client, seed_customer.id, [TAXABLE], 60000)
    app = _new_app(client, job["id"])
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
    app = _new_app(client, job["id"])
    assert not any("tax rate of 0" in w for w in app["warnings"])


def test_a_contractor_pays_pay_app_reads_no_taxable_billing(
    client, seed_accounts, seed_customer
):
    """L weakness: a contractor-pays pay app returned taxable_billed 5000.0
    beside tax_amount 0.0, which read like a missed tax. It reads 0, and the
    tax-rate-0 warning stays quiet (no tax is due)."""
    job, _saved, sov = _setup(
        client,
        seed_customer.id,
        [{**TAXABLE, "scheduled_value": 60000}],
        60000,
        sales_tax_treatment="contractor_pays",
    )
    app = _new_app(client, job["id"])
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
    app = _new_app(client, job["id"])
    r = client.put(
        f"/api/pay-applications/{app['id']}",
        json={
            "tax_rate": 0.065,
            "lines": [{"sov_line_id": sov[0]["id"], "this_period": 5000}],
        },
    )
    assert r.json()["totals"]["taxable_billed"] == 5000.0
    assert r.json()["totals"]["tax_amount"] == 325.0


# ── 4. Allowance return takes a date ─────────────────────────────────────


def _allowance(client, db_session, customer_id, contract_date="2026-07-15"):
    code = CostCode(code="09 70 00", name="Finishes", cost_type="material")
    db_session.add(code)
    db_session.commit()
    job, _saved, sov = _setup(
        client,
        customer_id,
        [
            {"description": "Base", "kind": "work", "scheduled_value": 75000},
            {
                "description": "Finishes allowance",
                "kind": "allowance",
                "scheduled_value": 25000,
                "cost_code_id": code.id,
            },
        ],
        100000,
        contract_date=contract_date,
    )
    db_session.add(
        JobBudget(
            job_id=job["id"],
            cost_code_id=code.id,
            cost_type="material",
            amount=Decimal("25000"),
            revenue_amount=Decimal("25000"),
            source="estimate",
            locked=True,
        )
    )
    db_session.commit()
    return job, next(s for s in sov if s["kind"] == "allowance")["id"]


def _return(client, allowance_id, **body):
    return client.post(
        "/api/change-orders/allowance-return",
        json={"allowance_sov_line_id": allowance_id, **body},
    )


def test_an_allowance_return_takes_a_date_and_approves_effective_that_day(
    client, db_session, seed_accounts, seed_customer
):
    """L weakness: the return was dated today (11/08), so approving it
    effective 10/28 was refused until a PUT moved the CO's date."""
    job, allowance_id = _allowance(client, db_session, seed_customer.id)
    r = _return(client, allowance_id, date="2026-10-28")
    assert r.status_code == 201, r.text
    co = r.json()
    assert co["date"] == "2026-10-28"
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(
        f"/api/change-orders/{co['id']}/approve", json={"approved_on": "2026-10-28"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["approved_on"] == "2026-10-28"


def test_an_allowance_return_without_a_date_is_dated_today(
    client, db_session, seed_accounts, seed_customer
):
    _job_, allowance_id = _allowance(client, db_session, seed_customer.id)
    r = _return(client, allowance_id)
    assert r.status_code == 201, r.text
    assert r.json()["date"] == date.today().isoformat()


def test_an_allowance_return_date_is_not_before_the_contract(
    client, db_session, seed_accounts, seed_customer
):
    job, allowance_id = _allowance(client, db_session, seed_customer.id)
    r = _return(client, allowance_id, date="2026-07-14")
    assert r.status_code == 422, r.text
    assert "2026-07-15" in r.json()["detail"]
    # nothing was made: a good date still works
    assert _return(client, allowance_id, date="2026-07-15").status_code == 201


def test_an_allowance_return_date_in_a_closed_period_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    _job_, allowance_id = _allowance(client, db_session, seed_customer.id)
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code
        == 200
    )
    r = _return(client, allowance_id, date="2026-09-15")
    assert r.status_code == 403, r.text
    assert "closing date" in r.json()["detail"]
    assert _return(client, allowance_id, date="2026-10-05").status_code == 201


def test_the_return_unspent_dialog_asks_for_the_date():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/jobs.js").read_text(
        encoding="utf-8"
    )
    assert "co-return-date" in js
    assert "allowance_sov_line_id: sovLineId, date" in js
