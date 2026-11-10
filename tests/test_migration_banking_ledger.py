"""e7f8a9b0c1d2 — banking on the ledger: the schema and data steps on a
pre-2.10 file, exercised on a fresh SQLite migrated to the previous head,
populated the way old installs are, then upgraded to head."""

import sqlite3
from pathlib import Path

PREV = "d6e7f8a9b0c1"
REVISION = "e7f8a9b0c1d2"  # banking on the ledger (#114), which follows PREV


def _cfg(db):
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


def test_upgrade_flags_bank_accounts_links_feeds_and_remaps_statement_lines(tmp_path):
    from alembic import command

    db = tmp_path / "old.db"
    cfg = _cfg(db)
    command.upgrade(cfg, PREV)

    con = sqlite3.connect(db)
    cur = con.cursor()
    accts = [
        ("1000", "Checking", "ASSET"),
        ("1010", "Savings", "ASSET"),
        ("1100", "Accounts Receivable", "ASSET"),
        ("1300", "Prepaid Insurance", "ASSET"),
        ("2000", "Accounts Payable", "LIABILITY"),
        ("2100", "Credit Card", "LIABILITY"),
        ("2150", "Chase Visa", "LIABILITY"),
        ("2200", "Sales Tax Payable", "LIABILITY"),
        ("4000", "Service Income", "INCOME"),
    ]
    for num, name, typ in accts:
        cur.execute(
            "INSERT INTO accounts (name, account_number, account_type, is_active, is_system, balance) "
            "VALUES (?, ?, ?, 1, 1, 0)",
            (name, num, typ),
        )
    ids = {r[1]: r[0] for r in cur.execute("SELECT id, account_number FROM accounts")}
    # a linked feed (to Savings) and an unlinked one with the old register balance
    cur.execute(
        "INSERT INTO bank_accounts (name, account_id, bank_name, balance, is_active) VALUES (?, ?, ?, ?, 1)",
        ("Savings feed", ids["1010"], "Numerica", 10.00),
    )
    cur.execute(
        "INSERT INTO bank_accounts (name, account_id, bank_name, balance, is_active) VALUES (?, NULL, ?, ?, 1)",
        ("Operating (old)", "Numerica", 123.45),
    )
    feeds = {r[1]: r[0] for r in cur.execute("SELECT id, name FROM bank_accounts")}
    cur.execute(
        "INSERT INTO reconciliations (bank_account_id, statement_date, statement_balance, status) VALUES (?, '2026-08-31', 10.00, 'COMPLETED')",
        (feeds["Savings feed"],),
    )
    for amount, reconciled, status in (
        (-5, 1, None),
        (-6, 0, "auto"),
        (-7, 0, None),
        (8, 0, "unmatched"),
    ):
        cur.execute(
            "INSERT INTO bank_transactions (bank_account_id, date, amount, reconciled, match_status) VALUES (?, '2026-08-01', ?, ?, ?)",
            (feeds["Savings feed"], amount, reconciled, status),
        )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db)
    kinds = dict(
        con.execute("SELECT account_number, bank_kind FROM accounts").fetchall()
    )
    assert kinds["1000"] == "bank" and kinds["1010"] == "bank"
    assert kinds["2100"] == "credit_card" and kinds["2150"] == "credit_card"  # by name
    assert kinds["1100"] is None and kinds["1300"] is None and kinds["2000"] is None
    assert kinds["2200"] is None and kinds["4000"] is None

    # the unlinked feed now has its own bank account: next free 10xx number
    row = con.execute(
        "SELECT a.account_number, a.account_type, a.bank_kind, b.legacy_balance FROM bank_accounts b "
        "JOIN accounts a ON a.id = b.account_id WHERE b.name = 'Operating (old)'"
    ).fetchone()
    assert row == ("1020", "ASSET", "bank", 123.45)
    assert (
        con.execute(
            "SELECT legacy_balance FROM bank_accounts WHERE name='Savings feed'"
        ).fetchone()[0]
        == 10.0
    )
    # no ledger line was written by the migration
    assert con.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0

    assert (
        con.execute("SELECT account_id FROM reconciliations").fetchone()[0]
        == con.execute(
            "SELECT id FROM accounts WHERE account_number='1010'"
        ).fetchone()[0]
    )
    assert (
        con.execute("SELECT beginning_balance FROM reconciliations").fetchone()[0] == 0
    )

    statuses = [
        r[0]
        for r in con.execute("SELECT match_status FROM bank_transactions ORDER BY id")
    ]
    assert statuses == ["excluded", "unmatched", "unmatched", "unmatched"]

    cols = {r[1]: r for r in con.execute("PRAGMA table_info(transaction_lines)")}
    assert cols["cleared"][3] == 1 and cols["cleared"][4] in ("0", "false", "'0'", 0)
    assert "reconciliation_id" in cols
    assert "transaction_line_id" in {
        r[1] for r in con.execute("PRAGMA table_info(bank_transactions)")
    }
    # The upgrade ran all the way to the repo's head. Asserting a literal
    # revision here made this test fail on the next release instead of on a
    # real defect, so ask alembic what head is.
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head
    # ...and this revision is part of how it got there.
    assert "e7f8a9b0c1d2" in {
        r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()
    }
    con.close()


def test_downgrade_is_a_mirror(tmp_path):
    from alembic import command

    db = tmp_path / "round.db"
    cfg = _cfg(db)
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    command.downgrade(cfg, PREV)
    con = sqlite3.connect(db)
    assert "bank_kind" not in {r[1] for r in con.execute("PRAGMA table_info(accounts)")}
    assert "balance" in {r[1] for r in con.execute("PRAGMA table_info(bank_accounts)")}
    assert "cleared" not in {
        r[1] for r in con.execute("PRAGMA table_info(transaction_lines)")
    }
    con.close()
