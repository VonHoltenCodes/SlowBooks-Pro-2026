"""End-to-end import of a real QuickBooks Mac 2019 IIF export.

The fixture is the file mikeorofino attached to issue #7 — 105 ACCNT rows with
opening balances stored in OBAMOUNT columns, no TRNS blocks, QB Mac quoted
thousands-separator amounts like `"99,250.02"`. This test pins the behavior
verified by the maintainer in the closing comment so that regressions in
`_parse_decimal`, the OBAMOUNT flow, or account deduplication can't silently
return us to "import runs, balances all $0.00".
"""
from decimal import Decimal
from pathlib import Path

SAMPLE_IIF = Path(__file__).parent / "fixtures" / "sample_qbmac_opening_balances.iif"


def _import(db_session):
    from app.services.iif_import import import_all
    text = SAMPLE_IIF.read_text(encoding="utf-8")
    result = import_all(db_session, text)
    db_session.commit()
    return result


def _balance(db_session, account_number):
    from app.models.accounts import Account
    acct = db_session.query(Account).filter_by(account_number=account_number).first()
    return acct.balance if acct else None


def test_sample_imports_without_errors(db_session, seed_accounts):
    result = _import(db_session)
    # Surface any errors so a failing assertion pinpoints the problem.
    assert not result.get("errors"), f"import reported errors: {result['errors']}"


def test_sample_balances_match_maintainer_verified_values(db_session, seed_accounts):
    _import(db_session)

    # From issue #7 closing comment, verified against the sample file by the maintainer.
    cases = {
        "1000": Decimal("99250.02"),   # Checking
        "1010": Decimal("5987.50"),    # Savings
        "1100": Decimal("35810.02"),   # Accounts Receivable
        "2000": Decimal("2578.69"),    # Accounts Payable
        "2200": Decimal("2086.50"),    # Sales Tax Payable
    }
    mismatches = []
    for acct_num, expected in cases.items():
        actual = _balance(db_session, acct_num)
        if actual is None or Decimal(str(actual)) != expected:
            mismatches.append(f"{acct_num}: expected {expected}, got {actual}")
    assert not mismatches, "\n".join(mismatches)


def test_sample_opening_balance_journal_is_balanced(db_session, seed_accounts):
    _import(db_session)

    from app.models.transactions import Transaction, TransactionLine
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.reference == "IIF-OPENING")
        .first()
    )
    assert txn is not None, "no opening-balance journal entry was created"

    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    total_dr = sum((Decimal(str(l.debit)) for l in lines), Decimal("0"))
    total_cr = sum((Decimal(str(l.credit)) for l in lines), Decimal("0"))
    assert total_dr == total_cr
    # Maintainer's closing comment reports $336,050.25 on each side against this
    # exact file. Pin it so any regression in OBAMOUNT parsing/aggregation shows up.
    assert total_dr == Decimal("336050.25")


def test_sample_quoted_amount_parsing(db_session, seed_accounts):
    """Direct unit check that the QB Mac `"99,250.02"` format round-trips."""
    from app.services.iif_import import _parse_decimal
    assert _parse_decimal('"99,250.02"') == Decimal("99250.02")
    assert _parse_decimal('"-1,725.00"') == Decimal("-1725.00")
    assert _parse_decimal("0.00") == Decimal("0.00")
    assert _parse_decimal("") == Decimal("0")
    assert _parse_decimal('""') == Decimal("0")
