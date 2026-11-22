"""A Books Check row's sentence gives the distance between the two figures
without a sign (#926, 2.32.0 round 6, W-154): "$80.00 apart", while the
Difference column and the JSON's `difference` keep the sign."""

from datetime import date

from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import books_check


def test_the_sentence_reads_the_distance_unsigned(db_session, seed_accounts):
    a = seed_accounts
    # an entry straight to 1100 with no customer: the control is 80 above
    # the aging, so the signed difference is negative
    txn = Transaction(date=date(2026, 9, 1), description="stray", source_type="manual")
    db_session.add(txn)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id, account_id=a["1100"].id, debit=80, credit=0
            ),
            TransactionLine(
                transaction_id=txn.id, account_id=a["4000"].id, debit=0, credit=80
            ),
        ]
    )
    db_session.commit()
    rows = {r["key"]: r for r in books_check(db_session, None)["rows"]}
    row = rows["ar_aging"]
    assert row["status"] == "difference"
    assert row["difference"] == -80.0
    assert "$80.00 apart" in row["sentence"], row["sentence"]
    assert "-$80.00 apart" not in row["sentence"]
