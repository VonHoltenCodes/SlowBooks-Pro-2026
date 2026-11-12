"""Make Deposits takes lines for money that was never invoiced (#792).

A check comes in for something nobody invoiced: a reimbursement, a vendor's
refund, a rebate. It goes on the same slip as the day's customer checks, as
a line naming who it came from, the account it goes to, a memo, the check
number, the method, a class and a job. The deposit is the payments taken
plus these lines: one debit to the bank and a credit per line, with the
line's customer, class and job. The slip prints them with the checks; the
edit, the void, the register and the drill-downs carry them; on the cash
basis an income line is cash on the deposit's date. A bank account,
Undeposited Funds, Accounts Receivable or Accounts Payable on a line is
refused in a sentence, and nothing posts.
"""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.deposit_lines import DepositOtherLine
from app.models.transactions import Transaction, TransactionLine
from app.models.users import ROLE_ACCOUNTANT, ROLE_BOOKKEEPER, ROLE_READONLY, User
from app.services import auth as auth_service
from app.services import pdf_service

D = Decimal
DAY = "2026-09-12"


@pytest.fixture
def acme(client):
    return client.post("/api/customers", json={"name": "Acme Diner"}).json()["id"]


@pytest.fixture
def supplier(client):
    r = client.post("/api/vendors", json={"name": "Flour Mill Co"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _receive(client, cid, amount, check=None, method="Check"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "method": method,
            "check_number": check,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _deposit(client, accounts, line_ids=(), other_lines=(), **extra):
    return client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": accounts["1000"].id,
            "date": DAY,
            "reference": "SLIP-9",
            "line_ids": list(line_ids),
            "other_lines": list(other_lines),
            **extra,
        },
    )


def _lines(db, txn_id):
    return (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == txn_id)
        .order_by(TransactionLine.id)
        .all()
    )


def _deposit_count(db):
    return db.query(Transaction).filter(Transaction.source_type == "deposit").count()


@pytest.fixture
def mixed(client, db_session, seed_accounts, acme, supplier):
    """Two customer checks taken from the list, an insurance refund from a
    vendor and a rebate from someone on no list, on one slip."""
    _receive(client, acme, "300.00", check="4420")
    _receive(client, acme, "100.00", check="4421")
    pending = client.get("/api/deposits/pending").json()
    cls = client.post("/api/classes", json={"name": "Bakery"}).json()
    r = _deposit(
        client,
        seed_accounts,
        line_ids=[p["transaction_line_id"] for p in pending],
        other_lines=[
            {
                "vendor_id": supplier,
                "account_id": seed_accounts["6300"].id,
                "memo": "Policy refund",
                "check_number": "88123",
                "method": "Check",
                "amount": "45.50",
            },
            {
                "received_from": "State Energy Office",
                "account_id": seed_accounts["4900"].id,
                "memo": "Oven rebate",
                "check_number": "5001",
                "method": "Check",
                "class_id": cls["id"],
                "amount": "120.00",
            },
        ],
        total="565.50",
    )
    assert r.status_code == 200, r.text
    return {"id": r.json()["transaction_id"], "class_id": cls["id"], **r.json()}


# --- create and the posting -------------------------------------------------


def test_the_deposit_is_the_payments_plus_the_other_lines(
    client, db_session, seed_accounts, mixed
):
    assert mixed["amount"] == 565.50
    assert mixed["items"] == 2 and mixed["other_items"] == 2
    lines = _lines(db_session, mixed["id"])
    by_acct = {}
    for ln in lines:
        by_acct.setdefault(ln.account_id, []).append(ln)
    bank = by_acct[seed_accounts["1000"].id]
    assert [D(str(b.debit)) for b in bank] == [D("565.50")]
    assert [D(str(u.credit)) for u in by_acct[seed_accounts["1200"].id]] == [
        D("400.00")
    ]
    assert [D(str(x.credit)) for x in by_acct[seed_accounts["6300"].id]] == [D("45.50")]
    rebate = by_acct[seed_accounts["4900"].id][0]
    assert D(str(rebate.credit)) == D("120.00")
    assert rebate.class_id == mixed["class_id"]
    assert rebate.description == "Oven rebate"
    # it foots: one debit, the credits add up to it
    assert sum(D(str(ln.debit)) for ln in lines) == sum(
        D(str(ln.credit)) for ln in lines
    )
    # the payments left the list
    assert client.get("/api/deposits/pending").json() == []


def test_the_deposit_page_lists_the_lines_by_name(client, mixed):
    d = client.get(f"/api/deposits/{mixed['id']}").json()
    assert d["amount"] == "565.50" or D(str(d["amount"])) == D("565.50")
    assert d["payments_total"] == 400.0 and d["other_total"] == 165.5
    names = [
        (o["received_from"], o["account_name"], o["amount"]) for o in d["other_lines"]
    ]
    assert names == [
        ("Flour Mill Co", "Insurance", 45.5),
        ("State Energy Office", "Other Income", 120.0),
    ]
    assert d["other_lines"][1]["class_name"] == "Bakery"
    assert d["other_lines"][0]["check_number"] == "88123"
    listed = client.get("/api/deposits").json()[0]
    assert listed["items"] == 2 and listed["other_items"] == 2


def test_a_deposit_of_other_lines_alone_takes_nothing_from_undeposited_funds(
    client, db_session, seed_accounts, acme
):
    _receive(client, acme, "50.00")
    r = _deposit(
        client,
        seed_accounts,
        other_lines=[
            {
                "customer_id": acme,
                "account_id": seed_accounts["6400"].id,
                "memo": "Paid us back for supplies",
                "amount": "18.25",
            }
        ],
    )
    assert r.status_code == 200, r.text
    lines = _lines(db_session, r.json()["transaction_id"])
    assert {ln.account_id for ln in lines} == {
        seed_accounts["1000"].id,
        seed_accounts["6400"].id,
    }
    credit = next(ln for ln in lines if ln.credit > 0)
    assert credit.customer_id == acme  # the name rides on the posting
    # the customer's payment is still waiting
    assert len(client.get("/api/deposits/pending").json()) == 1


def test_a_total_that_does_not_match_is_refused(
    client, db_session, seed_accounts, acme
):
    _receive(client, acme, "100.00")
    pending = client.get("/api/deposits/pending").json()
    r = _deposit(
        client,
        seed_accounts,
        line_ids=[pending[0]["transaction_line_id"]],
        other_lines=[{"account_id": seed_accounts["4900"].id, "amount": "10.00"}],
        total="100.00",
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "The payments ticked and the other lines add up to $110.00, not "
        "$100.00. Reload Make Deposits and try again."
    )
    assert _deposit_count(db_session) == 0


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    "number, words",
    [
        ("1010", "Savings is a bank account."),
        ("2100", "Credit Card is a credit card account."),
        ("1200", "Undeposited Funds is where payments wait for the bank"),
        ("1100", "Accounts Receivable is Accounts Receivable."),
        ("2000", "Accounts Payable is Accounts Payable."),
    ],
)
def test_an_account_a_line_cannot_take_is_refused_in_a_sentence(
    client, db_session, seed_accounts, acme, number, words
):
    _receive(client, acme, "100.00")
    pending = client.get("/api/deposits/pending").json()
    r = _deposit(
        client,
        seed_accounts,
        line_ids=[pending[0]["transaction_line_id"]],
        other_lines=[
            {"account_id": seed_accounts["4900"].id, "amount": "5.00"},
            {"account_id": seed_accounts[number].id, "amount": "10.00"},
        ],
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Other deposits, line 2: ")
    assert words in detail and detail.endswith("Nothing was saved.")
    # nothing posted, nothing taken off the list
    assert _deposit_count(db_session) == 0
    assert db_session.query(DepositOtherLine).count() == 0
    assert len(client.get("/api/deposits/pending").json()) == 1


def test_a_line_of_nothing_or_an_inactive_account_is_refused(
    client, db_session, seed_accounts, acme, supplier
):
    r = _deposit(
        client,
        seed_accounts,
        other_lines=[{"account_id": seed_accounts["4900"].id, "amount": "0"}],
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Other deposits, line 1: enter the amount that came in (more than "
        "$0.00), or remove the line. Nothing was saved."
    )
    old = seed_accounts["4300"]
    old.is_active = False
    db_session.commit()
    r = _deposit(
        client, seed_accounts, other_lines=[{"account_id": old.id, "amount": "9"}]
    )
    assert r.status_code == 400
    assert "Labor Income is inactive in the chart of accounts" in r.json()["detail"]
    r = _deposit(
        client,
        seed_accounts,
        other_lines=[
            {
                "account_id": seed_accounts["4900"].id,
                "amount": "9",
                "customer_id": acme,
                "vendor_id": supplier,
            }
        ],
    )
    assert r.status_code == 400
    assert "names both a customer and a vendor" in r.json()["detail"]
    assert _deposit_count(db_session) == 0


def test_a_job_of_another_customer_is_refused(client, db_session, seed_accounts, acme):
    other = client.post("/api/customers", json={"name": "Corner Cafe"}).json()["id"]
    job = client.post("/api/jobs", json={"customer_id": other, "name": "Remodel"})
    assert job.status_code in (200, 201), job.text
    r = _deposit(
        client,
        seed_accounts,
        other_lines=[
            {
                "customer_id": acme,
                "job_id": job.json()["id"],
                "account_id": seed_accounts["4900"].id,
                "amount": "9",
            }
        ],
    )
    assert r.status_code == 400
    assert "belongs to another customer" in r.json()["detail"]
    assert _deposit_count(db_session) == 0


# --- the slip -----------------------------------------------------------------


def test_the_slip_prints_the_other_lines_with_the_checks(client, mixed, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    html = client.get(f"/api/deposits/{mixed['id']}/pdf").text
    for text in (
        "Flour Mill Co",
        "Insurance",
        "Policy refund",
        "88123",
        "State Energy Office",
        "Oven rebate",
        "5001",
        "$45.50",
        "$120.00",
    ):
        assert text in html, text
    # four checks in all, subtotalled with the lines; the parts and the total
    assert "4 checks" in html and "$565.50" in html
    assert "Other deposits" in html and "$165.50" in html and "$400.00" in html
    preview = client.get(f"/api/deposits/{mixed['id']}/print-preview").text
    assert "State Energy Office" in preview and "Oven rebate" in preview


# --- edit and void ------------------------------------------------------------


def test_an_edit_changes_the_lines_and_reposts_the_deposit(
    client, db_session, seed_accounts, mixed
):
    r = client.put(
        f"/api/deposits/{mixed['id']}",
        json={
            "other_lines": [
                {
                    "received_from": "State Energy Office",
                    "account_id": seed_accounts["4900"].id,
                    "memo": "Oven rebate",
                    "amount": "150.00",
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(str(d["amount"])) == D("550.00")
    assert [o["amount"] for o in d["other_lines"]] == [150.0]
    assert len(d["payments"]) == 2
    lines = _lines(db_session, mixed["id"])
    assert {ln.account_id for ln in lines} == {
        seed_accounts["1000"].id,
        seed_accounts["1200"].id,
        seed_accounts["4900"].id,
    }
    assert sum(D(str(ln.debit)) for ln in lines) == D("550.00")
    assert sum(D(str(ln.credit)) for ln in lines) == D("550.00")
    # a save that leaves other_lines out keeps them
    r = client.put(f"/api/deposits/{mixed['id']}", json={"reference": "SLIP-10"})
    assert r.status_code == 200, r.text
    assert [o["amount"] for o in r.json()["other_lines"]] == [150.0]
    assert D(str(r.json()["amount"])) == D("550.00")


def test_an_edit_with_a_refused_line_changes_nothing(
    client, db_session, seed_accounts, mixed
):
    r = client.put(
        f"/api/deposits/{mixed['id']}",
        json={"other_lines": [{"account_id": seed_accounts["1100"].id, "amount": "5"}]},
    )
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Other deposits, line 1: Accounts Receivable")
    d = client.get(f"/api/deposits/{mixed['id']}").json()
    assert D(str(d["amount"])) == D("565.50") and len(d["other_lines"]) == 2


def test_an_edit_keeps_a_line_whose_account_was_made_inactive_since(
    client, db_session, seed_accounts, mixed
):
    seed_accounts["4900"].is_active = False
    db_session.commit()
    d = client.get(f"/api/deposits/{mixed['id']}").json()
    keep = [
        {k: o[k] for k in ("account_id", "memo", "amount")} for o in d["other_lines"]
    ]
    r = client.put(f"/api/deposits/{mixed['id']}", json={"other_lines": keep})
    assert r.status_code == 200, r.text


def test_a_void_takes_the_whole_deposit_back(client, db_session, seed_accounts, mixed):
    r = client.post(f"/api/deposits/{mixed['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["voided"] and r.json()["other_items"] == 2
    db_session.expire_all()
    for number in ("1000", "6300", "4900"):
        acct = db_session.get(Account, seed_accounts[number].id)
        net = sum(
            D(str(ln.debit)) - D(str(ln.credit))
            for ln in db_session.query(TransactionLine).filter(
                TransactionLine.account_id == acct.id
            )
        )
        assert net == 0, number
    # the payments are back on the list; the void deposit still lists its lines
    assert len(client.get("/api/deposits/pending").json()) == 2
    d = client.get(f"/api/deposits/{mixed['id']}").json()
    assert d["voided"] and len(d["other_lines"]) == 2


# --- cash basis, the register and the drill-down ------------------------------


def test_an_income_line_is_cash_received_on_the_deposit_date(client, mixed):
    for basis in ("cash", "accrual"):
        r = client.get(
            f"/api/reports/profit-loss?start_date={DAY}&end_date={DAY}&basis={basis}"
        )
        assert r.status_code == 200, r.text
        pl = r.json()
        income = {
            ln["account_number"]: ln["amount"]
            for key in ("income", "other_income")
            for ln in pl.get(key) or []
        }
        assert D(str(income["4900"])) == D("120.00"), basis
        # the insurance refund takes the expense down on the same day
        expenses = {ln["account_number"]: ln["amount"] for ln in pl["expenses"]}
        assert D(str(expenses["6300"])) == D("-45.50"), basis
    # the day before, nothing
    r = client.get(
        "/api/reports/profit-loss?start_date=2026-09-11&end_date=2026-09-11&basis=cash"
    )
    assert "4900" not in {
        ln["account_number"]
        for key in ("income", "other_income")
        for ln in r.json().get(key) or []
    }


def test_the_register_and_the_drill_down_open_the_deposit(client, seed_accounts, mixed):
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    row = next(e for e in reg["entries"] if e["transaction_id"] == mixed["id"])
    assert row["source_link"] == f"/#/deposits/{mixed['id']}"
    assert D(str(row["debit"])) == D("565.50")
    assert {c["account_name"] for c in row["category_lines"]} == {
        "Undeposited Funds",
        "Insurance",
        "Other Income",
    }
    drill = client.get(
        f"/api/reports/account-transactions?account_id={seed_accounts['4900'].id}"
        f"&start_date={DAY}&end_date={DAY}&class_id={mixed['class_id']}"
    ).json()
    assert [(e["credit"], e["source_link"]) for e in drill["entries"]] == [
        (120.0, f"/#/deposits/{mixed['id']}")
    ]


# --- roles --------------------------------------------------------------------


def _sign_in(client, db_session, username, role):
    db_session.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password("long-enough-pw"),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": "long-enough-pw"}
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_ACCOUNTANT])
def test_a_reader_or_the_accountant_cannot_make_one(
    client, db_session, seed_accounts, mixed, role
):
    _sign_in(client, db_session, f"u{role}", role)
    line = {"account_id": seed_accounts["4900"].id, "amount": "5"}
    assert _deposit(client, seed_accounts, other_lines=[line]).status_code == 403
    r = client.put(f"/api/deposits/{mixed['id']}", json={"other_lines": [line]})
    assert r.status_code == 403
    # but reads the deposit and its lines
    assert len(client.get(f"/api/deposits/{mixed['id']}").json()["other_lines"]) == 2


def test_a_bookkeeper_makes_one(client, db_session, seed_accounts):
    _sign_in(client, db_session, "kim", ROLE_BOOKKEEPER)
    r = _deposit(
        client,
        seed_accounts,
        other_lines=[{"account_id": seed_accounts["4900"].id, "amount": "5"}],
    )
    assert r.status_code == 200, r.text


# --- the migration ------------------------------------------------------------


def test_the_migration_adds_the_table_and_walks_back(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    db = tmp_path / "dep.db"
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("c7e3a9d5f1b4").down_revision == "b6e1d4a8c2f5"

    command.upgrade(cfg, "b6e1d4a8c2f5")  # the step below it (#795)
    command.upgrade(cfg, "c7e3a9d5f1b4")  # its own step, not the chain (#770)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(deposit_other_lines)")}
    assert {
        "deposit_transaction_id",
        "account_id",
        "amount",
        "customer_id",
        "vendor_id",
        "received_from",
        "memo",
        "check_number",
        "method",
        "class_id",
        "job_id",
    } <= cols
    command.downgrade(cfg, "b6e1d4a8c2f5")
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "deposit_other_lines" not in tables
