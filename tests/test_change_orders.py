"""Change orders (#554): the three kinds (potential / owner / sub), material
and labor priced separately with markup (and none when an allowance pays),
the lifecycle and its 409s, and the effects on the contract, the schedule of
values, the budget and a commitment — every one reversed on void.

Progress billing against the revised SOV and the change-order log report are
later issues (#555, #556); this one leaves those seams.
"""

import importlib.util
import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.job_costing import JobBudget
from app.models.settings import Settings
from app.services.change_orders import co_totals, effective_markup, line_cost, line_sell
from app.services.job_costing import job_cost_tree
from app.services.pdf_service import render_change_order_html
from app.services.settings_service import get_all_settings
from tests.test_wip_schedule_post import _bill_owner, _budget, _cost
from tests.test_wip_schedule_post import _job as _wip_job

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


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _co(client, job_id, lines, kind="owner", funding="contract", status=201, **extra):
    body = {"job_id": job_id, "kind": kind, "funding": funding, "lines": lines}
    body.update(extra)
    r = client.post("/api/change-orders", json=body)
    assert r.status_code == status, r.text
    return r.json()


LABOR = {
    "description": "Crew",
    "cost_type": "labor",
    "quantity": 10,
    "unit_cost": 50,
    "markup_pct": 15,
}
MATERIAL = {
    "description": "Steel",
    "cost_type": "material",
    "quantity": 1,
    "unit_cost": 1000,
    "markup_pct": 10,
}


def _submit_approve(client, co_id):
    assert client.post(f"/api/change-orders/{co_id}/submit").status_code == 200
    r = client.post(f"/api/change-orders/{co_id}/approve")
    return r


# ── Math (pure, exact Decimals) ────────────────────────────────────────────


def test_line_math_per_cost_type():
    # labor 10h × 50 at 15% → cost 500, sell 575.00
    cost = line_cost(10, 50)
    assert cost == Decimal("500.00")
    assert line_sell(cost, Decimal("15"), "owner") == Decimal("575.00")
    # material 1,000 at 10% → 1,100.00
    mcost = line_cost(1, 1000)
    assert line_sell(mcost, Decimal("10"), "owner") == Decimal("1100.00")


def test_allowance_forces_zero_markup():
    assert effective_markup(Decimal("15"), None, None, "allowance") == Decimal("0")
    assert effective_markup(Decimal("15"), None, None, "contract") == Decimal("15")
    # sell = cost when there is no markup
    assert line_sell(Decimal("500.00"), Decimal("0"), "owner") == Decimal("500.00")


def test_sub_co_carries_no_markup():
    # a commitment moves by cost
    assert line_sell(Decimal("500.00"), Decimal("15"), "sub") == Decimal("500.00")


def test_markup_fallback_order():
    # line wins, then CO, then job, then 0
    assert effective_markup(
        Decimal("5"), Decimal("10"), Decimal("20"), "contract"
    ) == Decimal("5")
    assert effective_markup(None, Decimal("10"), Decimal("20"), "contract") == Decimal(
        "10"
    )
    assert effective_markup(None, None, Decimal("20"), "contract") == Decimal("20")
    assert effective_markup(None, None, None, "contract") == Decimal("0")


def test_co_totals_per_cost_type(client, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR, MATERIAL])
    assert co["by_cost_type"]["labor"]["sell"] == 575.0
    assert co["by_cost_type"]["material"]["sell"] == 1100.0
    assert co["cost_total"] == 1500.0
    assert co["sell_total"] == 1675.0
    assert co["markup_total"] == 175.0


# ── Numbering per kind ──────────────────────────────────────────────────────


def test_numbering_per_kind(client, seed_customer):
    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR], kind="pco")
    owner = _co(client, job["id"], [LABOR], kind="owner")
    assert pco["number"] == "J1-PCO-001"
    assert owner["number"] == "J1-CO-001"
    owner2 = _co(client, job["id"], [LABOR], kind="owner")
    assert owner2["number"] == "J1-CO-002"


# ── Lifecycle + 409s ────────────────────────────────────────────────────────


def test_edit_after_approve_is_409(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    co = _co(client, job["id"], [LABOR])
    assert _submit_approve(client, co["id"]).status_code == 200
    r = client.put(f"/api/change-orders/{co['id']}", json={"title": "late edit"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_locked"


def test_approve_twice_is_409(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    co = _co(client, job["id"], [LABOR])
    assert _submit_approve(client, co["id"]).status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_not_pending"


def test_void_unapproved_is_409(client, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR])
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_not_approved"


def test_pco_approve_says_promote_instead(client, seed_customer):
    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR], kind="pco")
    client.post(f"/api/change-orders/{pco['id']}/submit")
    r = client.post(f"/api/change-orders/{pco['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "promote_instead"


def test_delete_only_draft(client, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR])
    client.post(f"/api/change-orders/{co['id']}/submit")
    r = client.delete(f"/api/change-orders/{co['id']}")
    assert r.status_code == 409  # pending is not draft
    # a fresh draft deletes
    co2 = _co(client, job["id"], [LABOR])
    assert client.delete(f"/api/change-orders/{co2['id']}").status_code == 200


# ── Owner CO on contract funding ────────────────────────────────────────────


def test_owner_co_moves_contract_and_appends_sov(client, db_session, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    # SOV with one work line balancing the contract
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Base", "kind": "work", "scheduled_value": 100000}
            ]
        },
    )
    co = _co(client, job["id"], [LABOR, MATERIAL], title="Extra steel")
    assert _submit_approve(client, co["id"]).status_code == 200

    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 101675.0  # 100000 + sell_total 1675
    assert contract["original_contract_amount"] == 100000.0  # unchanged
    assert contract["changes"] == 1675.0  # equals the delta
    assert contract["changes"] == contract["change_orders"]["approved_total"]
    assert contract["change_orders"]["approved_count"] == 1
    # the SOV gained a read-only change_order line and still balances
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()
    co_line = [line for line in sov["sov"] if line["kind"] == "change_order"]
    assert len(co_line) == 1
    assert float(co_line[0]["scheduled_value"]) == 1675.0
    assert co_line[0]["description"].startswith("J1-CO-001")
    assert sov["contract"]["sov_balance"] == 0.0  # 101675 sum vs 101675 contract


def test_owner_co_writes_change_budget_rows(client, db_session, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    # an original (manual) budget on a DIFFERENT key than the CO's lines
    client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={
            "rows": [{"cost_type": "other", "amount": 40000, "revenue_amount": 50000}]
        },
    )
    co = _co(client, job["id"], [LABOR, MATERIAL])
    assert _submit_approve(client, co["id"]).status_code == 200

    rows = db_session.query(JobBudget).filter(JobBudget.job_id == job["id"]).all()
    change_rows = [r for r in rows if r.source == "change"]
    assert change_rows and all(r.locked for r in change_rows)
    assert sum(Decimal(str(r.amount)) for r in change_rows) == Decimal("1500.00")

    totals = job_cost_tree(db_session, job["id"])["totals"]
    assert totals["original"] == 40000.0
    assert totals["changes"] == 1500.0
    assert totals["revised"] == 41500.0  # original + changes


def test_void_reverses_contract_sov_and_budget(client, db_session, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    co = _co(client, job["id"], [LABOR, MATERIAL])
    assert _submit_approve(client, co["id"]).status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 200

    contract = client.get(f"/api/jobs/{job['id']}/contract").json()
    assert contract["contract"]["contract_amount"] == 100000.0  # back to original
    assert not [line for line in contract["sov"] if line["kind"] == "change_order"]
    change_rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.source == "change")
        .all()
    )
    assert change_rows == []


# ── Contingency funding ─────────────────────────────────────────────────────


def test_contingency_used_and_exceeded(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=100000,
        contingency_amount=2000,
    )
    # markup turned on explicitly: a contingency draw is at sell (1675)
    co = _co(
        client, job["id"], [LABOR, MATERIAL], funding="contingency", apply_markup=True
    )
    assert _submit_approve(client, co["id"]).status_code == 200
    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 100000.0  # contingency does not add
    assert contract["contingency_used"] == 1675.0
    assert contract["contingency_remaining"] == 325.0

    # a second contingency CO would pass the 2000 limit → 409
    co2 = _co(
        client, job["id"], [MATERIAL], funding="contingency", apply_markup=True
    )  # sell 1100
    client.post(f"/api/change-orders/{co2['id']}/submit")
    r = client.post(f"/api/change-orders/{co2['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "contingency_exceeded"


# ── Allowance funding ───────────────────────────────────────────────────────


def _allowance_job(client, customer_id, name="Tower"):
    job = _job(client, customer_id, name=name, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Base", "kind": "work", "scheduled_value": 98000},
                {
                    "description": "Finishes allowance",
                    "kind": "allowance",
                    "scheduled_value": 2000,
                },
            ]
        },
    )
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    allowance_id = next(line["id"] for line in sov if line["kind"] == "allowance")
    return job, allowance_id


def test_allowance_used_remaining_and_exceeded(client, seed_customer):
    job, allowance_id = _allowance_job(client, seed_customer.id)
    # allowance-funded CO carries no markup: sell == cost == 1500
    co = _co(
        client,
        job["id"],
        [LABOR, MATERIAL],
        funding="allowance",
        allowance_sov_line_id=allowance_id,
    )
    assert co["sell_total"] == 1500.0  # no markup under an allowance
    assert _submit_approve(client, co["id"]).status_code == 200

    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 100000.0  # allowance does not add
    row = next(a for a in contract["allowances"] if a["sov_line_id"] == allowance_id)
    assert row["used"] == 1500.0
    assert row["remaining"] == 500.0

    # a second allowance CO beyond the 2000 allowance → 409
    co2 = _co(
        client,
        job["id"],
        [MATERIAL],
        funding="allowance",
        allowance_sov_line_id=allowance_id,
    )
    client.post(f"/api/change-orders/{co2['id']}/submit")
    r = client.post(f"/api/change-orders/{co2['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "allowance_exceeded"


def test_allowance_funding_needs_a_line(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "allowance",
            "lines": [LABOR],
        },
    )
    assert r.status_code == 422


# ── Promote a PCO ───────────────────────────────────────────────────────────


def test_promote_copies_lines_and_marks_promoted(client, seed_customer):
    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR, MATERIAL], kind="pco")
    r = client.post(f"/api/change-orders/{pco['id']}/promote")
    assert r.status_code == 200, r.text
    owner = r.json()
    assert owner["kind"] == "owner"
    assert owner["source_pco_id"] == pco["id"]
    assert len(owner["lines"]) == 2
    assert owner["sell_total"] == 1675.0
    # the PCO is now promoted
    assert client.get(f"/api/change-orders/{pco['id']}").json()["status"] == "promoted"


# ── Sub CO against a commitment ─────────────────────────────────────────────


def _vendor(client, name="Ace", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _sent_commitment(client, vendor_id, job_id, lines, **extra):
    body = {
        "vendor_id": vendor_id,
        "date": "2026-09-01",
        "job_id": job_id,
        "lines": lines,
    }
    body.update(extra)
    po = client.post("/api/purchase-orders", json=body).json()
    up = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return up.json()


def _sub_line(desc, unit_cost):
    return {
        "description": desc,
        "cost_type": "subcontract",
        "quantity": 1,
        "unit_cost": unit_cost,
    }


def test_sub_co_appends_and_keeps_billed_amount(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base scope", "quantity": 1, "rate": 10000}],
        commitment_type="subcontract",
    )
    base_line_id = po["lines"][0]["id"]
    # Bill PART of the base line first, so "billed_amount kept" can actually
    # fail if the append clobbered it (it was a no-op assertion before).
    conv = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": base_line_id, "amount": 4000}]},
    )
    assert conv.status_code == 200, conv.text

    owner = _co(client, job["id"], [_sub_line("Added scope", 2500)])
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order",
        json={"commitment_id": po["id"]},
    ).json()
    assert sub["kind"] == "sub"
    assert sub["sell_total"] == 2500.0  # no markup on a sub CO
    assert _submit_approve(client, owner["id"]).status_code == 200
    assert _submit_approve(client, sub["id"]).status_code == 200

    fresh = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert float(fresh["total"]) == 12500.0  # the commitment grew by cost
    assert len(fresh["lines"]) == 2

    db_session.expire_all()
    from app.models.purchase_orders import PurchaseOrderLine

    lines = (
        db_session.query(PurchaseOrderLine).filter_by(purchase_order_id=po["id"]).all()
    )
    base = next(line for line in lines if line.id == base_line_id)
    assert Decimal(str(base.billed_amount)) == Decimal("4000.00")  # kept
    added = [line for line in lines if line.change_order_id == sub["id"]]
    assert len(added) == 1
    assert Decimal(str(added[0].billed_amount)) == Decimal("0")


def test_sub_co_grows_a_partial_commitment_and_buyout(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    pkg = client.post(
        "/api/trade-packages", json={"name": "Concrete", "section_ids": []}
    ).json()
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Slab", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
        trade_package_id=pkg["id"],
    )
    # partially bill it → partial (still open)
    conv = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": po["lines"][0]["id"], "amount": 2000}]},
    )
    assert conv.status_code == 200, conv.text
    assert client.get(f"/api/purchase-orders/{po['id']}").json()["status"] == "partial"

    owner = _co(client, job["id"], [_sub_line("More slab", 1500)])
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order",
        json={"commitment_id": po["id"]},
    ).json()
    assert _submit_approve(client, owner["id"]).status_code == 200
    assert _submit_approve(client, sub["id"]).status_code == 200

    fresh = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert fresh["status"] == "partial"
    assert float(fresh["total"]) == 6500.0

    # the job's buyout is construction mode's, as its report is
    client.put("/api/settings", json={"company_type": "construction"})
    buyout = client.get(f"/api/jobs/{job['id']}/buyout").json()
    row = next(r for r in buyout["rows"] if r["package_id"] == pkg["id"])
    assert row["bought"] == 6500.0


def test_sub_co_refuses_a_closed_or_foreign_commitment(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    # a fully-billed (closed) commitment is not open
    closed_po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    assert (
        client.post(
            f"/api/purchase-orders/{closed_po['id']}/convert-to-bill", json=None
        ).status_code
        == 200
    )
    owner = _co(client, job["id"], [_sub_line("Extra", 1000)])
    r = client.post(
        f"/api/change-orders/{owner['id']}/sub-order",
        json={"commitment_id": closed_po["id"]},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "commitment_not_open"

    # a commitment on another job is a mismatch
    other = _job(client, seed_customer.id, name="Other")
    foreign_po = _sent_commitment(
        client,
        vendor["id"],
        other["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    r = client.post(
        f"/api/change-orders/{owner['id']}/sub-order",
        json={"commitment_id": foreign_po["id"]},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "commitment_job_mismatch"


def test_void_sub_co_removes_exactly_its_line_not_an_identical_original(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    base_line_id = po["lines"][0]["id"]
    # owner CO carries a line of the SAME amount as the original base line
    owner = _co(client, job["id"], [_sub_line("Identical", 5000)])
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, owner["id"]).status_code == 200
    assert _submit_approve(client, sub["id"]).status_code == 200
    assert len(client.get(f"/api/purchase-orders/{po['id']}").json()["lines"]) == 2

    r = client.post(f"/api/change-orders/{sub['id']}/void")
    assert r.status_code == 200, r.text
    fresh = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert len(fresh["lines"]) == 1  # exactly the CO line removed
    assert fresh["lines"][0]["id"] == base_line_id  # the original survived
    assert float(fresh["total"]) == 5000.0


def test_void_sub_co_refuses_when_billed(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    owner = _co(
        client,
        job["id"],
        [
            {
                "description": "Extra",
                "cost_type": "subcontract",
                "quantity": 1,
                "unit_cost": 2000,
            },
        ],
    )
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, owner["id"]).status_code == 200
    assert _submit_approve(client, sub["id"]).status_code == 200
    # bill the whole commitment (both lines) → the added line is now billed
    conv = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill", json=None)
    assert conv.status_code == 200, conv.text
    r = client.post(f"/api/change-orders/{sub['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_billed"


def test_linked_sub_co_does_not_double_count_the_budget(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    # owner CO (contract-funded) with subcontract scope → one change row (cost)
    owner = _co(client, job["id"], [_sub_line("Scope", 2500)])
    assert _submit_approve(client, owner["id"]).status_code == 200
    # its sub order carries the same scope to the commitment — and must NOT
    # write the cost again.
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, sub["id"]).status_code == 200

    rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.source == "change")
        .all()
    )
    assert sum(Decimal(str(r.amount)) for r in rows) == Decimal("2500.00")


# ── Same-key budget collision + manual budget stays editable ────────────────


def test_estimate_locked_row_and_co_share_a_key(
    client, db_session, seed_accounts, seed_customer
):
    code = _section(client, "03 30 00", "Cast-in-Place Concrete", cost_type="material")
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-02-01",
            "lines": [
                {
                    "description": "Slab",
                    "quantity": 1,
                    "rate": 10000,
                    "unit_cost": 8000,
                    "cost_code_id": code["id"],
                    "cost_type": "material",
                }
            ],
        },
    ).json()
    job = _job(client, seed_customer.id)
    accepted = client.post(
        f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]}
    )
    assert accepted.status_code == 200, accepted.text
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=10000)

    # an owner CO on the SAME code/type approves (no unique-key 500)
    co = _co(
        client,
        job["id"],
        [
            {
                "description": "More concrete",
                "cost_type": "material",
                "cost_code_id": code["id"],
                "quantity": 1,
                "unit_cost": 1000,
            }
        ],
    )
    assert _submit_approve(client, co["id"]).status_code == 200

    rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.cost_code_id == code["id"])
        .all()
    )
    sources = {r.source for r in rows}
    assert "estimate" in sources and "change" in sources  # coexist on one key

    tree = job_cost_tree(db_session, job["id"])["totals"]
    assert tree["original"] == 8000.0  # the estimate-locked cost
    assert tree["changes"] == 1000.0  # the change order's cost
    assert tree["revised"] == 9000.0


def test_manual_budget_edits_when_only_co_rows_are_locked(
    client, db_session, seed_customer
):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    # an approved owner CO leaves locked source="change" rows on (None, material)
    co = _co(client, job["id"], [MATERIAL])
    assert _submit_approve(client, co["id"]).status_code == 200
    # a manual budget on a DIFFERENT key still saves (not refused as locked)
    r = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_type": "other", "amount": 7000, "revenue_amount": 9000}]},
    )
    assert r.status_code == 200, r.text
    # but a manual row that names a CO-owned key is refused
    r2 = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_type": "material", "amount": 1, "revenue_amount": 1}]},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "co_row"


# ── Frozen math + numbering race + deduct ───────────────────────────────────


def test_approved_co_is_frozen_against_a_later_term_change(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=100000,
        co_markup_pct=10,
    )
    co = _co(
        client,
        job["id"],
        [
            {
                "description": "Steel",
                "cost_type": "material",
                "quantity": 1,
                "unit_cost": 1000,
            }
        ],
    )
    assert _submit_approve(client, co["id"]).status_code == 200
    assert client.get(f"/api/change-orders/{co['id']}").json()["sell_total"] == 1100.0

    # change the default markup AFTER approval
    _contract(client, job["id"], co_markup_pct=50)
    # the approved CO is frozen: its figures and the tile do not move
    assert client.get(f"/api/change-orders/{co['id']}").json()["sell_total"] == 1100.0
    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 101100.0
    assert contract["changes"] == 1100.0

    # void returns the contract exactly to the original
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 100000.0


def test_numbering_collision_retries(client, db_session, seed_customer):
    job = _job(client, seed_customer.id)
    other = _job(client, seed_customer.id, name="Other")
    # another job already holds J1-CO-001 (two jobs can share a job number)
    db_session.add(
        ChangeOrder(
            number="J1-CO-001", job_id=other["id"], kind="owner", status="draft"
        )
    )
    db_session.commit()
    owner = _co(client, job["id"], [LABOR])
    assert owner["number"] == "J1-CO-002"  # skipped the taken number, no 500


def test_deduct_co_sharing_a_key_leaves_the_add_on_void(
    client, db_session, seed_customer
):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    add = _co(
        client,
        job["id"],
        [
            {
                "description": "Add",
                "cost_type": "material",
                "quantity": 1,
                "unit_cost": 1000,
            }
        ],
    )
    assert _submit_approve(client, add["id"]).status_code == 200
    deduct = _co(
        client,
        job["id"],
        [
            {
                "description": "Deduct",
                "cost_type": "material",
                "quantity": 1,
                "unit_cost": -400,
            }
        ],
    )
    assert _submit_approve(client, deduct["id"]).status_code == 200

    # both on (None, material): a change row each, linked to its change
    # order, netting to 600 between them
    rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.source == "change")
        .all()
    )
    assert {r.change_order_id: Decimal(str(r.amount)) for r in rows} == {
        add["id"]: Decimal("1000.00"),
        deduct["id"]: Decimal("-400.00"),
    }

    # voiding the add leaves the deduct's figures behind (row not deleted)
    assert client.post(f"/api/change-orders/{add['id']}/void").status_code == 200
    db_session.expire_all()
    rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.source == "change")
        .all()
    )
    assert len(rows) == 1
    assert Decimal(str(rows[0].amount)) == Decimal("-400.00")
    assert rows[0].change_order_id == deduct["id"]


# ── PDF (HTML stage) ────────────────────────────────────────────────────────


def test_pdf_html_renders_for_an_owner_co(client, db_session, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(
        client,
        job["id"],
        [LABOR, MATERIAL],
        title="Steel upgrade",
        description="Owner-requested",
    )
    row = db_session.get(ChangeOrder, co["id"])
    html = render_change_order_html(
        row, co_totals(row, row.job), get_all_settings(db_session)
    )
    assert "CHANGE ORDER" in html
    assert "J1-CO-001" in html
    assert "Steel upgrade" in html
    assert "Total" in html


# ── Review round: guards, markup flag, numbering, revisions, roll-up ─────────


def _commit_with_sub_co(client, seed_accounts, seed_customer, approve_owner=True):
    """A job, an open subcontract, a $2,000 owner CO and its linked sub CO."""
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    owner = _co(client, job["id"], [_sub_line("Extra", 2000)])
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    if approve_owner:
        assert _submit_approve(client, owner["id"]).status_code == 200
    return job, po, owner, sub


def test_linked_sub_co_cannot_approve_while_its_owner_co_is_draft(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(
        client, seed_accounts, seed_customer, approve_owner=False
    )
    r = _submit_approve(client, sub["id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "owner_co_not_approved"
    # the commitment did not move
    assert float(client.get(f"/api/purchase-orders/{po['id']}").json()["total"]) == 5000
    # once the owner CO is approved the sub CO can follow
    assert client.post(f"/api/change-orders/{owner['id']}/approve").status_code == 409
    client.post(f"/api/change-orders/{owner['id']}/submit")
    assert client.post(f"/api/change-orders/{owner['id']}/approve").status_code == 200
    assert client.post(f"/api/change-orders/{sub['id']}/approve").status_code == 200


def test_owner_co_with_an_approved_sub_co_cannot_be_rejected(
    client, db_session, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    assert _submit_approve(client, sub["id"]).status_code == 200
    # force the owner CO back to pending: the guard is the last line of defence
    row = db_session.get(ChangeOrder, owner["id"])
    row.status = "pending"
    db_session.commit()
    r = client.post(f"/api/change-orders/{owner['id']}/reject")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "approved_sub_co"


def test_owner_co_with_an_approved_sub_co_cannot_be_voided(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    assert _submit_approve(client, sub["id"]).status_code == 200
    r = client.post(f"/api/change-orders/{owner['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "approved_sub_co"
    assert float(client.get(f"/api/purchase-orders/{po['id']}").json()["total"]) == 7000
    # void the sub CO first, then the owner CO goes
    assert client.post(f"/api/change-orders/{sub['id']}/void").status_code == 200
    assert client.post(f"/api/change-orders/{owner['id']}/void").status_code == 200


def test_allowance_line_must_belong_to_the_job_and_be_an_allowance(
    client, db_session, seed_customer
):
    job_a, allowance_a = _allowance_job(client, seed_customer.id)
    job_b, _ = _allowance_job(client, seed_customer.id, name="Other")
    # job B cannot spend job A's allowance
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job_b["id"],
            "funding": "allowance",
            "allowance_sov_line_id": allowance_a,
            "lines": [LABOR],
        },
    )
    assert r.status_code == 404
    # a bogus id is refused, not skipped
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job_a["id"],
            "funding": "allowance",
            "allowance_sov_line_id": 99999,
            "lines": [LABOR],
        },
    )
    assert r.status_code == 404
    # a work line of the same job is not an allowance
    sov = client.get(f"/api/jobs/{job_a['id']}/contract").json()["sov"]
    work_id = next(line["id"] for line in sov if line["kind"] == "work")
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job_a["id"],
            "funding": "allowance",
            "allowance_sov_line_id": work_id,
            "lines": [LABOR],
        },
    )
    assert r.status_code == 400
    # an edit is checked the same way
    co = _co(
        client,
        job_a["id"],
        [LABOR],
        funding="allowance",
        allowance_sov_line_id=allowance_a,
    )
    r = client.put(
        f"/api/change-orders/{co['id']}", json={"allowance_sov_line_id": 99999}
    )
    assert r.status_code == 404
    # and approve never skips the limit for a line that has gone bad
    row = db_session.get(ChangeOrder, co["id"])
    row.allowance_sov_line_id = 99999
    db_session.commit()
    client.post(f"/api/change-orders/{co['id']}/submit")
    r = client.post(f"/api/change-orders/{co['id']}/approve")
    assert r.status_code == 404


def test_plain_po_edit_cannot_drop_or_reprice_a_sub_co_line(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    assert _submit_approve(client, sub["id"]).status_code == 200
    lines = client.get(f"/api/purchase-orders/{po['id']}").json()["lines"]
    assert len(lines) == 2
    base = next(ln for ln in lines if ln["description"] == "Base")
    added = next(ln for ln in lines if ln["description"] == "Extra")

    def edit(rows):
        return client.put(f"/api/purchase-orders/{po['id']}", json={"lines": rows})

    def keep(ln, **over):
        row = {
            "id": ln["id"],
            "description": ln["description"],
            "quantity": float(ln["quantity"]),
            "rate": float(ln["rate"]),
        }
        row.update(over)
        return row

    # dropping the CO-added line
    r = edit([keep(base)])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "commitment_co_line"
    # changing its amount
    r = edit([keep(base), keep(added, rate=1)])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "commitment_co_line"
    # sending it back unchanged (the PO form does) is fine, and so is editing
    # an original line
    assert edit([keep(base, rate=5100), keep(added)]).status_code == 200
    fresh = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert float(fresh["total"]) == 7100


def test_sub_co_approve_refused_once_the_commitment_is_fully_billed(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    conv = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill", json=None)
    assert conv.status_code == 200, conv.text
    r = _submit_approve(client, sub["id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "commitment_not_open"


def test_markup_defaults_off_for_allowance_and_contingency_and_is_recorded(
    client, seed_customer
):
    job, allowance_id = _allowance_job(client, seed_customer.id)
    contract = _co(client, job["id"], [LABOR])
    assert contract["apply_markup"] is True and contract["sell_total"] == 575.0
    cont = _co(client, job["id"], [LABOR], funding="contingency")
    assert cont["apply_markup"] is False
    assert cont["sell_total"] == cont["cost_total"] == 500.0
    allow = _co(
        client,
        job["id"],
        [LABOR],
        funding="allowance",
        allowance_sov_line_id=allowance_id,
    )
    assert allow["apply_markup"] is False and allow["sell_total"] == 500.0
    # the GC can turn it on, and the record says so
    on = _co(client, job["id"], [LABOR], funding="contingency", apply_markup=True)
    assert on["apply_markup"] is True and on["sell_total"] == 575.0
    # an edit that switches the funding resets to that funding's default
    r = client.put(f"/api/change-orders/{on['id']}", json={"funding": "contract"})
    assert r.json()["apply_markup"] is True
    r = client.put(f"/api/change-orders/{on['id']}", json={"funding": "contingency"})
    assert r.json()["apply_markup"] is False and r.json()["sell_total"] == 500.0
    assert effective_markup(Decimal("15"), None, None, "contingency") == 0
    assert effective_markup(Decimal("15"), None, None, "contingency", True) == 15


def test_markup_pct_is_bounded(client, seed_customer):
    job = _job(client, seed_customer.id)
    for bad in (-150, -100, 1000.5):
        r = client.post(
            "/api/change-orders",
            json={"job_id": job["id"], "markup_pct": bad, "lines": [LABOR]},
        )
        assert r.status_code == 422, bad
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "lines": [{**LABOR, "markup_pct": -150}],
        },
    )
    assert r.status_code == 422
    r = client.put(f"/api/jobs/{job['id']}/contract", json={"co_markup_pct": -150})
    assert r.status_code == 422
    ok = _co(client, job["id"], [{**LABOR, "markup_pct": -10}], markup_pct=1000)
    assert ok["sell_total"] == 450.0  # the line's own -10% wins


def test_numbers_run_per_job(client, seed_customer):
    a = _job(client, seed_customer.id, name="A", job_number="1001")
    b = _job(client, seed_customer.id, name="B", job_number="1002")
    a1 = _co(client, a["id"], [LABOR])
    a2 = _co(client, a["id"], [LABOR])
    b1 = _co(client, b["id"], [LABOR])
    p_b = _co(client, b["id"], [LABOR], kind="pco")
    assert (a1["number"], a2["number"]) == ("1001-CO-001", "1001-CO-002")
    assert b1["number"] == "1002-CO-001"  # each job starts at 001
    assert p_b["number"] == "1002-PCO-001"


def test_old_company_wide_numbers_stay(client, db_session, seed_customer):
    job = _job(client, seed_customer.id, job_number="7")
    db_session.add(
        ChangeOrder(number="CO-0009", job_id=job["id"], kind="owner", status="draft")
    )
    db_session.commit()
    assert _co(client, job["id"], [LABOR])["number"] == "7-CO-001"
    assert db_session.query(ChangeOrder).filter_by(number="CO-0009").count() == 1


def test_pco_revision_supersedes_and_keeps_history(client, seed_customer):
    job = _job(client, seed_customer.id, job_number="55")
    pco = _co(client, job["id"], [LABOR], kind="pco", title="Option A")
    r = client.post(f"/api/change-orders/{pco['id']}/revise")
    assert r.status_code == 201, r.text
    rev2 = r.json()
    assert rev2["number"] == "55-PCO-001-R2"
    assert (rev2["revision"], rev2["supersedes_id"], rev2["status"]) == (
        2,
        pco["id"],
        "draft",
    )
    assert rev2["sell_total"] == pco["sell_total"]  # lines carried over
    # edit the revision, revise again
    client.put(f"/api/change-orders/{rev2['id']}", json={"lines": [MATERIAL]})
    rev3 = client.post(f"/api/change-orders/{rev2['id']}/revise").json()
    assert rev3["number"] == "55-PCO-001-R3" and rev3["revision"] == 3
    # the list shows the latest only; history keeps the rest
    shown = client.get(f"/api/change-orders?job_id={job['id']}").json()
    assert [c["id"] for c in shown] == [rev3["id"]]
    every = client.get(f"/api/change-orders?job_id={job['id']}&history=true").json()
    assert [c["status"] for c in every] == ["superseded", "superseded", "draft"]
    # a superseded revision can be neither revised nor promoted
    for path in ("revise", "promote"):
        r = client.post(f"/api/change-orders/{pco['id']}/{path}")
        assert r.status_code == 409, path
    # the latest promotes
    owner = client.post(f"/api/change-orders/{rev3['id']}/promote").json()
    assert owner["kind"] == "owner" and owner["source_pco_id"] == rev3["id"]
    # only a PCO can be revised
    assert client.post(f"/api/change-orders/{owner['id']}/revise").status_code == 409


def test_several_pcos_roll_up_into_one_owner_co(client, seed_customer):
    job = _job(client, seed_customer.id)
    p1 = _co(client, job["id"], [LABOR], kind="pco", title="Crew")
    p2 = _co(client, job["id"], [MATERIAL], kind="pco", title="Steel")
    r = client.post(
        f"/api/change-orders/{p1['id']}/promote", json={"pco_ids": [p2["id"]]}
    )
    assert r.status_code == 200, r.text
    owner = r.json()
    assert owner["kind"] == "owner" and len(owner["lines"]) == 2
    assert owner["sell_total"] == p1["sell_total"] + p2["sell_total"] == 1675.0
    assert owner["source_pco_ids"] == [p1["id"], p2["id"]]
    for pco in (p1, p2):
        got = client.get(f"/api/change-orders/{pco['id']}").json()
        assert got["status"] == "promoted" and got["promoted_to_id"] == owner["id"]
    # approving it moves the contract by the sum
    assert _submit_approve(client, owner["id"]).status_code == 200


def test_rolled_up_pcos_must_agree_and_be_promotable(client, seed_customer):
    job = _job(client, seed_customer.id)
    other = _job(client, seed_customer.id, name="Other")
    p1 = _co(client, job["id"], [LABOR], kind="pco")
    p_cont = _co(client, job["id"], [LABOR], kind="pco", funding="contingency")
    p_other = _co(client, other["id"], [LABOR], kind="pco")
    for bad in (p_cont, p_other):
        r = client.post(
            f"/api/change-orders/{p1['id']}/promote", json={"pco_ids": [bad["id"]]}
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "pco_mismatch"
    # nothing was promoted by the refused attempts
    assert client.get(f"/api/change-orders/{p1['id']}").json()["status"] == "draft"
    owner = _co(client, job["id"], [LABOR])
    r = client.post(
        f"/api/change-orders/{p1['id']}/promote", json={"pco_ids": [owner["id"]]}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_a_pco"


def test_pdf_says_whether_markup_was_applied_and_the_revision(
    client, db_session, seed_customer
):
    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR], kind="pco", funding="contingency")
    rev = client.post(f"/api/change-orders/{pco['id']}/revise").json()
    row = db_session.get(ChangeOrder, rev["id"])
    html = render_change_order_html(
        row, co_totals(row, row.job), get_all_settings(db_session)
    )
    assert "Not applied" in html and "revision 2" in html
    row.apply_markup = True
    assert "Applied" in render_change_order_html(
        row, co_totals(row, row.job), get_all_settings(db_session)
    )
    # contract-funded work shows no markup row at all
    owner = db_session.get(ChangeOrder, _co(client, job["id"], [LABOR])["id"])
    assert "Markup:" not in render_change_order_html(
        owner, co_totals(owner, owner.job), get_all_settings(db_session)
    )


# ── Migration ───────────────────────────────────────────────────────────────


def test_migration_upgrades_and_downgrades(tmp_path):
    db = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    assert run("upgrade", "head").returncode == 0
    assert run("downgrade", "-1").returncode == 0
    assert run("upgrade", "head").returncode == 0


def test_marked_up_allowance_co_draws_its_sell_price(client, seed_customer):
    job, allowance_id = _allowance_job(client, seed_customer.id)
    line = {**_sub_line("Tile", 2000), "markup_pct": 15}
    kw = dict(
        funding="allowance", allowance_sov_line_id=allowance_id, apply_markup=True
    )
    # $2,000 of cost at 15% sells for $2,300, over the $2,000 allowance
    co = _co(client, job["id"], [line], **kw)
    assert co["sell_total"] == 2300.0
    r = _submit_approve(client, co["id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "allowance_exceeded"

    # with room for the sell price it approves and draws 2,300
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Base", "kind": "work", "scheduled_value": 97700},
                {
                    "description": "Finishes allowance",
                    "kind": "allowance",
                    "scheduled_value": 2300,
                },
            ]
        },
    )
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    new_id = next(ln["id"] for ln in sov if ln["kind"] == "allowance")
    co2 = _co(
        client,
        job["id"],
        [line],
        funding="allowance",
        allowance_sov_line_id=new_id,
        apply_markup=True,
    )
    assert _submit_approve(client, co2["id"]).status_code == 200
    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    row = next(a for a in contract["allowances"] if a["sov_line_id"] == new_id)
    assert row["used"] == 2300.0
    assert row["remaining"] == 0.0


def test_contract_funded_co_is_always_marked_up(client, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR], apply_markup=False)
    assert co["apply_markup"] is True
    assert co["sell_total"] == 575.0
    r = client.put(f"/api/change-orders/{co['id']}", json={"apply_markup": False})
    assert r.status_code == 200
    assert r.json()["apply_markup"] is True


def _po_total(client, po):
    return float(client.get(f"/api/purchase-orders/{po['id']}").json()["total"])


def test_editing_a_linked_sub_co_past_its_owner_co_cannot_approve(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    r = client.put(
        f"/api/change-orders/{sub['id']}", json={"lines": [_sub_line("Extra", 20000)]}
    )
    assert r.status_code == 200, r.text
    r = _submit_approve(client, sub["id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "owner_co_budget_exceeded"
    assert _po_total(client, po) == 5000


def test_second_full_amount_sub_co_posted_directly_is_refused(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    assert _submit_approve(client, sub["id"]).status_code == 200
    second = _co(
        client,
        job["id"],
        [_sub_line("Extra", 2000)],
        kind="sub",
        commitment_id=po["id"],
        owner_change_order_id=owner["id"],
    )
    r = _submit_approve(client, second["id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "owner_co_budget_exceeded"
    assert _po_total(client, po) == 7000


def test_sub_cos_that_fit_the_owner_co_cost_both_approve(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    for amount in (1200, 800):
        extra = _co(
            client,
            job["id"],
            [_sub_line("Part", amount)],
            kind="sub",
            commitment_id=po["id"],
            owner_change_order_id=owner["id"],
        )
        assert _submit_approve(client, extra["id"]).status_code == 200
    assert _po_total(client, po) == 7000
    # the owner CO's $2,000 is spent; one more cent is not
    over = _co(
        client,
        job["id"],
        [_sub_line("Part", 0.01)],
        kind="sub",
        commitment_id=po["id"],
        owner_change_order_id=owner["id"],
    )
    assert _submit_approve(client, over["id"]).status_code == 409


def test_identical_owner_co_lines_can_go_to_two_subs(
    client, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    pos = [
        _sent_commitment(
            client,
            vendor["id"],
            job["id"],
            [{"description": "Base", "quantity": 1, "rate": 5000}],
            commitment_type="subcontract",
        )
        for _ in range(2)
    ]
    owner = _co(client, job["id"], [_sub_line("Pour", 1000), _sub_line("Pour", 1000)])
    assert _submit_approve(client, owner["id"]).status_code == 200
    ids = [ln["id"] for ln in owner["lines"]]
    for po, line_id in zip(pos, ids):
        r = client.post(
            f"/api/change-orders/{owner['id']}/sub-order",
            json={"commitment_id": po["id"], "line_ids": [line_id]},
        )
        assert r.status_code == 200, r.text
        assert _submit_approve(client, r.json()["id"]).status_code == 200
    assert [_po_total(client, po) for po in pos] == [6000, 6000]


def test_rejected_sub_co_does_not_count_toward_the_owner_co_cost(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    client.post(f"/api/change-orders/{sub['id']}/submit")
    assert client.post(f"/api/change-orders/{sub['id']}/reject").status_code == 200
    again = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, again["id"]).status_code == 200
    assert _po_total(client, po) == 7000


def test_voided_sub_co_does_not_count_toward_the_owner_co_cost(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    assert _submit_approve(client, sub["id"]).status_code == 200
    assert client.post(f"/api/change-orders/{sub['id']}/void").status_code == 200
    again = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, again["id"]).status_code == 200
    assert _po_total(client, po) == 7000


def test_plain_po_edit_cannot_move_a_sub_co_line_to_another_job(
    client, seed_accounts, seed_customer
):
    job, po, owner, sub = _commit_with_sub_co(client, seed_accounts, seed_customer)
    assert _submit_approve(client, sub["id"]).status_code == 200
    other = _job(client, seed_customer.id, name="Other")
    lines = client.get(f"/api/purchase-orders/{po['id']}").json()["lines"]
    base = next(ln for ln in lines if ln["description"] == "Base")
    added = next(ln for ln in lines if ln["description"] == "Extra")

    def row(ln, **over):
        d = {
            "id": ln["id"],
            "description": ln["description"],
            "quantity": float(ln["quantity"]),
            "rate": float(ln["rate"]),
        }
        d.update(over)
        return d

    for over in (
        {"job_id": other["id"]},
        {"cost_type": "labor"},
        {"quantity": 2, "rate": 1000},
    ):
        r = client.put(
            f"/api/purchase-orders/{po['id']}",
            json={"lines": [row(base), row(added, **over)]},
        )
        assert r.status_code == 409, over
        assert r.json()["detail"]["code"] == "commitment_co_line"
    fresh = client.get(f"/api/purchase-orders/{po['id']}").json()
    moved = next(ln for ln in fresh["lines"] if ln["description"] == "Extra")
    assert moved["job_id"] == added["job_id"]
    assert float(fresh["total"]) == 7000


def test_only_an_approved_owner_co_locks_the_contract_sum_and_contingency(
    client, seed_accounts, seed_customer
):
    """The sum and contingency edit freely until the job has an approved owner
    change order; from then on they move by change order only (#552, #554). A
    draft or pending one, a PCO or a sub CO does not lock; a void frees them."""
    job = _job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    assert client.put(base, json={"contract_amount": 110000}).status_code == 200

    # a PCO, a draft and a pending owner CO leave the sum alone
    _co(client, job["id"], [LABOR], kind="pco")
    co = _co(client, job["id"], [LABOR])
    assert client.put(base, json={"contract_amount": 111000}).status_code == 200
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    assert client.put(base, json={"contract_amount": 112000}).status_code == 200

    # a rejected one leaves nothing behind
    assert client.post(f"/api/change-orders/{co['id']}/reject").status_code == 200
    assert client.put(base, json={"contract_amount": 113000}).status_code == 200

    # an approved sub CO does not lock on its own
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    sub = _co(
        client,
        job["id"],
        [_sub_line("Extra", 500)],
        kind="sub",
        commitment_id=po["id"],
    )
    assert _submit_approve(client, sub["id"]).status_code == 200
    assert client.put(base, json={"contract_amount": 114000}).status_code == 200

    # an approved owner CO locks both
    owner = _co(client, job["id"], [LABOR])
    assert _submit_approve(client, owner["id"]).status_code == 200
    r = client.put(base, json={"contract_amount": 120000})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "contract_sum_locked"
    r = client.put(base, json={"contingency_amount": 5000})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "contingency_locked"
    # the same value is not a change
    same = client.get(base).json()["contract"]["contract_amount"]
    assert client.put(base, json={"contract_amount": same}).status_code == 200

    # a void puts it back
    assert client.post(f"/api/change-orders/{owner['id']}/void").status_code == 200
    assert client.put(base, json={"contract_amount": 120000}).status_code == 200


def test_approving_a_change_order_that_overflows_the_contract_is_a_422(
    client, seed_customer
):
    """A contract of 9,999,999,999,000.00 plus a 2,000.00 change order would
    not fit the contract column; the approve is refused and moves nothing."""
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=9_999_999_999_000,
    )
    co = _co(
        client,
        job["id"],
        [
            {
                "description": "Extra",
                "cost_type": "material",
                "quantity": 1,
                "unit_cost": 2000,
            }
        ],
        funding="contract",
    )
    r = _submit_approve(client, co["id"])
    assert r.status_code == 422, r.text
    assert "more than a contract can be" in r.text
    assert client.get(f"/api/jobs/{job['id']}").json()["contract_amount"] in (
        9_999_999_999_000,
        "9999999999000.00",
        9999999999000.0,
    )


def _message(r) -> str:
    """A 422's sentence, from an HTTPException detail or a schema error."""
    body = r.json()
    detail = body.get("detail")
    if isinstance(detail, list):
        return " ".join(e.get("message", "") for e in detail)
    return detail if isinstance(detail, str) else str(detail)


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


@pytest.fixture
def con(client):
    _construction(client)
    return client


def test_a_change_order_line_past_the_money_column_is_refused(
    con, seed_customer, db_session
):
    """A direct POST /api/change-orders took 99,999,999,999 ×
    99,999,999,999 too."""
    from app.models.change_orders import ChangeOrder

    job = _job(con, seed_customer.id)
    line = {"cost_type": "labor", "quantity": 99999999999, "unit_cost": 99999999999}
    r = con.post("/api/change-orders", json={"job_id": job["id"], "lines": [line]})
    assert r.status_code == 422, r.text
    assert "Line 1 comes to" in _message(r)
    assert db_session.query(ChangeOrder).count() == 0
    r = con.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "lines": [{"cost_type": "labor", "quantity": 1e15, "unit_cost": 1}],
        },
    )
    assert r.status_code == 422, r.text
    assert "Quantity must be between" in _message(r)
    # a markup change alone can take the sell past the column
    co = con.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "lines": [
                {"cost_type": "labor", "quantity": 100, "unit_cost": 90000000000}
            ],
        },
    )
    assert co.status_code == 201, co.text
    r = con.put(f"/api/change-orders/{co.json()['id']}", json={"markup_pct": 50})
    assert r.status_code == 422, r.text
    assert "more than a line can be" in _message(r)


def _approve(client, co_id, **body):
    assert client.post(f"/api/change-orders/{co_id}/submit").status_code == 200
    return client.post(f"/api/change-orders/{co_id}/approve", json=body)


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
    old = (date.today() - timedelta(days=365)).isoformat()
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


OCT15 = date(2026, 10, 15)


def _approve_co(client, job_id, unit_cost, approved_on, cost_type="material"):
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job_id,
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "Added scope",
                    "cost_type": cost_type,
                    "quantity": 1,
                    "unit_cost": unit_cost,
                    "markup_pct": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    co = r.json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(
        f"/api/change-orders/{co['id']}/approve",
        json={"approved_on": approved_on.isoformat()},
    )
    assert r.status_code == 200, r.text
    return co


def _over_billed_job(db, name="Clinic"):
    """Contract 100,000, budget 80,000 (material), cost 40,000 by 3/1,
    billed 60,000 by 4/1: earned 50,000, over-billed 10,000."""
    job = _wip_job(db, name=name)
    _budget(db, job, None, "material", "80000", source="manual")
    _cost(db, job, None, "material", "40000")
    _bill_owner(db, job, "60000")
    db.commit()
    return job


def _construction_2(db):
    db.add(Settings(key="company_type", value="construction"))
    db.commit()


def _setup(client, db):
    assert client.post("/api/construction/setup").status_code == 200
    _construction_2(db)


def test_each_change_order_writes_its_own_rows(client, db_session, seed_accounts):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    a = _approve_co(client, job.id, 1000, date(2026, 8, 10))
    b = _approve_co(client, job.id, -400, OCT15)
    rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job.id, JobBudget.source == "change")
        .all()
    )
    assert {r.change_order_id: Decimal(str(r.amount)) for r in rows} == {
        a["id"]: Decimal("1000.00"),
        b["id"]: Decimal("-400.00"),
    }
    # void takes the CO's own row only
    assert client.post(f"/api/change-orders/{a['id']}/void").status_code == 200
    db_session.expire_all()
    left = db_session.query(JobBudget).filter(JobBudget.source == "change").all()
    assert [r.change_order_id for r in left] == [b["id"]]


SEP30 = date(2026, 9, 30)


def _approved_co(db, job, number, lines, on, funding="contract", **extra):
    co = ChangeOrder(
        job_id=job.id,
        number=number,
        kind="owner",
        status="approved",
        funding=funding,
        approved_on=on,
        **extra,
    )
    db.add(co)
    db.flush()
    for i, (code_id, ctype, cost, sell) in enumerate(lines):
        db.add(
            ChangeOrderLine(
                change_order_id=co.id,
                line_order=i,
                description="x",
                cost_type=ctype,
                cost_code_id=code_id,
                quantity=1,
                unit_cost=Decimal(cost),
                amount=Decimal(cost),
                sell_amount=Decimal(sell),
            )
        )
    db.flush()
    return co


def _migration():
    path = ROOT / "migrations/versions/b5d2e8f4a6c3_wip_as_of_budgets_and_lock.py"
    spec = importlib.util.spec_from_file_location("r1_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_backfill_splits_a_shared_row_that_adds_up(db_session, seed_accounts):
    job = _wip_job(db_session)
    a = _approved_co(
        db_session, job, "CO-A", [(None, "material", "1000", "1100")], SEP30
    )
    b = _approved_co(
        db_session, job, "CO-B", [(None, "material", "-400", "-440")], OCT15
    )
    # the merged row the old code wrote for both, and one that no longer adds up
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("600"),
            revenue_amount=Decimal("660"),
            source="change",
            locked=True,
        )
    )
    _approved_co(db_session, job, "CO-C", [(None, "labor", "500", "500")], SEP30)
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="labor",
            amount=Decimal("700"),  # edited since: 500 cannot be recovered
            revenue_amount=Decimal("500"),
            source="change",
            locked=True,
        )
    )
    db_session.commit()

    _migration()._link_change_budgets(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    rows = db_session.query(JobBudget).filter(JobBudget.source == "change").all()
    material = {
        r.change_order_id: (Decimal(str(r.amount)), Decimal(str(r.revenue_amount)))
        for r in rows
        if r.cost_type == "material"
    }
    assert material == {
        a.id: (Decimal("1000.00"), Decimal("1100.00")),
        b.id: (Decimal("-400.00"), Decimal("-440.00")),
    }
    labor = [r for r in rows if r.cost_type == "labor"]
    assert len(labor) == 1 and labor[0].change_order_id is None


def test_allowance_return_outside_construction_is_a_409(client):
    """The mode sentence comes before any look at the line."""
    r = client.post(
        "/api/change-orders/allowance-return", json={"allowance_sov_line_id": 1}
    )
    assert r.status_code == 409, r.text
    assert "construction mode" in r.json()["detail"]
