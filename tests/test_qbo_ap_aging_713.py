"""The bills still open at a QuickBooks Online migration come across as bills
(#713), the payables mirror of the A/R Aging Detail report (NEW-120, #704).

The Export data zip carries the history: every bill, vendor credit and bill
payment posts as a journal entry on the export's payable account, "Accounts
Payable (A/P)", which is 2000 Accounts Payable here (#704). 2000 then held
the payables balance with no bill behind it, so A/P Aging and Pay Bills
showed nothing (both QA platforms at the 2.31.7 gate). QuickBooks Online's
A/P Aging Detail report, uploaded beside the zip, lists what was open: each
row comes across as a document on 2000 with no posting of its own (the
history put the balance there), so A/P Aging equals 2000 and every vendor's
balance is their aging row.

The books here are invented: three vendors, three open bills (one partly
paid), a vendor credit and a bill payment not applied, 1,025.00 owed in
all. The report is in the layout of a real download (the company, the
title, the period, a blank row, the header from the first column, a
section per aging bucket with its total row, TOTAL, the time it was run).
"""

import csv
import io
import zipfile
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.models.accounts import Account
from app.models.bills import Bill, BillPayment, BillPaymentAllocation
from app.models.contacts import Vendor
from app.models.transactions import Transaction, TransactionLine
from app.models.vendor_credits import VendorCredit
from app.services import qbo_export_import as qbo

HEADER = [
    "Date",
    "Transaction type",
    "Num",
    "Vendor display name",
    "Due date",
    "Past due",
    "Amount",
    "Open balance",
]
FOOTER = " Thursday, November 12, 2026 08:31 PM GMT-05:00"

BALANCE_SHEET = """Fieldstone Renovations,
Balance Sheet,
All Dates,
,
,Total
ASSETS,
   Current Assets,
      Bank Accounts,
         Operating Account,=4625.00
      Total Bank Accounts,=4625.00
   Total Current Assets,=4625.00
TOTAL ASSETS,=4625.00
LIABILITIES AND EQUITY,
   Liabilities,
      Current Liabilities,
         Accounts Payable,
            Accounts Payable (A/P),=1025.00
         Total Accounts Payable,=1025.00
      Total Current Liabilities,=1025.00
   Total Liabilities,=1025.00
   Equity,
      Opening Balance Equity,=5000.00
      Retained Earnings,
      Net Income,=-1400.00
   Total Equity,=3600.00
TOTAL LIABILITIES AND EQUITY,=4625.00
,
,
"Thursday, Nov 12, 2026 08:30:00 PM GMT-5 - Accrual Basis",
"""

PROFIT_AND_LOSS = """Fieldstone Renovations,
Profit and Loss,
All Dates,
,
,Total
Expenses,
   Lumber,=900.00
   Paint Supplies,=500.00
Total Expenses,=1400.00
Net Operating Income,=-1400.00
Net Income,=-1400.00
,
,
"Thursday, Nov 12, 2026 08:30:00 PM GMT-5 - Accrual Basis",
"""

TRIAL_BALANCE = """Fieldstone Renovations,,
Trial Balance,,
All Dates,,
,,
,Debit,Credit
Operating Account,=4625.00,
Accounts Payable (A/P),,=1025.00
Opening Balance Equity,,=5000.00
Lumber,=900.00,
Paint Supplies,=500.00,
TOTAL,=6025.00,=6025.00
,,
,,
"Thursday, Nov 12, 2026 08:30:00 PM GMT-5 - Accrual Basis",,
"""

# (date, type, num, name, [(memo, account, debit, credit), ...])
_BLOCKS = [
    (
        "01/05/2026",
        "Journal Entry",
        "1",
        "",
        [
            ("Opening deposit", "Operating Account", "5000.00", ""),
            ("Opening deposit", "Opening Balance Equity", "", "5000.00"),
        ],
    ),
    (
        "09/01/2026",
        "Bill",
        "B-100",
        "Harbor Paint Supply",
        [
            ("Primer", "Paint Supplies", "400.00", ""),
            ("Primer", "Accounts Payable (A/P)", "", "400.00"),
        ],
    ),
    (
        "09/10/2026",
        "Bill",
        "B-101",
        "Ridge Lumber Co",
        [
            ("Framing lumber", "Lumber", "900.00", ""),
            ("Framing lumber", "Accounts Payable (A/P)", "", "900.00"),
        ],
    ),
    (
        "09/15/2026",
        "Bill Payment (Check)",
        "3001",
        "Ridge Lumber Co",
        [
            ("", "Accounts Payable (A/P)", "300.00", ""),
            ("", "Operating Account", "", "300.00"),
        ],
    ),
    (
        "10/01/2026",
        "Bill",
        "B-102",
        "Quill Trade Goods",
        [
            ("Drop cloths", "Paint Supplies", "150.00", ""),
            ("Drop cloths", "Accounts Payable (A/P)", "", "150.00"),
        ],
    ),
    (
        "10/05/2026",
        "Vendor Credit",
        "VC-7",
        "Harbor Paint Supply",
        [
            ("Returned primer", "Accounts Payable (A/P)", "50.00", ""),
            ("Returned primer", "Paint Supplies", "", "50.00"),
        ],
    ),
    (
        "10/20/2026",
        "Bill Payment (Check)",
        "3002",
        "Ridge Lumber Co",
        [
            ("Deposit on trim", "Accounts Payable (A/P)", "75.00", ""),
            ("Deposit on trim", "Operating Account", "", "75.00"),
        ],
    ),
]


def _csv(rows) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def _journal() -> str:
    rows = [
        ["", "Fieldstone Renovations"],
        ["", "Journal"],
        ["", "All Dates"],
        [""],
        [
            "",
            "Date",
            "Transaction Type",
            "Num",
            "Name",
            "Memo/Description",
            "Account",
            "Debit",
            "Credit",
        ],
    ]
    for day, ttype, num, name, lines in _BLOCKS:
        debit = credit = Decimal("0")
        for i, (memo, account, dr, cr) in enumerate(lines):
            head = [day, ttype, num, name] if i == 0 else ["", "", "", ""]
            rows.append(
                [""]
                + head
                + [memo, account, f"={dr}" if dr else "", f"={cr}" if cr else ""]
            )
            debit += Decimal(dr or "0")
            credit += Decimal(cr or "0")
        rows.append(["", "", "", "", "", "", "", f"={debit}", f"={credit}"])
        rows.append([""])
    return _csv(rows)


def _vendors(label="Vendor") -> str:
    return _csv(
        [
            ["", "Fieldstone Renovations"],
            ["", f"{label} Contact List"],
            [""],
            [""],
            ["", label, "Phone Numbers", "Email", "Full Name", "Address", "Account #"],
            ["", "Harbor Paint Supply", "Phone: (555) 010-4401", "", "", "", ""],
            ["", "Ridge Lumber Co", "", "yard@example.com", "", "", ""],
            ["", "Quill Trade Goods", "", "", "", "", ""],
        ]
    )


AGING_ROWS = [
    ["31 - 60 days past due"],
    [
        "09/01/2026",
        "Bill",
        "B-100",
        "Harbor Paint Supply",
        "10/01/2026",
        "42",
        "400",
        "400",
    ],
    [
        "09/10/2026",
        "Bill",
        "B-101",
        "Ridge Lumber Co",
        "10/10/2026",
        "33",
        "900",
        "600",
    ],
    ["Total for 31 - 60 days past due", "", "", "", "", "", "=G7+G8", "=H7+H8"],
    ["1 - 30 days past due"],
    [
        "10/01/2026",
        "Bill",
        "B-102",
        "Quill Trade Goods",
        "10/31/2026",
        "12",
        "150",
        "150",
    ],
    [
        "10/05/2026",
        "Vendor Credit",
        "VC-7",
        "Harbor Paint Supply",
        "10/05/2026",
        "38",
        "-50",
        "-50",
    ],
    [
        "10/20/2026",
        "Bill Payment (Check)",
        "3002",
        "Ridge Lumber Co",
        "10/20/2026",
        "23",
        "-75",
        "-75",
    ],
    ["Total for 1 - 30 days past due", "", "", "", "", "", "=G11+G12+G13", "=H11"],
    ["TOTAL", "", "", "", "", "", "", "1025"],
]


def _aging(rows=None, header=None, title="A/P Aging Detail Report") -> str:
    return _csv(
        [
            ["Fieldstone Renovations"],
            [title],
            ["All Dates"],
            [""],
            header or HEADER,
        ]
        + (AGING_ROWS if rows is None else rows)
        + [[""], [""], [""], [FOOTER]]
    )


def _workbook(text: str) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    for row in csv.reader(io.StringIO(text)):
        sheet.append([cell if cell != "" else None for cell in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _files(aging=None, vendors=None, aging_name="Fieldstone_A_P_Aging_Detail.xlsx"):
    """The Export data zip and the A/P Aging Detail report beside it."""
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        for name, text in (
            ("Balance_sheet.xlsx", BALANCE_SHEET),
            ("Profit_and_loss.xlsx", PROFIT_AND_LOSS),
            ("Journal.xlsx", _journal()),
            ("Trial_balance.xlsx", TRIAL_BALANCE),
            ("Vendors.xlsx", vendors or _vendors()),
        ):
            archive.writestr(name, _workbook(text))
    files = [
        ("files", ("Export.zip", io.BytesIO(zipped.getvalue()), "application/zip"))
    ]
    if aging is not False:
        report = _workbook(aging if aging is not None else _aging())
        files.append(
            (
                "files",
                (
                    aging_name,
                    io.BytesIO(report),
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet",
                ),
            )
        )
    return files


def _journal_ap(db) -> Decimal:
    """What the Journal leaves on 2000, a credit as a positive figure."""
    ap = db.query(Account).filter(Account.account_number == "2000").one()
    rows = db.query(TransactionLine).filter(TransactionLine.account_id == ap.id).all()
    return sum(
        (Decimal(str(r.credit or 0)) - Decimal(str(r.debit or 0)) for r in rows),
        Decimal("0"),
    )


def _books_check(client) -> dict:
    data = client.get("/api/reports/books-check").json()
    return {r["key"]: r for r in data["rows"]}


# ── the report ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "Fieldstone_A_P_Aging_Detail_Report.xlsx",
        "Vendors_A_P_Aging_Detail_Report.xlsx",
        "Supplier_A_P_Aging_Detail.xlsx",
        "AP Aging.xlsx",
        "Payables aging.xlsx",
    ],
)
def test_the_report_is_told_apart_by_name(name):
    assert qbo.classify_filename(name) == "ap_aging"


def test_the_other_files_keep_their_names():
    assert qbo.classify_filename("Vendors_A_R_Aging_Detail_Report.xlsx") == "ar_aging"
    assert qbo.classify_filename("Members_A_R_Aging_Detail_Report.xlsx") == "ar_aging"
    assert qbo.classify_filename("Vendors.xlsx") == "vendors"
    assert qbo.classify_filename("Suppliers.xlsx") == "vendors"


def test_the_reports_rows_read_as_bills_credits_and_payments():
    items, errors, notes = qbo.parse_ap_aging(_aging())
    assert errors == [] and notes == []
    assert [(i["kind"], i["num"], i["vendor"], i["open"]) for i in items] == [
        ("bill", "B-100", "Harbor Paint Supply", Decimal("400")),
        ("bill", "B-101", "Ridge Lumber Co", Decimal("600")),
        ("bill", "B-102", "Quill Trade Goods", Decimal("150")),
        ("credit", "VC-7", "Harbor Paint Supply", Decimal("-50")),
        ("payment", "3002", "Ridge Lumber Co", Decimal("-75")),
    ]
    assert items[1]["amount"] == Decimal("900")
    assert items[1]["due_date"].isoformat() == "2026-10-10"
    assert items[1]["row"] == 8


def test_an_aging_file_named_only_aging_is_placed_by_its_title():
    placed = qbo._place_agings({"ar_aging": _aging()})
    assert "ap_aging" in placed and "ar_aging" not in placed


# ── the import ─────────────────────────────────────────────────────────────


def _assert_carried(client, db_session):
    bills = db_session.query(Bill).order_by(Bill.date).all()
    assert [(b.bill_number, b.vendor.name, b.balance_due) for b in bills] == [
        ("B-100", "Harbor Paint Supply", Decimal("400.00")),
        ("B-101", "Ridge Lumber Co", Decimal("600.00")),
        ("B-102", "Quill Trade Goods", Decimal("150.00")),
    ]
    assert [b.due_date.isoformat() for b in bills] == [
        "2026-10-01",
        "2026-10-10",
        "2026-10-31",
    ]
    # no posting of their own: the history has the balance on 2000
    assert all(b.transaction_id is None for b in bills)
    ap = db_session.query(Account).filter(Account.account_number == "2000").one()
    assert all([ln.account_id for ln in b.lines] == [ap.id] for b in bills)
    (credit,) = db_session.query(VendorCredit).all()
    assert (credit.credit_number, credit.vendor.name, credit.balance_remaining) == (
        "VC-7",
        "Harbor Paint Supply",
        Decimal("50.00"),
    )
    assert credit.transaction_id is None
    (payment,) = db_session.query(BillPayment).all()
    assert (payment.vendor.name, payment.amount, payment.check_number) == (
        "Ridge Lumber Co",
        Decimal("75.00"),
        "3002",
    )
    assert payment.transaction_id is None
    assert db_session.query(BillPaymentAllocation).count() == 0  # applied to none

    # the A/P aging reads them, and ties to the Journal's 2000
    aging = client.get("/api/reports/ap-aging").json()
    assert Decimal(str(aging["totals"]["total"])) == Decimal("1025.00")
    assert _journal_ap(db_session) == Decimal("1025.00")
    by_vendor = {r["vendor_name"]: Decimal(str(r["total"])) for r in aging["items"]}
    assert by_vendor == {
        "Harbor Paint Supply": Decimal("350.00"),
        "Ridge Lumber Co": Decimal("525.00"),
        "Quill Trade Goods": Decimal("150.00"),
    }
    from app.services.contact_balances import vendor_balances

    names = {v.id: v.name for v in db_session.query(Vendor).all()}
    balances = {names[k]: v for k, v in vendor_balances(db_session).items() if v}
    assert balances == by_vendor

    rows = _books_check(client)
    # the carried documents have no entry by design: the history's is theirs
    for key in ("ap_aging", "vendor_balances", "journal_entries"):
        assert rows[key]["status"] == "pass", rows[key]


def test_a_bundle_with_the_report_carries_its_bills_credits_and_payments(
    client, db_session, seed_accounts
):
    data = client.post("/api/migration/qbo_export/dry-run", files=_files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["open_bills"] == 3
    assert data["vendor_credits"] == 1
    assert data["unapplied_bill_payments"] == 1
    assert data["aging_vendors_matched"] == 3 and data["aging_vendors_created"] == 0
    assert data["open_payables_total"] == 1025.0
    said = " ".join(data["warnings"])
    assert "come to 1,025.00, what the Journal leaves on Accounts Payable (A/P)" in said
    # W-117's "no bills behind it" is what the report replaces
    assert "with no bills behind it" not in said
    assert db_session.query(Bill).count() == 0  # the dry run wrote nothing

    data = client.post("/api/migration/qbo_export/import", files=_files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_open_bills"] == 3
    assert data["imported_vendor_credits"] == 1
    assert data["imported_unapplied_bill_payments"] == 1
    _assert_carried(client, db_session)

    # a second upload carries nothing twice
    again = client.post("/api/migration/qbo_export/dry-run", files=_files()).json()
    assert again["open_bills"] == 0 and again["vendor_credits"] == 0
    assert again["unapplied_bill_payments"] == 0
    assert any("in the books already" in w for w in again["warnings"]), again


def test_a_supplier_labelled_report_imports_the_same(client, db_session, seed_accounts):
    header = [
        "Supplier display name" if h == "Vendor display name" else h for h in HEADER
    ]
    files = _files(
        aging=_aging(header=header),
        vendors=_vendors("Supplier"),
        aging_name="Fieldstone_Supplier_A_P_Aging.xlsx",
    )
    data = client.post("/api/migration/qbo_export/import", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_vendors"] == 3
    assert data["imported_open_bills"] == 3
    assert data["aging_vendors_matched"] == 3 and data["aging_vendors_created"] == 0
    _assert_carried(client, db_session)


def test_the_empty_report_imports_no_bills_and_no_error(
    client, db_session, seed_accounts
):
    # the real download of a company with nothing open: the header, no rows
    files = _files(aging=_aging(rows=[]))
    data = client.post("/api/migration/qbo_export/import", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert data["errors"] == []
    assert data["imported_open_bills"] == 0
    assert data["imported_vendor_credits"] == 0
    assert data["imported_unapplied_bill_payments"] == 0
    assert db_session.query(Bill).count() == 0
    assert any("lists no open bills" in w for w in data["warnings"]), data


def test_a_vendor_the_list_lacks_is_created(client, db_session, seed_accounts):
    vendors = _vendors().replace("Quill Trade Goods", "Some Other Vendor")
    data = client.post(
        "/api/migration/qbo_export/import", files=_files(vendors=vendors)
    ).json()
    assert data["ok"] is True, data["errors"]
    assert data["aging_vendors_matched"] == 2 and data["aging_vendors_created"] == 1
    assert any(
        w.startswith("Quill Trade Goods is on the A/P Aging Detail report but not")
        for w in data["warnings"]
    )
    assert db_session.query(Vendor).filter(Vendor.name == "Quill Trade Goods").count()
    _assert_carried(client, db_session)


def test_a_bad_amount_is_refused_by_its_row(client, db_session, seed_accounts):
    rows = [list(r) for r in AGING_ROWS]
    rows[2][7] = "six hundred"  # B-101's open balance, row 8
    files = _files(aging=_aging(rows=rows))
    data = client.post("/api/migration/qbo_export/dry-run", files=files).json()
    assert data["ok"] is False
    assert any(e.startswith("A/P Aging Detail row 8:") for e in data["errors"]), data
    data = client.post("/api/migration/qbo_export/import", files=files).json()
    assert data["ok"] is False
    assert data["imported_open_bills"] == 0 and data["imported_journals"] == 0
    assert db_session.query(Bill).count() == 0
    assert db_session.query(Transaction).count() == 0


def test_an_unknown_transaction_type_is_listed_by_its_row(
    client, db_session, seed_accounts
):
    rows = [list(r) for r in AGING_ROWS]
    rows.insert(
        -1,
        [
            "10/25/2026",
            "Journal Entry",
            "JE-9",
            "Quill Trade Goods",
            "10/25/2026",
            "",
            "20",
            "20",
        ],
    )
    rows[-1][7] = "1045"
    data = client.post(
        "/api/migration/qbo_export/dry-run", files=_files(aging=_aging(rows=rows))
    ).json()
    assert data["ok"] is True, data["errors"]
    said = [w for w in data["warnings"] if w.startswith("A/P Aging Detail row 15:")]
    assert len(said) == 1, data["warnings"]
    assert "Journal Entry JE-9" in said[0] and "is not carried over" in said[0]
    assert data["open_bills"] == 3


# ── the dry run on a company file on disk (W-148) ─────────────────────────


@pytest.fixture
def file_app(tmp_path, monkeypatch):
    """The app served from a company file on disk, as installed."""
    import app.database as db_module
    from app.database import enable_sqlite_tuning, get_db
    from app.main import app
    from app.services import company_file
    from app.services.audit import register_audit_hooks
    from app.services.company_service import _init_company_db

    path = tmp_path / "company.db"
    url = "sqlite:///" + path.as_posix()
    _init_company_db(url, company_name="File Books")
    engine = company_file.create_sqlite_engine(url, None, pool_pre_ping=True)
    enable_sqlite_tuning(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    register_audit_hooks(factory)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)

    def file_get_db(request=None):
        session = factory()
        http_session = getattr(request, "session", None) if request else None
        if isinstance(http_session, dict) and http_session.get("authenticated"):
            session.info["acting_username"] = http_session.get("username") or "operator"
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = file_get_db
    try:
        c = TestClient(app)
        r = c.post("/api/auth/setup", json={"password": "test-password-123"})
        assert r.status_code == 200, r.text
        yield c, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _on_disk(engine):
    with Session(engine) as s:
        return tuple(
            s.query(func.count(model.id)).scalar()
            for model in (
                Account,
                Bill,
                VendorCredit,
                BillPayment,
                Vendor,
                Transaction,
                TransactionLine,
            )
        )


def test_a_dry_run_on_a_company_file_writes_nothing(file_app):
    client, engine = file_app
    before = _on_disk(engine)
    for _ in range(2):  # twice: the second must find nothing from the first
        data = client.post("/api/migration/qbo_export/dry-run", files=_files()).json()
        assert data["ok"] is True, data["errors"]
        assert data["open_bills"] == 3 and data["unapplied_bill_payments"] == 1
    assert _on_disk(engine) == before
    assert before[1:5] == (0, 0, 0, 0)
