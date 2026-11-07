"""Skeptic round 1, workstream R4: Buyout agrees with Commitment Status and
places an allowance as its changes are; use tax says so when no rate is set;
the new routes refuse a misspelled filter; a change order's dates are checked;
the construction reports stay out of Business and Nonprofit.
"""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bills import Bill, BillStatus
from app.models.contacts import Vendor
from app.models.jobs import Job
from app.models.purchase_orders import POStatus, PurchaseOrder
from app.services import job_reports

ROOT = Path(__file__).resolve().parents[1]
needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


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


def _approve(client, co_id, **body):
    assert client.post(f"/api/change-orders/{co_id}/submit").status_code == 200
    return client.post(f"/api/change-orders/{co_id}/approve", json=body)


# ── 1. Buyout and Commitment Status agree on billed ─────────────────────────


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


# ── 2. Buyout places an allowance as its changes are ────────────────────────


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


# ── 3. Use tax says so when no rate is set ──────────────────────────────────


def _construction_company(client, **settings):
    r = client.put("/api/settings", json={"company_type": "construction", **settings})
    assert r.status_code == 200, r.text
    assert client.post("/api/construction/setup").status_code == 200


def _contractor_pays_job(client, customer_id):
    job = _job(client, customer_id, name="Clinic")
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"sales_tax_treatment": "contractor_pays"},
    )
    assert r.status_code == 200, r.text
    return job, r.json()


def _material_bill(client, job_id, number="MS-1", rate=1000):
    vendor = client.post(
        "/api/vendors?force=true", json={"name": f"Supply {number}"}
    ).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": number,
            "date": "2026-07-04",
            "job_id": job_id,
            "lines": [
                {
                    "description": "Studs",
                    "rate": rate,
                    "cost_type": "material",
                    "job_id": job_id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_saving_contractor_pays_with_no_use_tax_rate_warns(
    client, seed_accounts, seed_customer
):
    _construction_company(client, default_tax_rate="0")
    job, saved = _contractor_pays_job(client, seed_customer.id)
    assert len(saved["warnings"]) == 1
    assert "Use tax rate" in saved["warnings"][0]
    # a charge-owner job has nothing to warn about
    r = client.put(
        f"/api/jobs/{job['id']}/contract", json={"sales_tax_treatment": "charge_owner"}
    )
    assert r.json()["warnings"] == []
    # with a rate set, the contractor-pays save is quiet
    client.put("/api/settings", json={"use_tax_rate": "6.5"})
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"sales_tax_treatment": "contractor_pays"},
    )
    assert r.json()["warnings"] == []


def test_a_bill_that_accrues_use_tax_at_zero_percent_says_so(
    client, seed_accounts, seed_customer
):
    _construction_company(client, default_tax_rate="0")
    job, _ = _contractor_pays_job(client, seed_customer.id)
    bill = _material_bill(client, job["id"])
    assert len(bill["warnings"]) == 1
    assert "Use tax rate" in bill["warnings"][0]
    client.put("/api/settings", json={"use_tax_rate": "6.5"})
    assert _material_bill(client, job["id"], number="MS-2")["warnings"] == []


def test_the_sales_tax_report_shows_the_rate_each_accrual_was_booked_at(
    client, seed_accounts, seed_customer
):
    _construction_company(client, default_tax_rate="6.5")
    job, _ = _contractor_pays_job(client, seed_customer.id)
    _material_bill(client, job["id"], number="MS-1")
    client.put("/api/settings", json={"use_tax_rate": "7"})
    _material_bill(client, job["id"], number="MS-2")
    q = "start_date=2026-01-01&end_date=2026-12-31"
    u = client.get(f"/api/reports/sales-tax?{q}").json()["use_tax"]
    by_bill = {i["bill_number"]: i for i in u["items"]}
    assert by_bill["MS-1"]["rate"] == 0.065 and by_bill["MS-1"]["amount"] == 65.0
    assert by_bill["MS-2"]["rate"] == 0.07 and by_bill["MS-2"]["amount"] == 70.0
    assert by_bill["MS-1"]["description"] == "Use tax - Studs"
    assert u["rate"] == 0.07  # the setting today


# ── 4. A filter the route does not read is refused ──────────────────────────

_STRICT_GETS = (
    "/api/field-tickets?jobid=1",
    "/api/estimates?statuss=lost",
    "/api/reports/retainage?asof=2026-09-30",
    "/api/reports/retainage/pdf?asof=2026-09-30",
    "/api/reports/retainage/csv?asof=2026-09-30",
    "/api/reports/compliance?window_days=abc",
    "/api/reports/compliance/csv?window_days=30",
    "/api/reports/field-ticket-log?jobid=1",
    "/api/reports/field-ticket-log/csv?jobid=1",
)


def test_a_misspelled_filter_is_a_422_naming_the_accepted_ones(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    for url in _STRICT_GETS:
        r = client.get(url)
        assert r.status_code == 422, (url, r.status_code)
        detail = r.json()["detail"]
        assert "is not a filter here. This request accepts:" in detail, detail
    detail = client.get("/api/reports/retainage?asof=2026-09-30").json()["detail"]
    assert detail.startswith("asof is not a filter here.")
    assert "as_of" in detail and "job_id" in detail and "status" in detail


def test_wip_post_and_void_refuse_an_unknown_parameter(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    for verb in ("post", "void"):
        r = client.post(f"/api/reports/wip-schedule/{verb}?as_of=2026-07-31&job_id=9")
        assert r.status_code == 422, (verb, r.text)
        assert "job_id is not a filter here" in r.json()["detail"]
        assert "as_of" in r.json()["detail"]


def test_the_declared_filters_still_work(client, seed_accounts, seed_customer):
    _set_construction(client)
    for url in (
        "/api/field-tickets?job_id=1&kind=tm&status=draft",
        "/api/estimates?status=pending&customer_id=1&skip=0&limit=10",
        "/api/reports/retainage?as_of=2026-09-30&job_id=1&customer_id=1&status=in_progress",
        "/api/reports/compliance?as_of=2026-09-30",
        "/api/reports/field-ticket-log?as_of=2026-09-30&include_closed=true",
    ):
        r = client.get(url)
        assert r.status_code == 200, (url, r.text)


def test_retainage_refuses_a_job_status_that_does_not_exist(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    r = client.get("/api/reports/retainage?status=bogus")
    assert r.status_code == 422
    assert "pending, awarded, in_progress, closed, not_awarded" in r.json()["detail"]


def test_the_estimate_list_finds_lost_bids_by_the_uis_word(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 100}],
    )
    r = client.post(f"/api/estimates/{est['id']}/lost", json={"reason": "price"})
    assert r.status_code == 200, r.text
    lost = client.get("/api/estimates?status=lost").json()
    assert [e["id"] for e in lost] == [est["id"]]
    assert [e["id"] for e in client.get("/api/estimates?status=rejected").json()] == [
        est["id"]
    ]
    assert client.get("/api/estimates?status=pending").json() == []
    assert client.get("/api/estimates?status=bogus").status_code == 422


# ── 5. A change order's effective date is checked ───────────────────────────


def _co_job(client, customer_id, contract_date="2026-06-15"):
    job = _job(client, customer_id)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": 100000,
            "contract_date": contract_date,
        },
    )
    assert r.status_code == 200, r.text
    return job


def _draft_co(client, job_id, when):
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job_id,
            "kind": "owner",
            "funding": "contract",
            "date": when,
            "lines": [
                {
                    "description": "Extra",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 1000,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_approved_on_before_the_change_orders_own_date_is_refused(
    client, seed_accounts, seed_customer
):
    job = _co_job(client, seed_customer.id)
    co = _draft_co(client, job["id"], "2026-08-10")
    r = _approve(client, co["id"], approved_on="2026-08-01")
    assert r.status_code == 422
    assert "dated 2026-08-10" in r.json()["detail"]
    assert client.get(f"/api/change-orders/{co['id']}").json()["status"] == "pending"
    # its own date, and a later one, are fine
    assert (
        client.post(
            f"/api/change-orders/{co['id']}/approve", json={"approved_on": "2026-08-10"}
        ).status_code
        == 200
    )


def test_a_change_order_dated_before_the_contract_is_refused(
    client, seed_accounts, seed_customer
):
    job = _co_job(client, seed_customer.id, contract_date="2026-06-15")
    co = _draft_co(client, job["id"], "2026-01-05")
    # no date given: it would take effect on its own date, before the contract
    r = _approve(client, co["id"])
    assert r.status_code == 422
    assert "before the contract is dated (2026-06-15)" in r.json()["detail"]
    # a day before the contract is refused too; the contract's own day is fine
    r = client.post(
        f"/api/change-orders/{co['id']}/approve", json={"approved_on": "2026-06-14"}
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/change-orders/{co['id']}/approve", json={"approved_on": "2026-06-15"}
    )
    assert r.status_code == 200, r.text


def test_approving_with_no_date_a_month_back_warns(
    client, seed_accounts, seed_customer
):
    job = _co_job(client, seed_customer.id, contract_date="2020-01-01")
    old = date.today().replace(year=date.today().year - 1).isoformat()
    co = _draft_co(client, job["id"], old)
    r = _approve(client, co["id"])
    assert r.status_code == 200, r.text
    assert len(r.json()["warnings"]) == 1
    assert "days ago" in r.json()["warnings"][0] and old in r.json()["warnings"][0]

    # a date the person chose is not second-guessed, nor is a recent one
    co2 = _draft_co(client, job["id"], old)
    r = _approve(client, co2["id"], approved_on=old)
    assert r.json()["warnings"] == []
    co3 = _draft_co(client, job["id"], date.today().isoformat())
    assert _approve(client, co3["id"]).json()["warnings"] == []


# ── 6. The construction reports stay out of other modes ─────────────────────

_CONSTRUCTION_REPORTS = (
    "wip-schedule",
    "retainage",
    "compliance",
    "field-ticket-log",
)


@pytest.mark.parametrize("mode", ["business", "nonprofit"])
def test_the_construction_report_apis_answer_409_outside_construction(
    client, seed_accounts, seed_customer, mode
):
    assert client.put("/api/settings", json={"company_type": mode}).status_code == 200
    for name in _CONSTRUCTION_REPORTS:
        for suffix in ("", "/csv", "/pdf", "/print"):
            r = client.get(f"/api/reports/{name}{suffix}")
            assert r.status_code == 409, (name, suffix, r.status_code)
            assert "construction mode" in r.json()["detail"]
    _set_construction(client)
    for name in _CONSTRUCTION_REPORTS:
        assert client.get(f"/api/reports/{name}").status_code == 200, name


@needs_node
def test_a_construction_report_by_address_opens_the_report_center_elsewhere(tmp_path):
    probe = tmp_path / "deeplink.cjs"
    probe.write_text(
        """
const fs=require('fs'),vm=require('vm');
const src=fs.readFileSync(process.argv[2],'utf8');
async function run(isCon, name){
  const log={opened:[],toasts:[],center:0};
  const ctx={console,URLSearchParams,JSON,Object,
    T:s=>s, Terms:{text:s=>s,isNonprofit:()=>false,isConstruction:()=>isCon},
    escapeHtml:s=>String(s==null?'':s), document:{}, window:{}, location:{hash:'#/'},
    toast:(m,k)=>log.toasts.push([m,k]), $:()=>({classList:{contains:()=>false}}),
    App:{}};
  vm.createContext(ctx);
  vm.runInContext(src+'\\nthis.ReportsPage=ReportsPage;',ctx);
  const R=ctx.ReportsPage;
  R._addressReportCenter=()=>{log.center++;};
  for (const fn of ['wipSchedule','retainage','subCompliance','fieldTicketLog'])
    R[fn]=async()=>{log.opened.push(fn);};
  await R.openView(name,{});
  return log;
}
(async()=>{
  const out={};
  for (const name of ['wip-schedule','retainage','compliance','field-ticket-log']) {
    out[name]={con:await run(true,name), biz:await run(false,name)};
  }
  console.log(JSON.stringify(out));
})();
""",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["node", str(probe), str(ROOT / "app/static/js/reports.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert set(res) == set(_CONSTRUCTION_REPORTS)
    for name, got in res.items():
        assert len(got["con"]["opened"]) == 1, name
        assert got["con"]["toasts"] == [], name
        assert got["biz"]["opened"] == [], name
        assert got["biz"]["center"] == 1, name
        assert "construction-mode report" in got["biz"]["toasts"][0][0], name
