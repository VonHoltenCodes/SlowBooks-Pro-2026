"""Change orders take effect on a date, not on the click, and allowances
reconcile: approved_on (default the CO's own date) is what WIP, the log and a
pay application's contract sum read; a closed period refuses an approval; an
allowance-funded CO draws the allowance's budget down instead of adding to it;
and "return unspent allowance" leaves the allowance with nothing to bill."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget
from app.models.pay_applications import PayApplication
from app.services.job_reports import change_order_log, wip_row

LINE = {"description": "Deck", "cost_type": "material", "quantity": 1}


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _setup(client, customer_id, lines, amount, retainage=10, name="Tower"):
    job = _job(client, customer_id, name=name)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": amount,
            "retainage_pct": retainage,
        },
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/jobs/{job['id']}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text
    return job, client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]


def _owner_co(client, job_id, cost, **extra):
    body = {
        "job_id": job_id,
        "kind": "owner",
        "lines": [{**LINE, "unit_cost": cost}],
        **extra,
    }
    r = client.post("/api/change-orders", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _approve(client, co_id, **body):
    if client.get(f"/api/change-orders/{co_id}").json()["status"] == "draft":
        assert client.post(f"/api/change-orders/{co_id}/submit").status_code == 200
    return client.post(f"/api/change-orders/{co_id}/approve", json=body)


def _contract(client, job_id):
    return client.get(f"/api/jobs/{job_id}/contract").json()


def _app(client, job_id, day, lines=None, **body):
    r = client.post("/api/pay-applications", json={"job_id": job_id, "date": day})
    assert r.status_code == 201, r.text
    app = r.json()
    if lines is not None or body:
        r = client.put(
            f"/api/pay-applications/{app['id']}", json={"lines": lines, **body}
        )
        assert r.status_code == 200, r.text
    return app


# ── 1. The day Approve takes effect ──────────────────────────────────────


def test_approve_defaults_to_the_change_orders_own_date(client, seed_customer):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    co = _owner_co(client, job["id"], 1000, date="2026-08-03")
    r = _approve(client, co["id"])
    assert r.status_code == 200, r.text
    assert r.json()["approved_on"] == "2026-08-03"
    # the click, stamped in UTC (it can be tomorrow's date there in the evening)
    clicked = r.json()["approved_at"][:10]
    assert clicked in {
        date.today().isoformat(),
        datetime.now(timezone.utc).date().isoformat(),
    }


def test_approve_takes_a_date_and_falls_back_to_today(client, seed_customer):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    dated = _owner_co(client, job["id"], 1000, date="2026-08-03")
    assert (
        _approve(client, dated["id"], approved_on="2026-08-10").json()["approved_on"]
        == "2026-08-10"
    )
    undated = _owner_co(client, job["id"], 500)
    assert (
        _approve(client, undated["id"]).json()["approved_on"]
        == date.today().isoformat()
    )


def test_wip_and_the_log_read_the_date_it_takes_effect(
    client, db_session, seed_customer
):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    co = _owner_co(client, job["id"], 5000, date="2026-08-03")
    assert _approve(client, co["id"]).status_code == 200  # clicked today
    db_session.expire_all()
    from app.models.jobs import Job

    row = wip_row(db_session, db_session.get(Job, job["id"]), date(2026, 9, 30))
    assert row["approved_change_orders"] == 5000.0
    assert row["revised_contract"] == 105000.0
    log = change_order_log(db_session, as_of=date(2026, 9, 30), job_id=job["id"])
    assert log["jobs"][0]["contract"]["approved_changes"] == 5000.0
    assert log["jobs"][0]["rows"][0]["approved_on"] == "2026-08-03"
    # before it took effect it is not in the contract
    before = wip_row(db_session, db_session.get(Job, job["id"]), date(2026, 7, 31))
    assert before["approved_change_orders"] == 0.0
    assert before["revised_contract"] == 100000.0


def test_a_pay_application_before_the_change_order_does_not_carry_it(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _setup(
        client,
        seed_customer.id,
        [{"description": "Base", "scheduled_value": 100000}],
        100000,
    )
    co = _owner_co(client, job["id"], 13640, date="2026-08-10")
    assert _approve(client, co["id"]).status_code == 200  # entered late
    base = sov[0]["id"]
    july = _app(
        client,
        job["id"],
        "2026-07-31",
        [{"sov_line_id": base, "this_period": 10000, "stored": 0}],
    )
    # a draft reads the sum as it stood on its own date
    from app.services.contracts import application_contract_sums

    db_session.expire_all()
    job_row = db_session.get(PayApplication, july["id"]).job
    sums = application_contract_sums(
        db_session, db_session.get(PayApplication, july["id"]), job_row
    )
    assert sums["to_date"] == Decimal("100000.00")
    assert sums["net_change"] == Decimal("0.00")
    # approving snapshots it the same way
    assert client.post(f"/api/pay-applications/{july['id']}/approve").status_code == 200
    db_session.expire_all()
    pa = db_session.get(PayApplication, july["id"])
    assert pa.contract_sum_to_date == Decimal("100000.00")
    assert pa.contract_sum_original == Decimal("100000.00")
    # an application dated after the change order carries it
    august = _app(client, job["id"], "2026-08-31")
    assert (
        client.put(
            f"/api/pay-applications/{august['id']}",
            json={"lines": [{"sov_line_id": base, "this_period": 1000, "stored": 0}]},
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/pay-applications/{august['id']}/approve").status_code == 200
    )
    db_session.expire_all()
    pa = db_session.get(PayApplication, august["id"])
    assert pa.contract_sum_to_date == Decimal("113640.00")
    assert pa.contract_sum_original == Decimal("100000.00")


# ── 2. The closing date ──────────────────────────────────────────────────


def test_approving_in_a_closed_period_is_refused(client, seed_customer):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code
        == 200
    )
    co = _owner_co(client, job["id"], 1000, date="2026-08-10")
    r = _approve(client, co["id"])  # takes effect on its own date, in the closed period
    assert r.status_code == 403, r.text
    assert "on or before the closing date" in r.json()["detail"]
    after = client.get(f"/api/change-orders/{co['id']}").json()
    assert after["status"] == "pending"
    assert _contract(client, job["id"])["contract"]["contract_amount"] == 100000.0
    # a date after the closing date goes through
    assert _approve(client, co["id"], approved_on="2026-10-05").status_code == 200
    assert _contract(client, job["id"])["contract"]["contract_amount"] == 101000.0


# ── 3. An allowance-funded CO draws the allowance down ───────────────────


def _budget_total(db_session, job_id):
    db_session.expire_all()
    return sum(
        (
            Decimal(str(b.amount))
            for b in db_session.query(JobBudget).filter(JobBudget.job_id == job_id)
        ),
        Decimal("0"),
    )


def _allowance_with_budget(client, db_session, customer_id):
    """A job with a 25,000 finishes allowance whose cost Accept budgeted on
    its code (an estimate-source row, as Accept writes it)."""
    code = CostCode(code="09 70 00", name="Finishes", cost_type="material")
    db_session.add(code)
    db_session.commit()
    job, sov = _setup(
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
    allowance = next(s for s in sov if s["kind"] == "allowance")
    return job, sov, allowance["id"], code


def test_an_allowance_funded_co_does_not_add_to_the_budget(
    client, db_session, seed_customer
):
    job, _sov, allowance_id, code = _allowance_with_budget(
        client, db_session, seed_customer.id
    )
    assert _budget_total(db_session, job["id"]) == Decimal("25000")
    co = _owner_co(
        client,
        job["id"],
        9000,
        funding="allowance",
        allowance_sov_line_id=allowance_id,
    )
    assert _approve(client, co["id"]).status_code == 200
    # cost moved from the allowance cell to the CO's cell: the total is unchanged
    assert _budget_total(db_session, job["id"]) == Decimal("25000")
    # void puts it back
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    assert _budget_total(db_session, job["id"]) == Decimal("25000")
    rows = db_session.query(JobBudget).filter(JobBudget.job_id == job["id"]).all()
    assert [r.source for r in rows] == ["estimate"]


def test_an_allowance_co_on_the_allowances_own_code_nets_to_nothing(
    client, db_session, seed_customer
):
    job, _sov, allowance_id, code = _allowance_with_budget(
        client, db_session, seed_customer.id
    )
    body = {
        "job_id": job["id"],
        "kind": "owner",
        "funding": "allowance",
        "allowance_sov_line_id": allowance_id,
        "lines": [{**LINE, "unit_cost": 4000, "cost_code_id": code.id}],
    }
    co = client.post("/api/change-orders", json=body).json()
    assert _approve(client, co["id"]).status_code == 200
    assert _budget_total(db_session, job["id"]) == Decimal("25000")
    assert (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.source == "change")
        .count()
        == 0
    )


# ── 4. Return unspent allowance ──────────────────────────────────────────


def _balance_to_finish(client, db_session, job_id):
    """The balance to finish a new application would show. It leaves that
    draft open, so call it once per job."""
    app = _app(client, job_id, "2026-12-31")
    total = client.get(f"/api/pay-applications/{app['id']}").json()["totals"]
    return Decimal(str(total["balance_to_finish_total"]))


def _return(client, allowance_id, **body):
    # returning an allowance is construction mode's
    client.put("/api/settings", json={"company_type": "construction"})
    return client.post(
        "/api/change-orders/allowance-return",
        json={"allowance_sov_line_id": allowance_id, **body},
    )


def _bill(client, job_id, day, rows):
    app = _app(client, job_id, day)
    r = client.put(f"/api/pay-applications/{app['id']}", json={"lines": rows})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{app['id']}/approve")
    assert r.status_code == 200, r.text
    return app


def test_returning_unspent_allowance_leaves_nothing_to_finish(
    client, db_session, seed_accounts, seed_customer
):
    job, sov, allowance_id, _code = _allowance_with_budget(
        client, db_session, seed_customer.id
    )
    base = next(s for s in sov if s["kind"] == "work")["id"]
    # 21,500 of the 25,000 allowance billed, 3,500 unbilled and unspent
    _bill(
        client,
        job["id"],
        "2026-09-30",
        [
            {"sov_line_id": base, "this_period": 75000, "stored": 0},
            {"sov_line_id": allowance_id, "this_period": 21500, "stored": 0},
        ],
    )
    r = _return(client, allowance_id, amount=3500)
    assert r.status_code == 201, r.text
    co = r.json()
    assert co["allowance_return"] is True
    assert co["funding"] == "contract"
    assert co["allowance_sov_line_id"] == allowance_id
    assert co["sell_total"] == -3500.0  # no markup, whatever the job's default
    assert co["status"] == "draft"
    assert _approve(client, co["id"]).status_code == 200

    summary = _contract(client, job["id"])
    assert summary["contract"]["contract_amount"] == 96500.0
    line = next(s for s in summary["sov"] if s["id"] == allowance_id)
    assert Decimal(str(line["scheduled_value"])) == Decimal("21500.00")
    assert not any(s["kind"] == "change_order" for s in summary["sov"])
    assert _balance_to_finish(client, db_session, job["id"]) == Decimal("0.00")
    # the budget gives up the cost it will not spend
    assert _budget_total(db_session, job["id"]) == Decimal("21500")

    # void gives the allowance line its 3,500 back
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    after = _contract(client, job["id"])
    assert after["contract"]["contract_amount"] == 100000.0
    line = next(s for s in after["sov"] if s["id"] == allowance_id)
    assert Decimal(str(line["scheduled_value"])) == Decimal("25000.00")
    assert _budget_total(db_session, job["id"]) == Decimal("25000")


def test_a_billed_allowance_returns_through_a_credit_line(
    client, db_session, seed_accounts, seed_customer
):
    job, sov, allowance_id, _code = _allowance_with_budget(
        client, db_session, seed_customer.id
    )
    base = next(s for s in sov if s["kind"] == "work")["id"]
    # billed in full, although only 9,800 was spent
    _bill(
        client,
        job["id"],
        "2026-09-30",
        [
            {"sov_line_id": base, "this_period": 75000, "stored": 0},
            {"sov_line_id": allowance_id, "this_period": 25000, "stored": 0},
        ],
    )
    spend = _owner_co(
        client, job["id"], 9800, funding="allowance", allowance_sov_line_id=allowance_id
    )
    assert _approve(client, spend["id"]).status_code == 200

    # everything unspent: 15,200
    co = _return(client, allowance_id, date="2026-09-30").json()
    assert co["sell_total"] == -15200.0
    assert _approve(client, co["id"]).status_code == 200
    summary = _contract(client, job["id"])
    assert summary["contract"]["contract_amount"] == 84800.0
    credit = [s for s in summary["sov"] if s["kind"] == "change_order"]
    assert [Decimal(str(s["scheduled_value"])) for s in credit] == [
        Decimal("-15200.00")
    ]
    line = next(s for s in summary["sov"] if s["id"] == allowance_id)
    assert Decimal(str(line["scheduled_value"])) == Decimal("25000.00")
    row = next(
        a for a in summary["contract"]["allowances"] if a["sov_line_id"] == allowance_id
    )
    assert row["remaining"] == 0.0
    # and the credit is what the next application bills
    assert _balance_to_finish(client, db_session, job["id"]) == Decimal("-15200.00")


def test_nothing_unspent_is_refused_with_a_sentence(client, db_session, seed_customer):
    job, _sov, allowance_id, _code = _allowance_with_budget(
        client, db_session, seed_customer.id
    )
    spend = _owner_co(
        client,
        job["id"],
        25000,
        funding="allowance",
        allowance_sov_line_id=allowance_id,
    )
    assert _approve(client, spend["id"]).status_code == 200
    r = _return(client, allowance_id)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "allowance_nothing_unspent"
    assert "nothing unspent to return" in r.json()["detail"]["message"]


def test_a_return_is_capped_and_not_made_twice(client, db_session, seed_customer):
    job, _sov, allowance_id, _code = _allowance_with_budget(
        client, db_session, seed_customer.id
    )
    assert _return(client, allowance_id, amount=30000).status_code == 422
    assert _return(client, allowance_id, amount=0).status_code == 422
    first = _return(client, allowance_id, amount=1000)
    assert first.status_code == 201
    again = _return(client, allowance_id)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "allowance_return_open"
    # its funding and markup are fixed
    r = client.put(
        f"/api/change-orders/{first.json()['id']}", json={"funding": "allowance"}
    )
    assert r.status_code == 409
    # a second return after the first is approved can only take what is left
    assert _approve(client, first.json()["id"]).status_code == 200
    rest = _return(client, allowance_id).json()
    assert rest["sell_total"] == -24000.0


# ── 5. A credit after the final release ──────────────────────────────────


def test_a_credit_after_the_final_release_says_what_to_do(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _setup(
        client,
        seed_customer.id,
        [
            {"description": "Work", "kind": "work", "scheduled_value": 100000},
            {
                "description": "Allowance credit",
                "kind": "work",
                "scheduled_value": -3500,
            },
        ],
        96500,
    )
    work, credit = sov[0]["id"], sov[1]["id"]
    _bill(
        client,
        job["id"],
        "2026-08-31",
        [{"sov_line_id": work, "this_period": 100000, "stored": 0}],
    )
    release = _app(client, job["id"], "2026-09-30")
    assert (
        client.put(
            f"/api/pay-applications/{release['id']}", json={"release_retainage": 10000}
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/pay-applications/{release['id']}/approve").status_code == 200
    )
    late = _app(client, job["id"], "2026-10-31")
    r = client.put(
        f"/api/pay-applications/{late['id']}",
        json={"lines": [{"sov_line_id": credit, "this_period": -3500, "stored": 0}]},
    )
    assert r.status_code == 422
    msg = r.json()["detail"]
    assert "Retainage to date would be -350.00" in msg
    assert "Bill the credit before the final retainage release" in msg


# ── Migration ────────────────────────────────────────────────────────────


# the migration under test: change orders' approved_on and allowance_return
# (two steps above d7f3a9c1b5e2, after the collections migration)
CO_DATE_REVISION = "e2a6c8f4b1d9"


def test_the_migration_gives_existing_approved_rows_their_day(tmp_path):
    from pathlib import Path

    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "co.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "d7f3a9c1b5e2")
    engine = sa.create_engine("sqlite:///" + db.as_posix())
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO change_orders (job_id, number, kind, status, funding, "
                "apply_markup, revision, approved_at) VALUES (1, 'J1-CO-001', 'owner', "
                "'approved', 'contract', 1, 1, '2026-08-03 12:00:00'), "
                "(1, 'J1-CO-002', 'owner', 'draft', 'contract', 1, 1, NULL)"
            )
        )
    command.upgrade(cfg, CO_DATE_REVISION)  # its own step, not the chain (#770)
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                sa.text("SELECT number, approved_on FROM change_orders")
            ).fetchall()
        )
        returns = conn.execute(
            sa.text("SELECT allowance_return FROM change_orders")
        ).fetchall()
    assert rows["J1-CO-001"] == "2026-08-03"
    assert rows["J1-CO-002"] is None
    assert all(not r[0] for r in returns)


# ── Companies upgraded from the release before ───────────────────────────


def _migration(stem):
    import importlib.util
    from pathlib import Path

    path = next(
        (Path(__file__).resolve().parents[1] / "migrations" / "versions").glob(
            f"{stem}_*.py"
        )
    )
    spec = importlib.util.spec_from_file_location(f"mig_{stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_allowance_co(client, db_session, customer_id, shared_amount="4000"):
    """A 4,000 allowance CO on the allowance's own code as the release before
    left it: no draw out of the allowance's cell, its cost in the one shared
    change row, and no rows of its own."""
    from app.models.change_orders import ChangeOrder

    job, _sov, allowance_id, code = _allowance_with_budget(
        client, db_session, customer_id
    )
    body = {
        "job_id": job["id"],
        "kind": "owner",
        "funding": "allowance",
        "allowance_sov_line_id": allowance_id,
        "lines": [{**LINE, "unit_cost": 4000, "cost_code_id": code.id}],
    }
    co = client.post("/api/change-orders", json=body).json()
    assert _approve(client, co["id"]).status_code == 200
    db_session.get(ChangeOrder, co["id"]).allowance_draw_written = False
    db_session.add(
        JobBudget(
            job_id=job["id"],
            cost_code_id=code.id,
            cost_type="material",
            amount=Decimal(shared_amount),
            revenue_amount=Decimal("0"),
            source="change",
            locked=True,
        )
    )
    db_session.commit()
    assert _budget_total(db_session, job["id"]) == Decimal(25000) + Decimal(
        shared_amount
    )
    return job, co


def test_voiding_an_allowance_co_approved_before_the_upgrade_takes_its_cost_back(
    client, db_session, seed_customer
):
    job, co = _legacy_allowance_co(client, db_session, seed_customer.id)
    _migration("b5d2e8f4a6c3")._link_change_budgets(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    own = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.source == "change")
        .all()
    )
    assert [(r.change_order_id, Decimal(str(r.amount))) for r in own] == [
        (co["id"], Decimal("4000.00"))
    ]
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    assert _budget_total(db_session, job["id"]) == Decimal("25000")


def test_voiding_an_unlinked_allowance_co_subtracts_only_its_lines(
    client, db_session, seed_customer
):
    """The shared row also holds another change order's 500, so the backfill
    leaves it alone; the void takes back the 4,000 the lines wrote and not a
    draw the release before never made."""
    job, co = _legacy_allowance_co(
        client, db_session, seed_customer.id, shared_amount="4500"
    )
    _migration("b5d2e8f4a6c3")._link_change_budgets(db_session.connection())
    db_session.commit()
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    assert _budget_total(db_session, job["id"]) == Decimal("25500")


def test_voiding_a_partly_linked_allowance_co_from_before_the_upgrade_takes_back_its_lines(
    client, db_session, seed_customer
):
    """A 5,000 allowance CO from before the upgrade, 4,000 on the
    allowance's code and 1,000 on another. The upgrade links the 1,000 row
    to it but not the allowance code's shared row, which also holds another
    change order's 500. The void deletes the linked row and takes the 4,000
    its lines wrote off the shared row, not a draw it never made."""
    from app.models.change_orders import ChangeOrder

    job, _sov, allowance_id, code = _allowance_with_budget(
        client, db_session, seed_customer.id
    )
    tile = CostCode(code="09 30 00", name="Tile", cost_type="material")
    db_session.add(tile)
    db_session.commit()
    body = {
        "job_id": job["id"],
        "kind": "owner",
        "funding": "allowance",
        "allowance_sov_line_id": allowance_id,
        "lines": [
            {**LINE, "unit_cost": 4000, "cost_code_id": code.id},
            {**LINE, "unit_cost": 1000, "cost_code_id": tile.id},
        ],
    }
    co = client.post("/api/change-orders", json=body).json()
    assert _approve(client, co["id"]).status_code == 200
    # as the release before left it: no draw, its cost in shared rows
    db_session.get(ChangeOrder, co["id"]).allowance_draw_written = False
    db_session.query(JobBudget).filter(JobBudget.change_order_id == co["id"]).delete()
    for code_id, amount in ((code.id, "4500"), (tile.id, "1000")):
        db_session.add(
            JobBudget(
                job_id=job["id"],
                cost_code_id=code_id,
                cost_type="material",
                amount=Decimal(amount),
                revenue_amount=Decimal("0"),
                source="change",
                locked=True,
            )
        )
    db_session.commit()
    _migration("b5d2e8f4a6c3")._link_change_budgets(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    rows = {
        r.cost_code_id: (r.change_order_id, Decimal(str(r.amount)))
        for r in db_session.query(JobBudget).filter(
            JobBudget.job_id == job["id"], JobBudget.source == "change"
        )
    }
    assert rows == {
        code.id: (None, Decimal("4500.00")),
        tile.id: (co["id"], Decimal("1000.00")),
    }
    assert _budget_total(db_session, job["id"]) == Decimal("30500")

    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    assert _budget_total(db_session, job["id"]) == Decimal("25500")


def test_a_draft_application_billing_a_change_order_stays_saveable_after_the_upgrade(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.change_orders import ChangeOrder

    job, sov = _setup(
        client,
        seed_customer.id,
        [{"description": "Base", "scheduled_value": 100000}],
        100000,
    )
    co = _owner_co(client, job["id"], 10000, date="2026-08-20")
    assert _approve(client, co["id"]).status_code == 200
    co_line = next(
        s for s in _contract(client, job["id"])["sov"] if s["kind"] == "change_order"
    )
    draft = _app(
        client,
        job["id"],
        "2026-08-31",
        [{"sov_line_id": co_line["id"], "this_period": 5000, "stored": 0}],
    )
    # the upgrade stamps approved_on from a click made on 09-02
    row = db_session.get(ChangeOrder, co["id"])
    row.approved_at = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    row.approved_on = None
    db_session.commit()
    _migration("e2a6c8f4b1d9")._backfill_approved_on(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(ChangeOrder, co["id"]).approved_on == date(2026, 8, 20)

    body = {"lines": [{"sov_line_id": co_line["id"], "this_period": 6000, "stored": 0}]}
    assert (
        client.put(f"/api/pay-applications/{draft['id']}", json=body).status_code == 200
    )
    assert client.post(f"/api/pay-applications/{draft['id']}/submit").status_code == 200
    assert (
        client.post(f"/api/pay-applications/{draft['id']}/approve").status_code == 200
    )


@pytest.mark.parametrize(
    "co_date, draft_date",
    [
        ("2026-08-20", "2026-08-15"),
        ("2026-09-05", "2026-08-31"),
        (None, "2026-08-31"),
    ],
)
def test_a_draft_dated_before_the_change_order_stays_saveable_after_the_upgrade(
    client, db_session, seed_accounts, seed_customer, co_date, draft_date
):
    """A draft that bills a change order's line on a day before the change
    order's own date, or before the click of one with no date, takes that
    draft's date: PUT, submit and approve all go through."""
    from app.models.change_orders import ChangeOrder

    job, sov = _setup(
        client,
        seed_customer.id,
        [{"description": "Base", "scheduled_value": 100000}],
        100000,
    )
    co = _owner_co(client, job["id"], 10000, date="2026-08-01")
    assert _approve(client, co["id"]).status_code == 200
    co_line = next(
        s for s in _contract(client, job["id"])["sov"] if s["kind"] == "change_order"
    )
    draft = _app(
        client,
        job["id"],
        draft_date,
        [{"sov_line_id": co_line["id"], "this_period": 4000, "stored": 0}],
    )
    # as the release before left it: its own date, a click on 11-08
    row = db_session.get(ChangeOrder, co["id"])
    row.date = date.fromisoformat(co_date) if co_date else None
    row.approved_at = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    row.approved_on = None
    db_session.commit()
    _migration("e2a6c8f4b1d9")._backfill_approved_on(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(ChangeOrder, co["id"]).approved_on == date.fromisoformat(
        draft_date
    )

    body = {"lines": [{"sov_line_id": co_line["id"], "this_period": 5000, "stored": 0}]}
    r = client.put(f"/api/pay-applications/{draft['id']}", json=body)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{draft['id']}/submit")
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{draft['id']}/approve")
    assert r.status_code == 200, r.text


def test_a_change_order_billed_only_on_approved_applications_keeps_the_click_day(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.change_orders import ChangeOrder

    job, sov = _setup(
        client,
        seed_customer.id,
        [{"description": "Base", "scheduled_value": 100000}],
        100000,
    )
    co = _owner_co(client, job["id"], 10000, date="2026-08-20")
    assert _approve(client, co["id"]).status_code == 200
    row = db_session.get(ChangeOrder, co["id"])
    row.approved_at = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    row.approved_on = None
    db_session.commit()
    _migration("e2a6c8f4b1d9")._backfill_approved_on(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(ChangeOrder, co["id"]).approved_on == date(2026, 9, 2)


def _setup_2(client, customer_id, lines, amount, name="Tower", **terms):
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


def _allowance(client, db_session, customer_id, contract_date="2026-07-15"):
    code = CostCode(code="09 70 00", name="Finishes", cost_type="material")
    db_session.add(code)
    db_session.commit()
    job, _saved, sov = _setup_2(
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


def test_an_allowance_return_takes_a_date_and_approves_effective_that_day(
    client, db_session, seed_accounts, seed_customer
):
    """The return was dated today (11/08), so approving it
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
