"""The schedule of values' sections, change-order lines and pay-app drafts
(construction overhaul, skeptic round 1, R2).

A work line carries the section its estimate lines carry, so revenue lands
where the budget and cost are; a line with no package reads as its own
description, never "Unassigned". A change order's SOV line is taxed as the
section it extends (not as "any line on the schedule"), with its item number
and revenue account. Voiding a change order leaves no line on an open draft,
no approve saves a line whose SOV line is gone, refresh-lines refuses an
approved application, and the PDF's release note reads as a whole sentence.
"""

from decimal import Decimal

from app.models.job_costing import JobBudget
from app.models.jobs import SovLine
from app.models.pay_applications import PayApplication, PayApplicationLine
from tests.test_co_effective_date_allowance import _app, _approve, _setup
from tests.test_sov_mapping import _bill_all, _construction, _job

D = Decimal


def _codes(client):
    return {c["code"]: c for c in client.get("/api/cost-codes").json()}


def _packages(client):
    return {p["name"]: p for p in client.get("/api/trade-packages").json()}


def _line(desc, cost, code, pkg=None, **extra):
    return {
        "description": desc,
        "quantity": 1,
        "rate": cost,
        "unit_cost": cost,
        "cost_code_id": code["id"],
        "trade_package_id": pkg["id"] if pkg else None,
        **extra,
    }


def _build(client, customer_id, lines, **est):
    body = {"customer_id": customer_id, "date": "2026-06-01", "lines": lines}
    body.update(est)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    est_id = r.json()["id"]
    job = _job(client, customer_id)
    r = client.post(f"/api/estimates/{est_id}/accept", json={"job_id": job["id"]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est_id}")
    assert r.status_code == 200, r.text
    return job, r.json()["sov"], client.get(f"/api/estimates/{est_id}").json()


def _persona_bid(client, customer_id):
    """The skeptic's Lakeside Clinic TI bid on the seeded packages, whose
    lowest sections (02 41 00, 03 10 00, 09 20 00) are not the lines' own."""
    _construction(client)
    c, p = _codes(client), _packages(client)
    lines = [
        _line(
            "Selective demolition",
            18000,
            c["02 41 19"],
            p["Demolition"],
            is_taxable=False,
        ),
        _line(
            "Slab and footings",
            42000,
            c["03 30 00"],
            p["Concrete"],
            cost_type="material",
        ),
        _line(
            "Framing and drywall",
            96000,
            c["09 21 16"],
            p["Drywall and Ceilings"],
        ),
        _line("Electrical", 88000, c["26 05 00"], p["Electrical"], is_taxable=False),
        _line("HVAC", 74000, c["23 00 00"], p["HVAC"], is_taxable=False),
        {
            "description": "Finishes allowance",
            "quantity": 1,
            "rate": 25000,
            "line_kind": "allowance",
            "cost_code_id": c["09 60 00"]["id"],
        },
        _line(
            "Alt 1: owner-furnished tile (deduct)",
            -6000,
            c["09 30 00"],
            line_kind="alternate",
            alternate_accepted=True,
        ),
        {
            "description": "Rock excavation",
            "quantity": 1,
            "rate": 85,
            "unit": "CY",
            "line_kind": "unit_price",
        },
    ]
    return _build(
        client,
        customer_id,
        lines,
        tax_rate="0.065",
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )


# ── 1. A work line's section is its estimate lines' section ──────────────


def test_every_sov_work_line_is_on_a_budgeted_section(
    client, db_session, seed_accounts, seed_customer
):
    job, sov, est = _persona_bid(client, seed_customer.id)
    assert D(est["totals"]["bid_total"]) == D("387706.27")
    assert sum(D(str(s["scheduled_value"])) for s in sov) == D("387706.27")

    budget_codes = {
        b.cost_code_id
        for b in db_session.query(JobBudget).filter(JobBudget.job_id == job["id"])
    }
    work = [s for s in sov if s["kind"] == "work"]
    assert work and all(s["cost_code_id"] in budget_codes for s in work)
    c = _codes(client)
    by = {s["description"]: s for s in work}
    # the package's lowest section (02 41 00) is not where demolition's
    # budget and cost are; the line carries 02 41 19 now
    assert by["Demolition"]["cost_code_id"] == c["02 41 19"]["id"]
    assert by["Concrete"]["cost_code_id"] == c["03 30 00"]["id"]
    assert by["Drywall and Ceilings"]["cost_code_id"] == c["09 21 16"]["id"]
    # the deduct alternate, with no package, reads as its own text
    alt = by["Alt 1: owner-furnished tile (deduct)"]
    assert float(alt["scheduled_value"]) == -6000.0
    assert alt["cost_code_id"] == c["09 30 00"]["id"]
    assert not any("Unassigned" in (s["description"] or "") for s in sov)

    # billed at 100%, the application charges exactly the bid's tax
    approved = _bill_all(client, job["id"], sov)
    assert D(str(approved["totals"]["tax_amount"])) == D(est["tax_amount"])


def test_a_package_on_two_sections_is_a_line_per_section(
    client, seed_accounts, seed_customer
):
    _construction(client)
    c, p = _codes(client), _packages(client)
    concrete = p["Concrete"]
    _job_, sov, est = _build(
        client,
        seed_customer.id,
        [
            _line("Slab", 30000, c["03 30 00"], concrete),
            _line("Rebar", 5000, c["03 30 00"], concrete, is_taxable=False),
            _line("Forms", 7000, c["03 10 00"], concrete),
            # no code: the package's lowest section, as before
            {**_line("Misc", 1000, c["03 10 00"], concrete), "cost_code_id": None},
        ],
        tax_rate="0.065",
    )
    work = [
        (s["description"], s["cost_code_id"], float(s["scheduled_value"])) for s in sov
    ]
    forming = c["03 10 00"]
    cast = c["03 30 00"]
    assert work == [
        (f"Concrete — {forming['code']} {forming['name']}", forming["id"], 8000.0),
        (f"Concrete — {cast['code']} {cast['name']}", cast["id"], 30000.0),
        (f"Concrete — {cast['code']} {cast['name']} (not taxable)", cast["id"], 5000.0),
    ]
    assert sum(w[2] for w in work) == float(est["totals"]["bid_total"])


# ── 2/3. A change order's line: its section's tax, item and account ───────


def _mixed_job(client, customer_id):
    """Electrical (exempt) and Concrete (taxable); the Electrical line credits
    its own income account."""
    _construction(client)
    c, p = _codes(client), _packages(client)
    job, sov, _est = _build(
        client,
        customer_id,
        [
            _line("Slab", 40000, c["03 30 00"], p["Concrete"]),
            _line("Wiring", 60000, c["26 05 00"], p["Electrical"], is_taxable=False),
        ],
        tax_rate="0.065",
    )
    accounts = client.get("/api/accounts").json()
    income = next(
        a
        for a in accounts
        if a["account_type"] == "income" and a["account_number"] != "4050"
    )
    lines = [
        {
            "id": s["id"],
            "description": s["description"],
            "kind": s["kind"],
            "scheduled_value": s["scheduled_value"],
            "cost_code_id": s["cost_code_id"],
            "trade_package_id": s["trade_package_id"],
            **(
                {"revenue_account_id": income["id"]}
                if s["description"] == "Electrical"
                else {}
            ),
        }
        for s in sov
    ]
    r = client.put(f"/api/jobs/{job['id']}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text
    return job, c, p, income


def _co_line(client, job_id, code=None, pkg=None, cost=1000):
    line = {"description": "Extra", "cost_type": "material", "quantity": 1}
    line["unit_cost"] = cost
    if code is not None:
        line["cost_code_id"] = code["id"]
    if pkg is not None:
        line["trade_package_id"] = pkg["id"]
    r = client.post(
        "/api/change-orders",
        json={"job_id": job_id, "kind": "owner", "lines": [line]},
    )
    assert r.status_code == 201, r.text
    co = r.json()
    assert _approve(client, co["id"]).status_code == 200
    sov = client.get(f"/api/jobs/{job_id}/contract").json()["sov"]
    return next(
        s
        for s in sov
        if s["kind"] == "change_order" and s["description"].startswith(co["number"])
    )


def test_a_change_order_on_exempt_work_is_not_taxed(
    client, seed_accounts, seed_customer
):
    job, c, p, income = _mixed_job(client, seed_customer.id)
    elec = _co_line(client, job["id"], code=c["26 05 00"])
    assert elec["is_taxable"] is False
    # it extends the section's line: its revenue account, the next item
    assert elec["revenue_account_id"] == income["id"]
    assert elec["item_no"] == "03"
    conc = _co_line(client, job["id"], code=c["03 30 00"])
    assert conc["is_taxable"] is True
    assert conc["revenue_account_name"] == "Contract Revenue"
    assert conc["item_no"] == "04"
    # no section on the schedule: the change order's package decides
    by_pkg = _co_line(client, job["id"], code=c["26 20 00"], pkg=p["Electrical"])
    assert by_pkg["is_taxable"] is False
    # neither: a mixed schedule is not taxed, and the line credits 4050
    loose = _co_line(client, job["id"])
    assert loose["is_taxable"] is False
    assert loose["revenue_account_name"] == "Contract Revenue"
    assert loose["item_no"] == "06"


def test_a_change_order_on_an_all_taxable_schedule_is_taxed(
    client, seed_accounts, seed_customer
):
    job, _sov = _setup(
        client,
        seed_customer.id,
        [{"description": "Shell", "kind": "work", "scheduled_value": 100000}],
        100000,
    )
    line = _co_line(client, job["id"])
    assert line["is_taxable"] is True
    assert line["item_no"] == "02"
    assert line["revenue_account_name"] == "Contract Revenue"


# ── 4. Voiding a change order leaves nothing on an open draft ─────────────


def _base_job(client, customer_id):
    return _setup(
        client,
        customer_id,
        [{"description": "Base", "kind": "work", "scheduled_value": 100000}],
        100000,
    )


def _signage_co(client, job_id, cost=550):
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job_id,
            "kind": "owner",
            "date": "2026-08-10",
            "title": "Signage upgrade",
            "markup_pct": 0,
            "lines": [
                {
                    "description": "Signage",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": cost,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    co = r.json()
    assert _approve(client, co["id"]).status_code == 200
    return co


def _dangling(db_session):
    db_session.expire_all()
    live = {s.id for s in db_session.query(SovLine)}
    return [
        ln.id
        for ln in db_session.query(PayApplicationLine)
        if ln.sov_line_id is not None and ln.sov_line_id not in live
    ]


def test_voiding_an_unbilled_change_order_takes_its_line_off_the_draft(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _base_job(client, seed_customer.id)
    co = _signage_co(client, job["id"])
    draft = _app(
        client,
        job["id"],
        "2026-08-31",
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
    )
    got = client.get(f"/api/pay-applications/{draft['id']}").json()
    assert len(got["lines"]) == 2  # the change order's line came on

    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 200, r.text
    assert _dangling(db_session) == []
    r = client.post(f"/api/pay-applications/{draft['id']}/refresh-lines")
    assert r.status_code == 200, r.text
    assert [ln["description"] for ln in r.json()["lines"]] == ["Base"]
    r = client.post(f"/api/pay-applications/{draft['id']}/approve")
    assert r.status_code == 200, r.text
    totals = r.json()["totals"]
    assert totals["scheduled_total"] == 100000.0
    assert totals["balance_to_finish_total"] == 90000.0


def test_voiding_a_change_order_a_draft_bills_names_the_draft(
    client, seed_accounts, seed_customer
):
    job, sov = _base_job(client, seed_customer.id)
    # a deduct: its amount on the draft is negative and still counts
    co = _signage_co(client, job["id"], cost=-550)
    co_line = next(
        s
        for s in client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
        if s["kind"] == "change_order"
    )
    draft = _app(
        client,
        job["id"],
        "2026-08-31",
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0},
            {"sov_line_id": co_line["id"], "this_period": -550, "stored": 0},
        ],
    )
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "co_billed"
    assert draft["number"] in detail["message"]
    assert "draft" in detail["message"]
    # deleting the draft lets the void through
    assert client.delete(f"/api/pay-applications/{draft['id']}").status_code == 200
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200


def _drop_from_schedule(client, job_id, keep_ids):
    sov = client.get(f"/api/jobs/{job_id}/contract").json()["sov"]
    lines = [
        {
            "id": s["id"],
            "description": s["description"],
            "kind": s["kind"],
            "scheduled_value": s["scheduled_value"],
        }
        for s in sov
        if s["id"] in keep_ids
    ]
    r = client.put(f"/api/jobs/{job_id}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text


def _two_lines_one_billed(client, customer_id):
    job, sov = _setup(
        client,
        customer_id,
        [
            {"description": "Base", "kind": "work", "scheduled_value": 90000},
            {"description": "Extra", "kind": "work", "scheduled_value": 10000},
        ],
        100000,
    )
    first = _app(
        client,
        job["id"],
        "2026-07-31",
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
    )
    assert (
        client.post(f"/api/pay-applications/{first['id']}/approve").status_code == 200
    )
    return job, sov


def test_refresh_drops_a_draft_line_whose_sov_line_is_gone(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_lines_one_billed(client, seed_customer.id)
    draft = _app(
        client,
        job["id"],
        "2026-08-31",
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 5000, "stored": 0}],
    )
    _drop_from_schedule(client, job["id"], {sov[0]["id"]})
    r = client.post(f"/api/pay-applications/{draft['id']}/refresh-lines")
    assert r.status_code == 200, r.text
    assert [ln["description"] for ln in r.json()["lines"]] == ["Base"]
    db_session.expire_all()
    assert len(db_session.get(PayApplication, draft["id"]).lines) == 1


def test_no_approve_saves_a_line_whose_sov_line_is_gone(
    client, seed_accounts, seed_customer
):
    job, sov = _two_lines_one_billed(client, seed_customer.id)
    draft = _app(
        client,
        job["id"],
        "2026-08-31",
        lines=[{"sov_line_id": sov[1]["id"], "this_period": 4000, "stored": 0}],
    )
    _drop_from_schedule(client, job["id"], {sov[0]["id"]})
    r = client.post(f"/api/pay-applications/{draft['id']}/approve")
    assert r.status_code == 409, r.text
    assert "Extra is no longer on the job's schedule of values" in r.json()["detail"]
    assert draft["number"] in r.json()["detail"]
    got = client.get(f"/api/pay-applications/{draft['id']}").json()
    assert got["status"] == "draft"


# ── 7. refresh-lines refuses an approved application ──────────────────────


def test_refresh_lines_on_an_approved_application_is_refused(
    client, seed_accounts, seed_customer
):
    job, sov = _two_lines_one_billed(client, seed_customer.id)
    approved = client.get(f"/api/pay-applications?job_id={job['id']}").json()[0]
    r = client.post(f"/api/pay-applications/{approved['id']}/refresh-lines")
    assert r.status_code == 409, r.text
    assert "approved" in r.json()["detail"]
    assert "only a draft" in r.json()["detail"]


# ── 6. The PDF's release note is a whole sentence ─────────────────────────


def test_the_release_note_states_the_column_total_less_releases(
    client, db_session, seed_accounts, seed_customer
):
    from app.routes.pay_applications import _out
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings

    job, sov = _base_job(client, seed_customer.id)
    first = _app(
        client,
        job["id"],
        "2026-07-31",
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 100000, "stored": 0}],
    )
    assert (
        client.post(f"/api/pay-applications/{first['id']}/approve").status_code == 200
    )
    release = _app(client, job["id"], "2026-08-31", release_retainage=4000)
    assert (
        client.post(f"/api/pay-applications/{release['id']}/approve").status_code == 200
    )

    db_session.expire_all()
    pa = db_session.get(PayApplication, release["id"])
    html = render_pay_application_html(
        pa, _out(db_session, pa), pa.job, get_all_settings(db_session)
    )
    assert (
        "its total of $10,000.00, less retainage released to date of $4,000.00, "
        "equals retainage to date of $6,000.00." in html
    )
