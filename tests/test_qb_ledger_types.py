"""A QuickBooks Desktop ledger import keeps QuickBooks' Type (#663).

With QuickBooks' Name as the payee and its Num as the reference, the Type
(Check, Deposit, Transfer, General Journal, Bill Pmt -Check, Paycheck...)
was nowhere, and the register's Type column read "QuickBooks Desktop
import" on every entry. The import keeps it in transactions.source_kind,
and the register, the drill-down, the General Ledger, the journal page, the
P&L Detail, Possible Duplicates and the match list read "Check (imported)".
The upgrade fills it from the Type it reads out of an entry imported
before; an entry it leaves as it was has none.
"""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account
from app.models.banking import BankAccount, BankTransaction
from app.services.qb_ledger_import import SOURCE_TYPE
from tests.test_qb_desktop_ledger import FIXTURE, _chart, _upload
from tests.test_qb_ledger_payees import (
    YEAR,
    _as_2311,
    _imported,
    _migration,
    _upgrade,
)

ROOT = Path(__file__).resolve().parents[1]
KINDS = {
    "1001": "Check",
    "1002": "Check",
    "7": "General Journal",
    "8": "General Journal",
}


def _checking(db):
    acct = db.query(Account).filter(Account.name == "Operating Checking 4321").one()
    acct.bank_kind = "bank"  # a register, as the lists IIF makes it
    db.commit()
    return acct


def _kinds(db):
    return sorted((t.date.isoformat(), t.source_kind) for t in _imported(db))


FIXTURE_KINDS = [
    ("2024-01-01", "Transfer"),
    ("2024-01-05", "Deposit"),
    ("2024-01-05", "Deposit"),
    ("2024-01-08", "Check"),
    ("2024-01-10", "General Journal"),
    ("2024-01-15", "Transfer"),
    ("2024-01-31", "Check"),
    ("2024-02-27", "General Journal"),
]


def test_the_import_keeps_quickbooks_type(client, db_session, seed_accounts):
    _chart(db_session)
    assert _upload(client, FIXTURE.read_bytes())["journals"] == 8
    assert _kinds(db_session) == FIXTURE_KINDS


def test_the_register_and_its_reports_read_the_type(client, db_session, seed_accounts):
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    checking = _checking(db_session)
    reg = client.get(
        "/api/banking/check-register", params={"account_id": checking.id}
    ).json()
    assert [(e["reference"], e["source_kind"]) for e in reg["entries"]] == [
        ("", "Transfer"),
        ("", "Deposit"),
        ("", "Deposit"),
        ("1001", "Check"),
        ("", "Transfer"),
        ("1002", "Check"),
    ]
    fees = db_session.query(Account).filter(Account.name == "Card Fees").one()
    drill = client.get(
        "/api/reports/account-transactions", params={"account_id": fees.id, **YEAR}
    ).json()
    assert [e["source_kind"] for e in drill["entries"]] == ["General Journal"] * 2
    gl = client.get(
        "/api/reports/general-ledger", params={"account_id": fees.id, **YEAR}
    ).json()
    assert [e["source_kind"] for e in gl["accounts"][0]["entries"]] == [
        "General Journal"
    ] * 2
    listed = {
        e["reference"]: e["source_kind"]
        for e in client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
        if e["source_type"] == SOURCE_TYPE and e["reference"]
    }
    assert listed == KINDS
    one = next(t for t in _imported(db_session) if t.reference == "1002")
    assert client.get(f"/api/journal/{one.id}").json()["source_kind"] == "Check"
    # the P&L Detail's Type is the server's word
    detail = client.get("/api/reports/profit-loss-detail", params=YEAR).json()
    rent = next(a for a in _pl_accounts(detail) if a["account_name"] == "Rent")
    assert [e["type"] for e in rent["entries"]] == ["Check (imported)"]


def _pl_accounts(detail):
    """Every account block of a P&L Detail answer, whatever its sections."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            if "entries" in node and "account_name" in node:
                out.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(detail)
    return out


def test_possible_duplicates_and_the_match_list_read_the_type(
    client, db_session, seed_accounts
):
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    checking = _checking(db_session)
    rent = db_session.query(Account).filter(Account.name == "Rent").one()
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": checking.id,
            "date": "2024-02-01",
            "amount": "-1000.00",
            "category_account_id": rent.id,
            "payee": "Harbor Properties",
        },
    )
    assert r.status_code == 201, r.text
    dups = client.get(
        "/api/reports/possible-duplicates",
        params={"account_id": checking.id, **YEAR},
    ).json()
    (g,) = dups["groups"]
    assert sorted((e["source_type"], e["source_kind"]) for e in g["entries"]) == [
        ("bank_entry", None),
        (SOURCE_TYPE, "Check"),
    ]
    feed = BankAccount(name="Checking feed", account_id=checking.id)
    db_session.add(feed)
    db_session.flush()
    line = BankTransaction(
        bank_account_id=feed.id,
        date=date(2024, 1, 8),
        amount=Decimal("-75.00"),
        payee="OFFICE DEPOT",
        match_status="unmatched",
    )
    db_session.add(line)
    db_session.commit()
    found = client.get(f"/api/banking/transactions/{line.id}/candidates").json()
    assert [(c["reference"], c["source_kind"]) for c in found] == [("1001", "Check")]


def test_each_page_names_the_type_through_sourceword():
    js = {
        name: (ROOT / "app/static/js" / name).read_text(encoding="utf-8")
        for name in ("banking.js", "reports.js", "journal.js")
    }
    assert "sourceWord(e.source_type, e.source_kind)" in js["banking.js"]  # register
    assert "sourceWord(c.source_type, c.source_kind)" in js["banking.js"]  # match
    # the drill-down, the General Ledger and Possible Duplicates
    assert js["reports.js"].count("e.source_kind)") == 4
    assert "sourceWord(entry.source_type, entry.source_kind)" in js["journal.js"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_word_is_the_type_imported():
    script = (
        "const fs = require('fs'), vm = require('vm');"
        "const ctx = { window: {}, App: { settings: {} },"
        " document: { addEventListener() {} } };"
        "vm.createContext(ctx);"
        "vm.runInContext(fs.readFileSync('app/static/js/utils.js', 'utf8')"
        " + '\\nthis.sourceWord = sourceWord;', ctx);"
        "console.log(JSON.stringify([ctx.sourceWord('qb_ledger_import', 'Check'),"
        " ctx.sourceWord('qb_ledger_import', 'Bill Pmt -Check'),"
        " ctx.sourceWord('qb_ledger_import', null),"
        " ctx.sourceWord('manual_void', '')]));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout
    assert json.loads(out) == [
        "Check (imported)",
        "Bill Pmt -Check (imported)",
        "QuickBooks Desktop import",
        "Journal entry void",
    ]


# ── the upgrade ──────────────────────────────────────────────────────────


def test_the_upgrade_fills_the_type_it_reads_back(client, db_session, seed_accounts):
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    _as_2311(db_session, FIXTURE.read_bytes())
    for txn in _imported(db_session):
        txn.source_kind = None  # 2.31.1 had no such column
    unread = next(
        t for t in _imported(db_session) if t.description.startswith("Check 1001")
    )
    unread.description = "Office supplies"  # not as the importer wrote it
    db_session.commit()
    assert _upgrade(db_session) == 7
    kinds = dict(FIXTURE_KINDS)
    for txn in _imported(db_session):
        if txn.id == unread.id:
            assert txn.source_kind is None and txn.source_ref is None
        else:
            assert txn.source_kind == kinds[txn.date.isoformat()], txn.description


def test_what_the_upgrade_reads_as_the_type():
    type_of = _migration().type_of
    day = date(2026, 3, 2)
    assert type_of("Check 1001 · 2026-03-02 · Acme", day) == "Check"
    assert (
        type_of("Bill Pmt -Check 10452 · 2026-03-02 · Acme", day) == "Bill Pmt -Check"
    )
    assert (
        type_of("Credit Card Charge · 2026-03-02 · Cafe #2", day)
        == "Credit Card Charge"
    )
    assert type_of("Discount · 2026-03-02", day) == "Discount"
    assert type_of("Funds Moved 12 · 2026-03-02 · Acme", day) is None
    assert type_of("Check 9 · 2026-03-03 · Acme", day) is None


def test_a_downgrade_and_the_upgrade_again_come_back_whole(
    client, db_session, seed_accounts
):
    """The downgrade puts each key back in the reference and keeps the name
    as the description; upgraded again, every entry reads back: the Num as
    the reference, the key in source_ref, the Type in source_kind."""
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    fresh = [
        (t.id, t.description, t.reference, t.source_ref, t.source_kind)
        for t in _imported(db_session)
    ]
    for txn in _imported(db_session):
        txn.reference, txn.source_ref, txn.source_kind = txn.source_ref, None, None
    db_session.commit()
    assert _upgrade(db_session) == 8
    assert [
        (t.id, t.description, t.reference, t.source_ref, t.source_kind)
        for t in _imported(db_session)
    ] == fresh
    assert _upgrade(db_session) == 0


D = date(2026, 3, 2)
LONG = "Northwind Property Management Group:Building 7 Lobby Renovation Phase 2"


@pytest.mark.parametrize(
    "description, key, expected",
    [
        ("Acme Supply", "Check EFT · 2026-03-02 · Acme Supply", ("Acme Supply", "EFT")),
        ("", "General Journal AJE-3 · 2026-03-02", ("", "AJE-3")),
        ("", "Transfer · 2026-03-02 #2", ("", "")),
        ("Store", "Check · 2026-03-02 · Store #3", ("Store", "")),
        (LONG, f"Bill Pmt -Check 10452 · 2026-03-02 · {LONG}"[:100], (LONG, "10452")),
        # a name the key does not hold, a named key with no name, another day
        ("Zenith", "Check 9 · 2026-03-02 · Acme", None),
        ("", "Check 9 · 2026-03-02 · Acme", None),
        ("Acme", "Check 9 · 2026-03-03 · Acme", None),
        # two names (none in the key): not told apart from anything else
        ("Ann Lee", "Deposit · 2026-03-02", None),
        ("Funds Moved 12 — Acme", "Funds Moved 12 · 2026-03-02 · Acme", None),
    ],
)
def test_what_the_upgrade_reads_back_after_a_downgrade(description, key, expected):
    assert _migration().read_back_downgraded(description, key, D) == expected
