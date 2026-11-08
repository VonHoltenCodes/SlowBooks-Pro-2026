"""The contract on the job (#552): the three contract types and their terms,
the summary math, and the owner's schedule of values built from an accepted
estimate.

A construction job can carry a lump-sum, GMP or cost-plus contract, with a
fee, a cap, contingency and retainage. The SOV breaks the contract sum into
the lines a pay application bills against, and it must balance to the sum —
the summary's sov_balance shows the difference until it does. The contract
sum is PRE-TAX (tax is added on each invoice, #555). Later issues move the
contract (change orders, #554) and bill it (progress billing, #555); this one
sets it and builds the schedule.
"""

import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.estimates import Estimate
from app.models.jobs import SovLine
from app.services.accounting import create_journal_entry
from app.services.contracts import sov_from_estimate
from app.services.estimating import estimate_totals

ROOT = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _package(client, name, section_ids):
    return client.post(
        "/api/trade-packages", json={"name": name, "section_ids": section_ids}
    ).json()


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-02-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _income_expense(db):
    income = (
        db.query(Account).filter(Account.account_type == AccountType.INCOME).first()
    )
    expense = (
        db.query(Account).filter(Account.account_type == AccountType.EXPENSE).first()
    )
    assert income and expense
    return income, expense


def _post_cost(db, job_id, cost=Decimal("0"), revenue=Decimal("0")):
    """A posted journal entry carrying the job: a cost (expense debit) and a
    revenue (income credit), each balanced by a "No job" line so the entry
    nets to zero and profitability still ties to the P&L."""
    income, expense = _income_expense(db)
    create_journal_entry(
        db,
        date(2026, 7, 1),
        "job activity",
        [
            {"account_id": expense.id, "debit": cost, "credit": 0, "job_id": job_id},
            {"account_id": income.id, "debit": 0, "credit": revenue, "job_id": job_id},
            {"account_id": income.id, "debit": revenue, "credit": 0},
            {"account_id": expense.id, "debit": 0, "credit": cost},
        ],
        source_type="manual",
    )
    db.commit()


# ── PUT contract terms ────────────────────────────────────────────────────


def test_put_each_contract_type(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 500000},
    )
    assert r.status_code == 200, r.text
    c = r.json()["contract"]
    assert c["contract_type"] == "lump_sum"
    assert c["contract_amount"] == 500000.0
    assert c["original_contract_amount"] == 500000.0  # stamped on the first set
    assert c["fee"] == 0.0  # lump sum earns no separate fee

    job2 = _job(client, seed_customer.id, name="CP")
    r = client.put(
        f"/api/jobs/{job2['id']}/contract",
        json={"contract_type": "cost-plus", "contract_amount": 100, "fee_pct": 10},
    )
    assert r.status_code == 200, r.text
    assert r.json()["contract"]["contract_type"] == "cost_plus"


def test_gmp_defaults_the_cap_to_the_contract_amount(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "gmp", "contract_amount": 750000},
    )
    assert r.status_code == 200, r.text
    c = r.json()["contract"]
    assert c["cap_amount"] == 750000.0  # the guaranteed maximum defaults to the sum

    # a cap given explicitly is kept
    job2 = _job(client, seed_customer.id, name="GMP2")
    r = client.put(
        f"/api/jobs/{job2['id']}/contract",
        json={"contract_type": "gmp", "contract_amount": 800000, "cap_amount": 820000},
    )
    assert r.json()["contract"]["cap_amount"] == 820000.0

    # a GMP with no contract amount to default from is refused
    job3 = _job(client, seed_customer.id, name="GMP3")
    r = client.put(f"/api/jobs/{job3['id']}/contract", json={"contract_type": "gmp"})
    assert r.status_code == 422


def test_original_is_set_once_and_not_overwritten(client, seed_customer):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 100000},
    )
    # before billing or a change order the sum is just an edit, and the
    # original follows it (see the lock tests below for the locked case)
    r = client.put(f"/api/jobs/{job['id']}/contract", json={"contract_amount": 125000})
    assert r.status_code == 200
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["contract_amount"] == 125000.0
    assert c["original_contract_amount"] == 125000.0
    assert c["changes"] == 0.0


def test_the_job_form_amount_then_a_type_keeps_the_original(client, seed_customer):
    # the plain job-form contract amount is set first, with no type → the
    # original is not stamped yet (there is no contract)
    job = _job(client, seed_customer.id)
    client.put(f"/api/jobs/{job['id']}", json={"contract_amount": 100000})
    assert (
        client.get(f"/api/jobs/{job['id']}").json()["contract"][
            "original_contract_amount"
        ]
        is None
    )
    # choosing a type stamps the original from the amount already on the job
    client.put(f"/api/jobs/{job['id']}/contract", json={"contract_type": "lump_sum"})
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["original_contract_amount"] == 100000.0
    assert c["contract_amount"] == 100000.0


def test_validation_422s(client, seed_customer):
    job = _job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    assert client.put(base, json={"contract_type": "fixed_bid"}).status_code == 422
    assert client.put(base, json={"contract_amount": -5}).status_code == 422
    assert client.put(base, json={"retainage_pct": 150}).status_code == 422
    assert client.put(base, json={"fee_amount": -1}).status_code == 422


def test_fee_pct_is_bounded_0_to_100_and_amounts_are_cent_rounded(
    client, seed_customer
):
    job = _job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    # 99999 used to pass and 500 on Postgres (Numeric(7,4))
    assert client.put(base, json={"fee_pct": 99999}).status_code == 422
    assert client.put(base, json={"fee_pct": 100.01}).status_code == 422
    assert client.put(base, json={"fee_pct": -0.01}).status_code == 422
    assert client.put(base, json={"fee_pct": 0}).status_code == 200
    r = client.put(
        base,
        json={
            "fee_pct": 100,
            "contract_type": "cost_plus",
            "contract_amount": 1000.005,
        },
    )
    assert r.status_code == 200, r.text
    c = r.json()["contract"]
    assert c["fee_pct"] == 100.0
    assert c["contract_amount"] == 1000.01  # money goes through _q (half-up)
    assert c["original_contract_amount"] == 1000.01


# ── The contract sum and contingency lock at the first billing / CO ─────


@pytest.fixture
def billing_started(monkeypatch):
    """Stand in for the first billing against the SOV (#576) or a change order
    (#575), neither of which exists on this branch yet."""

    def start():
        monkeypatch.setattr("app.routes.jobs.contract_is_locked", lambda db, job: True)

    return start


def test_contract_sum_edits_freely_until_billing(client, seed_customer):
    job = _job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    assert (
        client.put(base, json={"contract_type": "lump_sum", "contract_amount": 100000})
    ).status_code == 200
    assert client.put(base, json={"contract_amount": 110000}).status_code == 200
    r = client.put(f"/api/jobs/{job['id']}", json={"contract_amount": 105000})
    assert r.status_code == 200
    assert client.put(base, json={"contract_amount": None}).status_code == 200


def test_building_does_not_lock_the_sum_and_a_bid_correction_balances(
    client, seed_customer
):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id, contract_amount=40000)
    _accept(client, est["id"], job["id"])
    c = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}").json()[
        "contract"
    ]
    assert c["contract_amount"] == 40000.0 and c["sov_balance"] != 0.0
    bid = client.get(f"/api/estimates/{est['id']}").json()["totals"]["bid_total"]
    r = client.put(f"/api/jobs/{job['id']}/contract", json={"contract_amount": bid})
    assert r.status_code == 200, r.text
    c = r.json()["contract"]
    assert c["sov_balance"] == 0.0
    assert c["original_contract_amount"] == float(bid) and c["changes"] == 0.0
    # contingency set after the build is an edit too: line and sum move together
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contingency_amount": 5000, "contract_amount": float(bid) + 5000},
    )
    assert r.status_code == 200 and r.json()["contract"]["sov_balance"] == 0.0


def test_sum_and_contingency_are_locked_once_billing_starts(
    client, seed_customer, billing_started
):
    job = _job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    assert (
        client.put(base, json={"contract_type": "lump_sum", "contract_amount": 100000})
    ).status_code == 200
    billing_started()
    r = client.put(base, json={"contract_amount": 110000})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "contract_sum_locked"
    assert client.put(base, json={"contract_amount": None}).status_code == 409
    assert (
        client.put(f"/api/jobs/{job['id']}", json={"contract_amount": 1}).status_code
        == 409
    )
    r = client.put(base, json={"contingency_amount": 5000})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "contingency_locked"
    # re-sending the same sum, and changing other terms, is fine
    assert client.put(base, json={"contract_amount": 100000}).status_code == 200
    assert client.put(base, json={"retainage_pct": 10}).status_code == 200
    assert client.put(base, json={"contingency_amount": None}).status_code == 200
    c = client.get(base).json()["contract"]
    assert c["contract_amount"] == 100000.0 and c["changes"] == 0.0


def test_money_fields_are_bounded_to_numeric_15_2(client, seed_customer):
    job = _job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    for field in ("contract_amount", "fee_amount", "cap_amount", "contingency_amount"):
        assert client.put(base, json={field: 1e16}).status_code == 422, field
    assert client.put(base, json={"fee_amount": 9999999999999.99}).status_code == 200
    assert (
        client.put(f"/api/jobs/{job['id']}", json={"contract_amount": 1e16}).status_code
        == 422
    )
    bad = {"lines": [{"kind": "work", "scheduled_value": 1e16}]}
    assert client.put(f"/api/jobs/{job['id']}/sov", json=bad).status_code == 422


def test_put_sov_quantizes_three_decimal_values(client, seed_customer):
    job = _job(client, seed_customer.id)
    client.put(f"/api/jobs/{job['id']}/contract", json={"contract_amount": 100})
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"kind": "work", "scheduled_value": 33.335},
                {"kind": "work", "scheduled_value": 66.665},
            ]
        },
    )
    assert r.status_code == 200
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert [float(x["scheduled_value"]) for x in sov] == [33.34, 66.67]
    # the balance sees what was stored, so the total's 0.01 over shows honestly
    assert r.json()["sov_total"] == 100.01 and r.json()["sov_balance"] == -0.01


# ── Summary math ────────────────────────────────────────────────────────


def test_cost_plus_fee_on_a_posted_cost(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "cost_plus", "contract_amount": 0, "fee_pct": 10},
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"), revenue=Decimal("30000"))
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["cost_to_date"] == 40000.0
    assert c["fee"] == 4000.0  # 10% of actual cost
    assert c["billed_to_date"] == 30000.0


def test_a_fixed_fee_wins_over_the_percent(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "cost_plus",
            "contract_amount": 0,
            "fee_pct": 10,
            "fee_amount": 7500,
        },
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"))
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["fee"] == 7500.0


def test_lump_sum_earns_no_fee_even_with_a_percent(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 50000, "fee_pct": 10},
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"))
    assert (
        client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]["fee"] == 0.0
    )


def test_cap_and_contingency_remaining(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "gmp",
            "contract_amount": 500000,
            "fee_pct": 5,
            "contingency_amount": 20000,
        },
    )
    _post_cost(db_session, job["id"], cost=Decimal("100000"))
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["cap_amount"] == 500000.0
    # cap_remaining = cap − (cost + fee) = 500000 − (100000 + 5000)
    assert c["cap_remaining"] == 395000.0
    assert c["contingency_amount"] == 20000.0
    assert c["contingency_used"] == 0.0
    assert c["contingency_remaining"] == 20000.0


# ── SOV from an accepted estimate ───────────────────────────────────────


def _build_estimate_with_shape(client, seed_customer):
    """Two packages, a direct-priced unassigned line (no unit cost), an
    allowance, an excluded and an accepted alternate — and real markups (gc 8%,
    fee 5%). The lines stay at cost; the markups are their own SOV lines, so
    accepting builds a true cost-plus-markup schedule, not a trivial one."""
    # Markups are a construction-mode field: a business-mode estimate stores 0.
    client.put("/api/settings", json={"company_type": "construction"})
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    elec = _section(client, "26 05 00", "Electrical")
    pkg_c = _package(client, "Concrete", [conc["id"]])
    pkg_e = _package(client, "Electrical", [elec["id"]])
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Slab",
                "quantity": 1,
                "rate": 10000,
                "unit_cost": 10000,
                "trade_package_id": pkg_c["id"],
            },
            {
                "description": "Service",
                "quantity": 1,
                "rate": 20000,
                "unit_cost": 20000,
                "trade_package_id": pkg_e["id"],
            },
            # a flat (direct) price with no unit cost — never marked up
            {"description": "General", "quantity": 1, "rate": 5000},
            {
                "description": "Flooring allowance",
                "quantity": 1,
                "rate": 3000,
                "line_kind": "allowance",
            },
            {
                "description": "Alt roof (declined)",
                "quantity": 1,
                "rate": 2000,
                "unit_cost": 2000,
                "line_kind": "alternate",
            },
            {
                "description": "Alt concrete finish",
                "quantity": 1,
                "rate": 4000,
                "unit_cost": 4000,
                "line_kind": "alternate",
                "alternate_accepted": True,
                "trade_package_id": pkg_c["id"],
            },
        ],
        gc_pct=8,
        fee_pct=5,
    )
    return est, pkg_c, pkg_e


def _accept(client, est_id, job_id):
    r = client.post(f"/api/estimates/{est_id}/accept", json={"job_id": job_id})
    assert r.status_code == 200, r.text


def test_from_estimate_builds_and_balances(client, db_session, seed_customer):
    est, pkg_c, pkg_e = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    _accept(client, est["id"], job["id"])

    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    sov = body["sov"]
    work = [line for line in sov if line["kind"] == "work"]
    allow = [line for line in sov if line["kind"] == "allowance"]
    # one work line per package in order (Concrete, Electrical), then the
    # line with no package under its own description
    assert [line["description"] for line in work] == [
        "Concrete",
        "Electrical",
        "General",
    ]
    by_desc = {line["description"]: float(line["scheduled_value"]) for line in work}
    # the work lines carry cost now — no markup rides the line
    assert by_desc["Concrete"] == 14000.0  # slab 10,000 + accepted alt 4,000
    assert by_desc["Electrical"] == 20000.0
    assert by_desc["General"] == 5000.0  # the direct-priced line
    # the declined alternate is in no line
    assert all("declined" not in line["description"].lower() for line in sov)
    assert len(allow) == 1 and float(allow[0]["scheduled_value"]) == 3000.0

    # the non-zero markups are their own lines, in order; insurance/bond are 0
    gc = [line for line in sov if line["kind"] == "general_conditions"]
    fee = [line for line in sov if line["kind"] == "fee"]
    assert len(gc) == 1 and float(gc[0]["scheduled_value"]) == 2720.0  # 8% of 34,000
    assert gc[0]["description"] == "General conditions (8%)"
    assert len(fee) == 1 and float(fee[0]["scheduled_value"]) == 1836.0  # 5% of 36,720
    assert fee[0]["description"] == "Fee (5%)"
    assert not [line for line in sov if line["kind"] in ("insurance", "bond")]

    assert [line["line_no"] for line in sov] == list(range(1, len(sov) + 1))
    assert sov[0]["item_no"] == "01"

    c = body["contract"]
    totals = estimate_totals(db_session.get(Estimate, est["id"]))
    bid_total = float(totals["bid_total"])
    assert bid_total == 46556.0  # 42,000 subtotal + 4,556 markups
    # work + allowance + markup lines foot exactly to the pre-tax bid
    assert sum(float(line["scheduled_value"]) for line in sov) == bid_total
    assert c["contract_type"] == "lump_sum"  # defaulted
    assert c["contract_amount"] == bid_total  # pre-tax bid
    assert c["sov_total"] == bid_total
    assert c["sov_balance"] == 0.0  # balances when the sum came from the estimate
    assert c["original_contract_amount"] == bid_total
    assert c["contract_estimate_id"] == est["id"]


def test_from_estimate_with_a_direct_priced_bid_balances(client, seed_customer):
    # a bid of only direct (flat) prices — no unit cost anywhere — still foots
    conc = _section(client, "03 30 00", "Concrete")
    pkg_c = _package(client, "Concrete", [conc["id"]])
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Slab",
                "quantity": 1,
                "rate": 12345.67,
                "trade_package_id": pkg_c["id"],
            },
            {"description": "General", "quantity": 1, "rate": 8000},
            {
                "description": "Allowance",
                "quantity": 1,
                "rate": 2500,
                "line_kind": "allowance",
            },
        ],
    )
    job = _job(client, seed_customer.id)
    client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]})
    c = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}").json()[
        "contract"
    ]
    assert c["sov_balance"] == 0.0


def test_from_estimate_folds_contingency_into_the_sum_and_balances(
    client, seed_customer
):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id, name="WithContingency")
    client.put(f"/api/jobs/{job['id']}/contract", json={"contingency_amount": 6000})
    _accept(client, est["id"], job["id"])
    body = client.post(
        f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}"
    ).json()
    cont = [line for line in body["sov"] if line["kind"] == "contingency"]
    assert len(cont) == 1 and float(cont[0]["scheduled_value"]) == 6000.0
    c = body["contract"]
    bid_total = client.get(f"/api/estimates/{est['id']}").json()["totals"]["bid_total"]
    # the contingency is carried inside the contract sum, so it balances to 0
    assert c["contract_amount"] == float(bid_total) + 6000.0
    assert c["sov_balance"] == 0.0


def test_from_estimate_does_not_overwrite_an_existing_sum_and_is_repeatable(
    client, seed_customer
):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    _accept(client, est["id"], job["id"])
    # the operator set the sum by hand first
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "gmp", "contract_amount": 999999},
    )
    first = client.post(
        f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}"
    ).json()
    assert first["contract"]["contract_amount"] == 999999.0  # not overwritten
    first_lines = len(first["sov"])
    # building again just replaces the schedule — no duplication
    second = client.post(
        f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}"
    ).json()
    assert len(second["sov"]) == first_lines
    assert second["contract"]["contract_amount"] == 999999.0


def test_from_estimate_409_when_not_accepted_or_foreign(client, seed_customer):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    # not accepted yet
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "estimate_not_accepted"

    # accepted onto another job → mismatch for this one
    other = _job(client, seed_customer.id, name="Other")
    client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": other["id"]})
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "estimate_job_mismatch"


def test_sov_from_estimate_is_pure(client, db_session, seed_customer):
    """The pure builder returns the line dicts without touching the DB."""
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    estimate = db_session.get(Estimate, est["id"])
    lines = sov_from_estimate(estimate)
    # work ×3, the allowance, then the two non-zero markups (gc, fee)
    assert [line["kind"] for line in lines] == [
        "work",
        "work",
        "work",
        "allowance",
        "general_conditions",
        "fee",
    ]
    assert db_session.query(SovLine).count() == 0  # nothing written


# ── PUT /sov ────────────────────────────────────────────────────────────


def test_put_sov_renumbers_and_defaults_item_no(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Sitework", "kind": "work", "scheduled_value": 10000},
                {
                    "item_no": "A",
                    "description": "Allowance",
                    "kind": "allowance",
                    "scheduled_value": 2000,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["sov_total"] == 12000.0
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert [line["line_no"] for line in sov] == [1, 2]
    assert sov[0]["item_no"] == "01"  # blank defaults to zero-padded line_no
    assert sov[1]["item_no"] == "A"  # a given item_no is kept


def test_put_sov_keeps_a_deduct_alternate_negative(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Base scope", "kind": "work", "scheduled_value": 50000},
                {
                    "description": "Alt: smaller lobby (deduct)",
                    "kind": "work",
                    "scheduled_value": -5000,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["sov_total"] == 45000.0  # the deduct nets down the schedule
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert float(sov[1]["scheduled_value"]) == -5000.0


def test_fee_and_general_conditions_lines_count_in_the_total(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Work", "kind": "work", "scheduled_value": 80000},
                {
                    "description": "GCs",
                    "kind": "general_conditions",
                    "scheduled_value": 12000,
                },
                {"description": "Fee", "kind": "fee", "scheduled_value": 8000},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["sov_total"] == 100000.0


def test_put_sov_refuses_a_bad_kind(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{"description": "Bad kind", "kind": "profit"}]},
    )
    assert r.status_code == 422


def test_put_sov_accepts_insurance_and_bond_kinds(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Work", "kind": "work", "scheduled_value": 80000},
                {
                    "description": "Insurance",
                    "kind": "insurance",
                    "scheduled_value": 5000,
                },
                {"description": "Bond", "kind": "bond", "scheduled_value": 3000},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["sov_total"] == 88000.0


def test_changing_the_sum_with_a_schedule_reports_the_balance(client, seed_customer):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [{"description": "Work", "kind": "work", "scheduled_value": 10000}]
        },
    )
    # raising the contract sum above the SOV surfaces the gap as the signal
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 12000},
    )
    assert r.json()["contract"]["sov_balance"] == 2000.0


# ── Contingency line kept in step (#552) ────────────────────────────────


def test_set_contract_syncs_the_contingency_sov_line(client, seed_customer):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [{"description": "Work", "kind": "work", "scheduled_value": 10000}]
        },
    )
    # setting a contingency adds a contingency line
    client.put(f"/api/jobs/{job['id']}/contract", json={"contingency_amount": 5000})
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    cont = [line for line in sov if line["kind"] == "contingency"]
    assert len(cont) == 1 and float(cont[0]["scheduled_value"]) == 5000.0
    # changing it rewrites the line's value
    client.put(f"/api/jobs/{job['id']}/contract", json={"contingency_amount": 8000})
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    cont = [line for line in sov if line["kind"] == "contingency"]
    assert len(cont) == 1 and float(cont[0]["scheduled_value"]) == 8000.0
    # clearing it removes the line and leaves no gap in the numbering
    client.put(f"/api/jobs/{job['id']}/contract", json={"contingency_amount": None})
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert [line["kind"] for line in sov] == ["work"]
    assert [line["line_no"] for line in sov] == [1]


# ── Detail response + report keys + migration ───────────────────────────


def test_detail_response_carries_contract(client, seed_customer):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 250000},
    )
    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["contract"]["contract_type"] == "lump_sum"
    assert detail["contract"]["contract_amount"] == 250000.0


def test_business_mode_job_detail_carries_null_contract(client, seed_customer):
    # the default company is business mode; a job with no contract terms still
    # answers with the additive contract block, its terms null
    job = _job(client, seed_customer.id)
    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert "contract" in detail
    assert detail["contract"]["contract_type"] is None
    assert detail["contract"]["contract_amount"] is None


def test_a_fixed_fee_has_no_period_fee(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "cost_plus", "contract_amount": 0, "fee_amount": 9000},
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"), revenue=Decimal("30000"))
    from app.services.jobs_service import job_profitability

    rows = job_profitability(db_session, job_ids=[job["id"]], include_no_job=False)
    row = next(r for r in rows if r["job_id"] == job["id"])
    assert row["contract_type"] == "cost_plus"
    assert "fee_period" not in row  # the fixed fee is a job figure, not a period one


def test_job_profitability_rows_gain_contract_type_and_period_fee(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "cost_plus", "contract_amount": 0, "fee_pct": 10},
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"), revenue=Decimal("30000"))
    from app.services.jobs_service import job_profitability

    rows = job_profitability(db_session, job_ids=[job["id"]], include_no_job=False)
    row = next(r for r in rows if r["job_id"] == job["id"])
    assert row["contract_type"] == "cost_plus"
    assert row["fee_period"] == 4000.0  # fee on the period's cost


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


# ── Building from the estimate: original, fee %, status hole ────────────


def test_build_stamps_the_original_when_the_job_form_set_the_sum_first(
    client, seed_customer
):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    # the job form's amount, off from the bid, with no type yet
    client.put(f"/api/jobs/{job['id']}", json={"contract_amount": 40000})
    _accept(client, est["id"], job["id"])
    c = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}").json()[
        "contract"
    ]
    assert c["contract_type"] == "lump_sum"
    assert c["contract_amount"] == 40000.0  # an existing sum is left alone
    assert c["original_contract_amount"] == 40000.0  # but the original is stamped
    assert c["changes"] == 0.0  # the gap to the SOV shows as sov_balance
    assert c["sov_balance"] == 40000.0 - 46556.0


def test_rebuild_does_not_give_a_cleared_sum_a_new_original(
    client, db_session, seed_customer
):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    _accept(client, est["id"], job["id"])
    url = f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}"
    client.post(url)
    # clear the sum behind the API's back (legacy data), original stays
    from app.models.jobs import Job

    row = db_session.get(Job, job["id"])
    row.contract_amount = None
    db_session.commit()
    c = client.post(url).json()["contract"]
    assert c["original_contract_amount"] == 46556.0
    assert c["contract_amount"] == 46556.0  # back to the original, no new one
    assert c["changes"] == 0.0


def test_build_defaults_the_job_fee_pct_from_the_estimate_only_when_unset(
    client, seed_customer
):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)  # fee_pct=5
    job = _job(client, seed_customer.id)
    _accept(client, est["id"], job["id"])
    c = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}").json()[
        "contract"
    ]
    assert c["fee_pct"] == 5.0


def test_build_keeps_a_fee_pct_already_on_the_job(client, seed_customer):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)  # fee_pct=5
    job = _job(client, seed_customer.id)
    client.put(f"/api/jobs/{job['id']}/contract", json={"fee_pct": 12})
    _accept(client, est["id"], job["id"])
    c = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}").json()[
        "contract"
    ]
    assert c["fee_pct"] == 12.0


def test_estimate_cannot_go_back_to_pending_once_the_sov_is_built(
    client, seed_customer
):
    """The #559 status hole, seen from the contract. The refusal lands in #559
    (stacked under this PR), so this passes once this branch is rebased onto
    it; on a head without it the PUT is accepted and this fails."""
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    _accept(client, est["id"], job["id"])
    url = f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}"
    client.post(url)
    before = client.get(f"/api/jobs/{job['id']}/contract").json()
    budgets_before = client.get(f"/api/jobs/{job['id']}/budgets").json()

    r = client.put(f"/api/estimates/{est['id']}", json={"status": "pending"})
    assert r.status_code in (400, 409), r.text

    assert client.get(f"/api/jobs/{job['id']}/contract").json() == before
    assert client.get(f"/api/jobs/{job['id']}/budgets").json() == budgets_before
