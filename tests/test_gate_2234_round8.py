"""The 2.23.4 gate's seventh round, on the timesheet import and the
statements (NEW-130 to NEW-135, W-7 to W-11).

- NEW-130: a timesheet in QuickBooks' Minutes format (0:20 three times under
  a total of 1:00) imports; totals are compared in whole minutes.
- NEW-132: a Total written as an Excel formula reads from the value Excel
  computed for it; a Total that cannot be read refuses the file whole.
- W-11 / NEW-131: the pay-run form's summary leaves imported time out, and
  the run's refusal says the approved time there is QuickBooks' history.
- W-7: the Trial Balance puts accounts with no number last, by name, a
  sub-account after its parent, as the statements do.
- W-8: a statement row names a sub-account by its path and its number.
- W-9, W-10, NEW-133, NEW-135: the file input takes .xlsx; the result names
  the report it read; a seam between the aging bar's slices; What's new
  says the statements changed their order.
"""

import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl

from app.models.accounts import Account, AccountType
from app.models.payroll import Employee
from app.models.time_entries import TimeEntry
from app.services.accounting import create_journal_entry
from app.services.qb_time_import import SOURCE

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "qb_desktop"
BY_NAME = FIXTURES / "Time_by_Name_by_Job.xlsx"
BY_JOB = FIXTURES / "Time_by_Job_by_Name.xlsx"
PERIOD = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


def _upload(client, name, data):
    return client.post(
        "/api/csv/import/qb-report",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
    ).json()


def _csv_of(path):
    from app.routes.migration import _sheet_as_csv

    return _sheet_as_csv(path.read_bytes())


MINUTES_CSV = (
    ",,,,Date,Item,Billing Status,Duration,Notes\n"
    ' ,,,,,,,,\n,"Alvarez, M",,,,,,,\n,,Northline Overhead,,,,,,\n'
    ",,,,01/02/2024,Indirect - Admin:Email/Phone,Not Billable,0:20,\n"
    ",,,,01/03/2024,Indirect - Admin:Email/Phone,Not Billable,0:20,\n"
    ",,,,01/04/2024,Indirect - Admin:Email/Phone,Not Billable,0:20,\n"
    ",,Total Northline Overhead,,,,,1:00,\n"
    ',"Total Alvarez, M",,,,,,1:00,\n'
    "TOTAL,,,,,,,1:00,\n"
)


def test_a_timesheet_in_minutes_format_imports(client, db_session, seed_accounts):
    r = _upload(client, "minutes.csv", MINUTES_CSV.encode("utf-8"))
    assert r["detected"] == "time" and r["errors"] == [], r
    assert r["time_entries"] == 3
    hours = [Decimal(str(e.hours_regular)) for e in db_session.query(TimeEntry)]
    assert all(
        h == Decimal("0.33") for h in hours
    ), hours  # twenty minutes, to the cent-hour
    # decimals that are minutes shown to two places count as minutes too
    decimal_csv = MINUTES_CSV.replace(",0:20,", ",0.33,").replace(",1:00,", ",1,")
    again = _upload(client, "decimal.csv", decimal_csv.encode("utf-8"))
    assert again["errors"] == [] and again["duplicates_skipped"] == 3, again
    # and a total that is really short is still refused, in minutes
    short = MINUTES_CSV.replace(
        ",,,,01/04/2024,Indirect - Admin:Email/Phone,Not Billable,0:20,\n", ""
    )
    bad = _upload(client, "short.csv", short.encode("utf-8"))
    assert bad["time_entries"] == 0
    assert any("40 minutes" in e and "60 minutes" in e for e in bad["errors"]), bad[
        "errors"
    ]


def _formula_workbook(cached: bool) -> bytes:
    """The by-Name fixture with its Total rows as =ROUND(SUM(...),5), as
    QuickBooks' Excel export writes them; with `cached`, the computed value
    Excel would have saved beside each formula."""
    book = openpyxl.load_workbook(BY_NAME)
    sheet = book.worksheets[0]
    totals = []
    for row in sheet.iter_rows():
        label = row[2].value or row[1].value or row[0].value
        if isinstance(label, str) and (label.startswith("Total ") or label == "TOTAL"):
            totals.append((row[7].coordinate, row[7].value))
            row[7].value = f"=ROUND(SUM(H1:H{row[7].row - 1}),5)"
    buf = io.BytesIO()
    book.save(buf)
    if not cached:
        return buf.getvalue()
    # openpyxl writes a formula with an empty value; Excel writes the value
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                for coordinate, value in totals:
                    marker = f'<c r="{coordinate}"'
                    start = text.index(marker)
                    end = text.index("</c>", start)
                    cell = text[start:end]
                    assert "<v></v>" in cell or "<v/>" in cell, cell
                    cell = cell.replace("<v></v>", f"<v>{value}</v>").replace(
                        "<v/>", f"<v>{value}</v>"
                    )
                    text = text[:start] + cell + text[end:]
                data = text.encode("utf-8")
            dst.writestr(item, data)
    return out.getvalue()


def test_excel_totals_written_as_formulas_read_from_their_computed_values(
    client, db_session, seed_accounts
):
    r = _upload(client, "Time by Name by Job.xlsx", _formula_workbook(cached=True))
    assert r["errors"] == [] and r["time_entries"] == 26, r
    assert r["report"] == "Time by Name by Job detail"


def test_a_total_that_cannot_be_read_refuses_the_file_whole(
    client, db_session, seed_accounts
):
    r = _upload(client, "Time by Name by Job.xlsx", _formula_workbook(cached=False))
    assert r["time_entries"] == 0
    assert any(
        "could not be read" in e
        and "ROUND(SUM" in e
        and "Save the file from Excel" in e
        for e in r["errors"]
    ), r["errors"]
    assert db_session.query(TimeEntry).count() == 0
    assert db_session.query(Employee).count() == 0


def test_the_result_names_the_report_it_read(client, db_session, seed_accounts):
    assert _upload(client, BY_JOB.name, BY_JOB.read_bytes())["report"] == (
        "Time by Job by Name detail"
    )
    js = (ROOT / "app/static/js/iif.js").read_text(encoding="utf-8")
    assert "result.report || kindLabel[result.detected]" in js
    assert 'accept=".csv,.xlsx"' in js and "Choose a file first" in js  # W-9


def _employee(client, first, last):
    r = client.post(
        "/api/employees",
        json={
            "first_name": first,
            "last_name": last,
            "pay_type": "hourly",
            "pay_rate": 30,
            "pay_frequency": "biweekly",
            "filing_status": "single",
            "work_state": "WA",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_the_pay_run_form_never_counts_imported_time_and_the_run_says_why(
    client, db_session, seed_accounts
):
    alvarez = _employee(client, "M", "Alvarez")
    _employee(client, "R", "Chen")
    _employee(client, "T", "Okafor")
    r = _upload(client, BY_NAME.name, BY_NAME.read_bytes())
    assert r["time_entries"] == 26
    # the form's preview: no imported hours (W-11 / NEW-131)
    summary = client.get(
        "/api/time-entries/summary",
        params={"period_start": "2024-01-01", "period_end": "2024-01-31"},
    ).json()
    assert summary == [], summary
    # the run refuses, and says the approved time there is QuickBooks'
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2024-01-01",
            "period_end": "2024-01-31",
            "pay_date": "2024-02-05",
            "stubs": [{"employee_id": alvarez["id"], "use_time_entries": True}],
        },
    )
    assert run.status_code == 422, run.text
    detail = run.json()["detail"]
    assert (
        "came from QuickBooks" in detail and "never pays imported time" in detail
    ), detail
    assert "44.5 hours" in detail, detail
    # with one ordinary approved entry, the form shows it alone and the run pays it alone
    te = client.post(
        "/api/time-entries",
        json={"employee_id": alvarez["id"], "date": "2024-01-15", "hours_regular": 2},
    ).json()
    client.post(f"/api/time-entries/{te['id']}/approve", json={"approved_by": "boss"})
    summary = client.get(
        "/api/time-entries/summary",
        params={"period_start": "2024-01-01", "period_end": "2024-01-31"},
    ).json()
    assert (
        len(summary) == 1
        and summary[0]["total"] == 2.0
        and summary[0]["entry_count"] == 1
    )
    paid = client.post(
        "/api/payroll",
        json={
            "period_start": "2024-01-01",
            "period_end": "2024-01-31",
            "pay_date": "2024-02-05",
            "stubs": [{"employee_id": alvarez["id"], "use_time_entries": True}],
        },
    ).json()
    assert paid["stubs"][0]["gross_pay"] == 60.0
    imported = db_session.query(TimeEntry).filter(TimeEntry.source == SOURCE).all()
    assert all(e.pay_run_id is None for e in imported)


def _account(db, name, kind, number=None, parent=None):
    acct = Account(
        name=name,
        account_type=kind,
        account_number=number,
        balance=Decimal("0"),
        parent_id=parent.id if parent is not None else None,
    )
    db.add(acct)
    db.flush()
    return acct


def _books(db):
    by_number = {a.account_number: a for a in db.query(Account) if a.account_number}
    auto = _account(db, "Automobile", AccountType.EXPENSE, "8600")
    fuel = _account(db, "Fuel", AccountType.EXPENSE, None, parent=auto)
    insurance = _account(db, "Insurance", AccountType.EXPENSE, None, parent=auto)
    dues = _account(db, "Aardvark Dues", AccountType.EXPENSE, None)
    legal = _account(db, "Legal", AccountType.EXPENSE, "8250")
    lines = [
        {
            "account_id": a.id,
            "debit": Decimal("10"),
            "credit": Decimal("0"),
            "description": a.name,
        }
        for a in (fuel, insurance, dues, legal)
    ]
    lines.append(
        {
            "account_id": by_number["1000"].id,
            "debit": Decimal("0"),
            "credit": Decimal("40"),
            "description": "the other side",
        }
    )
    create_journal_entry(db, date(2026, 3, 1), "order test", lines)
    db.commit()


def test_a_statement_row_names_a_sub_account_by_its_path_and_number(
    client, db_session, seed_accounts
):
    _books(db_session)
    body = client.get("/api/reports/profit-loss", params=PERIOD).json()
    rows = {r["account_name"]: r for r in body["expenses"]}
    # the two Insurance rows (the chart's 6300 and Automobile's) tell apart
    assert rows["Fuel"]["account_path"] == "Automobile: Fuel"
    assert rows["Fuel"]["account_label"] == "Automobile: Fuel"
    assert rows["Insurance"]["account_label"] == "Automobile: Insurance"
    assert rows["Legal"]["account_label"] == "8250 - Legal"
    assert rows["Aardvark Dues"]["account_label"] == "Aardvark Dues"
    # Automobile itself has no line, so its sub-accounts stand in its place, together
    names = [r["account_label"] for r in body["expenses"]]
    assert names.index("Automobile: Insurance") == names.index("Automobile: Fuel") + 1
    csv_text = client.get("/api/reports/profit-loss/csv", params=PERIOD).text
    assert "Automobile: Fuel" in csv_text and ",8250,Legal," in csv_text
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert (
        js.count("i.account_label || i.account_name") == 4
    )  # the P&L and the Balance Sheet rows, and their drill-down titles


def test_the_trial_balance_puts_unnumbered_accounts_last_and_grouped(
    client, db_session, seed_accounts
):
    _books(db_session)
    tb = client.get("/api/reports/trial-balance", params=PERIOD).json()
    names = [i["account_name"] for i in tb["items"]]
    numbers = [i["account_number"] for i in tb["items"] if i["account_number"]]
    assert numbers == sorted(numbers)
    assert names.index("Checking") < names.index("Legal")  # 1000 before 8250
    # Automobile's two sub-accounts stand together where 8600 belongs, before the unnumbered
    assert (
        names.index("Legal")
        < names.index("Fuel")
        < names.index("Insurance")
        < names.index("Aardvark Dues")
    )
    assert names[-1] == "Aardvark Dues"  # no number: last, by name
    # the General Ledger lists its accounts the same way
    gl = client.get("/api/reports/general-ledger", params=PERIOD).json()
    gl_names = [a["account_name"] for a in gl["accounts"]]
    assert gl_names.index("Checking") < gl_names.index("Legal") < gl_names.index("Fuel")
    assert gl_names[-1] == "Aardvark Dues"


def test_the_aging_bar_seams_its_slices_and_whats_new_names_the_order():
    dash = (ROOT / "app/static/js/dashboard.js").read_text(encoding="utf-8")
    assert "border-left:2px solid var(--panel-bg)" in dash  # NEW-133
    news = json.loads((ROOT / "app/static/whats-new.json").read_text(encoding="utf-8"))
    assert any("Sort by" in item for item in news["2.23.4"]["items"])  # NEW-135
