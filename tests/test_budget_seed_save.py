"""Save Budget after "Seed from an estimate" (fix/budget-seed).

Seeded rows are keyed (code, the line's cost type) since #550, while the budget
screen sends one row per code with no type. The save used to miss the seeded
row and write a manual twin beside it, so every job report counted the budget
twice, and on an accepted contract the plain save got around the lock. Now a
coded screen row stands for every non-change row on its code."""

from decimal import Decimal

import pytest

from app.models.job_costing import JobBudget


def _set_construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _code(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _estimate(client, customer_id, job_id, lines):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-02-01",
            "job_id": job_id,
            "lines": lines,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _line(desc, rate, cost, code_id, cost_type=None):
    out = {
        "description": desc,
        "quantity": 1,
        "rate": rate,
        "unit_cost": cost,
        "cost_code_id": code_id,
    }
    if cost_type:
        out["cost_type"] = cost_type
    return out


def _cents(v):
    return round(float(v or 0) * 100)


def _screen(client, job_id):
    """What jobs.js budgetHtml() shows: {key: (cost, revenue, source)} per
    screen line, summing a code's non-change rows and leaving out an old
    Save Budget twin (a manual untyped row equal to the code's other rows)."""
    groups = {}
    for r in client.get(f"/api/jobs/{job_id}/budgets").json():
        if r["source"] == "change":
            continue
        k = (
            ("c", r["cost_code_id"])
            if r["cost_code_id"]
            else (("t", r["cost_type"]) if r["cost_type"] else ("w", None))
        )
        groups.setdefault(k, []).append(r)
    out = {}
    for k, g in groups.items():

        def is_copy(r):
            return r["source"] == "manual" and not r["cost_type"]

        others = [r for r in g if not is_copy(r)]
        if k[0] == "c" and others and len(others) < len(g):
            oa = sum(_cents(r["amount"]) for r in others)
            orv = sum(_cents(r["revenue_amount"]) for r in others)
            g = [
                r
                for r in g
                if not (
                    is_copy(r)
                    and _cents(r["amount"]) == oa
                    and _cents(r["revenue_amount"]) == orv
                )
            ]
        sources = sorted({r["source"] for r in g})
        source = sources[0] if len(sources) == 1 else "mixed"
        if any(r["locked"] for r in g):
            source += " · locked"
        out[k] = (
            sum(_cents(r["amount"]) for r in g) / 100,
            sum(_cents(r["revenue_amount"]) for r in g) / 100,
            source,
        )
    return out


def _payload(client, job_id, edits=None):
    """What saveBudget() sends: every code, every type and the whole-job line,
    with the shown amounts unless edited ({code_id: cost})."""
    shown = _screen(client, job_id)
    rows = []
    for c in client.get("/api/cost-codes").json():
        cost, rev, _s = shown.get(("c", c["id"]), (0, 0, ""))
        if edits and c["id"] in edits:
            cost = edits[c["id"]]
        rows.append({"cost_code_id": c["id"], "amount": cost, "revenue_amount": rev})
    for t in client.get("/api/cost-types").json():
        cost, rev, _s = shown.get(("t", t["code"]), (0, 0, ""))
        rows.append({"cost_type": t["code"], "amount": cost, "revenue_amount": rev})
    cost, rev, _s = shown.get(("w", None), (0, 0, ""))
    rows.append({"amount": cost, "revenue_amount": rev})
    return rows


def _save(client, job_id, edits=None, blank=False):
    rows = _payload(client, job_id, edits)
    if blank:
        for r in rows:
            r["amount"] = r["revenue_amount"] = 0
    return client.put(f"/api/jobs/{job_id}/budgets", json={"rows": rows})


def _rows(client, job_id):
    return sorted(
        (
            (r["cost_code_id"], r["cost_type"], float(r["amount"]), r["source"])
            for r in client.get(f"/api/jobs/{job_id}/budgets").json()
        ),
        key=lambda t: (t[0] or 0, t[1] or "", t[3]),
    )


def _tree_original(client, job_id):
    return float(
        client.get(f"/api/jobs/{job_id}/cost-tree").json()["totals"]["original"]
    )


def _ctc_budget(client, job_id):
    r = client.get(f"/api/jobs/{job_id}/cost-to-complete-forecast")
    assert r.status_code == 200, r.text
    return float(r.json()["totals"]["budget"])


@pytest.fixture
def setup(client, seed_accounts, seed_customer):
    _set_construction(client)
    a = _code(client, "03 30 00", "Concrete")
    b = _code(client, "06 10 00", "Framing")
    r = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Annex"}
    )
    assert r.status_code == 201, r.text
    job = r.json()
    est = _estimate(
        client,
        seed_customer.id,
        job["id"],
        [_line("slab", 1000, 600, a["id"]), _line("framing", 500, 300, b["id"])],
    )
    return client, job, est, a, b


def _seed(client, job, est):
    r = client.post(f"/api/jobs/{job['id']}/budgets/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text


def _seeded(a, b):
    return [
        (a["id"], "material", 600.0, "estimate"),
        (b["id"], "material", 300.0, "estimate"),
    ]


def test_untouched_save_after_seed_changes_nothing(setup):
    client, job, est, a, b = setup
    _seed(client, job, est)
    assert _screen(client, job["id"])[("c", a["id"])] == (600.0, 600.0, "estimate")
    r = _save(client, job["id"])
    assert r.status_code == 200, r.text
    assert _rows(client, job["id"]) == _seeded(a, b)
    assert _tree_original(client, job["id"]) == 900.0
    assert _ctc_budget(client, job["id"]) == 900.0
    shown = _screen(client, job["id"])
    assert shown[("c", a["id"])] == (600.0, 600.0, "estimate")
    assert shown[("c", b["id"])] == (300.0, 300.0, "estimate")


def test_edited_code_replaces_its_rows_with_one_manual_row(setup):
    client, job, est, a, b = setup
    _seed(client, job, est)
    r = _save(client, job["id"], edits={a["id"]: 700})
    assert r.status_code == 200, r.text
    assert _rows(client, job["id"]) == [
        (a["id"], None, 700.0, "manual"),
        (b["id"], "material", 300.0, "estimate"),
    ]
    assert _tree_original(client, job["id"]) == 1000.0
    assert _screen(client, job["id"])[("c", a["id"])][2] == "manual"
    # blanking a code clears it instead of bringing the estimate row back
    r = _save(client, job["id"], edits={a["id"]: 0})
    assert r.status_code == 200, r.text
    # revenue was still sent, so the code keeps a manual revenue-only row
    assert _rows(client, job["id"]) == [
        (a["id"], None, 0.0, "manual"),
        (b["id"], "material", 300.0, "estimate"),
    ]
    r = _save(client, job["id"], blank=True)
    assert r.status_code == 200, r.text
    assert _rows(client, job["id"]) == []


def test_seed_save_seed_keeps_the_estimate_rows(setup):
    client, job, est, a, b = setup
    _seed(client, job, est)
    assert _save(client, job["id"]).status_code == 200
    _seed(client, job, est)
    assert _rows(client, job["id"]) == _seeded(a, b)
    assert _tree_original(client, job["id"]) == 900.0
    assert _save(client, job["id"]).status_code == 200
    assert _rows(client, job["id"]) == _seeded(a, b)


def test_accepted_contract_untouched_save_ok_edit_refused(setup):
    client, job, est, a, b = setup
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 200, r.text
    before = client.get(f"/api/jobs/{job['id']}/budgets").json()
    assert all(x["locked"] for x in before)
    r = _save(client, job["id"])
    assert r.status_code == 200, r.text
    assert client.get(f"/api/jobs/{job['id']}/budgets").json() == before
    assert _tree_original(client, job["id"]) == 900.0
    assert _screen(client, job["id"])[("c", a["id"])][2] == "estimate · locked"
    # an edited locked code is refused, from the screen and straight
    for rows in (
        _payload(client, job["id"], edits={a["id"]: 1}),
        [{"cost_code_id": a["id"], "amount": 1, "revenue_amount": 0}],
    ):
        r = client.put(f"/api/jobs/{job['id']}/budgets", json={"rows": rows})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "budget_locked"
    r = _save(client, job["id"], blank=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "budget_locked"
    assert client.get(f"/api/jobs/{job['id']}/budgets").json() == before


def _twin(db_session, job_id, code_id, amount, revenue):
    db_session.add(
        JobBudget(
            job_id=job_id,
            cost_code_id=code_id,
            cost_type=None,
            amount=Decimal(amount),
            revenue_amount=Decimal(revenue),
            source="manual",
        )
    )
    db_session.commit()


@pytest.mark.parametrize("accepted", [False, True])
def test_a_doubled_budget_is_put_right_by_an_untouched_save(
    setup, db_session, accepted
):
    client, job, est, a, b = setup
    if accepted:
        r = client.post(f"/api/estimates/{est['id']}/accept", json={})
        assert r.status_code == 200, r.text
    else:
        _seed(client, job, est)
    # the twins an older Save Budget wrote
    _twin(db_session, job["id"], a["id"], "600", "600")
    _twin(db_session, job["id"], b["id"], "300", "300")
    assert _tree_original(client, job["id"]) == 1800.0
    # the screen already shows the budget the estimate set
    assert _screen(client, job["id"])[("c", a["id"])][:2] == (600.0, 600.0)
    r = _save(client, job["id"])
    assert r.status_code == 200, r.text
    assert _rows(client, job["id"]) == _seeded(a, b)
    assert _tree_original(client, job["id"]) == 900.0
    assert _ctc_budget(client, job["id"]) == 900.0


def test_a_doubled_budget_folds_from_the_old_screen_payload_too(setup, db_session):
    """A page loaded before the fix sends the twin's own amounts."""
    client, job, est, a, b = setup
    _seed(client, job, est)
    _twin(db_session, job["id"], a["id"], "600", "600")
    r = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={
            "rows": [
                {"cost_code_id": a["id"], "amount": 600, "revenue_amount": 600},
                {"cost_code_id": b["id"], "amount": 300, "revenue_amount": 300},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _rows(client, job["id"]) == _seeded(a, b)


def test_a_code_with_two_cost_types_shows_and_keeps_both(
    client, seed_accounts, seed_customer
):
    _set_construction(client)
    a = _code(client, "03 30 00", "Concrete")
    r = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Annex"}
    )
    job = r.json()
    est = _estimate(
        client,
        seed_customer.id,
        job["id"],
        [
            _line("mix", 1000, 600, a["id"], "material"),
            _line("pour", 800, 400, a["id"], "labor"),
        ],
    )
    _seed(client, job, est)
    assert _screen(client, job["id"])[("c", a["id"])] == (1000.0, 1000.0, "estimate")
    assert _save(client, job["id"]).status_code == 200
    assert _rows(client, job["id"]) == [
        (a["id"], "labor", 400.0, "estimate"),
        (a["id"], "material", 600.0, "estimate"),
    ]
    assert _tree_original(client, job["id"]) == 1000.0


def test_change_order_rows_stay_out(setup, db_session):
    client, job, est, a, b = setup
    _seed(client, job, est)
    db_session.add(
        JobBudget(
            job_id=job["id"],
            cost_code_id=a["id"],
            cost_type="material",
            amount=Decimal("250"),
            revenue_amount=Decimal("0"),
            source="change",
            locked=True,
        )
    )
    db_session.commit()
    # the screen leaves the change row out, and an untouched save keeps it
    assert _screen(client, job["id"])[("c", a["id"])][:2] == (600.0, 600.0)
    assert _save(client, job["id"]).status_code == 200
    assert (a["id"], "material", 250.0, "change") in _rows(client, job["id"])
    # an edit replaces the code's own rows and still leaves the change row
    assert _save(client, job["id"], edits={a["id"]: 700}).status_code == 200
    assert _rows(client, job["id"]) == [
        (a["id"], None, 700.0, "manual"),
        (a["id"], "material", 250.0, "change"),
        (b["id"], "material", 300.0, "estimate"),
    ]
