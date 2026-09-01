"""Class tracking: CRUD + protections, posting propagation, P&L by class,
and IIF CLASS resolution."""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.classes import TxnClass
from app.models.transactions import Transaction
from app.services.accounting import create_journal_entry
from app.services.classes_service import uncategorized_class_id

# ── CRUD + protections ───────────────────────────────────────────────────


def test_list_creates_system_default(client):
    classes = client.get("/api/classes").json()
    assert any(c["is_system_default"] and c["name"] == "Uncategorized" for c in classes)


def test_create_rename_archive_delete(client):
    created = client.post("/api/classes", json={"name": "Retail"}).json()
    assert created["name"] == "Retail"

    dup = client.post("/api/classes", json={"name": "retail"})
    assert dup.status_code == 409

    renamed = client.put(f"/api/classes/{created['id']}", json={"name": "Retail East"})
    assert renamed.json()["name"] == "Retail East"

    archived = client.put(f"/api/classes/{created['id']}", json={"is_archived": True})
    assert archived.json()["is_archived"] is True
    # archived classes hidden from the default listing
    assert all(c["id"] != created["id"] for c in client.get("/api/classes").json())

    deleted = client.delete(f"/api/classes/{created['id']}")
    assert deleted.status_code == 200


def test_system_default_is_immutable(client):
    default = next(
        c for c in client.get("/api/classes").json() if c["is_system_default"]
    )
    assert (
        client.put(f"/api/classes/{default['id']}", json={"name": "X"}).status_code
        == 400
    )
    assert (
        client.put(
            f"/api/classes/{default['id']}", json={"is_archived": True}
        ).status_code
        == 400
    )
    assert client.delete(f"/api/classes/{default['id']}").status_code == 400


def test_class_in_use_cannot_be_deleted(client, db_session, seed_accounts):
    cls = client.post("/api/classes", json={"name": "Used"}).json()
    accounts = db_session.query(Account).limit(2).all()
    create_journal_entry(
        db_session,
        date(2026, 7, 1),
        "class usage",
        [
            {
                "account_id": accounts[0].id,
                "debit": Decimal("10"),
                "credit": Decimal("0"),
            },
            {
                "account_id": accounts[1].id,
                "debit": Decimal("0"),
                "credit": Decimal("10"),
            },
        ],
        class_id=cls["id"],
    )
    db_session.commit()
    resp = client.delete(f"/api/classes/{cls['id']}")
    assert resp.status_code == 400
    assert "archive" in resp.json()["detail"].lower()


def test_uncategorized_get_or_create_is_stable(db_session):
    first = uncategorized_class_id(db_session)
    second = uncategorized_class_id(db_session)
    assert first == second
    assert db_session.query(TxnClass).filter(TxnClass.is_system_default).count() == 1


# ── Posting propagation ──────────────────────────────────────────────────


def _income_expense(db_session):
    income = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.INCOME)
        .first()
    )
    expense = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.EXPENSE)
        .first()
    )
    return income, expense


def test_journal_route_carries_class(client, db_session, seed_accounts):
    cls = client.post("/api/classes", json={"name": "Ops"}).json()
    income, expense = _income_expense(db_session)
    resp = client.post(
        "/api/journal",
        json={
            "date": "2026-07-02",
            "description": "classed entry",
            "class_id": cls["id"],
            "lines": [
                {"account_id": expense.id, "debit": 25, "credit": 0},
                {"account_id": income.id, "debit": 0, "credit": 25},
            ],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "manual")
        .order_by(Transaction.id.desc())
        .first()
    )
    assert txn.class_id == cls["id"]


def test_invoice_posting_carries_class(
    client, db_session, seed_accounts, seed_customer
):
    cls = client.post("/api/classes", json={"name": "Consulting"}).json()
    resp = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-03",
            "class_id": cls["id"],
            "lines": [{"description": "work", "quantity": 1, "rate": 100}],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    inv = resp.json()
    assert inv["class_id"] == cls["id"]
    txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "invoice", Transaction.source_id == inv["id"]
        )
        .first()
    )
    assert txn is not None and txn.class_id == cls["id"]


# ── P&L by class ─────────────────────────────────────────────────────────


def test_profit_loss_by_class_reconciles(client, db_session, seed_accounts):
    cls = client.post("/api/classes", json={"name": "Side Gig"}).json()
    income, expense = _income_expense(db_session)

    # classed: 200 income; untagged: 50 income
    create_journal_entry(
        db_session,
        date(2026, 7, 10),
        "classed revenue",
        [
            {"account_id": expense.id, "debit": Decimal("200"), "credit": Decimal("0")},
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("200")},
        ],
        class_id=cls["id"],
    )
    create_journal_entry(
        db_session,
        date(2026, 7, 11),
        "untagged revenue",
        [
            {"account_id": expense.id, "debit": Decimal("50"), "credit": Decimal("0")},
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("50")},
        ],
    )
    db_session.commit()

    data = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    by_name = {c["class_name"]: c for c in data["classes"]}
    assert by_name["Side Gig"]["income"] == 200.0
    assert by_name["Uncategorized"]["income"] == 50.0

    plain = client.get(
        "/api/reports/profit-loss?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert abs(data["total_income"] - plain["total_income"]) < 0.01
    assert abs(data["total_net_income"] - plain["net_income"]) < 0.01


# ── IIF CLASS column ─────────────────────────────────────────────────────


IIF_BILL_WITH_CLASS = (
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO\tCLASS\n"
    "!ENDTRNS\n"
    "TRNS\tBILL\t07/05/2026\tAccounts Payable\tApple Store\t-100.00\tB-CL-1\t\n"
    "SPL\tBILL\t07/05/2026\tOffice Supplies\t100.00\tsupplies\tSide Gig\n"
    "ENDTRNS\n"
)


def test_iif_bill_resolves_class(client, db_session, seed_accounts):
    from app.models.bills import Bill
    from app.models.contacts import Vendor
    from app.services.iif_import import import_all

    cls = client.post("/api/classes", json={"name": "Side Gig"}).json()
    db_session.add(Vendor(name="Apple Store", is_active=True))
    db_session.commit()

    result = import_all(db_session, IIF_BILL_WITH_CLASS)
    assert result["bills"] == 1, result["errors"]
    bill = db_session.query(Bill).filter(Bill.bill_number == "B-CL-1").one()
    assert bill.class_id == cls["id"]
    txn = db_session.get(Transaction, bill.transaction_id)
    assert txn.class_id == cls["id"]


def test_iif_bill_unknown_class_errors(db_session, seed_accounts):
    from app.models.contacts import Vendor
    from app.services.iif_import import import_all

    db_session.add(Vendor(name="Apple Store", is_active=True))
    db_session.commit()
    iif = IIF_BILL_WITH_CLASS.replace("Side Gig", "No Such Class")
    result = import_all(db_session, iif)
    assert result["bills"] == 0
    assert any("No Such Class" in e["message"] for e in result["errors"])


# ── IIF class list (!CLASS) ──────────────────────────────────────────────


# Shape of a real QuickBooks class-list export: File > Utilities > Export >
# Lists > Class List. "Main Office:North" is a subclass — QB writes the full
# parent:child path here AND in the SPL CLASS column, so it has to survive
# the round trip verbatim for the two to match.
IIF_CLASS_LIST = (
    "!CLASS\tNAME\tREFNUM\tTIMESTAMP\tBASETYPE\tEXTRA\n"
    "CLASS\tSide Gig\t1\t1187300000\tCLASS\t\n"
    "CLASS\tMain Office\t2\t1187300000\tCLASS\t\n"
    "CLASS\tMain Office:North\t3\t1187300000\tCLASS\t\n"
)


def _class_names(db_session):
    return {c.name for c in db_session.query(TxnClass).all()}


def test_iif_class_list_imports(db_session):
    from app.services.iif_import import import_all

    result = import_all(db_session, IIF_CLASS_LIST)
    assert result["classes"] == 3, result["errors"]
    assert not result["errors"]
    names = _class_names(db_session)
    assert {"Side Gig", "Main Office", "Main Office:North"} <= names


def test_iif_class_list_dedups_case_insensitively(client, db_session):
    from app.services.iif_import import import_all

    client.post("/api/classes", json={"name": "side gig"})

    result = import_all(db_session, IIF_CLASS_LIST)
    assert result["classes"] == 2, result["errors"]
    # The pre-existing row keeps its own casing; no second "Side Gig" appears.
    names = _class_names(db_session)
    assert "side gig" in names
    assert "Side Gig" not in names


def test_iif_class_list_reimport_is_a_noop(db_session):
    from app.services.iif_import import import_all

    import_all(db_session, IIF_CLASS_LIST)
    again = import_all(db_session, IIF_CLASS_LIST)
    assert again["classes"] == 0
    assert not again["errors"]
    assert db_session.query(TxnClass).filter(TxnClass.name == "Side Gig").count() == 1


def test_iif_class_hidden_imports_archived(db_session):
    from app.services.iif_import import import_all

    iif = (
        "!CLASS\tNAME\tREFNUM\tTIMESTAMP\tBASETYPE\tEXTRA\tHIDDEN\n"
        "CLASS\tRetired Division\t1\t1187300000\tCLASS\t\tY\n"
        "CLASS\tLive Division\t2\t1187300000\tCLASS\t\tN\n"
    )
    result = import_all(db_session, iif)
    assert result["classes"] == 2, result["errors"]
    retired = (
        db_session.query(TxnClass).filter(TxnClass.name == "Retired Division").one()
    )
    live = db_session.query(TxnClass).filter(TxnClass.name == "Live Division").one()
    assert retired.is_archived is True
    assert live.is_archived is False


def test_iif_class_missing_name_errors(db_session):
    from app.services.iif_import import import_all

    iif = (
        "!CLASS\tNAME\tREFNUM\tTIMESTAMP\tBASETYPE\tEXTRA\n"
        "CLASS\t\t1\t1187300000\tCLASS\t\n"
        "CLASS\tGood One\t2\t1187300000\tCLASS\t\n"
    )
    result = import_all(db_session, iif)
    assert result["classes"] == 1
    assert any("Missing class NAME" in e["message"] for e in result["errors"])


def test_iif_class_overlong_name_errors_rather_than_truncating(db_session):
    """A truncated class would import but never match its SPL CLASS value."""
    from app.services.iif_import import _CLASS_NAME_MAX, import_all

    long_name = "L" * (_CLASS_NAME_MAX + 1)
    iif = (
        "!CLASS\tNAME\tREFNUM\tTIMESTAMP\tBASETYPE\tEXTRA\n"
        f"CLASS\t{long_name}\t1\t1187300000\tCLASS\t\n"
    )
    result = import_all(db_session, iif)
    assert result["classes"] == 0
    assert any(str(_CLASS_NAME_MAX) in e["message"] for e in result["errors"])
    assert db_session.query(TxnClass).filter(TxnClass.name == long_name).count() == 0


def test_iif_class_list_then_transaction_resolves(db_session, seed_accounts):
    """End-to-end: one file carrying both the class list and a bill that
    cites it imports cleanly, with no class pre-created by hand."""
    from app.models.bills import Bill
    from app.models.contacts import Vendor
    from app.services.iif_import import import_all

    db_session.add(Vendor(name="Apple Store", is_active=True))
    db_session.commit()

    result = import_all(db_session, IIF_CLASS_LIST + IIF_BILL_WITH_CLASS)
    assert result["classes"] == 3, result["errors"]
    assert result["bills"] == 1, result["errors"]

    cls = db_session.query(TxnClass).filter(TxnClass.name == "Side Gig").one()
    bill = db_session.query(Bill).filter(Bill.bill_number == "B-CL-1").one()
    assert bill.class_id == cls.id
    txn = db_session.get(Transaction, bill.transaction_id)
    assert txn.class_id == cls.id


def test_iif_class_list_imports_before_transactions_regardless_of_order(
    db_session, seed_accounts
):
    """The class list still lands first when it trails the transactions."""
    from app.models.bills import Bill
    from app.models.contacts import Vendor
    from app.services.iif_import import import_all

    db_session.add(Vendor(name="Apple Store", is_active=True))
    db_session.commit()

    result = import_all(db_session, IIF_BILL_WITH_CLASS + IIF_CLASS_LIST)
    assert result["bills"] == 1, result["errors"]
    assert db_session.query(Bill).filter(Bill.bill_number == "B-CL-1").one().class_id


def test_validate_reports_class_section():
    from app.services.iif_import import validate_iif

    report = validate_iif(IIF_CLASS_LIST)
    assert report["valid"] is True
    assert "CLASS" in report["sections_found"]
    assert report["record_counts"]["CLASS"] == 3
