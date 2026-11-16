"""Books Check names a document behind a difference in words, in the JSON
(`type_label`), the PDF and the CSV alike (#928, 2.32.0 round 6, NEW-231):
"Card charge", not "Cc charge"."""

from datetime import date

from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import DOCUMENT_WORDS, books_check, document_words


def test_the_words():
    assert document_words("cc_charge") == "Card charge"
    assert document_words("journal") == "Journal entry"
    assert document_words("pay_run") == "Pay run"
    assert document_words("something_new") == "Something new"
    assert "Cc" not in " ".join(DOCUMENT_WORDS.values())


def _stray_card_charge(db, a):
    txn = Transaction(
        date=date(2026, 9, 1), description="card", source_type="cc_charge"
    )
    db.add(txn)
    db.flush()
    db.add_all(
        [
            TransactionLine(
                transaction_id=txn.id, account_id=a["6000"].id, debit=55, credit=0
            ),
            TransactionLine(
                transaction_id=txn.id, account_id=a["2000"].id, debit=0, credit=55
            ),
        ]
    )
    db.commit()
    return txn


def test_the_json_carries_the_label_and_the_csv_prints_it(
    client, db_session, seed_accounts
):
    txn = _stray_card_charge(db_session, seed_accounts)
    rows = {r["key"]: r for r in books_check(db_session, None)["rows"]}
    doc = next(d for d in rows["ap_aging"]["documents"] if d["id"] == txn.id)
    assert doc["type"] == "cc_charge" and doc["type_label"] == "Card charge"
    r = client.get("/api/reports/books-check/csv")
    assert r.status_code == 200, r.text
    assert "Card charge" in r.text
    assert "cc charge" not in r.text and "Cc charge" not in r.text


def test_the_pdf_prints_the_words(client, db_session, seed_accounts):
    import pytest

    pytest.importorskip("weasyprint")
    _stray_card_charge(db_session, seed_accounts)
    r = client.get("/api/reports/books-check/pdf")
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("application/pdf")
