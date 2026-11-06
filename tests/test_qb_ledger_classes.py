"""Transaction Detail by Account keeps each line's Class (#722).

QuickBooks Desktop writes a Class column into the report when Class is among
its columns. The import read Type, Date, Num, Name, Memo, Debit and Credit
and never Class, so a company that keeps its entities apart by class came
across with every posting unclassified and P&L by Class empty. The other
report imports read it; the ledger import now does too, with the same
resolver (classes_service.report_class_id): exact, then case-insensitive, a
class the books lack created on first sight and named in the notes,
QuickBooks' "Parent:Child" path kept whole as the name, the name length
limit kept. Each row's own Class cell is its line's class; an empty cell
stays unclassified, and a file with no Class column imports as before.

Two layouts, both QuickBooks' own: the sample books of
tests/fixtures/qb_desktop/ (the interleaved columns) with the Class column
QuickBooks adds after Memo, and the one-label-column export with the header
QuickBooks writes when Class is among the columns. The companies, people and
classes are invented.
"""

import csv
import io

from app.models.classes import TxnClass
from app.models.transactions import Transaction, TransactionLine
from app.services.qb_ledger_import import SOURCE_TYPE
from tests.qb_ledger_files import send
from tests.test_qb_desktop_ledger import FIXTURE, _chart

YEAR = "?start_date=2024-01-01&end_date=2024-12-31"
UNIT = "Properties:Unit 4"

# The sample books with a Class column, by (Type, Num, Name, the section's
# account): the bank side of check 1001 left empty, its software line under
# a Parent:Child class the books lack, its supplies line under one they
# have; the walk-in deposit under North LLC written two ways; the card-sales
# journal's income under a class the books lack.
CLASSES = {
    ("Check", "1001", "Office Depot", "Software"): UNIT,
    ("Check", "1001", "Office Depot", "Supplies"): "North LLC",
    ("Deposit", "", "Walk-in", "Operating Checking 4321"): "North LLC",
    ("Deposit", "", "Walk-in", "Service Income"): "north llc",
    ("General Journal", "7", "", "Service Income"): "Rental Division",
    ("Check", "1002", "Harbor Properties", "Rent"): "South LLC",
    ("Check", "1002", "Harbor Properties", "Operating Checking 4321"): "South LLC",
}


def _fixture_with_class(classes=CLASSES) -> bytes:
    """The sample report with QuickBooks' Class column after Memo (and the
    empty column after it, as every field has), each transaction row's cell
    from ``classes``."""
    rows = list(csv.reader(io.StringIO(FIXTURE.read_bytes().decode("cp1252"))))
    header = rows[0]
    at = header.index("Memo") + 2
    section = None
    out = []
    for r in rows:
        if r is header:
            cells = ["Class", ""]
        elif len(r) > 5 and r[5].strip():
            key = (r[5], r[9], r[13], section)
            cells = [classes.get(key, ""), ""]
        else:
            label = next((c for c in r[:5] if c.strip()), "")
            if label and not label.startswith("Total") and label != "TOTAL":
                section = label
            cells = ["", ""]
        out.append(r[:at] + cells + r[at:])
    text = io.StringIO()
    csv.writer(text, lineterminator="\r\n").writerows(out)
    return text.getvalue().encode("cp1252")


def _lines(db):
    return (
        db.query(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .all()
    )


def _class(db, name):
    return db.query(TxnClass).filter(TxnClass.name == name).one()


def _seed_classes(db):
    for name in ("North LLC", "South LLC"):
        db.add(TxnClass(name=name))
    db.commit()


def _by_class(client):
    r = client.get("/api/reports/profit-loss-by-class" + YEAR)
    assert r.status_code == 200, r.text
    return {c["class_name"]: c for c in r.json()["classes"]}


def test_each_line_posts_with_its_own_rows_class(client, db_session, seed_accounts):
    _chart(db_session)
    _seed_classes(db_session)
    result = send(client, _fixture_with_class())
    assert result["errors"] == [], result
    assert result["journals"] == 8, result
    north, south = _class(db_session, "North LLC"), _class(db_session, "South LLC")
    unit, rental = _class(db_session, UNIT), _class(db_session, "Rental Division")
    # each class made once, on first sight, and the notes say so
    assert (
        db_session.query(TxnClass).filter(TxnClass.name.ilike("north llc")).count() == 1
    )
    for name in (UNIT, "Rental Division"):
        note = (
            f"Class '{name}' is named in the report and was not in your books: created"
        )
        assert result["warnings"].count(note) == 1, result["warnings"]
    assert result["class_lines"] == 7
    assert result["classes_new"] == ["Rental Division", UNIT]
    got = {
        (ln.account.name, ln.description, ln.debit or ln.credit): ln.class_id
        for ln in _lines(db_session)
    }
    assert got[("Software", "Scheduling app", 30)] == unit.id
    assert got[("Supplies", "Paper", 45)] == north.id
    assert got[("Operating Checking 4321", "Office Depot", 75)] is None  # empty
    assert got[("Service Income", "Deposit", 120)] == north.id  # "north llc"
    assert got[("Operating Checking 4321", "Deposit", 120)] == north.id
    assert got[("Operating Checking 4321", "Deposit", 300)] is None
    assert got[("Service Income", "Card sales, week 2", 500)] == rental.id
    assert got[("Card Fees", "Card sales, week 2", 15)] is None
    assert got[("Rent", "January rent", 1000)] == south.id
    assert got[("Operating Checking 4321", "January rent", 1000)] == south.id
    # the entry's header keeps none: each line says its own
    assert all(
        t.class_id is None
        for t in db_session.query(Transaction).filter_by(source_type=SOURCE_TYPE)
    )
    by = _by_class(client)
    assert by["North LLC"]["income"] == 120.0
    assert by["Rental Division"]["income"] == 500.0
    assert by["South LLC"]["expenses"] == 1000.0
    assert by[UNIT]["expenses"] == 30.0
    assert by["North LLC"]["expenses"] == 45.0


def test_a_file_without_a_class_column_imports_as_before(
    client, db_session, seed_accounts
):
    _chart(db_session)
    before = db_session.query(TxnClass).count()
    result = send(client, FIXTURE.read_bytes())
    assert result["errors"] == [] and result["journals"] == 8, result
    assert "class_lines" not in result and "classes_text" not in result
    assert all(ln.class_id is None for ln in _lines(db_session))
    assert db_session.query(TxnClass).count() == before


def test_a_class_column_with_every_cell_empty_imports_as_before(
    client, db_session, seed_accounts
):
    _chart(db_session)
    before = db_session.query(TxnClass).count()
    result = send(client, _fixture_with_class({}))
    assert result["errors"] == [] and result["journals"] == 8, result
    assert "class_lines" not in result
    assert all(ln.class_id is None for ln in _lines(db_session))
    assert db_session.query(TxnClass).count() == before


def test_a_second_upload_still_skips_every_duplicate(client, db_session, seed_accounts):
    _chart(db_session)
    _seed_classes(db_session)
    send(client, _fixture_with_class())
    posted = len(_lines(db_session))
    classes = db_session.query(TxnClass).count()
    again = send(client, _fixture_with_class())
    assert again["journals"] == 0 and again["duplicates_skipped"] == 8, again
    assert len(_lines(db_session)) == posted
    assert db_session.query(TxnClass).count() == classes


def test_lines_imported_before_do_not_gain_a_class_and_the_note_says_why(
    client, db_session, seed_accounts
):
    """The import key is the journal's Type, Num, Date and name, class
    left out, so the same file with its classes is the same journals: they
    are skipped, keep what they posted with, and the note says how to bring
    the classes in."""
    _chart(db_session)
    _seed_classes(db_session)
    send(client, FIXTURE.read_bytes())
    again = send(client, _fixture_with_class())
    assert again["journals"] == 0 and again["duplicates_skipped"] == 8, again
    assert all(ln.class_id is None for ln in _lines(db_session))
    assert any(
        "8 transactions already in your books are skipped and do not gain a class" in w
        and "fresh company" in w
        and "backup taken before the first import" in w
        for w in again["warnings"]
    ), again["warnings"]
    # nothing posted, so nothing made
    assert db_session.query(TxnClass).filter_by(name="Rental Division").count() == 0


# The one-label-column export, with the header QuickBooks writes when Class
# is among the report's columns.
HEADER = (
    ',"Type","Date","Num","Name","Memo","Class","Clr","Split","Debit","Credit",'
    '"Balance"'
)


def _row(ttype, day, num, name, memo, klass, split, debit="", credit=""):
    return ["", ttype, day, num, name, memo, klass, "", split, debit, credit, ""]


def _label(text, debit="", credit=""):
    return [text, "", "", "", "", "", "", "", "", debit, credit, ""]


def _small(rent_account="Rent", extra_class="", *, with_class=True) -> bytes:
    """Two checks across three accounts: check 2001 split over Rent and
    Supplies (its bank row's class empty), check 2002 to Rent alone."""
    rows = [
        _label("Operating Checking 4321"),
        _row(
            "Check",
            "03/02/2024",
            "2001",
            "Bayside Rentals",
            "",
            "",
            "-SPLIT-",
            credit="600.00",
        ),
        _row(
            "Check",
            "03/09/2024",
            "2002",
            "Bayside Rentals",
            "March",
            "South LLC",
            rent_account,
            credit="400.00",
        ),
        _label("Total Operating Checking 4321", "0.00", "1,000.00"),
        _label(rent_account),
        _row(
            "Check",
            "03/02/2024",
            "2001",
            "Bayside Rentals",
            "Lot rent",
            "Fleet:Yard " + extra_class if extra_class else "SOUTH LLC",
            "Operating Checking 4321",
            debit="500.00",
        ),
        _row(
            "Check",
            "03/09/2024",
            "2002",
            "Bayside Rentals",
            "March",
            "South LLC",
            "Operating Checking 4321",
            debit="400.00",
        ),
        _label(f"Total {rent_account}", "900.00", "0.00"),
        _label("Supplies"),
        _row(
            "Check",
            "03/02/2024",
            "2001",
            "Bayside Rentals",
            "Locks",
            "Fleet:Yard",
            "Operating Checking 4321",
            debit="100.00",
        ),
        _label("Total Supplies", "100.00", "0.00"),
        _label("TOTAL", "1,000.00", "1,000.00"),
    ]
    header = HEADER
    if not with_class:
        header = header.replace('"Class",', "")
        rows = [r[:6] + r[7:] for r in rows]
    out = io.StringIO()
    out.write(header + "\r\n")
    csv.writer(out, lineterminator="\r\n").writerows(rows)
    return out.getvalue().encode("cp1252")


def _small_chart(db, seed_accounts):
    from decimal import Decimal

    from app.models.accounts import Account, AccountType

    for name, kind in (
        ("Operating Checking 4321", AccountType.ASSET),
        ("Rent", AccountType.EXPENSE),
        ("Supplies", AccountType.EXPENSE),
    ):
        if not db.query(Account).filter(Account.name == name).first():
            db.add(Account(name=name, account_type=kind, balance=Decimal("0")))
    db.commit()


def test_the_one_column_layout_reads_its_class_column_row_by_row(
    client, db_session, seed_accounts
):
    _small_chart(db_session, seed_accounts)
    _seed_classes(db_session)
    result = send(client, _small())
    assert result["errors"] == [] and result["journals"] == 2, result
    south, yard = _class(db_session, "South LLC"), _class(db_session, "Fleet:Yard")
    got = {
        (ln.account.name, ln.description, ln.debit, ln.credit): ln.class_id
        for ln in _lines(db_session)
    }
    assert got[("Rent", "Lot rent", 500, 0)] == south.id  # "SOUTH LLC"
    assert got[("Supplies", "Locks", 100, 0)] == yard.id
    assert got[("Operating Checking 4321", "Bayside Rentals", 0, 600)] is None
    assert got[("Rent", "March", 400, 0)] == south.id
    assert got[("Operating Checking 4321", "March", 0, 400)] == south.id
    assert result["classes_text"] == "4 lines posted with a class."
    by = client.get("/api/reports/profit-loss-by-class" + YEAR).json()
    expenses = {c["class_name"]: c["expenses"] for c in by["classes"]}
    assert expenses["South LLC"] == 900.0 and expenses["Fleet:Yard"] == 100.0


def test_the_same_file_without_its_class_column_is_unchanged(
    client, db_session, seed_accounts
):
    _small_chart(db_session, seed_accounts)
    result = send(client, _small(with_class=False))
    assert result["errors"] == [] and result["journals"] == 2, result
    assert all(ln.class_id is None for ln in _lines(db_session))


def test_the_list_before_anything_posts_counts_the_classes(
    client, db_session, seed_accounts
):
    """A file with problems posts nothing on its first upload (#663): that
    answer says how many lines carry a class and which classes the import
    would create, and creates none."""
    _small_chart(db_session, seed_accounts)
    _seed_classes(db_session)
    before = db_session.query(TxnClass).count()
    result = send(client, _small(rent_account="Lot Rent"))  # not in the chart
    assert result["journals"] == 0 and result["problems"], result
    assert result["class_lines"] == 4
    assert result["classes_new"] == ["Fleet:Yard"]
    assert result["classes_text"] == (
        "4 of the file's 5 lines carry a class. 1 class the file names is not in "
        "your books and is created when a line that names it posts: Fleet:Yard."
    )
    assert db_session.query(TxnClass).count() == before
    assert _lines(db_session) == []


def test_the_list_says_fund_on_a_nonprofit(client, db_session, seed_accounts):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    _small_chart(db_session, seed_accounts)
    result = send(client, _small(rent_account="Lot Rent"))
    assert result["classes_text"].startswith("4 of the file's 5 lines carry a fund.")
    assert "2 funds the file names are not in your books" in result["classes_text"]


def test_a_class_name_over_the_limit_refuses_the_file(
    client, db_session, seed_accounts
):
    _small_chart(db_session, seed_accounts)
    limit = TxnClass.__table__.c.name.type.length
    result = send(client, _small(extra_class="x" * limit))
    assert result["journals"] == 0, result
    assert result["errors"][0] == "Nothing was imported."
    assert f"is over the {limit}-character limit" in result["errors"][1]
    assert _lines(db_session) == []
    assert db_session.query(TxnClass).filter(TxnClass.name.like("Fleet%")).count() == 0
