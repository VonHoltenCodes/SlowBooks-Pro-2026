"""The WIP schedule a bank can read (construction overhaul, workstream H):
cost types net within a section, budgets that never burn down are named, a
closed or finished job reads finished, a change order's budget alone is no
estimate, the bank's columns (GP to date, backlog), and Post / Void of the
adjusting entry with its next-day reversal on construction setup's accounts.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

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
from app.services import job_reports
from app.services.accounting import create_journal_entry
from app.services.job_costing import ensure_default_cost_types

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
    """The persona job in small: drywall bid as subcontract 96,000, bought as
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
