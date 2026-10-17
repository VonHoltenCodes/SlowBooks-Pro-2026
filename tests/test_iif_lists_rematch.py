"""The lists IIF import matches an account by its whole path and type, and
posts each opening balance once (2.23.4, the release gate's W-5 and NEW-127).

Two QuickBooks accounts can share a leaf name ("Maintenance & Repairs" under
Automobile, an expense, and under Landscaping Services:Labor, income), and a
file's numbers can be the seeded chart's for other accounts (3100 is
QuickBooks' Retained Earnings and the chart's Owner's Draw). The import
matched by the leaf alone, or by the number alone, and merged them in
silence; and a second upload of the same lists posted every opening balance
again. Now an account is its path, of its type; a number is never a match by
itself (a clashing number is dropped, with a note); the chart's well-known
names still catch QuickBooks' ("Inventory Asset" is 1300 Inventory), named in
the result; and a balance an earlier import posted is not posted again.

QuickBooks for Mac's own sample lists (tests/fixtures/
sample_qbmac_opening_balances.iif) carry every one of those cases.
"""

import re
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction

SAMPLE = Path(__file__).parent / "fixtures" / "sample_qbmac_opening_balances.iif"


def _import(db):
    from app.services.iif_import import import_all

    result = import_all(db, SAMPLE.read_text(encoding="utf-8"))
    db.commit()
    return result


def _account(db, name, parent=None, kind=None):
    q = db.query(Account).filter(Account.name == name)
    if kind is not None:
        q = q.filter(Account.account_type == kind)
    rows = q.all()
    if parent is not None:
        rows = [a for a in rows if a.parent is not None and a.parent.name == parent]
    assert len(rows) == 1, [(a.name, a.account_type, a.parent_id) for a in rows]
    return rows[0]


def _openings(db):
    return (
        db.query(Transaction)
        .filter(
            Transaction.source_type == "iif_import",
            Transaction.reference == "IIF-OPENING",
        )
        .all()
    )


def test_accounts_that_share_a_leaf_name_stay_apart_by_path_and_type(
    db_session, seed_accounts
):
    result = _import(db_session)
    assert not result["errors"], result["errors"]
    # W-5: an income sub-account and an expense sub-account with one leaf name
    income = _account(
        db_session, "Maintenance & Repairs", parent="Labor", kind=AccountType.INCOME
    )
    expense = _account(
        db_session,
        "Maintenance & Repairs",
        parent="Automobile",
        kind=AccountType.EXPENSE,
    )
    assert income.id != expense.id
    assert Decimal(str(income.balance)) == Decimal("5149.50")
    assert Decimal(str(expense.balance)) == Decimal("254.50")
    fountains_income = _account(
        db_session, "Fountains & Garden Lighting", kind=AccountType.INCOME
    )
    fountains_expense = _account(
        db_session, "Fountains & Garden Lighting", kind=AccountType.EXPENSE
    )
    assert fountains_income.parent.parent.name == "Landscaping Services"
    assert fountains_expense.parent.parent.name == "Job Expenses"
    assert Decimal(str(fountains_income.balance)) == Decimal("12082.89")
    assert Decimal(str(fountains_expense.balance)) == Decimal("1150.00")


def test_a_number_alone_never_merges_an_account(db_session, seed_accounts):
    result = _import(db_session)
    by_number = {
        a.account_number: a for a in db_session.query(Account).all() if a.account_number
    }
    # the chart's accounts at the file's numbers keep their own books
    assert (
        Decimal(str(by_number["3100"].balance)) == 0
    )  # Owner's Draw, not the file's Retained Earnings
    assert (
        # the file's own "Office Supplies" (#7300), by name: 295.87, not its
        # 6400 Bank Service Charges
        Decimal(str(by_number["6400"].balance))
        == Decimal("295.87")
    )
    assert (
        Decimal(str(by_number["1510"].balance)) == 0
    )  # Accumulated Depreciation, not the truck
    assert (
        Decimal(str(by_number["2100"].balance)) == 0
    )  # Credit Card, not the CalOil Card
    # the file's accounts come in under their names, without the clashing number
    assert Decimal(
        str(_account(db_session, "Bank Service Charges").balance)
    ) == Decimal("379.50")
    assert _account(db_session, "Bank Service Charges").account_number is None
    truck = _account(db_session, "Original Purchase", parent="Truck")
    assert (
        Decimal(str(truck.balance)) == Decimal("13750.00")
        and truck.account_number is None
    )
    caloil = _account(db_session, "CalOil Card")
    assert (
        caloil.account_type == AccountType.LIABILITY
        and caloil.bank_kind == "credit_card"
    )
    assert (
        Decimal(str(caloil.balance)) == Decimal("1403.99")
        and caloil.account_number is None
    )
    # Retained Earnings is the chart's Retained Earnings (by name), not its 3100
    assert _account(db_session, "Retained Earnings").account_number == "3200"
    assert Decimal(str(by_number["3200"].balance)) == Decimal("43954.66")
    # and the result names what was dropped
    assert any(
        "Bank Service Charges" in w and "6400" in w and "without a number" in w
        for w in result["warnings"]
    ), result["warnings"]


def test_well_known_names_still_match_and_are_named(db_session, seed_accounts):
    result = _import(db_session)
    # the maintainer-verified cases of test_iif_import_sample still hold
    by_number = {
        a.account_number: a for a in db_session.query(Account).all() if a.account_number
    }
    assert Decimal(str(by_number["1100"].balance)) == Decimal(
        "35810.02"
    )  # "Accounts Receivable" #1200 in the file
    assert Decimal(str(by_number["2200"].balance)) == Decimal(
        "2086.50"
    )  # "Sales Tax Payable" #2300 in the file
    assert Decimal(str(by_number["1200"].balance)) == Decimal(
        "110.00"
    )  # "Undeposited Funds" #1400 in the file
    assert (
        db_session.query(Account).filter(Account.name == "Inventory Asset").count() == 0
    )
    assert Decimal(str(by_number["1300"].balance)) == Decimal(
        "6937.08"
    )  # "Inventory Asset" is 1300 Inventory
    merges = [
        w
        for w in result["warnings"]
        if w.startswith("Matched to the chart by a name it knows")
    ]
    assert (
        merges and "'Inventory Asset' is the chart's 1300 Inventory" in merges[0]
    ), result["warnings"]


def test_a_second_import_posts_no_opening_balance_again(db_session, seed_accounts):
    first = _import(db_session)
    assert not first["errors"]
    before = {a.id: Decimal(str(a.balance)) for a in db_session.query(Account).all()}
    assert len(_openings(db_session)) == 1
    again = _import(db_session)
    assert not again["errors"], again["errors"]
    assert again["accounts"] == 0
    assert len(_openings(db_session)) == 1
    after = {a.id: Decimal(str(a.balance)) for a in db_session.query(Account).all()}
    assert after == before
    assert any("none were posted this time" in w for w in again["warnings"]), again[
        "warnings"
    ]
    assert not any("Summing both OBAMOUNTs" in w for w in again["warnings"])


def test_a_same_named_account_of_another_type_is_created_beside_it(
    db_session, seed_accounts
):
    from app.services.iif_import import import_all

    iif = (
        "!ACCNT\tNAME\tACCNTTYPE\tOBAMOUNT\r\n"
        "ACCNT\tInsurance\tINC\t-100.00\r\n"  # the chart's 6300 Insurance is an expense
        "ACCNT\tOpening Bal Equity\tEQUITY\t100.00\r\n"
    )
    result = import_all(db_session, iif)
    db_session.commit()
    assert not result["errors"], result["errors"]
    insurances = db_session.query(Account).filter(Account.name == "Insurance").all()
    assert {a.account_type for a in insurances} == {
        AccountType.EXPENSE,
        AccountType.INCOME,
    }
    expense = next(a for a in insurances if a.account_type == AccountType.EXPENSE)
    assert expense.account_number == "6300" and Decimal(str(expense.balance)) == 0
    assert any("created beside it" in w for w in result["warnings"]), result["warnings"]


def test_lists_imported_after_an_earlier_import_post_no_balances(
    db_session, seed_accounts
):
    """NEW-128: lists first imported on an older version (whose matching
    merged accounts wrongly), then imported again here, made the right
    accounts beside the wrong ones and posted their balances a second time:
    the same debt twice. Once a company holds an IIF opening entry, a later
    lists import posts no balance at all; the accounts it creates that carry
    one in the file are named, for the person to enter by hand."""
    from app.services.iif_import import import_all

    first = (
        "!ACCNT\tNAME\tACCNTTYPE\tOBAMOUNT\r\n"
        "ACCNT\tChecking\tBANK\t100.00\r\n"
        "ACCNT\tOpening Bal Equity\tEQUITY\t-100.00\r\n"
    )
    r1 = import_all(db_session, first)
    db_session.commit()
    assert not r1["errors"] and len(_openings(db_session)) == 1
    r2 = _import(db_session)
    assert not r2["errors"], r2["errors"]
    assert r2["accounts"] > 50
    assert len(_openings(db_session)) == 1
    checking = db_session.query(Account).filter(Account.account_number == "1000").one()
    assert Decimal(str(checking.balance)) == Decimal("100.00")  # not 100 + 99,250.02
    assert Decimal(str(_account(db_session, "CalOil Card").balance)) == 0
    note = [
        w
        for w in r2["warnings"]
        if w.startswith("This company's opening balances came with an earlier import")
    ]
    assert (
        note
        and "'CalOil Card' 1,403.99 credit" in note[0]
        and "keep the balance they have" in note[0]
    ), r2["warnings"]
    # W-6: every account created with a balance in the file is named, as
    # many as the sentence says (it stopped at twelve, "and 15 more")
    said = re.search(
        r"(\d+) account\(s\) created now carry a balance in the file, not posted: "
        r"(.*)\. Enter any that are real as an opening balance by hand",
        note[0],
    )
    assert said, note[0]
    named = said.group(2).split("; ")
    assert len(named) == int(said.group(1)) > 12, (said.group(1), len(named))
    assert all(n.endswith((" debit", " credit")) for n in named), named
    assert "; and " not in note[0] and " more" not in note[0]
    assert any(  # an income account far down the file, with its credit
        n.startswith("'Landscaping Services:Labor:Installation' 43,477.00")
        for n in named
    ), named[-5:]


def test_the_known_name_matches_are_listed_whole(
    db_session, seed_accounts, monkeypatch
):
    """W-6's other cut: the "Matched to the chart by a name it knows" note
    stopped at twelve names too. Thirteen aliases of the chart's 1100 all
    read in the note."""
    from app.services import iif_import

    aliases = [f"Receivables alias {n}" for n in range(1, 14)]
    for alias in aliases:
        monkeypatch.setitem(iif_import._WELL_KNOWN_ACCOUNTS, alias.lower(), "1100")
    iif = "!ACCNT\tNAME\tACCNTTYPE\r\n" + "".join(
        f"ACCNT\t{alias}\tAR\r\n" for alias in aliases
    )
    result = iif_import.import_all(db_session, iif)
    db_session.commit()
    assert not result["errors"], result["errors"]
    note = [
        w
        for w in result["warnings"]
        if w.startswith("Matched to the chart by a name it knows")
    ]
    assert len(note) == 1, result["warnings"]
    assert all(f"'{alias}' is the chart's 1100" in note[0] for alias in aliases), note
    assert "; and " not in note[0] and " more" not in note[0]
    assert (
        db_session.query(Account)
        .filter(Account.name.like("Receivables alias%"))
        .count()
        == 0
    )
