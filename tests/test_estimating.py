"""Construction estimating (#551): the bid math, the at-cost lines, the
accept-locks-the-budget flow, and the convert carve-outs.

The lines stay AT COST; the four markups (general conditions → insurance →
fee → bond) compound in order and sit under the subtotal as their own
figures. The bid total is subtotal + markups, pre-tax; tax covers the lines
and the markups together. Accepting an estimate turns its seeded budget into
the contract: those rows lock, and only a change order (a later issue) moves
them.
"""

import os
import subprocess
import sys
from pathlib import Path

from app.models.invoices import InvoiceLine

ROOT = Path(__file__).resolve().parents[1]


def _set_construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
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


def _lines(est):
    return {line["description"]: line for line in est["lines"]}


def _totals(client, est_id):
    return client.get(f"/api/estimates/{est_id}").json()["totals"]


# ── Lines stay at cost ──────────────────────────────────────────────────────


def test_construction_stores_rate_as_unit_cost(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Base", "quantity": 2, "rate": 0, "unit_cost": 200},
            {
                "description": "Alt",
                "quantity": 1,
                "rate": 999,
                "unit_cost": 150,
                "line_kind": "alternate",
            },
            {"description": "Direct", "quantity": 1, "rate": 75},
        ],
    )
    lines = _lines(est)
    assert float(lines["Base"]["rate"]) == 200.0  # rate mirrors the unit cost
    assert float(lines["Base"]["amount"]) == 400.0  # qty × unit cost
    assert float(lines["Alt"]["rate"]) == 150.0  # an alternate is at cost too
    assert float(lines["Direct"]["rate"]) == 75.0  # no unit cost → priced direct


def test_update_keeps_construction_lines_at_cost(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Base", "quantity": 1, "rate": 0, "unit_cost": 100}],
    )
    r = client.put(
        f"/api/estimates/{est['id']}",
        json={
            "lines": [
                {"description": "Base", "quantity": 1, "rate": 500, "unit_cost": 150}
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert float(_lines(r.json())["Base"]["rate"]) == 150.0


def test_put_round_trips_the_bid_shape_and_totals(client, seed_customer):
    _set_construction(client)
    pkg = client.post("/api/trade-packages", json={"name": "Concrete"}).json()
    lines = [
        {
            "description": "Shell",
            "quantity": 1,
            "rate": 0,
            "unit_cost": 100000,
            "unit": "LS",
            "trade_package_id": pkg["id"],
        },
        {
            "description": "Allowance",
            "quantity": 1,
            "rate": 5000,
            "line_kind": "allowance",
        },
        {
            "description": "Unit",
            "quantity": 1,
            "rate": 85,
            "unit": "CY",
            "line_kind": "unit_price",
        },
        {
            "description": "Alt",
            "quantity": 1,
            "rate": 0,
            "unit_cost": 1000,
            "line_kind": "alternate",
        },
    ]
    est = _estimate(client, seed_customer.id, list(lines), gc_pct=10)
    before = client.get(f"/api/estimates/{est['id']}").json()
    before_bid = float(before["totals"]["bid_total"])

    # PUT the identical lines back: the stored bid shape and totals survive
    r = client.put(f"/api/estimates/{est['id']}", json={"lines": list(lines)})
    assert r.status_code == 200, r.text
    full = client.get(f"/api/estimates/{est['id']}").json()
    ln = _lines(full)
    assert ln["Shell"]["line_kind"] == "base"
    assert ln["Shell"]["trade_package_id"] == pkg["id"]
    assert ln["Unit"]["line_kind"] == "unit_price" and ln["Unit"]["unit"] == "CY"
    assert ln["Allowance"]["line_kind"] == "allowance"
    assert ln["Alt"]["line_kind"] == "alternate"
    assert ln["Alt"]["alternate_accepted"] is False
    t = full["totals"]
    # the stored tax/total foot the response totals
    assert float(full["total"]) == float(t["bid_total"]) + float(t["bid_tax"])
    assert float(full["subtotal"]) == float(t["subtotal"])
    assert float(t["bid_total"]) == before_bid  # identical round-trip

    # a PUT flipping the alternate accepted is stored and moves the bid
    accept_lines = [dict(line) for line in lines]
    accept_lines[3]["alternate_accepted"] = True
    r2 = client.put(f"/api/estimates/{est['id']}", json={"lines": accept_lines})
    assert r2.status_code == 200, r2.text
    after = client.get(f"/api/estimates/{est['id']}").json()
    assert _lines(after)["Alt"]["alternate_accepted"] is True
    assert float(after["totals"]["bid_total"]) > before_bid  # the alt joined scope


def test_put_markup_only_recomputes_the_stored_totals(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Work", "quantity": 1, "rate": 0, "unit_cost": 1000}],
        tax_rate=0.1,
    )
    r = client.put(f"/api/estimates/{est['id']}", json={"gc_pct": 10})
    assert r.status_code == 200, r.text
    full = client.get(f"/api/estimates/{est['id']}").json()
    # 0.10 × (1,000 line + 100 gc) = 110 tax; 1,000 + 100 + 110 = 1,210
    assert float(full["tax_amount"]) == 110.0
    assert float(full["total"]) == 1210.0
    assert float(full["totals"]["bid_tax"]) == 110.0
    assert float(full["totals"]["bid_total"]) == 1100.0


def test_a_negative_unit_cost_is_refused_except_on_an_alternate(client, seed_customer):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-02-01",
            "lines": [
                {"description": "Base", "quantity": 1, "rate": 0, "unit_cost": -500}
            ],
        },
    )
    assert r.status_code == 422, r.text
    # a deduct alternate keeps its negative cost
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Deduct",
                "quantity": 1,
                "rate": -20,
                "unit_cost": -500,
                "line_kind": "alternate",
                "alternate_accepted": True,
            }
        ],
        tax_rate=0,
    )
    assert float(_lines(est)["Deduct"]["unit_cost"]) == -500.0


def test_a_business_company_rate_and_markups_are_untouched(client, seed_customer):
    # the default company is Business: its Cost column is the budget side and
    # the rate is what it charges, so a unit cost does not drive the rate; and
    # the markups are a construction-mode field, so percents an API caller
    # sends are stored 0 and the totals stay plain Subtotal / Tax / Total
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Design", "quantity": 1, "rate": 500, "unit_cost": 200}],
        tax_rate=0.1,
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    assert float(_lines(est)["Design"]["rate"]) == 500.0
    full = client.get(f"/api/estimates/{est['id']}").json()
    assert float(full["gc_pct"]) == 0.0
    assert float(full["insurance_pct"]) == 0.0
    assert float(full["fee_pct"]) == 0.0
    assert float(full["bond_pct"]) == 0.0
    # no markups: total is just subtotal + tax
    assert float(full["total"]) == float(full["subtotal"]) + float(full["tax_amount"])
    assert float(full["total"]) == 550.0


# ── The compounding markups under the subtotal ──────────────────────────────


def test_markups_compound_in_order_to_exact_cents(client, seed_customer):
    _set_construction(client)
    # cost of work 100,000; 8 / 1.5 / 5 / 1 percent, compounding
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    t = _totals(client, est["id"])
    assert float(t["markup_base"]) == 100000.0
    assert float(t["gc"]) == 8000.0
    assert float(t["insurance"]) == 1620.0
    assert float(t["fee"]) == 5481.0
    assert float(t["bond"]) == 1151.01
    assert float(t["markup_total"]) == 16252.01
    assert float(t["subtotal"]) == 100000.0
    assert float(t["bid_total"]) == 116252.01  # subtotal + the four markups


def test_bid_total_is_subtotal_plus_markups_and_total_minus_tax(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}],
        tax_rate=0.07,
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    t = full["totals"]
    assert float(t["bid_total"]) == float(t["subtotal"]) + float(t["markup_total"])
    assert float(t["bid_total"]) == float(full["total"]) - float(full["tax_amount"])
    assert float(t["bid_tax"]) == float(full["tax_amount"])
    assert float(t["bid_total_with_tax"]) == float(full["total"])


def test_tax_covers_the_markups(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}],
        tax_rate=0.07,
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    # 0.07 × (100,000 line + 16,252.01 markups) = 8,137.64, not 0.07 × 100,000
    assert float(full["tax_amount"]) == 8137.64
    assert float(full["total"]) == 116252.01 + 8137.64


def test_an_exempt_customer_pays_no_tax_on_the_markups(client, seed_customer):
    _set_construction(client)
    exempt = client.post(
        "/api/customers", json={"name": "Exempt", "is_taxable": False}
    ).json()
    est = _estimate(
        client,
        exempt["id"],
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}],
        tax_rate=0.07,
        gc_pct=8,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    assert float(full["tax_amount"]) == 0.0
    assert float(full["total"]) == float(full["totals"]["bid_total"])


def test_a_rate_priced_line_with_no_unit_cost_is_priced_direct(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Lump sum", "quantity": 1, "rate": 500}],
        gc_pct=8,
        fee_pct=5,
    )
    t = _totals(client, est["id"])
    assert float(t["markup_base"]) == 0.0  # nothing to mark up
    assert float(t["markup_total"]) == 0.0
    assert float(t["priced_direct"]) == 500.0
    assert float(t["subtotal"]) == 500.0
    assert float(t["bid_total"]) == 500.0


def test_allowances_are_in_the_subtotal_but_carry_no_markup(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000},
            {
                "description": "Appliance allowance",
                "quantity": 1,
                "rate": 5000,
                "line_kind": "allowance",
            },
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    t = _totals(client, est["id"])
    assert float(t["markup_base"]) == 100000.0  # allowance adds no markup base
    assert float(t["markup_total"]) == 16252.01
    assert float(t["allowance_total"]) == 5000.0
    assert float(t["subtotal"]) == 105000.0  # both line amounts
    assert float(t["bid_total"]) == 105000.0 + 16252.01


def test_an_alternate_with_a_cost_shows_its_marked_up_price(client, seed_customer):
    _set_construction(client)
    base = [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}]
    alt = {
        "description": "Upgrade alt",
        "quantity": 1,
        "rate": 999,
        "unit_cost": 1500,
        "line_kind": "alternate",
    }

    pending = _estimate(client, seed_customer.id, base + [dict(alt)], gc_pct=10)
    tp = _totals(client, pending["id"])
    assert float(tp["markup_base"]) == 100000.0  # not accepted → not in the base
    assert tp["alternates"][0]["accepted"] is False
    assert float(tp["alternates"][0]["amount"]) == 1650.0  # 1500 × 1.10 marked up
    pending_bid = float(tp["bid_total"])  # 100,000 + 10,000 gc

    accepted = _estimate(
        client, seed_customer.id, base + [dict(alt, alternate_accepted=True)], gc_pct=10
    )
    ta = _totals(client, accepted["id"])
    assert float(ta["markup_base"]) == 101500.0  # cost folds into the base
    # accepting moves the bid by the marked-up alternate amount, to rounding
    assert abs((float(ta["bid_total"]) - pending_bid) - 1650.0) <= 0.01


def test_unit_price_lines_are_in_no_total(client, seed_customer):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 5000},
            {
                "description": "Rock excavation",
                "quantity": 1,
                "rate": 85,
                "unit": "CY",
                "line_kind": "unit_price",
            },
        ],
    )
    t = _totals(client, est["id"])
    assert float(t["subtotal"]) == 5000.0  # unit price excluded
    assert float(t["bid_total"]) == 5000.0
    assert len(t["unit_prices"]) == 1
    assert t["unit_prices"][0]["unit"] == "CY"
    assert float(t["unit_prices"][0]["rate"]) == 85.0


def test_a_deduct_alternate_subtracts(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Base", "quantity": 1, "rate": 100},
            {
                "description": "Deduct skylight",
                "quantity": 1,
                "rate": -20,
                "line_kind": "alternate",
                "alternate_accepted": True,
            },
        ],
        tax_rate=0,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    assert float(full["totals"]["bid_total"]) == 80.0  # 100 − 20
    assert float(full["subtotal"]) == 80.0  # the stored subtotal foots the bid


def test_by_package_groups_by_id_not_name(client, seed_customer):
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    pkg = client.post(
        "/api/trade-packages", json={"name": "Concrete", "section_ids": [conc["id"]]}
    ).json()
    # a package literally named "Unassigned" must NOT merge with the bucket
    ghost = client.post("/api/trade-packages", json={"name": "Unassigned"}).json()
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Slab",
                "quantity": 100,
                "rate": 60,
                "unit_cost": 50,
                "trade_package_id": pkg["id"],
            },
            {
                "description": "Named-Unassigned work",
                "quantity": 1,
                "rate": 700,
                "unit_cost": 500,
                "trade_package_id": ghost["id"],
            },
            {"description": "No package", "quantity": 1, "rate": 400, "unit_cost": 300},
        ],
    )
    t = _totals(client, est["id"])
    by = {(g["package_id"], g["package_name"]): g for g in t["by_package"]}
    assert (pkg["id"], "Concrete") in by
    assert (ghost["id"], "Unassigned") in by  # the real package
    assert (None, "Unassigned") in by  # the no-package bucket, separate
    assert float(by[(pkg["id"], "Concrete")]["cost"]) == 5000.0
    assert float(by[(pkg["id"], "Concrete")]["price"]) == 6000.0


# ── Price from cost is gone ─────────────────────────────────────────────────


def test_reprice_route_is_gone(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100}],
    )
    assert client.post(f"/api/estimates/{est['id']}/reprice").status_code in (404, 405)


# ── Accept locks the budget ─────────────────────────────────────────────────


def test_accept_seeds_only_bought_scope_and_locks(client, seed_customer):
    job = _job(client, seed_customer.id)
    coded = _section(client, "06 10 00", "Rough Carpentry", "material")
    skipped = _section(client, "26 00 00", "Electrical", "subcontract")
    other = _section(client, "09 90 00", "Painting", "subcontract")
    # a manual budget row on a code the estimate never touches survives
    client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_code_id": other["id"], "amount": 1200}]},
    )
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Framing",
                "quantity": 10,
                "rate": 300,
                "unit_cost": 200,
                "cost_code_id": coded["id"],
            },
            # a unit-price line with a code must NOT seed a budget row
            {
                "description": "Unit",
                "quantity": 1,
                "rate": 5,
                "unit_cost": 4,
                "cost_code_id": skipped["id"],
                "line_kind": "unit_price",
            },
        ],
        job_id=job["id"],
    )
    accepted = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["accepted_at"] is not None

    rows = {
        r["cost_code_id"]: r
        for r in client.get(f"/api/jobs/{job['id']}/budgets").json()
    }
    assert skipped["id"] not in rows  # unit-price line seeded nothing
    assert (
        rows[coded["id"]]["source"] == "estimate"
        and rows[coded["id"]]["locked"] is True
    )
    assert (
        rows[other["id"]]["source"] == "manual" and rows[other["id"]]["locked"] is False
    )
    assert float(rows[other["id"]]["amount"]) == 1200.0


def test_an_allowance_budgets_its_amount_as_cost(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    coded = _section(client, "12 00 00", "Furnishings", "material")
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Appliance allowance",
                "quantity": 1,
                "rate": 5000,
                "cost_code_id": coded["id"],
                "line_kind": "allowance",
            }
        ],
        job_id=job["id"],
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    rows = {
        r["cost_code_id"]: r
        for r in client.get(f"/api/jobs/{job['id']}/budgets").json()
    }
    # the allowance budgets its amount as cost (not a zero-cost row)
    assert float(rows[coded["id"]]["amount"]) == 5000.0


def test_accept_distributes_the_markups_across_budget_revenue(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    a = _section(client, "03 30 00", "Concrete", "material")
    b = _section(client, "04 20 00", "Masonry", "material")
    est = _estimate(
        client,
        seed_customer.id,
        [
            # 60 / 40 split of a 100,000 cost of work, the 60% row first
            {
                "description": "A",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 60000,
                "cost_code_id": a["id"],
            },
            {
                "description": "B",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 40000,
                "cost_code_id": b["id"],
            },
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
        job_id=job["id"],
    )
    bid_total = float(_totals(client, est["id"])["bid_total"])  # 116,252.01
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    rows = {
        r["cost_code_id"]: r
        for r in client.get(f"/api/jobs/{job['id']}/budgets").json()
    }
    rev_a = float(rows[a["id"]]["revenue_amount"])
    rev_b = float(rows[b["id"]]["revenue_amount"])
    # the markups ride each row's revenue pro rata to its markup base; the
    # last row with a base takes the rounding remainder
    assert rev_a == 69751.21
    assert rev_b == 46500.80
    assert round(rev_a + rev_b, 2) == bid_total  # budgeted revenue foots the bid


def test_post_accept_needs_a_job(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
    )
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_job"


def test_accept_refuses_a_second_time(client, seed_customer):
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 409


def test_accept_refuses_another_estimate_while_locked(client, seed_customer):
    job = _job(client, seed_customer.id)
    first = _estimate(
        client,
        seed_customer.id,
        [{"description": "A", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    assert (
        client.post(f"/api/estimates/{first['id']}/accept", json={}).status_code == 200
    )
    second = _estimate(
        client,
        seed_customer.id,
        [{"description": "B", "quantity": 1, "rate": 200, "unit_cost": 150}],
        job_id=job["id"],
    )
    r = client.post(f"/api/estimates/{second['id']}/accept", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "budget_locked"


def test_put_accepted_locks_with_a_job_and_is_idempotent(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    r = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert any(b["locked"] for b in client.get(f"/api/jobs/{job['id']}/budgets").json())
    # re-PUT accepted is a no-op, not a 409
    again = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert again.status_code == 200 and again.json()["status"] == "accepted"


def test_put_accepted_without_a_job_is_a_plain_status_change(client, seed_customer):
    # a business company with no job accepts the estimate, nothing to lock
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100}],
    )
    r = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"


def test_a_settled_estimate_refuses_scope_edits(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    client.post(f"/api/estimates/{est['id']}/accept", json={})
    for edit in (
        {"lines": [{"description": "X", "quantity": 1, "rate": 1}]},
        {"gc_pct": 9},
        {"tax_rate": 0.05},
        {"job_id": _job(client, seed_customer.id, name="Other")["id"]},
    ):
        r = client.put(f"/api/estimates/{est['id']}", json=edit)
        assert r.status_code == 409, (edit, r.text)
    # notes and expiration still edit
    ok = client.put(
        f"/api/estimates/{est['id']}",
        json={"notes": "signed", "expiration_date": "2026-12-31"},
    )
    assert ok.status_code == 200 and ok.json()["notes"] == "signed"


def test_locked_budget_is_row_level(client, seed_customer):
    job = _job(client, seed_customer.id)
    free = _section(client, "09 90 00", "Painting", "subcontract")
    # an uncoded line seeds a whole-job budget row — a key a manual save can
    # also address, so the row-level lock is exercisable (a coded+typed row
    # can't be re-sent: the save schema forbids both keys on one row)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Whole job", "quantity": 1, "rate": 300, "unit_cost": 200}],
        job_id=job["id"],
    )
    client.post(f"/api/estimates/{est['id']}/accept", json={})
    locked = client.get(f"/api/jobs/{job['id']}/budgets").json()
    assert locked[0]["cost_code_id"] is None and locked[0]["locked"] is True

    # a manual row on a specific code still saves after acceptance
    add = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_code_id": free["id"], "amount": 500}]},
    )
    assert add.status_code == 200, add.text
    rows = {r["cost_code_id"]: r for r in add.json()}
    assert float(rows[free["id"]]["amount"]) == 500.0
    assert rows[None]["locked"] is True  # the whole-job estimate row untouched

    # overwriting the LOCKED whole-job row's key is refused
    hit = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"amount": 1}]},
    )
    assert hit.status_code == 409 and hit.json()["detail"]["code"] == "budget_locked"

    # re-seeding from another estimate stays a job-wide 409
    other = _estimate(
        client,
        seed_customer.id,
        [{"description": "Other", "quantity": 1, "rate": 10, "unit_cost": 5}],
        job_id=job["id"],
    )
    seed = client.post(f"/api/jobs/{job['id']}/budgets/from-estimate/{other['id']}")
    assert seed.status_code == 409 and seed.json()["detail"]["code"] == "budget_locked"


# ── Convert carries the cost basis, the markups, and drops what isn't scope ──


def test_convert_excludes_unit_price_and_pending_alternates(
    client, db_session, seed_accounts, seed_customer
):
    coded = _section(client, "26 00 00", "Electrical", "subcontract")
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Base work",
                "quantity": 2,
                "rate": 500,
                "unit_cost": 350,
                "cost_code_id": coded["id"],
            },
            {
                "description": "Accepted alt",
                "quantity": 1,
                "rate": 800,
                "unit_cost": 600,
                "line_kind": "alternate",
                "alternate_accepted": True,
            },
            {
                "description": "Pending alt",
                "quantity": 1,
                "rate": 900,
                "line_kind": "alternate",
            },
            {
                "description": "Unit price",
                "quantity": 1,
                "rate": 45,
                "unit": "LF",
                "line_kind": "unit_price",
            },
        ],
    )
    inv = client.post(f"/api/estimates/{est['id']}/convert")
    assert inv.status_code == 200, inv.text
    descs = [line["description"] for line in inv.json()["lines"]]
    assert descs == ["Base work", "Accepted alt"]  # unit price + pending alt dropped

    base_line = (
        db_session.query(InvoiceLine)
        .filter(InvoiceLine.description == "Base work")
        .one()
    )
    assert base_line.cost_type == "subcontract"
    assert float(base_line.unit_cost) == 350.0


def test_convert_carries_the_markup_lines_and_foots_to_the_bid(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    bid_total = float(_totals(client, est["id"])["bid_total"])
    inv = client.post(f"/api/estimates/{est['id']}/convert")
    assert inv.status_code == 200, inv.text
    body = inv.json()
    descs = [line["description"] for line in body["lines"]]
    assert "General conditions (8%)" in descs
    assert "Insurance (1.5%)" in descs  # percent without trailing zeros
    assert "Fee (5%)" in descs
    assert "Bond (1%)" in descs
    markup = next(
        ln for ln in body["lines"] if ln["description"] == "General conditions (8%)"
    )
    assert float(markup["rate"]) == 8000.0
    # the invoice foots to the pre-tax bid
    assert float(body["subtotal"]) == bid_total == 116252.01
    assert float(body["total"]) == bid_total


def test_convert_puts_the_markup_lines_on_the_job_and_foots_with_tax(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    coded = _section(client, "03 30 00", "Concrete", "material")
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Shell",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 1000,
                "cost_code_id": coded["id"],
                "job_id": job["id"],
            }
        ],
        gc_pct=10,
        tax_rate=0.1,
        job_id=job["id"],
    )
    bid_total = float(_totals(client, est["id"])["bid_total"])  # 1,100
    inv = client.post(f"/api/estimates/{est['id']}/convert")
    assert inv.status_code == 200, inv.text
    body = inv.json()
    # every line, scope and markup, carries the job so income posts under it
    assert all(ln["job_id"] == job["id"] for ln in body["lines"])
    assert "General conditions (10%)" in [ln["description"] for ln in body["lines"]]
    # the invoice foots to the bid plus tax on the lines and the markups
    assert float(body["subtotal"]) == bid_total == 1100.0
    assert float(body["tax_amount"]) == 110.0  # 0.10 × (1,000 + 100)
    assert float(body["total"]) == 1210.0


# ── Migration up/down ───────────────────────────────────────────────────────


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

    up = run("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = run("downgrade", "-1")
    assert down.returncode == 0, down.stderr
    again = run("upgrade", "head")
    assert again.returncode == 0, again.stderr


# ── status lock, allowance markup, GC cost, manual rows ─────


def _budget_snapshot(client, job_id):
    rows = client.get(f"/api/jobs/{job_id}/budgets").json()
    return sorted(
        (r["cost_code_id"], r["amount"], r["revenue_amount"], r["locked"]) for r in rows
    )


def test_a_settled_estimate_refuses_a_status_change(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 1000, "unit_cost": 1000}],
        job_id=job["id"],
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    before = _budget_snapshot(client, job["id"])
    # accepted -> pending (or any other status) would re-open the lock
    for status in ("pending", "rejected", "converted"):
        r = client.put(f"/api/estimates/{est['id']}", json={"status": status})
        assert r.status_code == 409, (status, r.text)
        assert r.json()["detail"].startswith("A settled estimate can't be")
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "accepted"
    assert _budget_snapshot(client, job["id"]) == before
    # re-setting the same status stays a no-op
    assert (
        client.put(
            f"/api/estimates/{est['id']}", json={"status": "accepted"}
        ).status_code
        == 200
    )


def test_a_converted_estimate_refuses_a_status_change(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 100}],
        job_id=job["id"],
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    assert client.post(f"/api/estimates/{est['id']}/convert").status_code == 200
    for status in ("pending", "accepted"):
        r = client.put(f"/api/estimates/{est['id']}", json={"status": status})
        assert r.status_code == 409, (status, r.text)
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "converted"


def _allowance_estimate(client, customer_id, **extra):
    return _estimate(
        client,
        customer_id,
        [
            {"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 10000},
            {
                "description": "Appliances",
                "quantity": 1,
                "rate": 2000,
                "line_kind": "allowance",
            },
        ],
        gc_pct=10,
        **extra,
    )


def test_markup_on_allowances_is_off_by_default(client, seed_customer):
    _set_construction(client)
    est = _allowance_estimate(client, seed_customer.id)
    assert est["markup_allowances"] is False
    t = _totals(client, est["id"])
    assert t["markup_allowances"] is False
    assert float(t["markup_base"]) == 10000.0
    assert float(t["gc"]) == 1000.0
    assert float(t["bid_total"]) == 13000.0  # 12,000 lines + 1,000 GC


def test_markup_on_allowances_when_on(client, seed_customer):
    _set_construction(client)
    est = _allowance_estimate(client, seed_customer.id, markup_allowances=True)
    assert est["markup_allowances"] is True
    t = _totals(client, est["id"])
    assert t["markup_allowances"] is True
    assert float(t["markup_base"]) == 12000.0
    assert float(t["gc"]) == 1200.0
    assert float(t["bid_total"]) == 13200.0
    # the stored total agrees with the totals (no tax here)
    assert float(est["total"]) == 13200.0
    # flipping it off on a pending estimate re-totals without resending lines
    r = client.put(f"/api/estimates/{est['id']}", json={"markup_allowances": False})
    assert r.status_code == 200, r.text
    assert float(r.json()["total"]) == 13000.0


def test_markup_on_allowances_is_frozen_once_settled(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est = _allowance_estimate(client, seed_customer.id, job_id=job["id"])
    client.post(f"/api/estimates/{est['id']}/accept", json={})
    r = client.put(f"/api/estimates/{est['id']}", json={"markup_allowances": True})
    assert r.status_code == 409


def test_markup_on_allowances_is_ignored_outside_construction(client, seed_customer):
    est = _allowance_estimate(client, seed_customer.id, markup_allowances=True)
    assert est["markup_allowances"] is False


def test_allowance_markup_reaches_the_budget_revenue(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    a = _section(client, "03 30 00", "Concrete", "material")
    b = _section(client, "12 00 00", "Furnishings", "material")
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-02-01",
            "job_id": job["id"],
            "gc_pct": 10,
            "markup_allowances": True,
            "lines": [
                {
                    "description": "Shell",
                    "quantity": 1,
                    "rate": 0,
                    "unit_cost": 10000,
                    "cost_code_id": a["id"],
                },
                {
                    "description": "Appliances",
                    "quantity": 1,
                    "rate": 2000,
                    "line_kind": "allowance",
                    "cost_code_id": b["id"],
                },
            ],
        },
    ).json()
    client.post(f"/api/estimates/{est['id']}/accept", json={})
    rows = {
        r["cost_code_id"]: r
        for r in client.get(f"/api/jobs/{job['id']}/budgets").json()
    }
    # the 1,200 GC splits 10,000 : 2,000 -> 1,000 and 200
    assert float(rows[a["id"]]["revenue_amount"]) == 11000.0
    assert float(rows[b["id"]]["revenue_amount"]) == 2200.0


def test_accept_budgets_general_conditions_as_a_cost(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    a = _section(client, "03 30 00", "Concrete", "material")
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Shell",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 10000,
                "cost_code_id": a["id"],
            }
        ],
        gc_pct=8,
        insurance_pct=2,
        job_id=job["id"],
    )
    bid_total = float(_totals(client, est["id"])["bid_total"])
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    rows = client.get(f"/api/jobs/{job['id']}/budgets").json()
    gc_rows = [r for r in rows if r["cost_code_id"] != a["id"]]
    # the GC and, now, the insurance premium are costs on codes of their own
    assert len(gc_rows) == 2
    gc = next(r for r in gc_rows if r["cost_code_label"].startswith("GC "))
    ins = next(r for r in gc_rows if r["cost_code_label"].startswith("INS "))
    assert float(ins["amount"]) == 216.0  # 2% of 10,000 + 800
    assert float(ins["revenue_amount"]) == 0.0
    assert float(gc["amount"]) == 800.0  # the GC amount, as cost
    assert float(gc["revenue_amount"]) == 0.0  # its revenue rides the code rows
    assert gc["locked"] is True
    coded = next(r for r in rows if r["cost_code_id"] == a["id"])
    assert float(coded["amount"]) == 10000.0
    # revenue still foots the bid exactly once: the GC is not double-counted
    assert sum(float(r["revenue_amount"]) for r in rows) == bid_total
    # no GC percent, no GC row
    est2 = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Shell",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 10000,
                "cost_code_id": a["id"],
            }
        ],
        fee_pct=5,
        job_id=_job(client, seed_customer.id, name="Other")["id"],
    )
    client.post(f"/api/estimates/{est2['id']}/accept", json={})
    rows2 = client.get(f"/api/jobs/{est2['job_id']}/budgets").json()
    assert [r["cost_code_id"] for r in rows2] == [a["id"]]


def test_a_manual_budget_codes_markup_share_is_not_redistributed(client, seed_customer):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    a = _section(client, "03 30 00", "Concrete", "material")
    b = _section(client, "04 20 00", "Masonry", "material")
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "A",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 60000,
                "cost_code_id": a["id"],
            },
            {
                "description": "B",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 40000,
                "cost_code_id": b["id"],
            },
        ],
        fee_pct=10,
        job_id=job["id"],
    )
    # the operator budgets Masonry by hand before the estimate is accepted
    assert (
        client.put(
            f"/api/jobs/{job['id']}/budgets",
            json={
                "rows": [
                    {"cost_code_id": b["id"], "amount": 41000, "revenue_amount": 45000}
                ]
            },
        ).status_code
        == 200
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    rows = {
        r["cost_code_id"]: r
        for r in client.get(f"/api/jobs/{job['id']}/budgets").json()
    }
    # Concrete keeps only its own 60% of the 10,000 fee, not Masonry's 4,000
    assert float(rows[a["id"]]["revenue_amount"]) == 66000.0
    assert float(rows[b["id"]]["revenue_amount"]) == 45000.0  # manual row untouched
    assert rows[b["id"]]["locked"] is False


def _form_payload(client, est_id):
    """What the estimate form sends on save: every field, resent as stored."""
    e = client.get(f"/api/estimates/{est_id}").json()
    keys = (
        "item_id description quantity rate is_taxable cost_code_id cost_type "
        "unit_cost trade_package_id line_kind unit alternate_accepted line_order"
    ).split()
    return {
        "customer_id": e["customer_id"],
        "date": e["date"],
        "expiration_date": e["expiration_date"],
        "tax_rate": float(e["tax_rate"]),
        "notes": e["notes"],
        "job_id": e["job_id"],
        "gc_pct": float(e["gc_pct"]),
        "insurance_pct": float(e["insurance_pct"]),
        "fee_pct": float(e["fee_pct"]),
        "bond_pct": float(e["bond_pct"]),
        "markup_allowances": e["markup_allowances"],
        "lines": [{k: ln[k] for k in keys} for ln in e["lines"]],
    }


def test_a_settled_estimate_takes_a_notes_only_save_from_the_form(
    client, seed_customer
):
    _set_construction(client)
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Shell", "quantity": 2, "rate": 80, "unit_cost": 80},
            {"description": "Fence", "quantity": 1, "rate": 50},
        ],
        job_id=job["id"],
        gc_pct=5,
        tax_rate=0.0825,
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    body = _form_payload(client, est["id"])
    body["notes"] = "signed by the owner"
    r = client.put(f"/api/estimates/{est['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "signed by the owner"
    # the same full payload with one line changed is still refused
    body["lines"][0]["quantity"] = 3
    assert client.put(f"/api/estimates/{est['id']}", json=body).status_code == 409
    body = _form_payload(client, est["id"])
    body["gc_pct"] = 6
    assert client.put(f"/api/estimates/{est['id']}", json=body).status_code == 409


def _business_or_nonprofit_settled(client, seed_customer, company_type):
    if company_type != "business":
        r = client.put("/api/settings", json={"company_type": company_type})
        assert r.status_code == 200, r.text
    job = _job(client, seed_customer.id, name=f"Job {company_type}")
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100}],
        job_id=job["id"],
    )
    r = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert r.status_code == 200, r.text
    return est


def test_business_and_nonprofit_estimates_are_not_frozen_when_accepted(
    client, seed_customer
):
    for company_type in ("business", "nonprofit"):
        est = _business_or_nonprofit_settled(client, seed_customer, company_type)
        url = f"/api/estimates/{est['id']}"
        r = client.put(
            url, json={"lines": [{"description": "Other", "quantity": 2, "rate": 7}]}
        )
        assert r.status_code == 200, (company_type, r.text)
        r = client.put(url, json={"status": "pending"})
        assert r.status_code == 200, (company_type, r.text)
        assert r.json()["status"] == "pending"


def test_an_estimate_line_too_large_for_the_column_is_a_422(client, seed_customer):
    """99,999,999 x 999,999 and 9,999,999 x 9,999,999 (figures that fit their
    columns, products that do not) are each a 422 sentence,
    never a 500 or an estimate whose subtotal is a float error."""
    for qty, rate in ((99_999_999, 999_999), (9_999_999, 9_999_999)):
        r = client.post(
            "/api/estimates",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-02-01",
                "lines": [{"description": "x", "quantity": qty, "rate": rate}],
            },
        )
        assert r.status_code == 422, r.text
        assert "more than a line can be" in r.text


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


def test_a_half_cent_deduct_alternate_totals_97_99_on_the_server(client, seed_customer):
    # #783: the estimate form showed $98.00 for these lines while the saved
    # estimate and its PDF said $97.99; the form now rounds as _q does. This
    # pins the server's side of the same figure
    # (tests/js/estimate_form_rounding_783.test.cjs pins the form's).
    _set_construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Base", "quantity": 1, "rate": 0, "unit_cost": 100},
            {
                "description": "Deduct",
                "quantity": 1,
                "rate": 0,
                "unit_cost": -2.005,
                "line_kind": "alternate",
                "alternate_accepted": True,
            },
        ],
    )
    assert float(_lines(est)["Deduct"]["amount"]) == -2.01
    assert float(est["total"]) == 97.99
    totals = _totals(client, est["id"])
    assert float(totals["subtotal"]) == 97.99
    assert float(totals["bid_total"]) == 97.99
