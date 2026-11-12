"""Job cost and journal entries stay off A/P and A/R; accrued job costs get
an account that reverses itself (#897).

1. A job cost entry (and an allocation), a manual journal entry and a
   memorized journal entry may not put a line on Accounts Payable or
   Accounts Receivable, create or edit, with a sentence saying where the
   amount belongs. An entry already on one is left as it is: an edit that
   keeps the line on its account goes through.
2. 2010 Accrued Job Costs: seeded in a new chart, made on the first accrual
   in an older one. A job cost entry crediting it is an accrual with a
   Reverses on date (default the first of the next month, editable before
   it reverses, clearable); the start-up pass posts the reversal once, the
   entry's Activity says so, WIP and cost-to-complete read the accrued cost
   until then, and Books Check sets the open accruals beside 2010.
3. An imported journal line on A/P or A/R that names its vendor or customer
   is that party's item in the aging and the balance, so Books Check ties; a
   nameless one is named, with where to move it.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.job_costing import JobCost
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine
from app.services import accrued_job_costs, control_accounts
from app.services.books_check import books_check
from app.services.job_costing import ensure_default_cost_types

ROOT = Path(__file__).resolve().parents[1]

AP_SENTENCE = (
    "Line {n} can't post to 2000 Accounts Payable, which holds vendor bills; "
    "enter the cost as a bill (Enter Bill, coded to the job) so it ages, shows "
    "on the vendor and can be paid."
)
AR_SENTENCE = (
    "Line {n} can't post to 1100 Accounts Receivable, which holds customer "
    "invoices; bill the customer with Create Invoice, or credit them with a "
    "credit memo, so it ages, shows on the customer and can be collected."
)
ACCRUED_ON = date(2026, 10, 30)
REVERSES = date(2026, 11, 1)


def _ok(r, codes=(200, 201)):
    assert r.status_code in codes, r.text
    return r.json()


def _ids(seed_accounts):
    return {n: a.id for n, a in seed_accounts.items()}


@pytest.fixture
def job(client, db_session, seed_accounts):
    """A contractor's job with a contract, the cost types seeded."""
    _ok(client.put("/api/settings", json={"company_type": "construction"}))
    _ok(client.post("/api/construction/setup"))
    ensure_default_cost_types(db_session)
    owner = Customer(name="Harbor Owner", is_active=True)
    db_session.add(owner)
    db_session.flush()
    row = Job(
        customer_id=owner.id,
        name="Pier Rebuild",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("100000"),
        original_contract_amount=Decimal("100000"),
    )
    db_session.add(row)
    db_session.commit()
    return row.id


def _cost_body(job_id, debit, credit=None, when=ACCRUED_ON, accrue=False, **extra):
    line = {
        "description": "Framing sub, October",
        "quantity": 1,
        "rate": 4000,
        "debit_account_id": debit,
    }
    if credit is not None:
        line["credit_account_id"] = credit
    if accrue:
        line["accrue"] = True
    return {"date": when.isoformat(), "job_id": job_id, "lines": [line], **extra}


def _balance(db, number, as_of=None, credit_normal=True):
    acct_id = control_accounts.find(db, number)
    q = (
        db.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == acct_id)
    )
    if as_of:
        q = q.filter(Transaction.date <= as_of)
    total = sum(
        (Decimal(str(ln.credit)) - Decimal(str(ln.debit)) for ln in q.all()),
        Decimal("0"),
    )
    return total if credit_normal else -total


def _rows(db, as_of=None):
    db.expire_all()
    return {r["key"]: r for r in books_check(db, as_of)["rows"]}


def _manual_entry(db, lines, when=date(2026, 9, 1), source_type="manual", **kw):
    """A journal entry as it was saved before 2.32 (straight to the ledger)."""
    txn = Transaction(
        date=when, description="before 2.32", source_type=source_type, **kw
    )
    db.add(txn)
    db.flush()
    for values in lines:
        db.add(TransactionLine(transaction_id=txn.id, **values))
    db.commit()
    return txn


# ── 1. the refusals ──────────────────────────────────────────────────────


def test_a_job_cost_entry_crediting_accounts_payable_is_refused(
    client, job, seed_accounts, db_session
):
    a = _ids(seed_accounts)
    r = client.post("/api/job-costs", json=_cost_body(job, a["5300"], a["2000"]))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail.startswith(AP_SENTENCE.format(n=1))
    # a job cost entry is told where an accrual goes instead
    assert "2010 Accrued Job Costs" in detail
    assert db_session.query(JobCost).count() == 0


def test_a_job_cost_entry_on_accounts_receivable_is_refused_naming_its_line(
    client, job, seed_accounts
):
    a = _ids(seed_accounts)
    body = _cost_body(job, a["5300"], a["2500"])
    body["lines"].append(
        {
            "description": "Back-charge",
            "quantity": 1,
            "rate": 50,
            "debit_account_id": a["1100"],
            "credit_account_id": a["2500"],
        }
    )
    r = client.post("/api/job-costs", json=body)
    assert r.status_code == 422
    assert r.json()["detail"] == AR_SENTENCE.format(n=2)


def test_an_allocation_on_accounts_payable_is_refused(client, job, seed_accounts):
    a = _ids(seed_accounts)
    r = client.post(
        "/api/job-costs/allocate",
        json={
            "date": ACCRUED_ON.isoformat(),
            "amount": 900,
            "method": "equal",
            "debit_account_id": a["5300"],
            "credit_account_id": a["2000"],
            "targets": [{"job_id": job}],
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith(
        "The allocation can't post to 2000 Accounts Payable, which holds vendor bills"
    )


@pytest.mark.parametrize(
    "number,sentence", [("2000", AP_SENTENCE), ("1100", AR_SENTENCE)]
)
def test_a_journal_entry_on_a_control_account_is_refused(
    client, seed_accounts, db_session, number, sentence
):
    a = _ids(seed_accounts)
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-10-31",
            "description": "month-end",
            "lines": [
                {"account_id": a["6000"], "debit": "300", "credit": "0"},
                {"account_id": a[number], "debit": "0", "credit": "300"},
            ],
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == sentence.format(n=2)
    assert db_session.query(Transaction).filter_by(description="month-end").count() == 0


def test_a_journal_edit_onto_a_control_account_is_refused(client, seed_accounts):
    a = _ids(seed_accounts)
    made = _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-10-31",
                "description": "accrue rent",
                "lines": [
                    {"account_id": a["6000"], "debit": "300", "credit": "0"},
                    {"account_id": a["2500"], "debit": "0", "credit": "300"},
                ],
            },
        )
    )
    for number, sentence in (("2000", AP_SENTENCE), ("1100", AR_SENTENCE)):
        r = client.put(
            f"/api/journal/{made['id']}",
            json={
                "date": "2026-10-31",
                "description": "accrue rent",
                "lines": [
                    {"account_id": a["6000"], "debit": "300", "credit": "0"},
                    {"account_id": a[number], "debit": "0", "credit": "300"},
                ],
            },
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == sentence.format(n=2)


def test_an_entry_already_on_accounts_payable_keeps_its_line_through_an_edit(
    client, seed_accounts, db_session
):
    """Existing entries are left exactly as they are: a memo fix to one saved
    before 2.32 goes through, and its line stays on A/P."""
    a = _ids(seed_accounts)
    txn = _manual_entry(
        db_session,
        [
            {"account_id": a["6000"], "debit": Decimal("120"), "credit": Decimal("0")},
            {"account_id": a["2000"], "debit": Decimal("0"), "credit": Decimal("120")},
        ],
    )
    r = client.put(
        f"/api/journal/{txn.id}",
        json={
            "date": "2026-09-01",
            "description": "memo fixed",
            "lines": [
                {"account_id": a["6000"], "debit": "120", "credit": "0"},
                {"account_id": a["2000"], "debit": "0", "credit": "120"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    accounts = {ln.account_id for ln in db_session.get(Transaction, txn.id).lines}
    assert a["2000"] in accounts
    # ...but it cannot gain an A/R line it never had
    r = client.put(
        f"/api/journal/{txn.id}",
        json={
            "date": "2026-09-01",
            "description": "memo fixed",
            "lines": [
                {"account_id": a["1100"], "debit": "120", "credit": "0"},
                {"account_id": a["2000"], "debit": "0", "credit": "120"},
            ],
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == AR_SENTENCE.format(n=1)


def test_a_memorized_journal_entry_on_accounts_payable_is_refused(
    client, seed_accounts
):
    a = _ids(seed_accounts)
    r = client.post(
        "/api/memorized",
        json={
            "kind": "journal",
            "name": "Monthly accrual",
            "mode": "remind",
            "frequency": "monthly",
            "start_date": "2026-11-30",
            "description": "accrue",
            "lines": [
                {"account_id": a["6000"], "debit": "300", "credit": "0"},
                {"account_id": a["2000"], "debit": "0", "credit": "300"},
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == AP_SENTENCE.format(n=2)


def test_the_refusals_resolve_the_accounts_through_the_registry():
    src = (ROOT / "app/services/journal_controls.py").read_text(encoding="utf-8")
    assert "control_accounts.is_receivable_or_payable" in src
    assert '"2000"' not in src and '"1100"' not in src


# ── 2. accrued job costs ─────────────────────────────────────────────────


def _accrue(client, job, seed_accounts, **extra):
    a = _ids(seed_accounts)
    return _ok(
        client.post(
            "/api/job-costs", json=_cost_body(job, a["5300"], accrue=True, **extra)
        )
    )


def test_2010_is_seeded_in_a_new_chart_and_registered():
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

    seeded = {e["account_number"]: e for e in CHART_OF_ACCOUNTS}
    assert seeded["2010"]["name"] == "Accrued Job Costs"
    assert seeded["2010"]["account_type"] == "liability"
    assert control_accounts.is_control("2010", "liability", "Accrued Job Costs")
    # optional: the boot check never asks an older chart for it
    assert "2010" in control_accounts.ON_DEMAND
    assert "2010" in control_accounts.seeded_numbers()


def test_an_accrual_posts_reads_in_wip_and_reverses_once_on_its_date(
    client, job, seed_accounts, db_session
):
    jc = _accrue(client, job, seed_accounts)
    assert jc["reverses_on"] == REVERSES.isoformat()  # the next month's 1st
    assert float(jc["accrued"]) == 4000.0 and jc["reversal_transaction_id"] is None
    assert _balance(db_session, "2010") == Decimal("4000")

    # the WIP schedule and cost-to-complete read the accrued cost
    wip = _ok(client.get(f"/api/reports/wip-schedule?as_of={ACCRUED_ON}"))
    row = next(r for r in wip["rows"] if r["job_id"] == job)
    assert row["cost_to_date"] == 4000.0
    ctc = _ok(client.get(f"/api/reports/cost-to-complete?as_of={ACCRUED_ON}"))
    assert (
        next(j for j in ctc["jobs"] if j["job_id"] == job)["totals"]["actual"] == 4000.0
    )

    # Books Check: the open accrual is 2010
    rows = _rows(db_session, ACCRUED_ON)
    acc = rows["accrued_job_costs"]
    assert acc["status"] == "pass" and acc["subledger"] == acc["control"] == 4000.0

    # the day before its date the start-up pass posts nothing
    assert accrued_job_costs.reverse_due_accruals(db_session, date(2026, 10, 31)) == {
        "reversed": [],
        "skipped": [],
    }
    done = accrued_job_costs.reverse_due_accruals(db_session, REVERSES)
    assert done["reversed"] == [jc["number"]]
    sentence = accrued_job_costs.reversal_sentence(done)
    assert jc["number"] in sentence and "reversed 1 accrual" in sentence
    # a restart posts nothing more
    assert accrued_job_costs.reverse_due_accruals(db_session, date(2026, 12, 5)) == {
        "reversed": [],
        "skipped": [],
    }
    db_session.expire_all()
    reversals = (
        db_session.query(Transaction)
        .filter_by(source_type="job_cost_reversal", source_id=jc["id"])
        .all()
    )
    assert len(reversals) == 1 and reversals[0].date == REVERSES
    assert _balance(db_session, "2010") == Decimal("0")

    after = _ok(client.get(f"/api/job-costs/{jc['id']}"))
    assert after["reversal_transaction_id"] == reversals[0].id
    assert after["reversed_on"] == REVERSES.isoformat()

    # the cost leaves the job on the reversal date (the bill brings it back)
    wip = _ok(client.get(f"/api/reports/wip-schedule?as_of={REVERSES}"))
    assert next(r for r in wip["rows"] if r["job_id"] == job)["cost_to_date"] == 0.0
    wip = _ok(client.get(f"/api/reports/wip-schedule?as_of={ACCRUED_ON}"))
    assert next(r for r in wip["rows"] if r["job_id"] == job)["cost_to_date"] == 4000.0

    # Books Check ties after the reversal, and before it as of the accrual
    rows = _rows(db_session)
    acc = rows["accrued_job_costs"]
    assert acc["status"] == "pass" and acc["subledger"] == acc["control"] == 0.0
    assert _rows(db_session, ACCRUED_ON)["accrued_job_costs"]["subledger"] == 4000.0

    # the entry's Activity names the reversal
    activity = _ok(client.get(f"/api/activity?type=job_cost&id={jc['id']}"))
    texts = [e["text"] for e in activity["events"]]
    assert any(
        t.startswith("Accrual reversed on 2026-11-01 by the system") for t in texts
    ), texts


def test_the_start_up_pass_runs_the_reversals():
    src = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "reverse_due_accruals(_db)" in src and "reversal_sentence" in src


def test_two_passes_racing_post_one_reversal(client, job, seed_accounts, db_session):
    """The claim is a guarded UPDATE: a pass that finds the entry already
    claimed (another process got there first) posts nothing."""
    jc = _accrue(client, job, seed_accounts)
    entry = db_session.get(JobCost, jc["id"])
    acct_id = accrued_job_costs.account_id(db_session)
    first = accrued_job_costs.post_reversal(db_session, entry, acct_id)
    assert first is not None
    db_session.commit()
    db_session.expire_all()
    entry = db_session.get(JobCost, jc["id"])
    entry.reversal_transaction_id = None  # as the second process read it
    db_session.flush()
    db_session.query(JobCost).filter_by(id=entry.id).update(
        {JobCost.reversal_transaction_id: first.id}, synchronize_session=False
    )
    assert accrued_job_costs.post_reversal(db_session, entry, acct_id) is None
    db_session.rollback()
    assert (
        db_session.query(Transaction)
        .filter_by(source_type="job_cost_reversal", source_id=jc["id"])
        .count()
        == 1
    )


def test_the_reverses_on_date_is_editable_until_it_reverses(
    client, job, seed_accounts, db_session
):
    jc = _accrue(client, job, seed_accounts, reverses_on="2026-11-15")
    assert jc["reverses_on"] == "2026-11-15"
    moved = _ok(
        client.put(
            f"/api/job-costs/{jc['id']}/reverses-on", json={"reverses_on": "2026-11-20"}
        )
    )
    assert moved["reverses_on"] == "2026-11-20"
    r = client.put(
        f"/api/job-costs/{jc['id']}/reverses-on", json={"reverses_on": "2026-10-30"}
    )
    assert r.status_code == 422 and "after the entry's date" in r.json()["detail"]
    cleared = _ok(
        client.put(f"/api/job-costs/{jc['id']}/reverses-on", json={"reverses_on": None})
    )
    assert cleared["reverses_on"] is None
    # cleared: the pass leaves it open, and Books Check counts it open
    assert (
        accrued_job_costs.reverse_due_accruals(db_session, date(2027, 1, 1))["reversed"]
        == []
    )
    acc = _rows(db_session)["accrued_job_costs"]
    assert acc["status"] == "pass" and acc["subledger"] == 4000.0
    _ok(
        client.put(
            f"/api/job-costs/{jc['id']}/reverses-on", json={"reverses_on": "2026-11-02"}
        )
    )
    assert accrued_job_costs.reverse_due_accruals(db_session, date(2026, 11, 2))[
        "reversed"
    ] == [jc["number"]]
    r = client.put(
        f"/api/job-costs/{jc['id']}/reverses-on", json={"reverses_on": "2026-12-01"}
    )
    assert r.status_code == 409 and "already reversed" in r.json()["detail"]
    # an ordinary entry has no reversal date
    a = _ids(seed_accounts)
    plain = _ok(
        client.post("/api/job-costs", json=_cost_body(job, a["5300"], a["2500"]))
    )
    assert plain["reverses_on"] is None
    r = client.put(
        f"/api/job-costs/{plain['id']}/reverses-on", json={"reverses_on": "2026-12-01"}
    )
    assert r.status_code == 422


def test_an_accrual_sent_with_no_date_stays_open(
    client, job, seed_accounts, db_session
):
    jc = _accrue(client, job, seed_accounts, reverses_on=None)
    assert jc["reverses_on"] is None
    assert (
        accrued_job_costs.reverse_due_accruals(db_session, date(2027, 6, 1))["reversed"]
        == []
    )


def test_naming_the_2010_account_by_id_is_an_accrual_too(client, job, seed_accounts):
    a = _ids(seed_accounts)
    jc = _ok(client.post("/api/job-costs", json=_cost_body(job, a["5300"], a["2010"])))
    assert jc["reverses_on"] == REVERSES.isoformat() and float(jc["accrued"]) == 4000.0


def test_a_chart_without_2010_gets_it_on_the_first_accrual(
    client, job, seed_accounts, db_session
):
    seed_accounts.pop("2010")
    db_session.query(Account).filter(Account.account_number == "2010").delete()
    db_session.commit()
    # the boot check does not ask for it
    assert "2010" not in {n for n, _ in control_accounts.missing(db_session)}
    assert "accrued_job_costs" not in _rows(db_session)  # nothing accrued, no row
    jc = _accrue(client, job, seed_accounts)
    acct = db_session.query(Account).filter(Account.account_number == "2010").one()
    assert acct.name == "Accrued Job Costs"
    assert acct.account_type == AccountType.LIABILITY
    assert jc["lines"][0]["credit_account_id"] == acct.id
    assert _rows(db_session, ACCRUED_ON)["accrued_job_costs"]["status"] == "pass"


def test_a_foreign_account_on_2010_is_never_taken_over(
    client, job, seed_accounts, db_session
):
    acct = db_session.query(Account).filter(Account.account_number == "2010").one()
    acct.name = "Line of Credit"
    db_session.commit()
    a = _ids(seed_accounts)
    r = client.post("/api/job-costs", json=_cost_body(job, a["5300"], accrue=True))
    assert r.status_code == 409
    assert "Account number 2010 is Line of Credit" in r.json()["detail"]
    # an older chart's own 2010 is an ordinary account: not flagged, and it
    # renumbers (then the first accrual makes 2010 Accrued Job Costs)
    assert control_accounts.is_control("2010", "liability", "Line of Credit") is False
    row = next(x for x in _ok(client.get("/api/accounts")) if x["id"] == acct.id)
    assert row["is_control"] is False
    _ok(client.put(f"/api/accounts/{acct.id}", json={"account_number": "2410"}))
    jc = _ok(
        client.post("/api/job-costs", json=_cost_body(job, a["5300"], accrue=True))
    )
    assert float(jc["accrued"]) == 4000.0


def test_voiding_an_accrual_after_it_reversed_takes_the_reversal_too(
    client, job, seed_accounts, db_session
):
    jc = _accrue(client, job, seed_accounts)
    accrued_job_costs.reverse_due_accruals(db_session, REVERSES)
    _ok(client.post(f"/api/job-costs/{jc['id']}/void"))
    db_session.expire_all()
    assert _balance(db_session, "2010") == Decimal("0")
    wip = _ok(client.get(f"/api/reports/wip-schedule?as_of={date(2026, 12, 1)}"))
    assert next(r for r in wip["rows"] if r["job_id"] == job)["cost_to_date"] == 0.0
    acc = _rows(db_session)["accrued_job_costs"]
    assert acc["status"] == "pass", acc
    assert _rows(db_session, ACCRUED_ON)["accrued_job_costs"]["status"] == "pass"


def test_books_check_names_an_entry_on_2010_that_no_accrual_made(
    client, job, seed_accounts, db_session
):
    a = _ids(seed_accounts)
    _accrue(client, job, seed_accounts)
    txn = _manual_entry(
        db_session,
        [
            {"account_id": a["6000"], "debit": Decimal("75"), "credit": Decimal("0")},
            {"account_id": a["2010"], "debit": Decimal("0"), "credit": Decimal("75")},
        ],
        when=ACCRUED_ON,
    )
    acc = _rows(db_session, ACCRUED_ON)["accrued_job_costs"]
    assert acc["status"] == "difference" and acc["difference"] == -75.0
    kinds = {(d["type"], d["id"]) for d in acc["documents"]}
    assert ("journal", txn.id) in kinds
    assert any(d["type"] == "job_cost" for d in acc["documents"])


# ── 3. imported books tie ────────────────────────────────────────────────

IIF_HEADER = (
    "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
    "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
    "!ENDTRNS\n"
)


def _iif_journal(name, docnum, amount="300.00"):
    return (
        f"TRNS\t\tGENERAL JOURNAL\t09/30/2026\tSubcontractor Costs\t\t{amount}\t{docnum}\tSub at month-end\n"
        f"SPL\t\tGENERAL JOURNAL\t09/30/2026\tAccounts Payable\t{name}\t-{amount}\t{docnum}\t\n"
        "ENDTRNS\n"
    )


def test_an_imported_vendor_named_payables_line_ties(client, seed_accounts, db_session):
    from app.services.iif_import import import_all

    vendor = Vendor(name="Coastline Framing", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    result = import_all(
        db_session, IIF_HEADER + _iif_journal("Coastline Framing", "GJ-1")
    )
    assert result["errors"] == [], result
    db_session.expire_all()
    txn = db_session.query(Transaction).filter_by(reference="GJ-1").one()
    assert txn.vendor_id == vendor.id

    ap = _ok(client.get("/api/reports/ap-aging?as_of_date=2026-10-15"))
    row = next(r for r in ap["items"] if r["vendor_id"] == vendor.id)
    assert row["total"] == 300.0 and row["over_30"] == 300.0
    from app.services.contact_balances import vendor_balance, vendor_balances

    assert vendor_balance(db_session, vendor.id) == Decimal("300.00")
    assert vendor_balances(db_session, iter([vendor.id]))[vendor.id] == Decimal(
        "300.00"
    )

    rows = _rows(db_session)
    for key in ("ap_aging", "vendor_balances"):
        assert rows[key]["status"] == "pass", rows[key]


def test_an_imported_customer_named_receivables_line_ties(
    client, seed_accounts, db_session
):
    from app.services.iif_import import import_all

    customer = Customer(name="Dockside Marina", is_active=True)
    db_session.add(customer)
    db_session.commit()
    text = IIF_HEADER + (
        "TRNS\t\tGENERAL JOURNAL\t09/30/2026\tAccounts Receivable\tDockside Marina\t250.00\tGJ-2\tCarried balance\n"
        "SPL\t\tGENERAL JOURNAL\t09/30/2026\tService Income\t\t-250.00\tGJ-2\t\n"
        "ENDTRNS\n"
    )
    result = import_all(db_session, text)
    assert result["errors"] == [], result
    ar = _ok(client.get("/api/reports/ar-aging?as_of_date=2026-09-30"))
    row = next(r for r in ar["items"] if r["customer_id"] == customer.id)
    assert row["total"] == 250.0 and row["current"] == 250.0
    from app.services.contact_balances import customer_balance

    assert customer_balance(db_session, customer.id) == Decimal("250.00")
    rows = _rows(db_session)
    for key in ("ar_aging", "customer_balances"):
        assert rows[key]["status"] == "pass", rows[key]


def test_a_nameless_payables_line_is_named_with_where_it_belongs(
    client, seed_accounts, db_session
):
    from app.services.iif_import import import_all

    result = import_all(db_session, IIF_HEADER + _iif_journal("", "GJ-3"))
    assert result["errors"] == [], result
    txn = db_session.query(Transaction).filter_by(reference="GJ-3").one()
    assert txn.vendor_id is None
    rows = _rows(db_session)
    ap = rows["ap_aging"]
    assert ap["status"] == "difference" and ap["difference"] == -300.0
    named = [d for d in ap["documents"] if d["id"] == txn.id]
    assert named and named[0]["amount"] == 300.0
    assert "move it to a bill or to 2010 Accrued Job Costs" in named[0]["why"]


def test_the_bakery_entry_on_accounts_payable_is_untouched(client, books, db_session):
    """The bakery's job cost entry, saved on A/P before 2.32, is still there
    and still named by Books Check."""
    jc = db_session.get(JobCost, books["job_cost"])
    assert jc.status == "posted" and jc.reverses_on is None
    assert jc.lines[0].credit_account.account_number == "2000"
    ap = _rows(db_session)["ap_aging"]
    assert ap["status"] == "difference" and ap["difference"] == -300.0
    named = [d for d in ap["documents"] if d["type"] == "job_cost"]
    assert [d["id"] for d in named] == [books["job_cost"]]
    assert "move it to a bill or to 2010 Accrued Job Costs" in named[0]["why"]


# ── the migration ────────────────────────────────────────────────────────


def test_the_accrual_migration_is_its_own_step(tmp_path):
    import sqlite3

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "accrual.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("d897a2c4e6f1").down_revision == "c894a1e7d2b3"
    assert script.get_heads() == ["d897a2c4e6f1"]
    command.upgrade(cfg, "c894a1e7d2b3")  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO job_costs (id, number, date, source, status, total)"
            " VALUES (1, 'JC-000001', '2026-09-12', 'manual', 'posted', 300)"
        )
    command.upgrade(cfg, "d897a2c4e6f1")  # its own step (#770)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job_costs)")}
        assert {"reverses_on", "reversal_transaction_id"} <= cols
        fks = {r[2] for r in conn.execute("PRAGMA foreign_key_list(job_costs)")}
        assert "transactions" in fks
        # the entry from before is an ordinary one: no date, no reversal
        assert conn.execute(
            "SELECT reverses_on, reversal_transaction_id FROM job_costs"
        ).fetchall() == [(None, None)]
    command.downgrade(cfg, "c894a1e7d2b3")
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job_costs)")}
        assert not {"reverses_on", "reversal_transaction_id"} & cols
        assert conn.execute("SELECT number FROM job_costs").fetchall() == [
            ("JC-000001",)
        ]


def test_a_quickbooks_ledger_journal_naming_its_vendor_ties(
    client, seed_accounts, db_session
):
    """A Transaction Detail by Account report's journal on A/P names its
    vendor in the Name column: the entry keeps it, the aging and the vendor
    balance read it, and the import's warning is only for nameless ones."""
    from tests.test_qb_desktop_ledger import _upload
    from tests.test_qb_ledger_payees import _report

    vendor = Vendor(name="Coastline Framing", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    data = _upload(
        client,
        _report(
            [
                (
                    "General Journal",
                    "09/30/2026",
                    "GJ-9",
                    [
                        ("Subcontractor Costs", "", "Sub", "300.00", ""),
                        ("Accounts Payable", "Coastline Framing", "Sub", "", "300.00"),
                    ],
                )
            ]
        ),
    )
    assert data["errors"] == [], data
    assert not any("without naming" in w for w in data.get("warnings", [])), data
    db_session.expire_all()
    txn = db_session.query(Transaction).filter_by(reference="GJ-9").one()
    assert txn.vendor_id == vendor.id
    from app.services.contact_balances import vendor_balance

    assert vendor_balance(db_session, vendor.id) == Decimal("300.00")
    rows = _rows(db_session)
    assert rows["ap_aging"]["status"] == "pass", rows["ap_aging"]
    assert rows["vendor_balances"]["status"] == "pass", rows["vendor_balances"]
