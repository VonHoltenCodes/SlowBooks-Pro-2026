"""QBO General Ledger postings become complete, idempotent local journals."""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction, TransactionLine
from app.services import qbo_ledger_import

COLUMNS = [
    "Date",
    "Transaction Type",
    "Num",
    "Name",
    "Memo/Description",
    "Split",
    "Amount",
    "Balance",
]


def _posting(txn_id, kind, amount, *, day="2026-08-03"):
    values = [day, kind, txn_id, "Payee", "Memo", "", str(amount), "0"]
    return {
        "type": "Data",
        "ColData": [
            {"value": value, **({"id": txn_id} if index == 1 else {})}
            for index, value in enumerate(values)
        ],
    }


def _report(postings, first="2026-08-03", last="2026-08-03"):
    return {
        "Header": {"StartPeriod": first, "EndPeriod": last, "ReportBasis": "Accrual"},
        "Columns": {"Column": [{"ColTitle": title} for title in COLUMNS]},
        "Rows": {
            "Row": [
                {
                    "type": "Section",
                    "Header": {"ColData": [{"id": account_id, "value": account_id}]},
                    "Rows": {"Row": rows},
                }
                for account_id, rows in postings.items()
            ]
        },
    }


def _setup(db_session, monkeypatch, postings):
    for qbo_id, name, kind in [
        ("1", "Checking", AccountType.ASSET),
        ("2", "Expenses", AccountType.EXPENSE),
        ("3", "Income", AccountType.INCOME),
    ]:
        account = Account(name=name, account_type=kind, balance=0)
        db_session.add(account)
        db_session.flush()
        db_session.add(
            QBOMapping(entity_type="account", qbo_id=qbo_id, slowbooks_id=account.id)
        )
    db_session.flush()

    class Client:
        def get_report(self, name, qs):
            assert name == "GeneralLedger"
            assert qs["accounting_method"] == "Accrual"
            return _report(postings, qs["start_date"], qs["end_date"])

    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: Client())


def _import(db_session):
    return qbo_ledger_import.import_ledger(
        db_session, start=date(2026, 8, 3), end=date(2026, 8, 3)
    )


def test_imports_purchase_and_deposit_once(db_session, monkeypatch):
    _setup(
        db_session,
        monkeypatch,
        {
            "1": [_posting("10", "Purchase", "-50"), _posting("11", "Deposit", "100")],
            "2": [_posting("10", "Purchase", "50")],
            "3": [_posting("11", "Deposit", "100")],
        },
    )
    db_session.query(Account).filter_by(name="Checking").one().balance = Decimal("1000")
    assert _import(db_session) == {"imported": 2, "errors": []}
    assert db_session.query(Transaction).count() == 2
    assert db_session.query(TransactionLine).count() == 4
    checking = db_session.query(Account).filter_by(name="Checking").one()
    assert checking.balance == Decimal("50")
    assert {txn.source_type for txn in db_session.query(Transaction)} == {"qbo_ledger"}
    assert _import(db_session) == {"imported": 0, "errors": []}
    assert db_session.query(Transaction).count() == 2
    assert db_session.query(QBOMapping).filter_by(entity_type="ledger").count() == 2


def test_missing_account_blocks_all_postings(db_session, monkeypatch):
    _setup(
        db_session,
        monkeypatch,
        {
            "1": [_posting("10", "Purchase", "-50")],
            "99": [_posting("10", "Purchase", "50")],
        },
    )
    result = _import(db_session)
    assert result["imported"] == 0
    assert "99" in result["errors"][0]["message"]
    assert db_session.query(Transaction).count() == 0


def test_unbalanced_posting_blocks_all_postings(db_session, monkeypatch):
    _setup(
        db_session,
        monkeypatch,
        {
            "1": [_posting("10", "Purchase", "-50")],
            "2": [_posting("10", "Purchase", "40")],
        },
    )
    result = _import(db_session)
    assert result["imported"] == 0
    assert any("balance" in error["message"] for error in result["errors"])
    assert db_session.query(Transaction).count() == 0


def test_changed_posting_is_reported_without_duplicate(db_session, monkeypatch):
    postings = {
        "1": [_posting("10", "Purchase", "-50")],
        "2": [_posting("10", "Purchase", "50")],
    }
    _setup(db_session, monkeypatch, postings)
    assert _import(db_session)["imported"] == 1
    postings["1"][0]["ColData"][6]["value"] = "-60"
    postings["2"][0]["ColData"][6]["value"] = "60"
    result = _import(db_session)
    assert result["imported"] == 0
    assert "local debit-minus-credit 50.00, QBO 60.00" in result["errors"][0]["message"]
    assert result["errors"][0]["qbo_id"] == "Purchase:10"
    assert db_session.query(Transaction).count() == 1


def test_failed_posting_rolls_back_entire_batch(db_session, monkeypatch):
    _setup(
        db_session,
        monkeypatch,
        {
            "1": [_posting("10", "Purchase", "-50"), _posting("11", "Deposit", "100")],
            "2": [_posting("10", "Purchase", "50")],
            "3": [_posting("11", "Deposit", "100")],
        },
    )
    original = qbo_ledger_import.create_journal_entry
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated database failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(qbo_ledger_import, "create_journal_entry", fail_second)
    result = _import(db_session)
    assert result["imported"] == 0
    assert result["errors"]
    assert db_session.query(Transaction).count() == 0
    assert db_session.query(QBOMapping).filter_by(entity_type="ledger").count() == 0


def test_nested_account_section_and_period_split(monkeypatch):
    nested = _report({"1": []})
    nested["Rows"]["Row"][0]["Rows"] = {
        "Row": [
            {
                "type": "Section",
                "Header": {"ColData": [{"value": "Grouping without an account ID"}]},
                "Rows": {"Row": [_posting("10", "Purchase", "-50")]},
            }
        ]
    }
    assert list(qbo_ledger_import._rows(nested))[0][0] == "1"
    nested["Rows"]["Row"][0]["Rows"]["Row"][0]["Header"]["ColData"][0]["id"] = "child-2"
    assert list(qbo_ledger_import._rows(nested))[0][0] == "child-2"
    assert list(qbo_ledger_import._periods(date(1999, 12, 31), date(2001, 1, 1))) == [
        (date(1999, 12, 31), date(1999, 12, 31)),
        (date(2000, 1, 1), date(2000, 12, 31)),
        (date(2001, 1, 1), date(2001, 1, 1)),
    ]


def _nested_payroll(db, monkeypatch):
    _setup(db, monkeypatch, {})
    parent = db.query(Account).filter_by(name="Expenses").one()
    wages = Account(
        name="Wages", account_type=AccountType.EXPENSE, parent_id=parent.id, balance=0
    )
    db.add(wages)
    db.flush()
    db.add(QBOMapping(entity_type="account", qbo_id="115", slowbooks_id=wages.id))
    db.flush()
    report = _report({"1": [_posting("11063", "Journal Entry", "-50")], "2": []})
    report["Rows"]["Row"][1]["Rows"]["Row"] = [
        {
            "type": "Section",
            "Header": {"ColData": [{"id": "115", "value": "Wages"}]},
            "Rows": {"Row": [_posting("11063", "Journal Entry", "50")]},
        }
    ]

    class Client:
        def get_report(self, name, qs):
            return report

    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: Client())
    return parent, wages


def test_nested_ledger_posts_to_child_account(db_session, monkeypatch):
    parent, wages = _nested_payroll(db_session, monkeypatch)
    assert _import(db_session) == {"imported": 1, "errors": []}
    assert parent.balance == 0
    assert wages.balance == Decimal("50")
    assert _import(db_session) == {"imported": 0, "errors": []}


def test_ledger_repair_preserves_line_ids_and_dry_run_does_not_mutate(
    db_session, monkeypatch
):
    parent, wages = _nested_payroll(db_session, monkeypatch)
    assert _import(db_session)["imported"] == 1
    txn = db_session.query(Transaction).one()
    debit = next(line for line in txn.lines if line.debit)
    debit.account_id = parent.id
    mapping = db_session.query(QBOMapping).filter_by(entity_type="ledger").one()
    mapping.qbo_sync_token = "old-rollup"
    db_session.flush()
    line_ids = [line.id for line in txn.lines]
    assert qbo_ledger_import.import_ledger(
        db_session, start=date(2026, 8, 3), end=date(2026, 8, 3), dry_run=True
    ) == {"imported": 0, "ready": 1, "errors": []}
    assert debit.account_id == parent.id
    assert mapping.qbo_sync_token == "old-rollup"
    assert _import(db_session) == {"imported": 0, "errors": []}
    assert [line.id for line in txn.lines] == line_ids
    assert debit.account_id == wages.id
    assert debit.debit == Decimal("50")
    assert parent.balance == 0 and wages.balance == Decimal("50")
    assert db_session.query(Transaction).count() == 1


def test_missing_accounts_keep_every_source_item_id(db_session, monkeypatch):
    postings = {"99": [_posting(str(i), "Purchase", "50") for i in range(61)]}
    _setup(db_session, monkeypatch, postings)
    result = _import(db_session)
    assert len(result["errors"]) == 61
    assert {row["qbo_id"] for row in result["errors"]} == {
        f"Purchase:{i}" for i in range(61)
    }
    assert all("99" in row["message"] for row in result["errors"])
    assert db_session.query(Transaction).count() == 0
