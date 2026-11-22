"""Books Check names what posted a stray entry in words (#922, 2.32.0 round 5,
W-153 / NEW-229): "an IIF import", "a journal entry", "a QuickBooks Online
import", not the raw source type ("a iif import", "a manual")."""

from datetime import date

from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import _source_words, books_check


def _rows(db):
    db.expire_all()
    return {r["key"]: r for r in books_check(db, None)["rows"]}


def _stray(db, a, source_type):
    txn = Transaction(
        date=date(2026, 9, 1), description="stray", source_type=source_type
    )
    db.add(txn)
    db.flush()
    db.add_all(
        [
            TransactionLine(
                transaction_id=txn.id, account_id=a["1100"].id, debit=10, credit=0
            ),
            TransactionLine(
                transaction_id=txn.id, account_id=a["4000"].id, debit=0, credit=10
            ),
        ]
    )
    db.commit()
    return txn


def test_the_words():
    assert _source_words("iif_import") == "an IIF import"
    assert _source_words("manual") == "a journal entry"
    assert _source_words(None) == "a journal entry"
    assert _source_words("qbo_export_import") == "a QuickBooks Online import"
    assert _source_words("bank_entry") == "a register entry"
    assert _source_words("job_cost_void") == "a job cost entry"
    assert _source_words("something_new") == "a journal entry"


def test_the_sentence_reads_the_source_in_words(db_session, seed_accounts):
    a = seed_accounts
    iif = _stray(db_session, a, "iif_import")
    manual = _stray(db_session, a, "manual")
    row = _rows(db_session)["ar_aging"]
    assert row["status"] == "difference", row
    why = {d["id"]: d["why"] for d in row["documents"] if d["type"] == "journal"}
    assert why[iif.id].startswith(
        "Posted straight to 1100 Accounts Receivable by an IIF import that names no customer"
    )
    assert why[manual.id].startswith(
        "Posted straight to 1100 Accounts Receivable by a journal entry that names no customer"
    )
    assert not any(" by a iif" in w or " by a manual" in w for w in why.values())
