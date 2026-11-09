"""Estimates, commitments, buyout and job status.

Convert refuses a contract job; Mark lost; Accept awards a pending job and the
first approved pay application starts it; an unknown PO status is a 422;
Buyout reads an allowance at its amount, reconciles to the locked budget with
General conditions / Insurance / Bond rows, carries approved change orders in
a Changes column and plain bills in "Spent, no commitment"; packages order by
their lowest section code; insurance and bond budget as cost; the seeded
sections and a new section's default package.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.bills import Bill, BillStatus
from app.models.contacts import Vendor
from app.models.job_costing import JobBudget
from app.models.jobs import Job
from app.models.purchase_orders import POStatus, PurchaseOrder
from app.services import job_reports
from tests.test_wip_schedule_post import _job as _wip_job


def _set_construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _package(client, name, section_ids=()):
    r = client.post(
        "/api/trade-packages", json={"name": name, "section_ids": list(section_ids)}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-02-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(client, name="Ace Electric", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _sent_po(client, vendor_id, job_id, lines, **extra):
    body = {
        "vendor_id": vendor_id,
        "date": "2026-09-01",
        "job_id": job_id,
        "lines": lines,
    }
    body.update(extra)
    po = client.post("/api/purchase-orders", json=body)
    assert po.status_code == 201, po.text
    up = client.put(f"/api/purchase-orders/{po.json()['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return up.json()


def _buyout(client, job_id):
    r = client.get(f"/api/jobs/{job_id}/buyout")
    assert r.status_code == 200, r.text
    return r.json()


def _contract_job(client, customer_id, name="Contract Job", **extra):
    """A job that bills by pay application: a contract type and a one-line SOV."""
    job = _job(client, customer_id, name=name, **extra)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 10000},
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [{"description": "All", "kind": "work", "scheduled_value": 10000}]
        },
    )
    assert r.status_code == 200, r.text
    return job


LINE = {"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 10000}


# ── 1. Convert on a contract job ─────────────────────────────────────────────


def test_convert_is_refused_on_a_job_that_bills_by_pay_application(
    client, seed_customer
):
    _set_construction(client)
    job = _contract_job(client, seed_customer.id)
    est = _estimate(client, seed_customer.id, [LINE], job_id=job["id"])
    assert est["bills_by_pay_app"] is True
    listed = client.get("/api/estimates").json()
    assert next(e for e in listed if e["id"] == est["id"])["bills_by_pay_app"] is True

    r = client.post(f"/api/estimates/{est['id']}/convert")
    assert r.status_code == 409, r.text
    assert "bills by pay application" in r.json()["detail"]["message"]
    # nothing was made
    assert client.get("/api/invoices").json() == []
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "pending"


def test_convert_still_works_on_a_plain_job(client, seed_accounts, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est = _estimate(client, seed_customer.id, [LINE], job_id=job["id"])
    assert est["bills_by_pay_app"] is False
    r = client.post(f"/api/estimates/{est['id']}/convert")
    assert r.status_code == 200, r.text


def test_a_pay_application_alone_marks_the_job(
    client, seed_accounts, seed_customer, db_session
):
    """A job with no contract type but a pay application bills by pay app."""
    from app.models.pay_applications import PayApplication

    _set_construction(client)
    job = _contract_job(client, seed_customer.id)
    est = _estimate(client, seed_customer.id, [LINE], job_id=job["id"])
    assert (
        client.post(
            "/api/pay-applications", json={"job_id": job["id"], "date": "2026-06-01"}
        ).status_code
        == 201
    )
    assert db_session.query(PayApplication).count() == 1
    assert client.get(f"/api/estimates/{est['id']}").json()["bills_by_pay_app"] is True


# ── 2. Mark lost ─────────────────────────────────────────────────────────────


def test_mark_lost_saves_the_reason_in_the_notes(client, seed_customer):
    _set_construction(client)
    est = _estimate(client, seed_customer.id, [LINE], notes="Bid for Lakeside")
    r = client.post(
        f"/api/estimates/{est['id']}/lost", json={"reason": "Went with a cheaper GC"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected"
    assert body["notes"].startswith("Bid for Lakeside\n")
    assert "Went with a cheaper GC" in body["notes"]
    # the list filters by status
    lost = client.get("/api/estimates?status=rejected").json()
    assert [e["id"] for e in lost] == [est["id"]]
    # already lost
    assert client.post(f"/api/estimates/{est['id']}/lost").status_code == 409


def test_mark_lost_without_a_reason_and_not_on_an_accepted_bid(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    won = _estimate(client, seed_customer.id, [LINE], job_id=job["id"])
    assert client.post(f"/api/estimates/{won['id']}/accept", json={}).status_code == 200
    r = client.post(f"/api/estimates/{won['id']}/lost", json={})
    assert r.status_code == 409
    assert "won the job" in r.json()["detail"]

    other = _estimate(client, seed_customer.id, [LINE])
    r = client.post(f"/api/estimates/{other['id']}/lost", json={})
    assert r.status_code == 200 and r.json()["notes"] in (None, "")


# ── 3. Issue a commitment; an unknown status is a 422 ────────────────────────


def test_unknown_po_status_is_a_422_naming_the_valid_ones(
    client, seed_accounts, seed_customer
):
    v = _vendor(client)
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-01",
            "lines": [{"description": "x", "quantity": 1, "rate": 100}],
        },
    ).json()
    r = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "issued"})
    assert r.status_code == 422, r.text
    text = str(r.json())
    for word in ("draft", "sent", "partial", "received", "closed"):
        assert word in text
    # the real word issues it
    r = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert r.status_code == 200 and r.json()["status"] == "sent"


def test_po_ui_has_issue_and_a_status_select():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1] / "app/static/js/purchase_orders.js"
    ).read_text()
    assert "PurchaseOrdersPage.issue(" in js
    assert "status: 'sent'" in js
    assert '<select name="status"' in js


# ── 4. Buyout: an allowance is its amount, and the table reconciles ──────────


def _bid(client, customer_id, job_id):
    """Concrete 03 30 00 (package Concrete) 40,000 + a Finishes allowance of
    25,000 in package Flooring, GC 8 / insurance 1.5 / fee 5 / bond 1."""
    c = _section(client, "03 30 00", "Concrete")
    f = _section(client, "09 60 00", "Flooring")
    pc = _package(client, "Concrete", [c["id"]])
    pf = _package(client, "Flooring", [f["id"]])
    est = _estimate(
        client,
        customer_id,
        [
            {
                "description": "Slab",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 40000,
                "cost_code_id": c["id"],
                "trade_package_id": pc["id"],
            },
            {
                "description": "Finishes allowance",
                "quantity": 1,
                "rate": 25000,
                "line_kind": "allowance",
                "cost_code_id": f["id"],
                "trade_package_id": pf["id"],
            },
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
        job_id=job_id,
    )
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 200, r.text
    return est, pc, pf, c, f


def test_buyout_reads_an_allowance_as_its_amount(client, seed_accounts, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    _est, _pc, pf, _c, f = _bid(client, seed_customer.id, job["id"])
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    _sent_po(
        client,
        v["id"],
        job["id"],
        [{"description": "Tile", "quantity": 1, "rate": 11500}],
        trade_package_id=pf["id"],
    )
    rows = {r["package_name"]: r for r in _buyout(client, job["id"])["rows"]}
    assert rows["Flooring"]["bid_cost"] == 25000.0
    assert rows["Flooring"]["bought"] == 11500.0
    assert rows["Flooring"]["variance"] == 13500.0


def test_buyout_bid_total_is_the_locked_budget_cost(client, db_session, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est, *_ = _bid(client, seed_customer.id, job["id"])
    data = _buyout(client, job["id"])
    budget_cost = sum(
        Decimal(str(b.amount))
        for b in db_session.query(JobBudget).filter(JobBudget.job_id == job["id"])
    )
    assert Decimal(str(data["totals"]["bid_cost"])) == budget_cost
    # the markups that are costs show as rows of their own, after the packages
    names = [r["package_name"] for r in data["rows"]]
    assert names[-3:] == ["General conditions", "Insurance", "Bond"]
    by = {r["package_name"]: r for r in data["rows"]}
    t = client.get(f"/api/estimates/{est['id']}").json()["totals"]
    # 8% of the 40,000 markup base (the allowance is not marked up)
    assert by["General conditions"]["bid_cost"] == 3200.0 == float(t["gc"])
    assert by["Insurance"]["bid_cost"] == float(t["insurance"])
    assert by["Bond"]["bid_cost"] == float(t["bond"])
    assert by["General conditions"]["kind"] == "markup"
    # and the estimate says the same number it locks
    assert Decimal(t["budget_cost"]) == budget_cost


# ── 5. Buyout: approved changes and plain bills ──────────────────────────────


def _co_line(desc, cost, code_id=None, package_id=None):
    return {
        "description": desc,
        "cost_type": "subcontract",
        "quantity": 1,
        "unit_cost": cost,
        "cost_code_id": code_id,
        "trade_package_id": package_id,
    }


def test_an_owner_co_that_funds_a_sub_co_is_not_a_loss(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    _est, pc, _pf, c, _f = _bid(client, seed_customer.id, job["id"])
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(
        client,
        v["id"],
        job["id"],
        [{"description": "Slab sub", "quantity": 1, "rate": 38000}],
        commitment_type="subcontract",
        trade_package_id=pc["id"],
    )
    owner = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "contract",
            "lines": [_co_line("Extra slab", 5000, code_id=c["id"])],
        },
    ).json()
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    )
    assert sub.status_code in (200, 201), sub.text
    assert client.post(f"/api/change-orders/{owner['id']}/submit").status_code == 200
    assert client.post(f"/api/change-orders/{owner['id']}/approve").status_code == 200
    sub_id = sub.json()["id"]
    client.post(f"/api/change-orders/{sub_id}/submit")
    client.post(f"/api/change-orders/{sub_id}/approve")

    row = next(
        r for r in _buyout(client, job["id"])["rows"] if r["package_name"] == "Concrete"
    )
    assert row["bid_cost"] == 40000.0
    assert row["changes"] == 5000.0
    assert row["bought"] == 43000.0  # 38,000 + the 5,000 sub CO
    # bid 40,000 + changes 5,000 - bought 43,000: the CO paid for the extra
    assert row["variance"] == 2000.0


def test_buyout_changes_count_only_change_orders_in_effect_by_the_date(
    client, db_session, seed_accounts, seed_customer
):
    from datetime import date

    from app.models.jobs import Job
    from app.services.buyout import job_buyout

    _set_construction(client)
    job = _job(client, seed_customer.id)
    _est, pc, _pf, c, _f = _bid(client, seed_customer.id, job["id"])
    owner = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "contract",
            "date": "2026-08-10",
            "lines": [_co_line("Extra slab", 5000, code_id=c["id"])],
        },
    ).json()
    assert client.post(f"/api/change-orders/{owner['id']}/submit").status_code == 200
    r = client.post(
        f"/api/change-orders/{owner['id']}/approve",
        json={"approved_on": "2026-08-10"},
    )
    assert r.status_code == 200, r.text

    j = db_session.get(Job, job["id"])

    def concrete(as_of):
        rows = job_buyout(db_session, j, as_of=as_of)["rows"]
        return next(r for r in rows if r["package_name"] == "Concrete")

    assert concrete(date(2026, 7, 31))["changes"] == 0.0
    assert concrete(date(2026, 8, 31))["changes"] == 5000.0


def test_plain_bills_count_as_spent_not_zero_bought(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    _est, _pc, _pf, c, _f = _bid(client, seed_customer.id, job["id"])
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-10",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "Slab pour",
                    "amount": 17200,
                    "account_id": seed_accounts["5100"].id,
                    "cost_code_id": c["id"],
                    "job_id": job["id"],
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    data = _buyout(client, job["id"])
    row = next(r for r in data["rows"] if r["package_name"] == "Concrete")
    assert row["bought"] == 0.0
    assert row["spent_no_commitment"] == 17200.0
    assert data["totals"]["spent_no_commitment"] == 17200.0


# ── 6. Packages in bid-book order ────────────────────────────────────────────


def test_packages_sort_by_their_lowest_section_code(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    # alphabetical order is Concrete, Demolition, Electrical; the bid book is
    # 02, 03, 26
    codes = {
        "Concrete": _section(client, "03 30 00", "Cast-in-Place Concrete"),
        "Demolition": _section(client, "02 41 19", "Selective Demolition"),
        "Electrical": _section(client, "26 05 00", "Electrical"),
    }
    lines = []
    for name, code in codes.items():
        pkg = _package(client, name, [code["id"]])
        lines.append(
            {
                "description": name,
                "quantity": 1,
                "rate": 0,
                "unit_cost": 1000,
                "cost_code_id": code["id"],
                "trade_package_id": pkg["id"],
            }
        )
    lines.append({"description": "Loose", "quantity": 1, "rate": 0, "unit_cost": 5})
    est = _estimate(client, seed_customer.id, lines, job_id=job["id"])
    names = [
        p["package_name"]
        for p in client.get(f"/api/estimates/{est['id']}").json()["totals"][
            "by_package"
        ]
    ]
    assert names == ["Demolition", "Concrete", "Electrical", "Unassigned"]

    # the schedule of values and the Buyout follow
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    sov = [
        ln["description"]
        for ln in client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
        if ln["kind"] == "work"
    ]
    # a line with no package is its own SOV line, under its own description
    # (the owner's schedule never prints "Unassigned"; the Buyout keeps it)
    assert sov == ["Demolition", "Concrete", "Electrical", "Loose"]
    rows = [r["package_name"] for r in _buyout(client, job["id"])["rows"]]
    assert rows[:4] == ["Demolition", "Concrete", "Electrical", "Unassigned"]


# ── 7. Insurance and bond budget as cost ─────────────────────────────────────


def test_accept_budgets_insurance_and_bond_as_cost_so_margin_is_the_fee(
    client, db_session, seed_customer
):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est, *_ = _bid(client, seed_customer.id, job["id"])
    t = client.get(f"/api/estimates/{est['id']}").json()["totals"]
    rows = db_session.query(JobBudget).filter(JobBudget.job_id == job["id"]).all()
    cost = sum(Decimal(str(r.amount)) for r in rows)
    revenue = sum(Decimal(str(r.revenue_amount)) for r in rows)
    by_label = {r.cost_code.code: r for r in rows}
    assert Decimal(str(by_label["INS"].amount)) == Decimal(t["insurance"])
    assert Decimal(str(by_label["BOND"].amount)) == Decimal(t["bond"])
    assert Decimal(str(by_label["GC"].amount)) == Decimal(t["gc"])
    assert revenue == Decimal(t["bid_total"])
    # budgeted margin is exactly the fee
    assert revenue - cost == Decimal(t["fee"])


# ── 8. Accept awards the job; the first approved pay application starts it ───


def test_accept_awards_a_pending_job_and_the_first_pay_app_starts_it(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    job = _job(client, seed_customer.id, status="pending")
    assert job["status"] == "pending"
    _bid(client, seed_customer.id, job["id"])
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "awarded"

    est_id = client.get("/api/estimates").json()[0]["id"]
    assert (
        client.post(
            f"/api/jobs/{job['id']}/contract/from-estimate/{est_id}"
        ).status_code
        == 200
    )
    app = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-06-01"}
    ).json()
    lines = [
        {"sov_line_id": ln["sov_line_id"], "this_period": 1000 if i == 0 else 0}
        for i, ln in enumerate(app["lines"])
    ]
    up = client.put(f"/api/pay-applications/{app['id']}", json={"lines": lines})
    assert up.status_code == 200, up.text
    assert client.post(f"/api/pay-applications/{app['id']}/approve").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "in_progress"


def test_accept_leaves_a_job_that_is_already_further_along(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id, status="in_progress")
    _bid(client, seed_customer.id, job["id"])
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "in_progress"


def test_accept_confirm_names_the_budget_and_warns_when_expired():
    """The Accept confirm text, run through node against the real function."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = (
        Path(__file__).resolve().parents[1] / "app/static/js/estimates.js"
    ).read_text()
    start = js.index("acceptConfirmText(est) {")
    end = js.index("async accept(id)")
    body = js[start:end].rstrip().rstrip(",")
    script = (
        "const formatCurrency = v => '$' + Number(v).toFixed(2);"
        "const formatDate = d => d;"
        "const todayISO = () => '2026-11-07';"
        f"const P = {{ {body} }};"
        "console.log(JSON.stringify(["
        "P.acceptConfirmText({totals:{budget_cost:'361960.00', bid_total:'387706.27'}}),"
        "P.acceptConfirmText({expiration_date:'2026-10-01', totals:{budget_cost:'1.00', bid_total:'2.00'}})]));"
    )
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    )
    fresh, expired = json.loads(out.stdout)
    assert "$361960.00 of cost against a $387706.27 bid" in fresh
    assert "expired" not in fresh
    assert "expired on 2026-10-01" in expired


# ── 9. Seeded sections and a new section's package ───────────────────────────


def test_the_standard_catalog_has_the_sections_a_gc_bids(client, db_session):
    _set_construction(client)
    assert client.post("/api/construction/setup").status_code == 200
    from app.models.cost_codes import CostCode
    from app.models.trade_packages import TradePackage

    pkg_of = {}
    for pkg in db_session.query(TradePackage):
        for m in pkg.sections:
            pkg_of[m.cost_code.code] = pkg.name
    assert pkg_of["02 41 19"] == "Demolition"
    assert pkg_of["09 21 16"] == "Drywall and Ceilings"
    assert pkg_of["23 00 00"] == "HVAC"
    names = {c.code: c.name for c in db_session.query(CostCode)}
    assert names["02 41 19"] == "Selective Demolition"
    assert names["09 21 16"] == "Gypsum Board Assemblies"
    assert names["23 00 00"].startswith("Heating, Ventilating")


def test_a_new_section_joins_its_divisions_only_package(client, db_session):
    from app.models.trade_packages import TradePackageSection

    _set_construction(client)
    a = _section(client, "55 10 00", "Widgets")
    pkg = _package(client, "Widget Trade", [a["id"]])
    b = _section(client, "55 20 00", "More widgets")
    mapped = [
        m.package_id
        for m in db_session.query(TradePackageSection).filter_by(cost_code_id=b["id"])
    ]
    assert mapped == [pkg["id"]]


def test_a_new_section_is_left_alone_when_its_division_has_two_packages(
    client, db_session
):
    from app.models.trade_packages import TradePackageSection

    _set_construction(client)
    a = _section(client, "56 10 00", "A")
    b = _section(client, "56 20 00", "B")
    _package(client, "Pkg A", [a["id"]])
    _package(client, "Pkg B", [b["id"]])
    c = _section(client, "56 30 00", "C")
    assert (
        db_session.query(TradePackageSection).filter_by(cost_code_id=c["id"]).count()
        == 0
    )


def test_mark_lost_validates_its_body(client, seed_customer):
    _set_construction(client)
    est = _estimate(client, seed_customer.id, [LINE])
    url = f"/api/estimates/{est['id']}/lost"
    for bad in (
        {"reason": "x" * 501},  # a note, not a document
        {"reason": 7},
        {"reason": ["a"]},
        {"why": "Went with a cheaper GC"},  # a misspelled field is not ignored
        ["Went with a cheaper GC"],
    ):
        assert client.post(url, json=bad).status_code == 422, bad
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "pending"
    assert client.post(url, json={"reason": "x" * 500}).status_code == 200


def test_buyout_leaves_retainage_release_bills_out_of_billed(db_session, seed_accounts):
    from app.models.contacts import Customer

    customer = Customer(name="Owner Co")
    db_session.add(customer)
    db_session.flush()
    job = Job(customer_id=customer.id, name="Releases", status="in_progress")
    vendor = Vendor(name="Sub Co")
    db_session.add_all([job, vendor])
    db_session.flush()
    po = PurchaseOrder(
        po_number="SC-1",
        vendor_id=vendor.id,
        status=POStatus.SENT,
        date=date(2026, 2, 1),
        job_id=job.id,
        subtotal=Decimal("20000"),
        commitment_type="subcontract",
    )
    db_session.add(po)
    db_session.flush()

    def bill(number, subtotal, when, released="0"):
        db_session.add(
            Bill(
                bill_number=number,
                vendor_id=vendor.id,
                status=BillStatus.UNPAID,
                po_id=po.id,
                job_id=job.id,
                date=when,
                subtotal=Decimal(subtotal),
                total=Decimal(subtotal),
                balance_due=Decimal(subtotal),
                retainage_held=Decimal("0"),
                retainage_released=Decimal(released),
            )
        )

    bill("S-1", "9000", date(2026, 3, 1))
    bill("S-2", "9000", date(2026, 4, 1))
    # the release pays retainage already inside the two bills above
    bill("REL-1", "1800", date(2026, 5, 1), released="1800")
    db_session.commit()

    for day in (date(2026, 4, 15), date(2026, 6, 1)):
        buy = job_reports.buyout_report(db_session, as_of=day)["totals"]
        status = job_reports.commitment_status(db_session, as_of=day)["totals"]
        assert buy["billed_to_date"] == status["billed"] == 18000.0
        assert buy["committed_remaining"] == status["remaining"] == 2000.0


def _allowance_job(client, customer_id):
    """Concrete 40,000 (package Concrete), a 25,000 Finishes allowance on code
    09 60 00 (mapped to package Flooring, line has no package) and an accepted
    -6,000 deduct alternate on 09 30 00 (in no package). The SOV is built."""
    c = _section(client, "03 30 00", "Concrete")
    f = _section(client, "09 60 00", "Flooring")
    d = _section(client, "09 30 00", "Tile")
    pc = _package(client, "Concrete", [c["id"]])
    pf = _package(client, "Flooring", [f["id"]])
    job = _job(client, customer_id)
    est = _estimate(
        client,
        customer_id,
        [
            {
                "description": "Slab",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 40000,
                "cost_code_id": c["id"],
                "trade_package_id": pc["id"],
            },
            {
                "description": "Finishes allowance",
                "quantity": 1,
                "rate": 25000,
                "line_kind": "allowance",
                "cost_code_id": f["id"],
            },
            {
                "description": "Deduct tile",
                "quantity": 1,
                "rate": -6000,
                "unit_cost": -6000,
                "line_kind": "alternate",
                "alternate_accepted": True,
                "cost_code_id": d["id"],
            },
        ],
        job_id=job["id"],
    )
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    allowance = next(s for s in r.json()["sov"] if s["kind"] == "allowance")
    return job, allowance["id"], f, pf


def _approve(client, co_id, **body):
    assert client.post(f"/api/change-orders/{co_id}/submit").status_code == 200
    return client.post(f"/api/change-orders/{co_id}/approve", json=body)


def test_buyout_places_an_allowance_by_its_code_and_draws_it_down(
    client, db_session, seed_accounts, seed_customer
):
    _set_construction(client)
    job, allowance_id, f, pf = _allowance_job(client, seed_customer.id)
    v = client.post(
        "/api/vendors",
        json={
            "name": "Tile Co",
            "default_expense_account_id": seed_accounts["5100"].id,
        },
    ).json()
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-01",
            "job_id": job["id"],
            "trade_package_id": pf["id"],
            "lines": [{"description": "Tile", "quantity": 1, "rate": 9000}],
        },
    ).json()
    assert (
        client.put(
            f"/api/purchase-orders/{po['id']}", json={"status": "sent"}
        ).status_code
        == 200
    )
    # the allowance pays for 9,000 of flooring (a draw, not new scope) ...
    co = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "allowance",
            "allowance_sov_line_id": allowance_id,
            "date": "2026-09-10",
            "lines": [
                {
                    "description": "Flooring",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 9000,
                    "cost_code_id": f["id"],
                }
            ],
        },
    ).json()
    assert _approve(client, co["id"], approved_on="2026-09-10").status_code == 200
    # ... and the unspent 16,000 goes back to the owner on 10/15
    ret = client.post(
        "/api/change-orders/allowance-return",
        json={"allowance_sov_line_id": allowance_id},
    )
    assert ret.status_code == 201, ret.text
    # the draft is dated the day it was made; a change order cannot take
    # effect before its own date
    r = client.put(
        f"/api/change-orders/{ret.json()['id']}", json={"date": "2026-10-15"}
    )
    assert r.status_code == 200, r.text
    assert (
        _approve(client, ret.json()["id"], approved_on="2026-10-15").status_code == 200
    )

    j = db_session.get(Job, job["id"])

    def rows(as_of):
        from app.services.buyout import job_buyout

        return {r["package_name"]: r for r in job_buyout(db_session, j, as_of)["rows"]}

    after = rows(date(2026, 11, 7))
    flooring = after["Flooring"]
    assert flooring["bid_cost"] == 25000.0  # the allowance, placed by its code
    assert flooring["bought"] == 9000.0
    # bid + changes - bought reconciles: the draw and the return empty it
    assert flooring["variance"] == 0.0
    # the deduct stays by itself (its code is in no package)
    assert after["Unassigned"]["bid_cost"] == -6000.0
    assert after["Unassigned"]["changes"] == 0.0

    # as of 9/30 the return is not in effect yet: 16,000 of the allowance
    # is unspent, shown on the allowance's own row and nowhere else
    mid = rows(date(2026, 9, 30))
    assert mid["Flooring"]["bid_cost"] == 25000.0
    assert mid["Flooring"]["variance"] == 16000.0
    assert mid["Unassigned"]["bid_cost"] == -6000.0


@pytest.mark.parametrize("mode", ("business", "nonprofit"))
def test_the_jobs_buyout_is_construction_modes(client, db_session, seed_accounts, mode):
    """GET /api/jobs/{id}/buyout answered in Business and
    Nonprofit while the Report Center's Buyout Report is gated."""
    job = _wip_job(db_session)
    db_session.commit()
    assert client.put("/api/settings", json={"company_type": mode}).status_code == 200
    r = client.get(f"/api/jobs/{job.id}/buyout")
    assert r.status_code == 409
    assert "part of construction mode" in r.json()["detail"]
    # the cost tree (job costing) stays open
    assert client.get(f"/api/jobs/{job.id}/cost-tree").status_code == 200
    client.put("/api/settings", json={"company_type": "construction"})
    assert client.get(f"/api/jobs/{job.id}/buyout").status_code == 200


def test_marking_an_estimate_lost_refuses_a_field_it_does_not_take(
    client, seed_customer
):
    """A body field other than the reason is a 422 naming it; the estimate
    stays open."""
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "lines": [{"description": "Slab", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    est = r.json()["id"]
    r = client.post(f"/api/estimates/{est}/lost", json={"why": "x"})
    assert r.status_code == 422, r.text
    assert "'why' is not a field this accepts" in r.text
    assert client.get(f"/api/estimates/{est}").json()["status"] == "pending"
    assert (
        client.post(f"/api/estimates/{est}/lost", json={"reason": "x"}).status_code
        == 200
    )
