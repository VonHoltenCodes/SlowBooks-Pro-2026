"""QBO JournalEntry API imports complete journals independently of reports."""

from datetime import date
from decimal import Decimal
import re

import pytest

from app.models.accounts import Account, AccountType
from app.models.qbo_mapping import QBOMapping
from app.models.settings import Settings
from app.models.transactions import Transaction, TransactionLine
from app.routes.journal import list_journal_entries
from app.services import accounting, qbo_import, qbo_ledger_import, qbo_service
from app.services.bank_register import source_link


def _entry(qbo_id="227", amount=25.54):
    # JournalEntry.TotalAmt is always zero, even when its lines have amounts.
    return {
        "Id": qbo_id,
        "SyncToken": "0",
        "TxnDate": "2026-08-03",
        "DocNumber": "ADJ-7",
        "PrivateNote": "Opening adjustment",
        "TotalAmt": 0,
        "Line": [
            {
                "Id": "0",
                "Amount": amount,
                "Description": "Expense debit",
                "DetailType": "JournalEntryLineDetail",
                "JournalEntryLineDetail": {
                    "PostingType": "Debit",
                    "AccountRef": {"value": "2"},
                },
            },
            {
                "Id": "1",
                "Amount": amount,
                "Description": "Checking credit",
                "DetailType": "JournalEntryLineDetail",
                "JournalEntryLineDetail": {
                    "PostingType": "Credit",
                    "AccountRef": {"value": "1"},
                },
            },
        ],
    }


class QBOClient:
    """Exercise the real SDK's query generation and JSON deserialization."""

    def __init__(self, entries):
        self.entries = entries
        self.queries = []
        self.kind = "General Journal"

    def query(self, query):
        self.queries.append(query)
        match = re.fullmatch(
            r"SELECT \* FROM JournalEntry STARTPOSITION (\d+) MAXRESULTS 100", query
        )
        assert match, query
        start = int(match[1]) - 1
        return {"QueryResponse": {"JournalEntry": self.entries[start : start + 100]}}

    def get_report(self, name, qs):
        assert name == "GeneralLedger"

        def row(qbo_id, kind, amount):
            values = ["2026-08-03", kind, "ADJ-7", "", "Memo", "", str(amount), "0"]
            return {
                "type": "Data",
                "ColData": [
                    {"value": value, **({"id": qbo_id} if index == 1 else {})}
                    for index, value in enumerate(values)
                ],
            }

        return {
            "Header": {
                "StartPeriod": qs["start_date"],
                "EndPeriod": qs["end_date"],
                "ReportBasis": "Accrual",
            },
            "Columns": {
                "Column": [
                    {"ColTitle": title}
                    for title in [
                        "Date",
                        "Transaction Type",
                        "Num",
                        "Name",
                        "Memo/Description",
                        "Split",
                        "Amount",
                        "Balance",
                    ]
                ]
            },
            "Rows": {
                "Row": [
                    {
                        "type": "Section",
                        "Header": {"ColData": [{"id": account_id}]},
                        "Rows": {
                            "Row": [
                                row("227", self.kind, journal_amount),
                                row("228", "Purchase", purchase_amount),
                            ]
                        },
                    }
                    for account_id, journal_amount, purchase_amount in [
                        ("1", "-25.54", "-10"),
                        ("2", "25.54", "10"),
                    ]
                ]
            },
        }


@pytest.fixture
def qbo(db_session, monkeypatch):
    accounts = {}
    for qbo_id, name, kind in [
        ("1", "Checking", AccountType.ASSET),
        ("2", "Expenses", AccountType.EXPENSE),
    ]:
        account = Account(name=name, account_type=kind, balance=Decimal("1000"))
        db_session.add(account)
        db_session.flush()
        db_session.add(
            QBOMapping(entity_type="account", qbo_id=qbo_id, slowbooks_id=account.id)
        )
        accounts[qbo_id] = account
    db_session.flush()
    client = QBOClient([_entry()])
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: client)
    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: client)
    return client, accounts


def _ledger(db):
    return qbo_ledger_import.import_ledger(
        db, start=date(2026, 8, 3), end=date(2026, 8, 3)
    )


def test_api_import_preserves_lines_and_posts_once(db_session, qbo):
    client, accounts = qbo
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 1,
        "errors": [],
    }
    txn = db_session.query(Transaction).one()
    assert txn.date == date(2026, 8, 3)
    assert txn.reference == "ADJ-7"
    assert txn.description == "Opening adjustment"
    assert txn.source_type == "qbo_journal"
    assert source_link(txn) == f"/#/journal/{txn.id}"
    assert [
        (line.account_id, line.debit, line.credit, line.description)
        for line in txn.lines
    ] == [
        (accounts["2"].id, Decimal("25.54"), Decimal("0"), "Expense debit"),
        (accounts["1"].id, Decimal("0"), Decimal("25.54"), "Checking credit"),
    ]
    assert accounts["1"].balance == Decimal("-25.54")
    assert accounts["2"].balance == Decimal("25.54")
    mapping = db_session.query(QBOMapping).filter_by(entity_type="journal_entry").one()
    assert (mapping.qbo_id, mapping.slowbooks_id, mapping.qbo_sync_token) == (
        "227",
        txn.id,
        "0",
    )
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 0,
        "errors": [],
    }
    assert db_session.query(TransactionLine).count() == 2
    entries = list_journal_entries(db=db_session)
    assert entries[0].lines[0].account_number == ""
    assert entries[0].total_debit == entries[0].total_credit == 25.54


def test_queries_every_page_and_does_not_dedup_by_document_number(db_session, qbo):
    client, accounts = qbo
    client.entries = [_entry(str(index)) for index in range(101)]
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 101,
        "errors": [],
    }
    assert client.queries == [
        "SELECT * FROM JournalEntry STARTPOSITION 1 MAXRESULTS 100",
        "SELECT * FROM JournalEntry STARTPOSITION 101 MAXRESULTS 100",
    ]
    assert db_session.query(Transaction).count() == 101
    assert accounts["1"].balance == Decimal("-2579.54")


@pytest.mark.parametrize("kind", ["General Journal", "Journal Entry", "JournalEntry"])
@pytest.mark.parametrize("ledger_first", [False, True])
def test_api_and_report_share_one_posting(db_session, qbo, kind, ledger_first):
    client, accounts = qbo
    client.kind = kind
    if ledger_first:
        assert _ledger(db_session) == {"imported": 2, "errors": []}
        # Simulate the source_type used before direct journal imports existed.
        journal_map = (
            db_session.query(QBOMapping)
            .filter_by(entity_type="ledger", qbo_id=f"{kind}:227")
            .one()
        )
        db_session.get(Transaction, journal_map.slowbooks_id).source_type = "qbo_ledger"
        assert qbo_import.import_journal_entries(db_session) == {
            "imported": 0,
            "errors": [],
        }
    else:
        assert qbo_import.import_journal_entries(db_session)["imported"] == 1
        assert _ledger(db_session) == {"imported": 1, "errors": []}
    assert _ledger(db_session) == {"imported": 0, "errors": []}
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 0,
        "errors": [],
    }
    assert db_session.query(Transaction).count() == 2
    assert (
        db_session.query(Transaction).filter_by(source_type="qbo_journal").count() == 1
    )
    assert accounts["1"].balance == Decimal("-35.54")


@pytest.mark.parametrize(
    "fault, message",
    [
        ("missing_account", "import QBO Accounts first"),
        ("unbalanced", "does not balance"),
        ("invalid_date", "invalid transaction date"),
        ("invalid_posting", "posting type"),
        ("negative", "non-negative"),
        ("nan", "finite"),
        ("invalid_rate", "exchange rate"),
        ("unsupported_line", "unsupported line type"),
    ],
)
def test_bad_journal_blocks_batch_without_partial_lines(
    db_session, qbo, fault, message
):
    client, accounts = qbo
    broken = _entry("bad")
    if fault == "missing_account":
        broken["Line"][1]["JournalEntryLineDetail"]["AccountRef"]["value"] = "missing"
    elif fault == "unbalanced":
        broken["Line"][1]["Amount"] = 24
    elif fault == "invalid_date":
        broken["TxnDate"] = "not-a-date"
    elif fault == "invalid_posting":
        broken["Line"][1]["JournalEntryLineDetail"]["PostingType"] = "unknown"
    elif fault in {"negative", "nan"}:
        broken["Line"][1]["Amount"] = -1 if fault == "negative" else "NaN"
    elif fault == "invalid_rate":
        broken["ExchangeRate"] = 0
    elif fault == "unsupported_line":
        broken["Line"][1]["DetailType"] = "TaxLineDetail"
    client.entries.append(broken)
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 0
    assert message in result["errors"][0]["message"]
    assert db_session.query(Transaction).count() == 0
    assert db_session.query(TransactionLine).count() == 0
    assert accounts["1"].balance == Decimal("1000")


def test_closed_period_is_not_imported(db_session, qbo):
    db_session.add(Settings(key="closing_date", value="2026-08-31"))
    db_session.flush()
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 0
    assert "closing date" in result["errors"][0]["message"]
    assert db_session.query(Transaction).count() == 0


def test_description_only_and_voided_lines_are_not_posted(db_session, qbo):
    client, accounts = qbo
    client.entries[0]["Line"].append(
        {"DetailType": "DescriptionOnly", "Description": "Note"}
    )
    client.entries.append(_entry("voided", amount=0))
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 1,
        "errors": [],
    }
    assert db_session.query(TransactionLine).count() == 2


def test_exchange_rate_posts_home_currency(db_session, qbo):
    client, accounts = qbo
    client.entries[0]["ExchangeRate"] = 1.25
    assert qbo_import.import_journal_entries(db_session)["imported"] == 1
    assert accounts["1"].balance == Decimal("-31.93")
    assert accounts["2"].balance == Decimal("31.93")


def test_journal_list_includes_old_report_journals_and_excludes_purchases(
    db_session, qbo
):
    assert _ledger(db_session)["imported"] == 2
    journal_map = (
        db_session.query(QBOMapping)
        .filter_by(entity_type="ledger", qbo_id="General Journal:227")
        .one()
    )
    txn = db_session.get(Transaction, journal_map.slowbooks_id)
    txn.source_type = "qbo_ledger"
    db_session.flush()
    entries = list_journal_entries(db=db_session)
    assert [entry.id for entry in entries] == [txn.id]
    assert list_journal_entries(source_type="manual", db=db_session) == []


def test_missing_lines_are_reported(db_session, qbo):
    client, accounts = qbo
    client.entries[0]["Line"] = []
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 0
    assert "missing its debit and credit lines" in result["errors"][0]["message"]
    assert db_session.query(Transaction).count() == 0


def test_failure_on_later_api_page_does_not_post_first_page(
    db_session, qbo, monkeypatch
):
    client, accounts = qbo
    client.entries = [_entry(str(index)) for index in range(100)]
    original = client.query

    def fail_later(query):
        if "STARTPOSITION 101 " in query:
            raise RuntimeError("remote query failure")
        return original(query)

    monkeypatch.setattr(client, "query", fail_later)
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 0
    assert "Failed to query QBO" in result["errors"][0]["message"]
    assert db_session.query(Transaction).count() == 0


@pytest.mark.parametrize("change", ["amount", "deleted_local"])
def test_changed_import_is_reported_without_duplicate(db_session, qbo, change):
    client, accounts = qbo
    assert qbo_import.import_journal_entries(db_session)["imported"] == 1
    local_id = db_session.query(Transaction).one().id
    if change == "amount":
        for line in client.entries[0]["Line"]:
            line["Amount"] = 50
    else:
        db_session.delete(db_session.query(Transaction).one())
        db_session.flush()
    count_before = db_session.query(Transaction).count()
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 0
    error = result["errors"][0]
    assert error["code"] == "IMPORT_POSTING_MISMATCH"
    assert "JournalEntry QBO #227 (document ADJ-7)" in error["message"]
    assert f"local transaction #{local_id}" in error["message"].lower()
    if change == "amount":
        assert "account QBO #2" in error["message"]
        assert "25.54" in error["message"] and "50.00" in error["message"]
    else:
        assert "does not exist" in error["message"]
    assert db_session.query(Transaction).count() == count_before


def test_sync_token_change_with_identical_posting_is_not_an_error(db_session, qbo):
    client, _ = qbo
    assert qbo_import.import_journal_entries(db_session)["imported"] == 1
    client.entries[0]["SyncToken"] = "1"
    client.entries[0]["PrivateNote"] = "Metadata edit"
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 0,
        "errors": [],
    }
    assert (
        db_session.query(QBOMapping)
        .filter_by(entity_type="journal_entry")
        .one()
        .qbo_sync_token
        == "1"
    )
    assert db_session.query(Transaction).count() == 1
    assert db_session.query(TransactionLine).count() == 2


def _old_parent_journal(db, accounts):
    parent = Account(
        name="Payroll Expenses", account_type=AccountType.EXPENSE, balance=0
    )
    db.add(parent)
    db.flush()
    db.add(QBOMapping(entity_type="account", qbo_id="parent", slowbooks_id=parent.id))
    accounts["2"].parent_id = parent.id
    txn = accounting.create_journal_entry(
        db,
        date(2026, 8, 3),
        "Old payroll",
        [
            {"account_id": parent.id, "debit": Decimal("25.54"), "credit": Decimal(0)},
            {
                "account_id": accounts["1"].id,
                "debit": Decimal(0),
                "credit": Decimal("25.54"),
            },
        ],
        source_type="qbo_ledger",
    )
    db.add(
        QBOMapping(
            entity_type="ledger",
            qbo_id="General Journal:227",
            slowbooks_id=txn.id,
            qbo_sync_token="old-rollup",
        )
    )
    db.flush()
    return txn, parent


def test_legacy_parent_account_is_repaired_without_replacing_lines(db_session, qbo):
    _, accounts = qbo
    txn, parent = _old_parent_journal(db_session, accounts)
    ids = [line.id for line in txn.lines]
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 0,
        "errors": [],
    }
    assert [line.id for line in txn.lines] == ids
    assert txn.lines[0].account_id == accounts["2"].id
    assert txn.lines[0].debit == Decimal("25.54")
    assert parent.balance == 0
    assert accounts["2"].balance == Decimal("25.54")
    assert db_session.query(Transaction).count() == 1
    assert (
        db_session.query(QBOMapping)
        .filter_by(entity_type="journal_entry")
        .one()
        .slowbooks_id
        == txn.id
    )
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 0,
        "errors": [],
    }


@pytest.mark.parametrize("blocker", ["amount", "closed", "invalid_other"])
def test_parent_repair_does_not_override_financial_blockers(db_session, qbo, blocker):
    client, accounts = qbo
    txn, parent = _old_parent_journal(db_session, accounts)
    if blocker == "amount":
        for line in client.entries[0]["Line"]:
            line["Amount"] = 50
    elif blocker == "closed":
        db_session.add(Settings(key="closing_date", value="2026-08-31"))
    else:
        broken = _entry("bad")
        broken["Line"][0]["Amount"] = 40
        client.entries.append(broken)
    db_session.flush()
    result = qbo_import.import_journal_entries(db_session)
    assert result["errors"]
    assert txn.lines[0].account_id == parent.id
    assert db_session.query(Transaction).count() == 1


@pytest.mark.parametrize("has_posting", [False, True])
def test_empty_journal_stub_requires_ledger_confirmation(
    db_session, qbo, monkeypatch, has_posting
):
    client, _ = qbo
    stub = {
        "Id": "68",
        "TxnDate": "2026-08-03",
        "Line": [
            {
                "Id": "0",
                "DetailType": "JournalEntryLineDetail",
                "JournalEntryLineDetail": {"AccountRef": {"value": "1"}},
            },
            {"Id": "1", "DetailType": "DescriptionOnly"},
        ],
    }
    client.entries.append(stub)
    original = client.get_report

    def ledger(name, qs):
        report = original(name, qs)
        if has_posting:
            report["Rows"]["Row"][0]["Rows"]["Row"][0]["ColData"][1]["id"] = "68"
        return report

    monkeypatch.setattr(client, "get_report", ledger)
    result = qbo_import.import_journal_entries(db_session)
    if has_posting:
        assert result["imported"] == 0
        message = result["errors"][0]["message"]
        assert "JournalEntry QBO #68" in message
        assert "line #0" in message and "account QBO #1" in message
        assert "Amount=0" in message
    else:
        assert result == {"imported": 1, "errors": []}
        assert db_session.query(Transaction).count() == 1


def test_failed_second_posting_rolls_back_journals_and_balances(
    db_session, qbo, monkeypatch
):
    client, accounts = qbo
    client.entries.append(_entry("228"))
    original = accounting.create_journal_entry
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(accounting, "create_journal_entry", fail_second)
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 0
    assert result["errors"]
    assert db_session.query(Transaction).count() == 0
    assert (
        db_session.query(QBOMapping).filter_by(entity_type="journal_entry").count() == 0
    )
    assert accounts["1"].balance == Decimal("1000")


def test_journal_page_shows_import_and_prevents_void(
    client, db_session, qbo, monkeypatch
):
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)
    response = client.post("/api/qbo/import/journal_entries")
    assert response.status_code == 200
    assert response.json() == {"imported": 1, "errors": []}
    entries = client.get("/api/journal").json()
    assert len(entries) == 1
    assert entries[0]["source_type"] == "qbo_journal"
    assert entries[0]["total_debit"] == entries[0]["total_credit"] == 25.54
    assert client.get("/api/journal?source_type=manual").json() == []
    assert client.get(f"/api/journal/{entries[0]['id']}").status_code == 200
    assert client.post(f"/api/journal/{entries[0]['id']}/void").status_code == 400
    assert db_session.query(Transaction).count() == 1


def test_import_all_keeps_journals_when_report_fails(
    client, db_session, qbo, monkeypatch
):
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)
    for name in [
        "accounts",
        "customers",
        "vendors",
        "items",
        "invoices",
        "payments",
        "sales_receipts",
    ]:
        monkeypatch.setattr(
            qbo_import, f"import_{name}", lambda db: {"imported": 0, "errors": []}
        )
    monkeypatch.setattr(
        qbo_ledger_import,
        "import_ledger",
        lambda db: {
            "imported": 0,
            "errors": [{"entity": "ledger", "message": "Report unavailable"}],
        },
    )
    response = client.post("/api/qbo/import")
    assert response.status_code == 200
    assert response.json()["journal_entries"] == 1
    assert response.json()["errors"][0]["entity"] == "ledger"
    assert (
        db_session.query(Transaction).filter_by(source_type="qbo_journal").count() == 1
    )
