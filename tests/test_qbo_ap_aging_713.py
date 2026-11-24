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

QuickBooks Online writes the report in two shapes, both seen in real
downloads. A company with bills open (exported 2026-11-22, five bills):
the company, the title and the period, a blank row, then the header with
an EMPTY first column (Date, Transaction type, Num, Vendor display name,
Due date, Past due, Amount, Open balance from column B); a section per
aging bucket, its heading in column A ("31 - 60 days past due", "1 - 30
days past due", "CURRENT"), each row's Date in column B, Num an empty
string, the dates as text, Past due negative for a bill not yet due, and
the section closed by a "Total for <bucket>" row whose Amount and Open
balance are formulas (=H10+H11+H12); a TOTAL row of formulas; three blank
rows and the time it was run. REAL_LAYOUT below is that file cell for
cell with invented vendors and amounts. A company with nothing open
(exported 2026-11-13) writes the header from the FIRST column and no
rows.

A later download of the same company (2026-11-22, All Dates) added a
partly paid bill and an unapplied vendor credit: the bill's Amount above
its Open balance (205.00, 105.00 open), the credit's Amount and Open
balance both negative, its Due date and Past due empty strings, Num empty
on every row. REAL_LAYOUT_PAID_AND_CREDIT below is that layout with
invented vendors and amounts. No real download has shown an unapplied
bill payment yet: the one in AGING_ROWS and that layout is invented.

The books here are invented: three vendors on the list, three open bills
(one partly paid), a vendor credit and a bill payment not applied,
1,025.00 owed in all.
"""

import csv
import io
import secrets
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


# The header-from-the-first-column shape with rows of every type the import
# carries. Only the bills are of a kind a real download has shown; the
# partly paid bill (B-101), the vendor credit and the bill payment are
# invented to exercise what the import does with them.
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
        # as QuickBooks Online names the download, and a second copy of it
        "Fieldstone Renovations_A_P Aging Detail Report.xlsx",
        "Fieldstone Renovations_A_P Aging Detail Report(1).xlsx",
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


# ── the report as a real download writes it ───────────────────────────────

# Row by row, as the 2026-11-22 download (five bills, nothing else): the
# header from column B, the buckets' headings and totals in column A, the
# totals as formulas with no figure of their own, Num an empty string, the
# dates as text, Past due below zero for the bill not yet due. The vendors
# and amounts are invented; three vendors are on the Vendor list, two are
# not. The bills come to 1,025.00, what the Journal leaves on A/P.
REAL_LAYOUT = [
    ["Fieldstone Renovations"],
    ["A/P Aging Detail Report"],
    ["All Dates"],
    [],
    [None] + HEADER,
    ["31 - 60 days past due"],
    [None, "09/14/2026", "Bill", "", "Harbor Paint Supply", "10/14/2026"]
    + [39, 112.4, 112.4],
    ["Total for 31 - 60 days past due"] + [None] * 6 + ["=H7", "=I7"],
    ["1 - 30 days past due"],
    [None, "10/22/2026", "Bill", "", "Ridge Lumber Co", "10/28/2026"]
    + [25, 318.0, 318.0],
    [None, "10/28/2026", "Bill", "", "Copperline Electric", "10/28/2026"]
    + [25, 207.6, 207.6],
    [None, "10/21/2026", "Bill", "", "Quill Trade Goods", "10/31/2026"]
    + [22, 237.0, 237.0],
    ["Total for 1 - 30 days past due"] + [None] * 6 + ["=H10+H11+H12", "=I10+I11+I12"],
    ["CURRENT"],
    [None, "10/26/2026", "Bill", "", "Saltmarsh Waste Hauling", "11/25/2026"]
    + [-3, 150.0, 150.0],
    ["Total for CURRENT"] + [None] * 6 + ["=H15", "=I15"],
    ["TOTAL"] + [None] * 6 + ["=H8+H13+H16", "=I8+I13+I16"],
    [],
    [],
    [],
    [" Sunday, November 22, 2026 03:41 PM GMTZ"],
]


def _real_workbook(rows=None) -> bytes:
    """REAL_LAYOUT as an .xlsx: the formulas as formulas (openpyxl stores
    no computed value for them), the merged title and footer cells."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    for row in REAL_LAYOUT if rows is None else rows:
        sheet.append(row)
    for n in (1, 2, 3, len(REAL_LAYOUT if rows is None else rows)):
        sheet.merge_cells(f"A{n}:H{n}")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _as_uploaded(content: bytes) -> str:
    """The workbook as the upload reads it."""
    from app.routes.migration import _sheet_as_csv

    return _sheet_as_csv(content)


def _real_files(rows=None):
    files = _files(aging=False)
    files.append(
        (
            "files",
            (
                "Fieldstone Renovations_A_P Aging Detail Report(1).xlsx",
                io.BytesIO(_real_workbook(rows)),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        )
    )
    return files


def test_the_real_layout_reads_as_five_bills():
    items, errors, notes = qbo.parse_ap_aging(_as_uploaded(_real_workbook()))
    assert errors == [] and notes == []
    assert [
        (i["kind"], i["row"], i["date"].isoformat(), i["num"], i["vendor"], i["open"])
        for i in items
    ] == [
        ("bill", 7, "2026-09-14", "", "Harbor Paint Supply", Decimal("112.40")),
        ("bill", 10, "2026-10-22", "", "Ridge Lumber Co", Decimal("318")),
        ("bill", 11, "2026-10-28", "", "Copperline Electric", Decimal("207.60")),
        ("bill", 12, "2026-10-21", "", "Quill Trade Goods", Decimal("237")),
        ("bill", 15, "2026-10-26", "", "Saltmarsh Waste Hauling", Decimal("150")),
    ]
    assert [i["due_date"].isoformat() for i in items] == [
        "2026-10-14",
        "2026-10-28",
        "2026-10-28",
        "2026-10-31",
        "2026-11-25",
    ]
    assert all(i["amount"] == i["open"] for i in items)
    assert sum((i["open"] for i in items), Decimal("0")) == Decimal("1025.00")


@pytest.mark.parametrize(
    "header",
    [HEADER, [""] + HEADER],
    ids=["from the first column", "after an empty first column"],
)
def test_a_header_with_no_rows_is_no_open_bills(header):
    # the 2026-11-13 download of a company with nothing open: the header
    # from the first column; the other shape, header only, is read the same
    text = _csv(
        [["Fieldstone Renovations"], ["A/P Aging Detail Report"], ["All Dates"]]
        + [[""], header, [""], [""], [""], [FOOTER]]
    )
    assert qbo.parse_ap_aging(text) == ([], [], [])


def test_the_totals_as_formulas_are_worked_out_and_checked():
    # TOTAL is a formula of the bucket totals, each a formula of its rows'
    # cells, with no figure of its own in the upload: it is worked out from
    # the sheet and checked against the rows read; a bucket's own total is
    # not checked
    rows = [list(r) for r in REAL_LAYOUT]
    rows[12][8] = "=I10"
    rows[16][8] = "=I8+I15"  # leaves the 1 - 30 days bucket out
    items, errors, _ = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [
        "The A/P Aging Detail report's TOTAL (262.40) is not the sum of the "
        "rows read (1,025.00): export it again and upload it as it downloads"
    ]
    rows[16][8] = "=I7+I10+I11+I12+I15"
    items, errors, notes = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [] and notes == [] and len(items) == 5


def test_a_row_the_reader_skips_is_caught_by_the_total():
    # a row with an open balance but no date: refused by its row, and the
    # formula TOTAL would not have matched the rows read either
    rows = [list(r) for r in REAL_LAYOUT]
    rows[6][1] = None
    items, errors, _ = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [
        "A/P Aging Detail row 7 has an amount but no date: export the report "
        "again with its own columns"
    ]
    rows = [list(r) for r in REAL_LAYOUT]
    rows[6][2] = None
    _, errors, _ = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [
        "A/P Aging Detail row 7 has an amount but no transaction type: export "
        "the report again with its own columns"
    ]


def test_a_total_that_cannot_be_worked_out_is_said():
    rows = [list(r) for r in REAL_LAYOUT]
    rows[16][8] = "=SUM(I7:I15)"
    items, errors, notes = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [] and len(items) == 5
    assert notes == [
        "The A/P Aging Detail report's TOTAL could not be worked out, so the "
        "rows read were not checked against it."
    ]


def test_a_total_given_as_a_figure_is_checked_against_the_rows():
    # a workbook saved again with figures where the formulas were: TOTAL is
    # checked against the rows, as on the A/R side; a bucket's total is not
    rows = [list(r) for r in REAL_LAYOUT]
    rows[12][8] = 1.0  # Total for 1 - 30 days past due, wrong
    rows[16][8] = 1025.0
    items, errors, _ = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [] and len(items) == 5
    rows[16][8] = 999.0
    _, errors, _ = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [
        "The A/P Aging Detail report's TOTAL (999.00) is not the sum of the "
        "rows read (1,025.00): export it again and upload it as it downloads"
    ]


def test_the_real_layout_imports_its_bills_onto_2000(client, db_session, seed_accounts):
    data = client.post("/api/migration/qbo_export/dry-run", files=_real_files()).json()
    assert data["ok"] is True, data["errors"]
    assert (data["open_bills"], data["vendor_credits"]) == (5, 0)
    assert data["unapplied_bill_payments"] == 0
    assert data["aging_vendors_matched"] == 3 and data["aging_vendors_created"] == 2
    assert data["open_payables_total"] == 1025.0
    said = " ".join(data["warnings"])
    assert "come to 1,025.00, what the Journal leaves on Accounts Payable (A/P)" in said
    for name in ("Copperline Electric", "Saltmarsh Waste Hauling"):
        assert f"{name} is on the A/P Aging Detail report but not on" in said

    data = client.post("/api/migration/qbo_export/import", files=_real_files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_open_bills"] == 5
    bills = db_session.query(Bill).order_by(Bill.date, Bill.id).all()
    assert [(b.vendor.name, b.total, b.balance_due) for b in bills] == [
        ("Harbor Paint Supply", Decimal("112.40"), Decimal("112.40")),
        ("Quill Trade Goods", Decimal("237.00"), Decimal("237.00")),
        ("Ridge Lumber Co", Decimal("318.00"), Decimal("318.00")),
        ("Saltmarsh Waste Hauling", Decimal("150.00"), Decimal("150.00")),
        ("Copperline Electric", Decimal("207.60"), Decimal("207.60")),
    ]
    # the report gives no number: each takes the product's own default
    assert all(b.bill_number for b in bills)
    assert len({b.bill_number for b in bills}) == 5
    assert all(b.transaction_id is None for b in bills)
    assert db_session.query(VendorCredit).count() == 0
    assert db_session.query(BillPayment).count() == 0
    aging = client.get("/api/reports/ap-aging").json()
    assert Decimal(str(aging["totals"]["total"])) == Decimal("1025.00")
    assert _journal_ap(db_session) == Decimal("1025.00")
    rows = _books_check(client)
    for key in ("ap_aging", "vendor_balances", "journal_entries"):
        assert rows[key]["status"] == "pass", rows[key]


def test_a_report_above_what_the_journal_leaves_says_so(
    client, db_session, seed_accounts
):
    rows = [list(r) for r in REAL_LAYOUT]
    rows[14][7] = rows[14][8] = 250.0  # 100.00 more than the Journal's A/P
    data = client.post(
        "/api/migration/qbo_export/dry-run", files=_real_files(rows)
    ).json()
    assert data["ok"] is True, data["errors"]
    said = [w for w in data["warnings"] if "more than the 1,025.00" in w]
    assert len(said) == 1, data["warnings"]
    assert "come to 1,125.00, 100.00 more than the 1,025.00" in said[0]


def test_a_report_with_no_payable_in_the_export_is_refused(
    client, db_session, seed_accounts
):
    # the report beside an export whose Balance Sheet has no Accounts
    # Payable (another company's, say): refused, nothing written
    files = _real_files()
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        for name, text in (
            ("Balance_sheet.xlsx", BALANCE_SHEET.replace("Accounts Payable", "Loans")),
            ("Profit_and_loss.xlsx", PROFIT_AND_LOSS),
            ("Journal.xlsx", _journal().replace("Accounts Payable", "Loans")),
            ("Trial_balance.xlsx", TRIAL_BALANCE.replace("Accounts Payable", "Loans")),
            ("Vendors.xlsx", _vendors()),
        ):
            archive.writestr(name, _workbook(text))
    files[0] = (
        "files",
        ("Export.zip", io.BytesIO(zipped.getvalue()), "application/zip"),
    )
    data = client.post("/api/migration/qbo_export/import", files=files).json()
    assert data["ok"] is False
    assert any(
        e.startswith("The A/P Aging Detail report lists 5 open items (1,025.00)")
        and "has no Accounts Payable account" in e
        for e in data["errors"]
    ), data["errors"]
    assert data["imported_journals"] == 0 and db_session.query(Bill).count() == 0


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
    # the partly paid bill: at its full amount, the paid part a bill payment
    # applied to it, dated and numbered as the Journal's payment (#711's mirror)
    partly = bills[1]
    assert (partly.total, partly.amount_paid, partly.balance_due) == (
        Decimal("900.00"),
        Decimal("300.00"),
        Decimal("600.00"),
    )
    assert partly.status.value == "partial"
    assert [ln.amount for ln in partly.lines] == [Decimal("900.00")]
    payments = db_session.query(BillPayment).order_by(BillPayment.date).all()
    assert [
        (p.vendor.name, p.date.isoformat(), p.amount, p.check_number) for p in payments
    ] == [
        ("Ridge Lumber Co", "2026-09-15", Decimal("300.00"), "3001"),
        ("Ridge Lumber Co", "2026-10-20", Decimal("75.00"), "3002"),
    ]
    assert all(p.transaction_id is None for p in payments)
    (alloc,) = db_session.query(BillPaymentAllocation).all()
    assert (alloc.bill_payment_id, alloc.bill_id, alloc.amount) == (
        payments[0].id,
        partly.id,
        Decimal("300.00"),
    )  # the unapplied one is applied to none

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
        # a throwaway sign-in made up per run, never one written down
        r = c.post("/api/auth/setup", json={"password": secrets.token_urlsafe(16)})
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


# ── voids and edits of the carried documents (#704's mirror) ──────────────

# What each void adds to (or takes off) the A/P aging: a bill's open balance
# leaves it; a credit's or a payment's amount comes back onto it.
VOIDS = {
    "bill": ("/api/bills/{}/void", Decimal("-400.00")),
    "vendor_credit": ("/api/vendor-credits/{}/void", Decimal("50.00")),
    "unapplied_payment": ("/api/bill-payments/{}/void", Decimal("75.00")),
    "paid_part": ("/api/bill-payments/{}/void", Decimal("300.00")),
}


def _carried_id(db_session, kind):
    if kind == "bill":
        return db_session.query(Bill).filter(Bill.bill_number == "B-100").one().id
    if kind == "vendor_credit":
        return db_session.query(VendorCredit).one().id
    number = "3002" if kind == "unapplied_payment" else "3001"
    return (
        db_session.query(BillPayment).filter(BillPayment.check_number == number).one()
    ).id


def _ties(client, db_session, total):
    aging = client.get("/api/reports/ap-aging").json()
    assert Decimal(str(aging["totals"]["total"])) == total
    db_session.expire_all()
    assert _journal_ap(db_session) == total
    rows = _books_check(client)
    for key in ("ap_aging", "vendor_balances", "journal_entries", "journal_voids"):
        assert rows[key]["status"] == "pass", rows[key]


@pytest.mark.parametrize("kind", list(VOIDS))
def test_a_void_of_a_carried_document_keeps_the_aging_on_2000(
    client, db_session, seed_accounts, kind
):
    data = client.post("/api/migration/qbo_export/import", files=_files()).json()
    assert data["ok"] is True, data["errors"]
    path, change = VOIDS[kind]
    r = client.post(path.format(_carried_id(db_session, kind)))
    assert r.status_code == 200, r.text
    _ties(client, db_session, Decimal("1025.00") + change)
    # the amount moved to QuickBooks' own payable account, where it sat
    moved = (
        db_session.query(Account)
        .filter(Account.name == "Accounts Payable (A/P) (QuickBooks Online)")
        .one_or_none()
        or db_session.query(Account)
        .filter(Account.name == "Accounts Payable (A/P)")
        .one()
    )
    assert moved.account_number != "2000"


def test_voiding_every_carried_document_leaves_2000_at_zero(
    client, db_session, seed_accounts
):
    client.post("/api/migration/qbo_export/import", files=_files())
    # a paid bill is voided after its payment, as on any bill
    for kind in ("paid_part", "unapplied_payment", "vendor_credit", "bill"):
        path, _ = VOIDS[kind]
        r = client.post(path.format(_carried_id(db_session, kind)))
        assert r.status_code == 200, r.text
    for number in ("B-101", "B-102"):
        bill = db_session.query(Bill).filter(Bill.bill_number == number).one()
        r = client.post(f"/api/bills/{bill.id}/void")
        assert r.status_code == 200, r.text
    _ties(client, db_session, Decimal("0.00"))


def test_a_carried_bills_lines_stay_as_they_came(client, db_session, seed_accounts):
    client.post("/api/migration/qbo_export/import", files=_files())
    bill = db_session.query(Bill).filter(Bill.bill_number == "B-100").one()
    line = bill.lines[0]
    same = {
        "id": line.id,
        "account_id": line.account_id,
        "description": line.description,
        "quantity": 1,
        "rate": 400,
    }
    r = client.put(f"/api/bills/{bill.id}", json={"lines": [{**same, "rate": 450}]})
    assert r.status_code == 409, r.text
    assert "carried over from QuickBooks Online" in r.json()["detail"]
    # the same lines and a new due date and memo: saved, nothing posted
    r = client.put(
        f"/api/bills/{bill.id}",
        json={"lines": [same], "due_date": "2026-12-01", "notes": "Call first"},
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    bill = db_session.get(Bill, bill.id)
    assert bill.due_date.isoformat() == "2026-12-01"
    assert bill.transaction_id is None and bill.balance_due == Decimal("400.00")
    _ties(client, db_session, Decimal("1025.00"))
    # still carried, notes edited or not: a void still moves it off 2000
    r = client.post(f"/api/bills/{bill.id}/void")
    assert r.status_code == 200, r.text
    _ties(client, db_session, Decimal("625.00"))


# ── rows that do not read right are refused by their row ─────────────────


def _real_row_changed(**cells):
    """REAL_LAYOUT with the first bill's cells (row 7) changed, by column
    index: 1 Date, 2 Transaction type, 3 Num, 4 Vendor, 5 Due date,
    7 Amount, 8 Open balance."""
    rows = [list(r) for r in REAL_LAYOUT]
    for i, value in cells.items():
        rows[6][int(i[1:])] = value
    return qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))


@pytest.mark.parametrize(
    "cells,error",
    [
        (
            {"c1": "14/09/2026", "c5": "14/10/2026"},
            "A/P Aging Detail row 7: the date 14/09/2026 reads day first. SlowBooks "
            "Pro reads this report's dates month first (MM/DD/YYYY), as QuickBooks "
            "Online writes them for a US company: set the company's date format to "
            "MM/DD/YYYY and export the report again",
        ),
        (
            {"c7": 12.4},
            "A/P Aging Detail row 7: the Bill of 09/14/2026 for Harbor Paint Supply "
            "is open for 112.40, which does not fit within its amount of 12.40. "
            "Check the row in QuickBooks Online and export the report again",
        ),
        (
            {"c7": -112.4},
            "A/P Aging Detail row 7: the Bill of 09/14/2026 for Harbor Paint Supply "
            "is open for 112.40, which does not fit within its amount of -112.40. "
            "Check the row in QuickBooks Online and export the report again",
        ),
        (
            {"c7": None},
            "A/P Aging Detail row 7: the Bill of 09/14/2026 for Harbor Paint Supply "
            "has no Amount: export the report again",
        ),
        (
            {"c8": None},
            "A/P Aging Detail row 7: the Bill of 09/14/2026 for Harbor Paint Supply "
            "has no Open balance: export the report again",
        ),
        (
            {"c4": None},
            "A/P Aging Detail row 7: the Bill of 09/14/2026 names no vendor",
        ),
    ],
    ids=[
        "day first",
        "open above amount",
        "amount the other sign",
        "no amount",
        "no open balance",
        "no vendor",
    ],
)
def test_a_row_that_does_not_read_right_is_refused_by_its_row(cells, error):
    items, errors, _ = _real_row_changed(**cells)
    assert errors == [error]


def test_a_row_with_nothing_open_is_said_and_not_carried():
    items, errors, notes = _real_row_changed(c8=0)
    assert errors == [] and len(items) == 4
    assert notes == [
        "A/P Aging Detail row 7: the Bill of 09/14/2026 for Harbor Paint Supply "
        "has nothing open, so there is nothing to carry over for it."
    ]


def test_a_long_number_is_said_when_it_is_cut():
    items, errors, notes = _real_row_changed(c3="N" * 60)
    assert errors == [] and items[0]["num"] == "N" * 50
    assert notes == [
        f"A/P Aging Detail row 7: the number {'N' * 60} is longer than 50 "
        f"characters and is carried over as {'N' * 50}."
    ]


def test_a_report_without_due_dates_says_so():
    rows = [list(r) for r in REAL_LAYOUT]
    rows[4] = [None] + ["Due" if h == "Due date" else h for h in HEADER]
    items, errors, notes = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [] and len(items) == 5
    assert all(i["due_date"] == i["date"] for i in items)
    assert notes == [
        "The A/P Aging Detail report has no Due date column: each bill is "
        "carried with its own date as its due date. Export the report with "
        "its own columns to keep the due dates."
    ]


def test_two_open_balance_columns_are_refused():
    rows = [list(r) for r in REAL_LAYOUT]
    rows[4] = [None] + HEADER + ["Open balance"]
    _, errors, _ = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == [
        "The A/P Aging Detail report has more than one Open balance column, so "
        "the import cannot tell which to read: export it with the report's own "
        "columns"
    ]


def test_the_summary_report_is_named_as_the_wrong_report():
    text = _csv(
        [
            ["Fieldstone Renovations"],
            ["A/P Aging Summary Report"],
            ["As of November 22, 2026"],
            [""],
            ["", "Current", "1 - 30", "31 - 60", "61 - 90", "91 and over", "Total"],
            ["Harbor Paint Supply", "", "", "112.40", "", "", "112.40"],
            ["TOTAL", "", "", "112.40", "", "", "112.40"],
        ]
    )
    assert qbo.parse_ap_aging(text) == (
        [],
        [
            "This is the A/P Aging Summary report, which lists each vendor's "
            "total and none of the bills behind it. Upload the A/P Aging Detail "
            "report instead (Reports → A/P Aging Detail → Export to Excel), as it "
            "downloads"
        ],
        [],
    )


def test_a_sub_vendor_the_list_lacks_is_refused_by_its_row(
    client, db_session, seed_accounts
):
    rows = [list(r) for r in REAL_LAYOUT]
    rows[10][4] = "Copperline Group:Copperline Electric"
    data = client.post(
        "/api/migration/qbo_export/import", files=_real_files(rows)
    ).json()
    assert data["ok"] is False
    assert data["errors"][-1] == (
        "A/P Aging Detail row 11: the vendor Copperline Group:Copperline Electric "
        "is not on the Vendor list, and its name reads as a sub-vendor "
        "(Parent:Child), which the import does not carry over. Upload the Vendors "
        "list from the same Export data zip, or enter this bill after the import"
    )
    assert db_session.query(Bill).count() == 0
    assert db_session.query(Vendor).count() == 0


# ── a second import keeps A/P Aging on 2000 ───────────────────────────────


def test_a_first_import_without_the_report_then_one_with_it(
    client, db_session, seed_accounts
):
    data = client.post(
        "/api/migration/qbo_export/import", files=_files(aging=False)
    ).json()
    assert data["ok"] is True, data["errors"]
    named = db_session.query(Transaction).filter(Transaction.vendor_id.isnot(None))
    assert named.count() > 0  # the Journal's A/P entries age by vendor (#915)

    dry = client.post("/api/migration/qbo_export/dry-run", files=_real_files()).json()
    assert dry["ok"] is True, dry["errors"]
    assert any("vendor names are cleared" in w for w in dry["warnings"]), dry
    db_session.expire_all()
    assert named.count() > 0  # the dry run cleared nothing

    data = client.post("/api/migration/qbo_export/import", files=_real_files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_open_bills"] == 5
    db_session.expire_all()
    ap = db_session.query(Account).filter(Account.account_number == "2000").one()
    on_ap = db_session.query(TransactionLine.transaction_id).filter(
        TransactionLine.account_id == ap.id
    )
    assert (
        db_session.query(Transaction)
        .filter(Transaction.vendor_id.isnot(None), Transaction.id.in_(on_ap))
        .count()
        == 0
    )
    _ties(client, db_session, Decimal("1025.00"))


def test_a_carried_bill_with_its_notes_edited_is_not_carried_again(
    client, db_session, seed_accounts
):
    client.post("/api/migration/qbo_export/import", files=_files())
    bill = db_session.query(Bill).filter(Bill.bill_number == "B-100").one()
    r = client.put(f"/api/bills/{bill.id}", json={"notes": "Call first"})
    assert r.status_code == 200, r.text
    data = client.post("/api/migration/qbo_export/import", files=_files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_open_bills"] == 0
    assert db_session.query(Bill).count() == 3
    _ties(client, db_session, Decimal("1025.00"))


def test_a_voided_unapplied_payment_is_not_carried_again(
    client, db_session, seed_accounts
):
    client.post("/api/migration/qbo_export/import", files=_files())
    r = client.post(
        f"/api/bill-payments/{_carried_id(db_session, 'unapplied_payment')}/void"
    )
    assert r.status_code == 200, r.text
    data = client.post("/api/migration/qbo_export/import", files=_files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_unapplied_bill_payments"] == 0
    assert db_session.query(BillPayment).count() == 2
    _ties(client, db_session, Decimal("1100.00"))


def test_odd_dates_are_carried_as_quickbooks_has_them():
    """A due date before the bill's own date, and a bill dated after the day
    the report was run, are things QuickBooks Online lets a person enter:
    both are carried as the report gives them, not refused."""
    rows = [list(r) for r in REAL_LAYOUT]
    rows[6][5] = "09/01/2026"  # due before its own date (09/14/2026)
    items, errors, _notes = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == []
    assert len(items) == 5

    rows = [list(r) for r in REAL_LAYOUT]
    rows[6][1], rows[6][5] = "12/01/2026", "12/31/2026"  # after the run date
    items, errors, _notes = qbo.parse_ap_aging(_as_uploaded(_real_workbook(rows)))
    assert errors == []
    assert len(items) == 5


# The later download's new row kinds, row by row as it writes them (the
# vendors and amounts invented, the Journal's own): a bill partly paid (its
# Amount above its Open balance) and a vendor credit not applied, whose Due
# date and Past due are empty strings and whose Amount and Open balance are
# both negative, in the CURRENT section; Num empty on every row; the totals
# formulas. The unapplied bill payment is invented: no download has one.
REAL_LAYOUT_PAID_AND_CREDIT = [
    ["Fieldstone Renovations"],
    ["A/P Aging Detail Report"],
    ["All Dates"],
    [],
    [None] + HEADER,
    ["31 - 60 days past due"],
    [None, "09/01/2026", "Bill", "", "Harbor Paint Supply", "10/01/2026"]
    + [52, 400.0, 400.0],
    [None, "09/10/2026", "Bill", "", "Ridge Lumber Co", "10/10/2026"]
    + [43, 900.0, 600.0],
    ["Total for 31 - 60 days past due"] + [None] * 6 + ["=H7+H8", "=I7+I8"],
    ["1 - 30 days past due"],
    [None, "10/01/2026", "Bill", "", "Quill Trade Goods", "10/31/2026"]
    + [22, 150.0, 150.0],
    [None, "10/20/2026", "Bill Payment (Check)", "", "Ridge Lumber Co"]
    + ["10/20/2026", 33, -75.0, -75.0],
    ["Total for 1 - 30 days past due"] + [None] * 6 + ["=H11+H12", "=I11+I12"],
    ["CURRENT"],
    [None, "10/05/2026", "Vendor Credit", "", "Harbor Paint Supply", ""]
    + ["", -50.0, -50.0],
    ["Total for CURRENT"] + [None] * 6 + ["=H15", "=I15"],
    ["TOTAL"] + [None] * 6 + ["=H9+H13+H16", "=I9+I13+I16"],
    [],
    [],
    [],
    [" Sunday, November 22, 2026 05:08 PM GMTZ"],
]


def test_the_real_layout_of_a_partly_paid_bill_and_a_credit_reads_and_imports(
    client, db_session, seed_accounts
):
    content = _real_workbook(REAL_LAYOUT_PAID_AND_CREDIT)
    items, errors, notes = qbo.parse_ap_aging(_as_uploaded(content))
    assert errors == [] and notes == []
    assert [
        (i["kind"], i["row"], i["date"].isoformat(), i["amount"], i["open"])
        for i in items
    ] == [
        ("bill", 7, "2026-09-01", Decimal("400"), Decimal("400")),
        ("bill", 8, "2026-09-10", Decimal("900"), Decimal("600")),
        ("bill", 11, "2026-10-01", Decimal("150"), Decimal("150")),
        ("payment", 12, "2026-10-20", Decimal("-75"), Decimal("-75")),
        ("credit", 15, "2026-10-05", Decimal("-50"), Decimal("-50")),
    ]
    # the credit's empty Due date reads as its own date
    assert items[4]["due_date"].isoformat() == "2026-10-05"

    files = _real_files(REAL_LAYOUT_PAID_AND_CREDIT)
    data = client.post("/api/migration/qbo_export/import", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert (data["imported_open_bills"], data["imported_vendor_credits"]) == (3, 1)
    assert data["imported_unapplied_bill_payments"] == 1
    said = " ".join(data["warnings"])
    assert (
        "row 15: carried over as vendor credit QBO-VC-20261005 (the report "
        "gives it no number)" in said
    )
    # the partly paid bill at its 900.00 with the Journal's 300.00 payment
    (partly,) = [
        b for b in db_session.query(Bill) if b.vendor.name == "Ridge Lumber Co"
    ]
    assert (partly.total, partly.amount_paid, partly.balance_due) == (
        Decimal("900.00"),
        Decimal("300.00"),
        Decimal("600.00"),
    )
    (alloc,) = db_session.query(BillPaymentAllocation).all()
    paid = db_session.get(BillPayment, alloc.bill_payment_id)
    assert (paid.date.isoformat(), paid.amount, paid.check_number) == (
        "2026-09-15",
        Decimal("300.00"),
        "3001",
    )
    (credit,) = db_session.query(VendorCredit).all()
    assert (
        credit.credit_number,
        credit.vendor.name,
        credit.date.isoformat(),
        credit.total,
        credit.amount_applied,
        credit.balance_remaining,
    ) == (
        "QBO-VC-20261005",
        "Harbor Paint Supply",
        "2026-10-05",
        Decimal("50.00"),
        Decimal("0.00"),
        Decimal("50.00"),
    )
    _ties(client, db_session, Decimal("1025.00"))

    again = client.post(
        "/api/migration/qbo_export/import",
        files=_real_files(REAL_LAYOUT_PAID_AND_CREDIT),
    ).json()
    assert again["ok"] is True, again["errors"]
    assert (again["imported_open_bills"], again["imported_vendor_credits"]) == (0, 0)
    assert again["imported_unapplied_bill_payments"] == 0
    db_session.expire_all()
    counts = (
        db_session.query(Bill).count(),
        db_session.query(VendorCredit).count(),
        db_session.query(BillPayment).count(),
    )
    assert counts == (3, 1, 2)
    _ties(client, db_session, Decimal("1025.00"))


@pytest.mark.parametrize("choice", ["reference", "memo"])
def test_paying_a_carried_bill_puts_no_carry_over_note_on_the_check(
    client, db_session, seed_accounts, choice
):
    """A real report gives no bill a number, so a carried bill has no
    reference, and its notes are the carry-over's own sentence about the
    migration. Paid in Pay Bills with no memo typed, that sentence is no
    check memo under either choice: the vendor would read it on the check."""
    r = client.put("/api/settings", json={"check_memo": choice})
    assert r.status_code == 200, r.text
    data = client.post("/api/migration/qbo_export/import", files=_real_files()).json()
    assert data["ok"] is True, data["errors"]
    (bill,) = [
        b for b in db_session.query(Bill) if b.vendor.name == "Copperline Electric"
    ]
    assert bill.notes.startswith(qbo._AP_CARRIED)
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": bill.vendor_id,
            "date": "2026-11-24",
            "amount": 207.6,
            "check_number": "5001",
            "allocations": [{"bill_id": bill.id, "amount": 207.6}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["notes"] is None
    db_session.expire_all()
    _ties(client, db_session, Decimal("1025.00") - Decimal("207.60"))
