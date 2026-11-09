"""The WIP schedule a bank can read:
cost types net within a section, budgets that never burn down are named, a
closed or finished job reads finished, a change order's budget alone is no
estimate, the bank's columns (GP to date, backlog), and Post / Void of the
adjusting entry with its next-day reversal on construction setup's accounts.
"""

import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.accounts import Account
from app.models.contacts import Customer, Vendor
from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget
from app.models.jobs import Job
from app.models.purchase_orders import POStatus, PurchaseOrder
from app.models.settings import Settings
from app.models.transactions import Transaction, TransactionLine
from app.models.users import ROLE_ACCOUNTANT, ROLE_BOOKKEEPER, User
from app.services import auth as auth_service
from app.services import job_reports, wip_posting
from app.services.accounting import create_journal_entry
from app.services.job_costing import (
    budget_vs_actual_all_jobs,
    ensure_default_cost_types,
    job_cost_tree,
)

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 6, 30)
OTHER_PW = "other-password-123"


# ── builders ────────────────────────────────────────────────────────────


def _acct(db, number):
    return db.query(Account).filter(Account.account_number == number).one()


def _job(db, name="Clinic", contract="100000", status="in_progress"):
    ensure_default_cost_types(db)
    customer = Customer(name=f"{name} Owner", is_active=True)
    db.add(customer)
    db.flush()
    job = Job(
        customer_id=customer.id,
        name=name,
        status=status,
        contract_type="lump_sum",
        contract_amount=Decimal(contract),
        original_contract_amount=Decimal(contract),
    )
    db.add(job)
    db.flush()
    return job


def _section(db, code, name):
    div = CostCode(code=code[:2], name=f"{name} division", level="division")
    db.add(div)
    db.flush()
    sec = CostCode(code=code, name=name, level="section", parent_id=div.id)
    db.add(sec)
    db.flush()
    return sec


def _budget(db, job, code, cost_type, amount, source="estimate"):
    db.add(
        JobBudget(
            job_id=job.id,
            cost_code_id=code.id if code else None,
            cost_type=cost_type,
            amount=Decimal(amount),
            source=source,
            locked=source != "manual",
        )
    )
    db.flush()


def _cost(db, job, code, cost_type, amount, when=date(2026, 3, 1)):
    create_journal_entry(
        db,
        when,
        "job cost",
        [
            {
                "account_id": _acct(db, "5100").id,
                "debit": Decimal(amount),
                "job_id": job.id,
                "cost_code_id": code.id if code else None,
                "cost_type": cost_type,
            },
            {"account_id": _acct(db, "2000").id, "credit": Decimal(amount)},
        ],
    )


def _bill_owner(db, job, amount, when=date(2026, 4, 1)):
    """Billing by posted revenue (no pay application): A/R to income, on the job."""
    create_journal_entry(
        db,
        when,
        "owner billing",
        [
            {"account_id": _acct(db, "1100").id, "debit": Decimal(amount)},
            {
                "account_id": _acct(db, "4000").id,
                "credit": Decimal(amount),
                "job_id": job.id,
            },
        ],
    )


def _row(db, job_id, **kw):
    data = job_reports.wip_schedule(db, as_of=AS_OF, **kw)
    return next(r for r in data["rows"] if r["job_id"] == job_id), data


def _drywall_job(db):
    """A job in small: drywall bid as subcontract 96,000, bought as
    material 38,000; electrical 88,000 spent 84,500. Contract 250,000, billed
    240,000 (96%)."""
    job = _job(db, contract="250000")
    drywall = _section(db, "09 21 16", "Gypsum board")
    elec = _section(db, "26 00 00", "Electrical")
    _budget(db, job, drywall, "subcontract", "96000")
    _budget(db, job, elec, "subcontract", "88000")
    _cost(db, job, drywall, "material", "38000")
    _cost(db, job, elec, "subcontract", "84500")
    _bill_owner(db, job, "240000")
    db.commit()
    return job, drywall, elec


# ── 1. cost types net within a section ─────────────────────────────────


def test_cost_types_net_within_a_section(db_session, seed_accounts):
    job, drywall, elec = _drywall_job(db_session)
    f = job_reports.ctc_forecast(db_session, job, AS_OF)
    cells = {(r["cost_code_id"], r["cost_type"]): r for r in f["rows"]}
    sub, mat = cells[(drywall.id, "subcontract")], cells[(drywall.id, "material")]
    # the section nets: 96,000 budget − 38,000 actual = 58,000 still to come,
    # shown against the cost type that has budget left (not 96,000)
    assert sub["default_cost_to_complete"] == 58000.0
    assert mat["default_cost_to_complete"] == 0.0
    assert sub["cost_to_complete"] + mat["cost_to_complete"] == 58000.0
    assert cells[(elec.id, "subcontract")]["cost_to_complete"] == 3500.0
    assert f["totals"]["cost_to_complete"] == 61500.0

    # a typed amount still wins for its own cell
    job_reports.set_ctc_entries(
        db_session,
        job,
        [{"cost_code_id": drywall.id, "cost_type": "subcontract", "amount": 0}],
    )
    f = job_reports.ctc_forecast(db_session, job, AS_OF)
    assert f["totals"]["cost_to_complete"] == 3500.0


def test_netted_default_splits_over_cells_with_budget_left_and_foots(
    db_session, seed_accounts
):
    """Labor 1,000 / material 2,000 / sub 0 budget, 700 of sub cost: the section
    has 2,300 left, shared 1,000 : 2,000 between the two cells with budget left
    (766.67 + 1,533.33, the last cell taking the rounding)."""
    job = _job(db_session)
    sec = _section(db_session, "03 30 00", "Slab")
    _budget(db_session, job, sec, "labor", "1000")
    _budget(db_session, job, sec, "material", "2000")
    _cost(db_session, job, sec, "subcontract", "700")
    db_session.commit()
    f = job_reports.ctc_forecast(db_session, job, AS_OF)
    by_type = {r["cost_type"]: r["default_cost_to_complete"] for r in f["rows"]}
    assert by_type == {"labor": 766.67, "material": 1533.33, "subcontract": 0.0}
    assert f["totals"]["cost_to_complete"] == 2300.0


# ── 2. budgets that never burn down ─────────────────────────────────────


def test_unburned_section_is_warned_on_a_nearly_billed_job(db_session, seed_accounts):
    job, _drywall, _elec = _drywall_job(db_session)
    gc = _section(db_session, "01 50 00", "General conditions")
    _budget(db_session, job, gc, "other", "24960")
    db_session.commit()

    _row_, data = _row(db_session, job.id)
    said = [w for w in data["warnings"] if "General conditions" in w]
    assert len(said) == 1
    assert "$24,960.00" in said[0] and "96.0%" in said[0]
    assert "enter 0" in said[0]
    # a code with cost against it is not named
    assert not any("Electrical" in w for w in data["warnings"])

    # entering 0 for it clears the warning and the cost still to come
    job_reports.set_ctc_entries(
        db_session, job, [{"cost_code_id": gc.id, "cost_type": "other", "amount": 0}]
    )
    r, data = _row(db_session, job.id)
    assert not any("General conditions" in w for w in data["warnings"])
    assert r["cost_to_complete"] == 61500.0


def test_unburned_section_is_not_warned_early_in_the_job(db_session, seed_accounts):
    job = _job(db_session, contract="250000")
    gc = _section(db_session, "01 50 00", "General conditions")
    work = _section(db_session, "03 30 00", "Slab")
    _budget(db_session, job, gc, "other", "20000")
    _budget(db_session, job, work, "material", "180000")
    _cost(db_session, job, work, "material", "50000")
    _bill_owner(db_session, job, "100000")  # 40% billed
    db_session.commit()
    _r, data = _row(db_session, job.id)
    assert data["warnings"] == []


# ── 3. a closed or finished job ─────────────────────────────────────────


def test_closed_job_reads_100_percent_with_nothing_to_complete(
    db_session, seed_accounts
):
    job, _d, _e = _drywall_job(db_session)
    job.status = "closed"
    db_session.commit()
    r, data = _row(db_session, job.id, include_closed=True)
    assert r["percent_complete"] == 100.0
    assert r["cost_to_complete"] == 0.0
    assert r["estimated_total_cost"] == r["cost_to_date"] == 122500.0
    assert r["earned_revenue"] == 250000.0
    assert r["under_billing"] == 10000.0 and r["over_billing"] == 0.0
    assert r["projected_loss"] == 0.0 and r["loss_provision"] == 0.0
    assert (
        job_reports.ctc_forecast(db_session, job, AS_OF)["totals"]["cost_to_complete"]
        == 0.0
    )
    assert data["warnings"] == []


def test_closed_job_with_no_budget_still_reads_finished(db_session, seed_accounts):
    job = _job(db_session, status="closed")
    _cost(db_session, job, None, "material", "40000")
    _bill_owner(db_session, job, "100000")
    db_session.commit()
    r, _data = _row(db_session, job.id, include_closed=True)
    assert r["percent_complete"] == 100.0 and r["earned_revenue"] == 100000.0
    assert "excluded" not in r


def test_finished_job_still_showing_cost_to_complete_is_flagged(
    db_session, seed_accounts
):
    job, _d, _e = _drywall_job(db_session)
    _bill_owner(db_session, job, "10000")  # 250,000: billed in full
    vendor = Vendor(name="Sparky", is_active=True)
    db_session.add(vendor)
    db_session.flush()
    po = PurchaseOrder(
        po_number="SC-1",
        vendor_id=vendor.id,
        job_id=job.id,
        status=POStatus.SENT,
        date=date(2026, 2, 1),
    )
    db_session.add(po)
    db_session.commit()
    finished = "billed in full, retainage collected and every commitment closed"

    # an open commitment: not finished yet, no flag
    _r, data = _row(db_session, job.id)
    assert not any(finished in w for w in data["warnings"])

    po.status = POStatus.CLOSED
    db_session.commit()
    _r, data = _row(db_session, job.id)
    said = [w for w in data["warnings"] if finished in w]
    assert len(said) == 1 and "$61,500.00" in said[0]


# ── 4. a change order's budget alone is no estimate ─────────────────────


def test_change_order_budget_alone_earns_nothing(db_session, seed_accounts):
    """Job 2602: an SOV typed by hand, no estimate, one 1,000 owner CO whose
    budget row is the job's only one: nothing to measure against."""
    job = _job(db_session, name="Hand SOV")
    _budget(db_session, job, None, "other", "1000", source="change")
    _cost(db_session, job, None, "material", "1565")
    db_session.commit()
    r, data = _row(db_session, job.id)
    assert r.get("excluded") is True
    assert r["earned_revenue"] == 0.0 and r["under_billing"] == 0.0
    assert any(
        "Hand SOV: no budget or forecast; nothing earned." == w
        for w in data["warnings"]
    )
    assert data["adjusting_entry"]["jobs"] == []


def test_change_order_budget_beside_an_estimate_still_counts(db_session, seed_accounts):
    job = _job(db_session)
    _budget(db_session, job, None, "other", "50000", source="estimate")
    _budget(db_session, job, None, "other", "10000", source="change")
    _cost(db_session, job, None, "other", "30000")
    db_session.commit()
    r, _data = _row(db_session, job.id)
    assert r["estimated_total_cost"] == 60000.0 and r["percent_complete"] == 50.0


# ── 5. the bank's columns ────────────────────────────────────────────────


def test_gross_profit_to_date_and_backlog(db_session, seed_accounts):
    job = _job(db_session)
    _budget(db_session, job, None, "material", "80000", source="manual")
    _cost(db_session, job, None, "material", "40000")
    _bill_owner(db_session, job, "45000")
    db_session.commit()
    r, data = _row(db_session, job.id)
    assert r["earned_revenue"] == 50000.0
    assert r["gross_profit_to_date"] == 10000.0  # 50,000 earned − 40,000 cost
    assert r["backlog"] == 50000.0  # 100,000 revised − 50,000 earned
    assert r["estimated_gross_profit"] == 20000.0
    assert data["totals"]["gross_profit_to_date"] == 10000.0
    assert data["totals"]["backlog"] == 50000.0
    keys = [c["key"] for c in data["columns"]]
    for key in ("gross_profit_to_date", "backlog", "estimated_gross_profit"):
        assert key in keys


def test_the_screen_shows_the_bank_columns_full_width():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    start = js.index("ReportsPage.wipSchedule = ")
    body = js[start : js.index("ReportsPage.costToComplete = ", start)]
    for text in (
        "r.gross_profit_to_date",
        "r.backlog",
        "r.estimated_gross_profit",
        ">GP to date<",
        ">Backlog<",
        ">Est. GP<",
        "wide: true",
        "_postWip",
        "_voidWip",
        "ln.account_number",
    ):
        assert text in body, text


# ── 6. Post / void ──────────────────────────────────────────────────────


def _setup_and_job(client, db):
    # the schedule's reads are construction mode's (a Business company: 409)
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    assert client.post("/api/construction/setup").status_code == 200
    job = _job(db)
    _budget(db, job, None, "material", "80000", source="manual")
    _cost(db, job, None, "material", "40000")
    _bill_owner(db, job, "60000")  # earned 50,000: over-billed 10,000
    db.commit()
    return job


def _balance(db, number, on):
    """Credit less debit on the account through `on`."""
    acct = _acct(db, number)
    rows = (
        db.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == acct.id, Transaction.date <= on)
        .all()
    )
    return sum(
        (Decimal(str(r.credit)) - Decimal(str(r.debit)) for r in rows), Decimal(0)
    )


def test_setup_seeds_the_wip_accounts(client, seed_accounts):
    r = client.post("/api/construction/setup")
    by_name = {a["name"]: a for a in r.json()}
    want = {
        job_reports.WIP_UNDERBILLING_ACCOUNT: ("1360", "asset", "other_current_asset"),
        job_reports.WIP_OVERBILLING_ACCOUNT: (
            "2060",
            "liability",
            "other_current_liability",
        ),
        job_reports.WIP_LOSS_ACCOUNT: ("2070", "liability", "other_current_liability"),
        job_reports.WIP_REVENUE_ACCOUNT: ("4060", "income", "income"),
        job_reports.WIP_LOSS_EXPENSE_ACCOUNT: ("5600", "cogs", "cost_of_goods_sold"),
    }
    for name, (number, atype, detail) in want.items():
        a = by_name[name]
        assert (a["account_number"], a["account_type"], a["detail_type"]) == (
            number,
            atype,
            detail,
        ), name


def test_entry_names_real_account_numbers(client, db_session, seed_accounts):
    _setup_and_job(client, db_session)
    entry = client.get(f"/api/reports/wip-schedule?as_of={AS_OF}").json()[
        "adjusting_entry"
    ]
    numbers = {ln["account"]: ln["account_number"] for ln in entry["lines"]}
    assert numbers == {
        job_reports.WIP_OVERBILLING_ACCOUNT: "2060",
        job_reports.WIP_REVENUE_ACCOUNT: "4060",
    }
    assert entry["posted"] is False and entry["posted_entry"] is None


def test_post_books_the_entry_and_reverses_it_the_next_day(
    client, db_session, seed_accounts
):
    job = _setup_and_job(client, db_session)
    before = client.get(f"/api/reports/wip-schedule?as_of={AS_OF}").json()

    r = client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["date"] == "2026-06-30" and out["reversal_date"] == "2026-07-01"

    entry = db_session.get(Transaction, out["transaction_id"])
    reversal = db_session.get(Transaction, out["reversal_id"])
    assert entry.source_type == "wip_adjustment" and entry.is_adjusting
    assert reversal.source_type == "wip_reversal" and reversal.source_id == entry.id
    assert entry.date == AS_OF and reversal.date == date(2026, 7, 1)
    lines = db_session.query(TransactionLine).filter_by(transaction_id=entry.id).all()
    assert all(job.name in ln.description for ln in lines)
    assert all(ln.job_id == job.id for ln in lines)  # each line carries its job

    # the balances stand on the as-of date and are gone the next day
    assert _balance(db_session, "2060", AS_OF) == Decimal("10000.00")
    assert _balance(db_session, "4060", AS_OF) == Decimal("-10000.00")
    assert _balance(db_session, "2060", date(2026, 7, 1)) == Decimal("0")
    assert _balance(db_session, "4060", date(2026, 7, 1)) == Decimal("0")

    # posting changes nothing the schedule is computed from
    after = client.get(f"/api/reports/wip-schedule?as_of={AS_OF}").json()
    assert after["rows"] == before["rows"]
    assert after["adjusting_entry"]["posted"] is True
    assert after["adjusting_entry"]["posted_entry"]["transaction_id"] == entry.id


def test_posting_the_same_date_again_is_refused_until_voided(
    client, db_session, seed_accounts
):
    _setup_and_job(client, db_session)
    url = f"/api/reports/wip-schedule/post?as_of={AS_OF}"
    assert client.post(url).status_code == 200
    again = client.post(url)
    assert again.status_code == 409
    assert "Void it first" in again.json()["detail"]

    void = client.post(f"/api/reports/wip-schedule/void?as_of={AS_OF}")
    assert void.status_code == 200, void.text
    assert _balance(db_session, "2060", AS_OF) == Decimal("0")
    assert _balance(db_session, "2060", date(2026, 7, 1)) == Decimal("0")
    status = client.get(f"/api/reports/wip-schedule?as_of={AS_OF}").json()
    assert status["adjusting_entry"]["posted"] is False

    assert client.post(url).status_code == 200
    assert _balance(db_session, "2060", AS_OF) == Decimal("10000.00")
    # nothing left to void a second time once voided
    client.post(f"/api/reports/wip-schedule/void?as_of={AS_OF}")
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={AS_OF}").status_code == 404
    )


def test_journal_void_refuses_the_wip_entry(client, db_session, seed_accounts):
    _setup_and_job(client, db_session)
    out = client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}").json()
    for txn_id in (out["transaction_id"], out["reversal_id"]):
        r = client.post(f"/api/journal/{txn_id}/void")
        assert r.status_code == 400
        assert "Void it on the WIP schedule" in r.json()["detail"]


def test_post_respects_the_closing_date(client, db_session, seed_accounts):
    _setup_and_job(client, db_session)
    db_session.add(Settings(key="closing_date", value="2026-06-30"))
    db_session.commit()
    r = client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}")
    assert r.status_code == 403, r.text
    assert (
        db_session.query(Transaction).filter_by(source_type="wip_adjustment").count()
        == 0
    )


def test_nothing_to_post_is_said(client, db_session, seed_accounts):
    client.post("/api/construction/setup")
    db_session.add(Settings(key="company_type", value="construction"))
    db_session.commit()
    r = client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}")
    assert r.status_code == 422
    assert "Nothing to post" in r.json()["detail"]


def _login_as(client, db, username, role):
    db.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password(OTHER_PW),
            role=role,
            is_active=True,
        )
    )
    db.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": OTHER_PW}
    )
    assert r.status_code == 200, r.text


def test_a_bookkeeper_cannot_post(client, db_session, seed_accounts):
    _setup_and_job(client, db_session)
    _login_as(client, db_session, "keeper", ROLE_BOOKKEEPER)
    r = client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}")
    assert r.status_code == 403
    assert "accountant's or an administrator's" in r.json()["detail"]


def test_an_accountant_posts_and_voids(client, db_session, seed_accounts):
    _setup_and_job(client, db_session)
    _login_as(client, db_session, "cpa", ROLE_ACCOUNTANT)
    r = client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}")
    assert r.status_code == 200, r.text
    assert db_session.get(Transaction, r.json()["transaction_id"]).posted_by == "cpa"
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={AS_OF}").status_code == 200
    )


# ── Companies upgraded from the release before ───────────────────────────


def test_a_job_closed_before_the_upgrade_stays_off_a_back_dated_wip(
    db_session, seed_accounts
):
    """The upgrade leaves the closing day empty, which reads as closed on
    every date: the job is off a WIP dated before today, as it always was,
    and in it under Include closed."""
    job = _job(db_session, status="closed")
    _budget(db_session, job, None, "material", "80000", source="manual")
    _cost(db_session, job, None, "material", "40000")
    db_session.commit()
    assert job.closed_on is None
    plain = job_reports.wip_schedule(db_session, as_of=date(2026, 3, 31))
    assert job.id not in [r["job_id"] for r in plain["rows"]]
    shown = job_reports.wip_schedule(
        db_session, as_of=date(2026, 3, 31), include_closed=True
    )
    assert job.id in [r["job_id"] for r in shown["rows"]]


def test_the_upgrade_does_not_date_a_closed_job(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    db = tmp_path / "closed.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "c7e3a1f5d9b2")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)")
        )
        conn.execute(
            text(
                "INSERT INTO jobs (id, customer_id, name, status, is_active, updated_at) "
                "VALUES (1, 1, 'J', 'closed', 1, '2026-11-05 14:00:00')"
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT closed_on FROM jobs")).scalar() is None


AUG31 = date(2026, 8, 31)


SEP30 = date(2026, 9, 30)


def _approve(client, co_id, on=None):
    body = {"approved_on": on.isoformat()} if on else {}
    r = client.post(f"/api/change-orders/{co_id}/approve", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _co(client, job_id, unit_cost, when=None):
    """A pending owner change order (contract-funded, material, no markup)."""
    body = {
        "job_id": job_id,
        "kind": "owner",
        "funding": "contract",
        "lines": [
            {
                "description": "Added scope",
                "cost_type": "material",
                "quantity": 1,
                "unit_cost": unit_cost,
                "markup_pct": 0,
            }
        ],
    }
    if when is not None:
        body["date"] = when.isoformat()
    r = client.post("/api/change-orders", json=body)
    assert r.status_code == 201, r.text
    co = r.json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    return co


def _over_billed_job(db, name="Clinic"):
    """Contract 100,000, budget 80,000 (material), cost 40,000 by 3/1,
    billed 60,000 by 4/1: earned 50,000, over-billed 10,000."""
    job = _job(db, name=name)
    _budget(db, job, None, "material", "80000", source="manual")
    _cost(db, job, None, "material", "40000")
    _bill_owner(db, job, "60000")
    db.commit()
    return job


def _post(client, as_of):
    r = client.post(f"/api/reports/wip-schedule/post?as_of={as_of}")
    assert r.status_code == 200, r.text
    return r.json()


def _construction(db):
    db.add(Settings(key="company_type", value="construction"))
    db.commit()


def _setup(client, db):
    assert client.post("/api/construction/setup").status_code == 200
    _construction(db)


def test_a_back_dated_approval_names_the_posted_wip_months_it_moves(
    client, db_session, seed_accounts
):
    """Post 8/31 and 9/30, then approve a CO dated
    8/10 (+2,000 cost): the approval says which posted months it changes."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _post(client, AUG31)
    _post(client, SEP30)

    co = _co(client, job.id, 2000, when=date(2026, 8, 10))
    out = _approve(client, co["id"], date(2026, 8, 10))
    assert out["warnings"] == [
        f"{co['number']} takes effect on 2026-08-10, so it changes the WIP "
        "entries posted for 2026-08-31 and 2026-09-30; void and re-post them "
        "on the WIP schedule."
    ]


def test_an_approval_after_the_posted_months_says_nothing_of_them(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _post(client, AUG31)
    _post(client, SEP30)
    # a voided month is not on the books: only 9/30 is named
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={AUG31}").status_code == 200
    )
    co = _co(client, job.id, 2000, when=date(2026, 8, 10))
    out = _approve(client, co["id"], date(2026, 8, 20))
    assert "posted for 2026-09-30; void and re-post it" in out["warnings"][0]
    assert "2026-08-31" not in out["warnings"][0]

    later = _co(client, job.id, 500, when=date(2026, 10, 1))
    assert _approve(client, later["id"], date(2026, 10, 1))["warnings"] == []


def _wip_row(db, job_id, as_of, **kw):
    data = job_reports.wip_schedule(db, as_of=as_of, **kw)
    return next((r for r in data["rows"] if r["job_id"] == job_id), None), data


def test_the_schedule_shows_the_posted_entry_and_says_when_it_differs(
    client, db_session, seed_accounts
):
    """After the back-dated CO the 8/31 schedule reads 10,243.90 over-billed
    while 10,000 is posted: the entry shown is the posted one, with the
    schedule's own figures and a sentence beside it."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _post(client, AUG31)
    data = job_reports.wip_schedule(db_session, AUG31)
    assert data["adjusting_entry"]["posted"] is True
    assert data["adjusting_entry"]["posted_differs"] is False
    assert not any("WIP entry posted" in w for w in data["warnings"])

    co = _co(client, job.id, 2000, when=date(2026, 8, 10))
    _approve(client, co["id"], date(2026, 8, 10))
    db_session.expire_all()

    row, data = _wip_row(db_session, job.id, AUG31)
    entry = data["adjusting_entry"]
    assert row["over_billing"] == 10243.9  # 60,000 − 102,000 × 40/82
    assert entry["posted"] is True and entry["posted_differs"] is True
    # the lines shown are what was posted
    assert entry["totals"]["over_billing"] == 10000.0
    assert entry["jobs"][0]["over_billing"] == 10000.0
    assert entry["total_debits"] == 10000.0
    assert entry["computed"]["totals"]["over_billing"] == 10243.9
    sentence = next(w for w in data["warnings"] if "WIP entry posted" in w)
    assert sentence == entry["posted_warning"]
    assert "books over-billings $10,000.00" in sentence
    assert "the schedule now reads $10,243.90" in sentence
    assert "Void the posted entry and post it again" in sentence

    csv_text = client.get(f"/api/reports/wip-schedule/csv?as_of={AUG31}").text
    assert "books over-billings $10,000.00" in csv_text

    # void and re-post: the books and the schedule agree again
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={AUG31}").status_code == 200
    )
    _post(client, AUG31)
    db_session.expire_all()
    entry = job_reports.wip_schedule(db_session, AUG31)["adjusting_entry"]
    assert entry["posted_differs"] is False
    assert entry["totals"]["over_billing"] == 10243.9


def test_the_wip_screen_shows_the_difference_beside_void():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/reports.js").read_text(
        encoding="utf-8"
    )
    assert "entry.posted_differs" in js and "data-posted-differs" in js
    jobs_js = (Path(__file__).resolve().parents[1] / "app/static/js/jobs.js").read_text(
        encoding="utf-8"
    )
    assert "co-approve-warnings" in jobs_js


SEP30_2 = date(2026, 9, 30)


def test_a_second_post_that_passed_the_check_meets_the_lock_row(
    client, db_session, seed_accounts, monkeypatch
):
    """Both requests read "nothing posted" before either wrote: the lock
    row's unique key, not the re-check, refuses the second."""
    _setup(client, db_session)
    _over_billed_job(db_session)
    monkeypatch.setattr(wip_posting, "posted_entry", lambda db, as_of: None)
    url = f"/api/reports/wip-schedule/post?as_of={SEP30_2}"
    assert client.post(url).status_code == 200
    again = client.post(url)
    assert again.status_code == 409, again.text
    assert "already posted for 2026-09-30" in again.json()["detail"]
    assert (
        db_session.query(Transaction).filter_by(source_type="wip_adjustment").count()
        == 1
    )


def test_void_frees_the_date_for_a_new_post(client, db_session, seed_accounts):
    _setup(client, db_session)
    _over_billed_job(db_session)
    url = f"/api/reports/wip-schedule/post?as_of={SEP30_2}"
    assert client.post(url).status_code == 200
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={SEP30_2}").status_code
        == 200
    )
    assert client.post(url).status_code == 200


def _file_books(tmp_path):
    """A company file on disk, so two threads can each have a session."""
    from app.database import Base, enable_sqlite_tuning
    from app.models.accounts import Account, AccountType
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
    from app.services.accounting import ensure_construction_accounts

    eng = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False}
    )
    enable_sqlite_tuning(eng)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        for data in CHART_OF_ACCOUNTS:
            s.add(
                Account(
                    account_number=data["account_number"],
                    name=data["name"],
                    account_type=AccountType(data["account_type"]),
                    detail_type=data.get("detail_type"),
                    is_system=True,
                    balance=Decimal("0"),
                )
            )
        s.commit()
        ensure_construction_accounts(s)
        _construction(s)
        _over_billed_job(s)
    return eng


def test_two_posts_at_once_only_one_lands(tmp_path, monkeypatch):
    eng = _file_books(tmp_path)
    # both requests read "nothing posted" (the race), so only the lock row
    # can stop the second
    monkeypatch.setattr(wip_posting, "posted_entry", lambda db, as_of: None)
    Local = sessionmaker(bind=eng)
    go = threading.Event()
    results = []

    def post():
        with Local() as s:
            go.wait()
            try:
                wip_posting.post_wip_entry(s, SEP30_2, "admin")
                s.commit()
                results.append(200)
            except Exception as e:  # HTTPException carries the status
                s.rollback()
                results.append(getattr(e, "status_code", repr(e)))

    threads = [threading.Thread(target=post) for _ in range(2)]
    for t in threads:
        t.start()
    go.set()
    for t in threads:
        t.join(30)
    assert sorted(results, key=str) == [200, 409], results
    with Session(eng) as s:
        assert s.query(Transaction).filter_by(source_type="wip_adjustment").count() == 1
    eng.dispose()


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


def test_a_later_change_order_leaves_the_posted_month_alone(
    client, db_session, seed_accounts
):
    """Post 9/30, approve a CO effective 10/15, and the
    9/30 schedule still reproduces the posted entry."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    before, _ = _wip_row(db_session, job.id, SEP30_2)
    posted = client.post(f"/api/reports/wip-schedule/post?as_of={SEP30_2}")
    assert posted.status_code == 200, posted.text

    _approve_co(client, job.id, 20000, OCT15)
    db_session.expire_all()

    row, data = _wip_row(db_session, job.id, SEP30_2)
    assert row["estimated_total_cost"] == before["estimated_total_cost"] == 80000.0
    assert row["over_billing"] == before["over_billing"] == 10000.0
    assert data["adjusting_entry"]["posted"] is True
    assert data["adjusting_entry"]["total_debits"] == posted.json()["total_debits"]
    # on and after the CO's day its budget counts
    later, _ = _wip_row(db_session, job.id, OCT15)
    assert later["estimated_total_cost"] == 100000.0


def test_a_job_closed_in_november_is_in_septembers_wip(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    job.status, job.closed_on = "closed", date(2026, 11, 5)
    db_session.commit()

    row, data = _wip_row(db_session, job.id, SEP30_2)
    assert row is not None and row["percent_complete"] == 50.0  # open on 9/30
    assert [j["job_id"] for j in data["adjusting_entry"]["jobs"]] == [job.id]
    # from the day it closed it is off the board (and 100% with include_closed)
    assert _wip_row(db_session, job.id, date(2026, 11, 30))[0] is None
    closed, _ = _wip_row(db_session, job.id, date(2026, 11, 30), include_closed=True)
    assert closed["percent_complete"] == 100.0


def test_closing_a_job_stamps_the_day_and_reopening_clears_it(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post("/api/jobs", json={"customer_id": seed_customer.id, "name": "J"})
    job_id = r.json()["id"]
    client.put(f"/api/jobs/{job_id}", json={"status": "closed"})
    db_session.expire_all()
    assert db_session.get(Job, job_id).closed_on == date.today()
    client.put(f"/api/jobs/{job_id}", json={"status": "in_progress"})
    db_session.expire_all()
    assert db_session.get(Job, job_id).closed_on is None


def test_the_last_day_of_the_calendar_gets_a_sentence(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    _over_billed_job(db_session)
    r = client.post("/api/reports/wip-schedule/post?as_of=9999-12-31")
    assert r.status_code == 422, r.text
    assert "last day the calendar has" in r.json()["detail"]
    assert client.get("/api/reports/compliance?as_of=9999-12-31").status_code == 200


def test_post_is_refused_outside_construction(client, db_session, seed_accounts):
    client.post("/api/construction/setup")
    _over_billed_job(db_session)
    for mode in ("business", "nonprofit"):
        db_session.query(Settings).filter_by(key="company_type").delete()
        db_session.add(Settings(key="company_type", value=mode))
        db_session.commit()
        r = client.post(f"/api/reports/wip-schedule/post?as_of={SEP30_2}")
        assert r.status_code == 409, r.text
        assert "part of construction mode" in r.json()["detail"]
    assert (
        db_session.query(Transaction).filter_by(source_type="wip_adjustment").count()
        == 0
    )


def _two_jobs(db):
    """One over-billed job and one under-billed: cost 40,000 of 80,000 on a
    100,000 contract, billed 30,000 (earned 50,000)."""
    over = _over_billed_job(db, "Clinic")
    under = _job(db, name="Library")
    _budget(db, under, None, "material", "80000", source="manual")
    _cost(db, under, None, "material", "40000")
    _bill_owner(db, under, "30000")
    db.commit()
    return over, under


def test_posting_ties_job_reports_to_the_schedule(client, db_session, seed_accounts):
    _setup(client, db_session)
    over, under = _two_jobs(db_session)
    before = client.get(f"/api/reports/wip-schedule?as_of={SEP30_2}").json()
    tree_before = job_cost_tree(db_session, over.id, end_date=SEP30_2)["totals"]
    assert (
        client.post(f"/api/reports/wip-schedule/post?as_of={SEP30_2}").status_code
        == 200
    )

    after = client.get(f"/api/reports/wip-schedule?as_of={SEP30_2}").json()
    assert after["rows"] == before["rows"]  # posting moves nothing the WIP reads
    tree_after = job_cost_tree(db_session, over.id, end_date=SEP30_2)["totals"]
    assert (tree_after["actual"], tree_after["act_revenue"]) == (
        tree_before["actual"],
        tree_before["act_revenue"],
    )

    gp = {r["job_id"]: r["gross_profit_to_date"] for r in after["rows"]}
    by_job = client.get(
        f"/api/reports/profit-loss-by-job?start_date=2026-01-01&end_date={SEP30_2}"
    ).json()
    cols = {c["job_id"]: c for c in by_job["jobs"]}
    for job in (over, under):
        assert cols[job.id]["gross_profit"] == gp[job.id], job.name

    prof = client.get(
        f"/api/reports/job-profitability?start_date=2026-01-01&end_date={SEP30_2}"
    ).json()["jobs"]
    assert [r["job_id"] for r in prof if r["job_id"] is None] == []  # no No-job row
    by_id = {r["job_id"]: r for r in prof}
    assert by_id[over.id]["gross_profit"] == gp[over.id] == 10000.0
    assert by_id[under.id]["gross_profit"] == gp[under.id] == 10000.0


def test_a_loss_provision_is_not_the_jobs_cost(client, db_session, seed_accounts):
    """A loss job's provision posts to 5600 on the job; its cost to date,
    forecast and Budget vs Actual stay the job's own cost."""
    _setup(client, db_session)
    job = _job(db_session)
    _budget(db_session, job, None, "material", "120000", source="manual")
    _cost(db_session, job, None, "material", "60000")
    _bill_owner(db_session, job, "50000")
    db_session.commit()
    row, data = _wip_row(db_session, job.id, SEP30_2)
    assert row["loss_provision"] > 0
    assert (
        client.post(f"/api/reports/wip-schedule/post?as_of={SEP30_2}").status_code
        == 200
    )
    db_session.expire_all()
    loss = (
        db_session.query(TransactionLine)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(Account.name == job_reports.WIP_LOSS_EXPENSE_ACCOUNT)
        .all()
    )
    assert len(loss) == 2  # the entry and its reversal
    assert all(ln.job_id == job.id for ln in loss)
    again, _ = _wip_row(db_session, job.id, SEP30_2)
    assert again == row
    assert (
        job_reports.ctc_forecast(db_session, job, SEP30_2)["totals"]["actual"]
        == 60000.0
    )
    bva = {r["job_id"]: r for r in budget_vs_actual_all_jobs(db_session, None, SEP30_2)}
    assert bva[job.id]["actual"] == 60000.0


def test_the_migration_up_down_and_up(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    db = tmp_path / "r1.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "c7e3a1f5d9b2")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)")
        )
        conn.execute(
            text(
                "INSERT INTO jobs (id, customer_id, name, status, is_active, updated_at) "
                "VALUES (1, 1, 'J', 'closed', 1, '2026-11-05 14:00:00')"
            )
        )
        for co_id, number, cost, on in (
            (1, "CO-1", 1000, "2026-08-10"),
            (2, "CO-2", -400, "2026-10-15"),
        ):
            conn.execute(
                text(
                    "INSERT INTO change_orders (id, job_id, number, kind, status, funding, "
                    "apply_markup, revision, allowance_return, approved_on) VALUES "
                    "(:i, 1, :n, 'owner', 'approved', 'contract', 1, 1, 0, :on)"
                ),
                {"i": co_id, "n": number, "on": on},
            )
            conn.execute(
                text(
                    "INSERT INTO change_order_lines (change_order_id, line_order, cost_type, "
                    "quantity, unit_cost, amount, sell_amount) VALUES "
                    "(:i, 0, 'material', 1, :c, :c, :c)"
                ),
                {"i": co_id, "c": cost},
            )
        conn.execute(
            text(
                "INSERT INTO job_budgets (job_id, cost_type, amount, revenue_amount, "
                "source, locked) VALUES (1, 'material', 600, 600, 'change', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO transactions (id, date, description, source_type, source_id) "
                "VALUES (7, '2026-09-30', 'WIP', 'wip_adjustment', 20260930)"
            )
        )
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT change_order_id, amount FROM job_budgets ORDER BY change_order_id"
            )
        ).fetchall()
        assert [(r[0], float(r[1])) for r in rows] == [(1, 1000.0), (2, -400.0)]
        assert conn.execute(text("SELECT closed_on FROM jobs")).scalar() is None
        assert conn.execute(
            text("SELECT as_of, transaction_id FROM wip_entry_locks")
        ).fetchall() == [("2026-09-30", 7)]
    names = {i["name"] for i in inspect(engine).get_indexes("job_budgets")}
    assert {"uq_job_budget", "uq_job_budget_change_order"} <= names

    command.downgrade(cfg, "c7e3a1f5d9b2")
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT amount FROM job_budgets")).fetchall()
        assert [float(r[0]) for r in rows] == [600.0]  # folded back into one row
    assert "wip_entry_locks" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    engine.dispose()
