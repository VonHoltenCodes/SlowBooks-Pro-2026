"""Transaction Detail by Account entries read in the register as a typed
entry does (#663).

The import wrote each journal's description as "{Type} {Num} — {Name}:
{Memo}", and the register shows a journal's description as its payee, so a
payee read "Check 1001 — Office Depot: Scheduling app". QuickBooks' Name is
now the payee, its Num the reference (Ref #), and each line's Memo stays on
its line. The import's key, which was the reference, has a column of its own
(source_ref), so a second upload still posts nothing twice; an entry imported
before keeps its key in the reference until the upgrade rewrites it, and is
found there.
"""

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account
from app.models.transactions import Transaction
from app.services.accounting import create_journal_entry
from app.services.qb_ledger_import import SOURCE_TYPE
from tests.test_qb_desktop_ledger import FIXTURE, _chart, _upload

ROOT = Path(__file__).resolve().parents[1]
REVISION = "4810bd59e481"  # the column, and the rewrite of what was in
YEAR = {"start_date": "2024-01-01", "end_date": "2024-12-31"}


def _report(txns) -> bytes:
    """A Transaction Detail by Account export of `txns`, [(type, date, num,
    [(account, name, memo, debit, credit)])]: one label column before Type,
    a section per account in the order met, each closed by QuickBooks'
    Total row, and the TOTAL row."""
    sections: dict = {}
    for ttype, day, num, parts in txns:
        for account, name, memo, debit, credit in parts:
            others = {p[0] for p in parts} - {account}
            split = others.pop() if len(others) == 1 else "-SPLIT-"
            sections.setdefault(account, []).append(
                ["", ttype, day, num, name, memo, split, debit, credit, ""]
            )
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(
        [
            "",
            "Type",
            "Date",
            "Num",
            "Name",
            "Memo",
            "Split",
            "Debit",
            "Credit",
            "Balance",
        ]
    )
    grand = [Decimal("0"), Decimal("0")]
    for account, rows in sections.items():
        writer.writerow([account] + [""] * 9)
        writer.writerows(rows)
        debit = sum((Decimal(r[7] or "0") for r in rows), Decimal("0"))
        credit = sum((Decimal(r[8] or "0") for r in rows), Decimal("0"))
        writer.writerow([f"Total {account}"] + [""] * 6 + [f"{debit}", f"{credit}", ""])
        grand = [grand[0] + debit, grand[1] + credit]
    writer.writerow(["TOTAL"] + [""] * 6 + [f"{grand[0]}", f"{grand[1]}", ""])
    return out.getvalue().encode("utf-8")


def _account(db, name):
    return db.query(Account).filter(Account.name == name).one()


def _imported(db):
    return (
        db.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .order_by(Transaction.id)
        .all()
    )


def _as_2311(db, data: bytes) -> None:
    """Put the entries this report imported back as 2.31.1 wrote them: the
    description "{Type} {Num} — {Name}: {Memo}" and the key as the
    reference, with no source_ref."""
    from app.routes.csv import _decode_csv_upload
    from app.services.qb_ledger_import import (
        LEDGER_COLUMNS,
        _import_key,
        group_journals,
        parse_ledger,
    )
    from app.services.qb_report_import import _csv_rows, _find_header_for

    rows = _csv_rows(_decode_csv_upload(data))
    at, cols = _find_header_for(rows, LEDGER_COLUMNS)
    journals, _ = group_journals(parse_ledger(rows, at, cols)[0])
    seen, old = {}, {}
    for journal in journals:
        first = journal[0]
        names = [ln["name"] for ln in journal if ln["name"]]
        memos = [ln["memo"] for ln in journal if ln["memo"]]
        old[_import_key(journal, seen)] = (
            f"{first['type']} {first['num']}".strip()
            + (f" — {names[0]}" if names else "")
            + (f": {memos[0]}" if memos else "")
        )[:200]
    for txn in _imported(db):
        if txn.source_ref in old:
            txn.description = old[txn.source_ref]
            txn.reference, txn.source_ref = txn.source_ref, None
    db.commit()


def test_the_register_shows_the_name_the_num_and_the_memo(
    client, db_session, seed_accounts
):
    _chart(db_session)
    data = _upload(client, FIXTURE.read_bytes())
    assert data["errors"] == [] and data["journals"] == 8
    checking = _account(db_session, "Operating Checking 4321")
    reg = client.get(
        "/api/banking/check-register", params={"account_id": checking.id}
    ).json()
    assert [
        (e["date"], e["payee"], e["reference"], e["description"])
        for e in reg["entries"]
    ] == [
        ("2024-01-01", "", "", "Beginning balance for this acct on 1-1-24"),
        ("2024-01-05", "Card Processor", "", "Deposit"),
        ("2024-01-05", "Walk-in", "", "Deposit"),
        # no memo on the check's bank line: the Memo column shows the payee,
        # as it does for an entry typed with none
        ("2024-01-08", "Office Depot", "1001", "Office Depot"),
        ("2024-01-15", "", "", "Funds Transfer"),
        ("2024-01-31", "Harbor Properties", "1002", "January rent"),
    ]
    check = next(t for t in _imported(db_session) if t.reference == "1001")
    assert check.description == "Office Depot"
    assert check.source_ref == "Check 1001 · 2024-01-08 · Office Depot"
    # the duplicate check number warning knows QuickBooks' check numbers;
    # the next check number does not follow them
    from app.services.check_numbers import next_check_number, uses_of

    assert next_check_number(db_session, checking.id) == "1001"
    assert [u["payee"] for u in uses_of(db_session, checking.id, "1001")] == [
        "Office Depot"
    ]
    # each split line keeps its own memo, in the drill-down too
    supplies = (
        db_session.query(Account)
        .filter(
            Account.name == "Supplies",
            Account.parent_id == _account(db_session, "Office Expense").id,
        )
        .one()
    )
    drill = client.get(
        "/api/reports/account-transactions",
        params={"account_id": supplies.id, **YEAR},
    ).json()
    assert [
        (e["payee"], e["reference"], e["description"]) for e in drill["entries"]
    ] == [("Office Depot", "1001", "Paper")]


def test_a_journal_entry_with_no_name_reads_as_its_memo(
    client, db_session, seed_accounts
):
    """A general journal entry has no name: no payee, and the journal page,
    the drill-down, the general ledger and its void read its memo."""
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    listed = {
        e["reference"]: e
        for e in client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
        if e["source_type"] == SOURCE_TYPE
    }
    assert listed["7"]["description"] == "Card sales, week 2"
    assert listed["8"]["description"] == "Chargeback fee"
    assert listed["1002"]["description"] == "Harbor Properties"
    fees = _account(db_session, "Card Fees")
    drill = client.get(
        "/api/reports/account-transactions", params={"account_id": fees.id, **YEAR}
    ).json()
    assert [
        (e["payee"], e["reference"], e["description"]) for e in drill["entries"]
    ] == [
        ("", "7", "Card sales, week 2"),
        ("", "8", "Chargeback fee"),
    ]
    gl = client.get(
        "/api/reports/general-ledger", params={"account_id": fees.id, **YEAR}
    ).json()
    assert [
        (e["reference"], e["description"]) for e in gl["accounts"][0]["entries"]
    ] == [("7", "Card sales, week 2"), ("8", "Chargeback fee")]
    void = client.post(f"/api/journal/{listed['7']['id']}/void")
    assert void.status_code == 200, void.text
    assert void.json()["description"] == "VOID: Card sales, week 2"
    assert void.json()["reference"] == "7"


def test_adjusting_entries_with_no_name_list_with_their_memo(
    client, db_session, seed_accounts
):
    _chart(db_session)
    report = _report(
        [
            (
                "General Journal",
                "12/31/2024",
                "AJE-3",
                [
                    ("Card Fees", "", "Year-end fees", "40.00", ""),
                    ("Card Clearing", "", "Year-end fees", "", "40.00"),
                ],
            )
        ]
    )
    body = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("aje.csv", io.BytesIO(report), "text/csv")},
        data={"adjusting_only": "true"},
    ).json()
    assert body["errors"] == [] and body["journals"] == 1, body
    aje = client.get("/api/reports/adjusting-entries", params=YEAR).json()
    assert [(e["reference"], e["description"]) for e in aje["entries"]] == [
        ("AJE-3", "Year-end fees")
    ]


NEXT_MONTH = [
    # in the fixture already
    (
        "Deposit",
        "01/05/2024",
        "",
        [
            ("Operating Checking 4321", "Walk-in", "Deposit", "120.00", ""),
            ("Service Income", "Walk-in", "Deposit", "", "120.00"),
        ],
    ),
    (
        "Check",
        "01/31/2024",
        "1002",
        [
            ("Rent", "Harbor Properties", "January rent", "1000.00", ""),
            (
                "Operating Checking 4321",
                "Harbor Properties",
                "January rent",
                "",
                "1000.00",
            ),
        ],
    ),
    # new
    (
        "Check",
        "02/29/2024",
        "1003",
        [
            ("Rent", "Harbor Properties", "February rent", "1000.00", ""),
            (
                "Operating Checking 4321",
                "Harbor Properties",
                "February rent",
                "",
                "1000.00",
            ),
        ],
    ),
]


def test_an_overlapping_file_posts_only_what_is_new(client, db_session, seed_accounts):
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    again = _upload(client, _report(NEXT_MONTH))
    assert again["errors"] == [], again["errors"]
    assert again["journals"] == 1 and again["duplicates_skipped"] == 2
    new = next(t for t in _imported(db_session) if t.reference == "1003")
    assert new.description == "Harbor Properties"
    assert new.source_ref == "Check 1003 · 2024-02-29 · Harbor Properties"
    assert len(_imported(db_session)) == 9


def test_an_entry_imported_before_is_found_by_the_key_in_its_reference(
    client, db_session, seed_accounts
):
    """Books imported by 2.31.1 that the upgrade could not read back keep
    their key in the reference: a second upload still skips them."""
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    _as_2311(db_session, FIXTURE.read_bytes())
    check = next(
        t for t in _imported(db_session) if t.reference.startswith("Check 1002")
    )
    assert check.description == "Check 1002 — Harbor Properties: January rent"
    assert check.source_ref is None
    again = _upload(client, FIXTURE.read_bytes())
    assert again["journals"] == 0 and again["duplicates_skipped"] == 8
    overlapping = _upload(client, _report(NEXT_MONTH))
    assert overlapping["journals"] == 1 and overlapping["duplicates_skipped"] == 2
    assert len(_imported(db_session)) == 9


# ── the upgrade: what 2.31.1 imported, rewritten once ────────────────────


def _alembic():
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _migration():
    """The migration, for its rewrite and how it reads an entry back."""
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic()).get_revision(REVISION).module


def _upgrade(db) -> int:
    """The migration's rewrite, on this test's books."""
    rewritten = _migration().rewrite(db.connection())
    db.expire_all()
    return rewritten


def test_the_upgrade_writes_what_a_new_import_writes(client, db_session, seed_accounts):
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    fresh = [
        (t.id, t.description, t.reference, t.source_ref) for t in _imported(db_session)
    ]
    _as_2311(db_session, FIXTURE.read_bytes())
    assert [t.source_ref for t in _imported(db_session)] == [None] * 8
    assert _upgrade(db_session) == 8
    assert [
        (t.id, t.description, t.reference, t.source_ref) for t in _imported(db_session)
    ] == fresh


def test_a_second_upload_skips_what_is_in_before_and_after_the_upgrade(
    client, db_session, seed_accounts
):
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    _as_2311(db_session, FIXTURE.read_bytes())
    before = _upload(client, _report(NEXT_MONTH))
    assert before["journals"] == 1 and before["duplicates_skipped"] == 2
    # the one posted since is in the new shape already and is left as it is
    assert _upgrade(db_session) == 8
    after = _upload(client, _report(NEXT_MONTH))
    assert after["journals"] == 0 and after["duplicates_skipped"] == 3
    again = _upload(client, FIXTURE.read_bytes())
    assert again["journals"] == 0 and again["duplicates_skipped"] == 8
    assert len(_imported(db_session)) == 9
    checking = _account(db_session, "Operating Checking 4321")
    reg = client.get(
        "/api/banking/check-register", params={"account_id": checking.id}
    ).json()
    rows = [(e["payee"], e["reference"], e["description"]) for e in reg["entries"]]
    assert rows[-2:] == [
        ("Harbor Properties", "1002", "January rent"),
        ("Harbor Properties", "1003", "February rent"),
    ]


D = date(2026, 3, 2)


def _written(ttype, num, lines, repeat=1, day=D):
    """What 2.31.1 stored for a journal of `lines`, [(name, memo)]: its
    description, its key (the reference), its date and its lines' texts."""
    names = [name for name, _ in lines if name]
    memos = [memo for _, memo in lines if memo]
    head = f"{ttype} {num}".strip()
    description = (
        head + (f" — {names[0]}" if names else "") + (f": {memos[0]}" if memos else "")
    )[:200]
    key = f"{head} · {day:%Y-%m-%d}"
    if len(set(names)) == 1:
        key += f" · {names[0]}"
    if repeat > 1:
        key += f" #{repeat}"
    texts = [(memo or name or ttype)[:300] for name, memo in lines]
    return description, key[:100], day, texts


LONG = "Northwind Property Management Group:Building 7 Lobby Renovation Phase 2"
READ_BACK = [
    pytest.param(
        ("Check", "EFT", [("Acme Supply", ""), ("Acme Supply", "memo")]),
        ("Acme Supply", "EFT"),
        id="the issue's check",
    ),
    pytest.param(
        (
            "Check",
            "2001",
            [
                ("Lakeside — North:Dock 4", ""),
                ("Lakeside — North:Dock 4", "Re: invoice 12 — final"),
            ],
        ),
        ("Lakeside — North:Dock 4", "2001"),
        id="a job with a dash, a memo with a colon and a dash",
    ),
    pytest.param(
        ("Bill Pmt -Check", "10452", [(LONG, "Retainage: final 10%"), (LONG, "")]),
        (LONG, "10452"),
        id="a name the key cut short",
    ),
    pytest.param(
        ("Deposit", "", [("", "Batch: week 9"), ("Ann Lee", ""), ("Bo Chen", "")]),
        ("Ann Lee", ""),
        id="two names, neither in the key",
    ),
    pytest.param(
        ("General Journal", "AJE-3", [("", "Accrual: Q4"), ("", "Accrual: Q4")]),
        ("", "AJE-3"),
        id="a journal entry with no name",
    ),
    pytest.param(
        ("Transfer", "", [("", ""), ("", "")]),
        ("", ""),
        id="no name, number or memo",
    ),
    pytest.param(
        ("Credit Card Charge", "", [("Corner Cafe", "Lunch: crew")] * 2),
        ("Corner Cafe", ""),
        id="a type of three words",
    ),
    pytest.param(
        ("Check", "", [("Store #2", "")] * 2),
        ("Store #2", ""),
        id="a name that ends like a repeat",
    ),
    pytest.param(
        ("Check", "", [("Store", "")] * 2, 2),
        ("Store", ""),
        id="a key that repeated in its file",
    ),
    pytest.param(
        ("Check", "77", [("Smith: Annex", "")] * 2),
        ("Smith: Annex", "77"),
        id="a name with a colon and a space",
    ),
    pytest.param(
        ("Discount", "", [("Acme", "x")]),
        ("Acme", ""),
        id="a type of one word not on the list",
    ),
    pytest.param(
        ("Check", "5", [("Hale: Annex", "Roof"), ("Hale: Annex", "Annex: Roof")]),
        ("Hale: Annex", "5"),
        id="two readings the lines allow, the key holds one",
    ),
    pytest.param(
        ("Check", "1", [(LONG + " West", "")] * 2, 2),
        (LONG + " West", "1"),
        id="a repeat number the key cut short",
    ),
    pytest.param(
        ("Check", "3", [("Acme", "Retainage released on the final lien waiver. " * 6)]),
        ("Acme", "3"),
        id="a memo the description cut short",
    ),
]


@pytest.mark.parametrize("journal, expected", READ_BACK)
def test_what_the_upgrade_reads_back(journal, expected):
    assert _migration().read_back(*_written(*journal)) == expected


LEFT_ALONE = [
    pytest.param(
        _written("Funds Moved", "12", [("Acme", "")] * 2),
        id="a type of two words not on the list",
    ),
    pytest.param(
        ("Office supplies", "Check 9 · 2026-03-02 · Acme", D, ["Acme"]),
        id="a description the importer did not write",
    ),
    pytest.param(
        ("Check 9 — Acme", "Check 9 · 2026-03-03 · Acme", D, ["Acme"]),
        id="a key of another day",
    ),
    pytest.param(
        ("Check 9 — Acme: memo", "Check 9 · 2026-03-02 · Zenith", D, ["memo", "Acme"]),
        id="a name the key does not hold",
    ),
    pytest.param(
        ("Check 9 — Acme: memo", "Check 9 · 2026-03-02 · Acme", D, ["Acme", "other"]),
        id="a memo on no line",
    ),
    pytest.param(
        _written("Deposit", "", [("", "x: y"), ("A", "y"), ("B", "")]),
        id="two readings",
    ),
    pytest.param(("Acme", "1001", D, ["Acme"]), id="a reference that is no key"),
]


@pytest.mark.parametrize("entry", LEFT_ALONE)
def test_what_the_upgrade_leaves_alone(entry):
    assert _migration().read_back(*entry) is None


def test_the_upgrade_rewrites_ledger_imports_alone_and_once(db_session, seed_accounts):
    first, second = (
        db_session.query(Account).order_by(Account.account_number).limit(2).all()
    )

    def entry(source_type, description, reference, texts, source_ref=None):
        return create_journal_entry(
            db_session,
            D,
            description,
            [
                {
                    "account_id": first.id,
                    "debit": Decimal("10"),
                    "credit": Decimal("0"),
                    "description": texts[0],
                },
                {
                    "account_id": second.id,
                    "debit": Decimal("0"),
                    "credit": Decimal("10"),
                    "description": texts[1],
                },
            ],
            source_type=source_type,
            reference=reference,
            source_ref=source_ref,
        ).id

    key = "Check 9 · 2026-03-02 · Acme"
    old = entry(SOURCE_TYPE, "Check 9 — Acme: Paper", key, ["Acme", "Paper"])
    unread = entry(
        SOURCE_TYPE,
        "Funds Moved 12 — Acme",
        "Funds Moved 12 · 2026-03-02 · Acme",
        ["Acme", "Acme"],
    )
    typed = entry("manual", "Check 9 — Acme: Paper", key, ["Acme", "Paper"])
    new = entry(SOURCE_TYPE, "Acme", "9", ["Acme", "Paper"], source_ref=key + " #2")
    db_session.commit()
    assert _upgrade(db_session) == 1
    got = {
        t.id: (t.description, t.reference, t.source_ref)
        for t in db_session.query(Transaction)
    }
    assert got[old] == ("Acme", "9", key)
    assert got[unread] == (
        "Funds Moved 12 — Acme",
        "Funds Moved 12 · 2026-03-02 · Acme",
        None,
    )
    assert got[typed] == ("Check 9 — Acme: Paper", key, None)
    assert got[new] == ("Acme", "9", key + " #2")
    assert _upgrade(db_session) == 0


def test_the_migration_rewrites_and_its_downgrade_puts_the_keys_back(tmp_path):
    import sqlalchemy as sa
    from alembic import command
    from alembic.script import ScriptDirectory

    url = "sqlite:///" + (tmp_path / "books.db").as_posix()
    cfg = _alembic()
    cfg.attributes["database_url"] = url
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    before = script.get_revision(REVISION).down_revision
    command.upgrade(cfg, before)
    entries = {
        1: (
            "Check 1001 — Office Depot: Paper",
            "Check 1001 · 2024-01-08 · Office Depot",
        ),
        2: ("Funds Moved 12 — Acme", "Funds Moved 12 · 2024-01-08 · Acme"),
        # a partial entry (#663) as a downgrade left it: in the new shape,
        # its key in the reference, its difference on Import Differences
        3: ("Harbor Properties", "Check 1002 · 2024-01-08 · Harbor Properties"),
    }
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO accounts (id, name, account_type, is_active, is_system, "
                "balance) VALUES (1, 'Checking', 'ASSET', 1, 0, 0), "
                "(2, 'Supplies', 'EXPENSE', 1, 0, 0), "
                "(3, 'Import Differences', 'EQUITY', 1, 1, 0)"
            )
        )
        for txn_id, (description, reference) in entries.items():
            conn.execute(
                sa.text(
                    "INSERT INTO transactions (id, date, description, reference, "
                    "source_type, is_adjusting) VALUES (:id, '2024-01-08', :d, :r, "
                    "'qb_ledger_import', 0)"
                ),
                {"id": txn_id, "d": description, "r": reference},
            )
        conn.execute(
            sa.text(
                "INSERT INTO transaction_lines (transaction_id, account_id, debit, "
                "credit, description, is_billable, cleared) VALUES "
                "(1, 1, 0, 75, 'Office Depot', 0, 0), (1, 2, 75, 0, 'Paper', 0, 0), "
                "(2, 1, 0, 5, 'Acme', 0, 0), (2, 2, 5, 0, 'Acme', 0, 0), "
                "(3, 1, 0, 1000, 'January rent', 0, 0), "
                "(3, 2, 1010, 0, 'January rent', 0, 0), "
                "(3, 3, 0, 10, 'Difference in the QuickBooks file', 0, 0)"
            )
        )
    engine.dispose()

    def stored(*columns):
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                return {
                    row[0]: tuple(row[1:])
                    for row in conn.execute(
                        sa.text(f"SELECT id, {', '.join(columns)} FROM transactions")
                    )
                }
        finally:
            engine.dispose()

    harbor = "Check 1002 · 2024-01-08 · Harbor Properties"
    upgraded = {
        1: (
            "Office Depot",
            "1001",
            "Check 1001 · 2024-01-08 · Office Depot",
            "Check",
            None,
        ),
        2: (*entries[2], None, None, None),
        # the partial entry's difference, its file's debits less its credits
        3: ("Harbor Properties", "1002", harbor, "Check", 10),
    }
    columns = ("description", "reference", "source_ref", "source_kind")

    def stored_whole():
        return {
            i: (*rest, None if off is None else float(off))
            for i, (*rest, off) in stored(*columns, "source_difference").items()
        }

    command.upgrade(cfg, REVISION)
    assert stored_whole() == upgraded

    command.downgrade(cfg, before)
    engine = sa.create_engine(url)
    assert not {"source_ref", "source_kind", "source_difference"} & {
        c["name"] for c in sa.inspect(engine).get_columns("transactions")
    }
    engine.dispose()
    assert stored("description", "reference") == {
        1: ("Office Depot", "Check 1001 · 2024-01-08 · Office Depot"),
        2: entries[2],
        3: entries[3],
    }
    # and upgraded again, as the first upgrade left them
    command.upgrade(cfg, REVISION)
    assert stored_whole() == upgraded
