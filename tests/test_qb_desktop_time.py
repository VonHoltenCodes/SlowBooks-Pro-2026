"""QuickBooks Desktop's timesheets come across as time entries (2.23.4,
#323).

Desktop does not export timesheets to IIF, so a company has a report: Time
by Name by Job detail (or by Job by Name), saved as CSV or Excel, with a
heading row per person and per customer:job, one row per entry (Date, Item,
Billing Status, Duration, Notes), QuickBooks' own Total rows at both levels
and a TOTAL. Each row becomes an approved time entry under its employee,
marked as QuickBooks' so no pay run picks it up; the whole file is checked
against its totals first; a second upload posts nothing twice.

The fixtures are invented timesheets (Northline Surveying) in the layout of
the real report the importer was built against; the real file stays private.
"""

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.contacts import Customer
from app.models.jobs import Job
from app.models.payroll import Employee
from app.models.time_entries import TimeEntry, TimeEntryStatus
from app.services.qb_time_import import SOURCE

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qb_desktop"
BY_NAME = FIXTURES / "Time_by_Name_by_Job.xlsx"
BY_JOB = FIXTURES / "Time_by_Job_by_Name.xlsx"
LABELS = {
    "Northline Overhead",
    "Harbor Dist:Pier Survey",
    "Harbor Dist:Boundary Plat",
    "Town of Elm:Storm Mapping",
}


def _upload(client, path, data=None, name=None):
    return client.post(
        "/api/csv/import/qb-report",
        files={
            "file": (
                name or path.name,
                io.BytesIO(path.read_bytes() if data is None else data),
                "application/octet-stream",
            )
        },
    ).json()


def _csv_of(path):
    from app.routes.migration import _sheet_as_csv

    return _sheet_as_csv(path.read_bytes())


def _imported(db):
    return db.query(TimeEntry).filter(TimeEntry.source == SOURCE).all()


def _pier_survey(db):
    harbor = Customer(name="Harbor Dist")
    db.add(harbor)
    db.flush()
    pier = Job(customer_id=harbor.id, name="Pier Survey")
    db.add(pier)
    db.commit()
    return pier


def test_the_report_comes_across_as_approved_time_under_each_person(
    client, db_session, seed_accounts
):
    pier = _pier_survey(db_session)
    r = _upload(client, BY_NAME)
    assert r["detected"] == "time" and r["errors"] == [], r
    assert r["time_entries"] == 26
    entries = _imported(db_session)
    assert len(entries) == 26
    assert all(e.status == TimeEntryStatus.APPROVED for e in entries)
    assert sum(Decimal(str(e.hours_regular)) for e in entries) == Decimal("104.75")
    # the three people, added from their names and named in the result
    assert {(e.employee.first_name, e.employee.last_name) for e in entries} == {
        ("M", "Alvarez"),
        ("R", "Chen"),
        ("T", "Okafor"),
    }
    assert any(
        "3 person(s)" in w and "'Alvarez, M'" in w and "'Okafor, T'" in w
        for w in r["warnings"]
    ), r["warnings"]
    # the one label that is a job on the Jobs list is linked; the others are
    # kept as QuickBooks printed them, and named
    linked = [e for e in entries if e.job_id == pier.id]
    assert len(linked) == 6
    assert all(e.customer_job == "Harbor Dist:Pier Survey" for e in linked)
    assert {e.customer_job for e in entries if e.job_id is None} == LABELS - {
        "Harbor Dist:Pier Survey"
    }
    assert any(
        "3 customer or job label(s)" in w and "'Northline Overhead' (8)" in w
        for w in r["warnings"]
    ), r["warnings"]
    # the service item, the billing status and the notes ride along
    one = next(e for e in entries if e.notes == "Drone pilot recert")
    assert one.service == "Indirect - Admin:Training"
    assert one.billing == "not_billable" and one.date == date(2024, 1, 3)
    assert Decimal(str(one.hours_regular)) == 2
    assert {e.billing for e in entries} == {"not_billable", "billed", "unbilled"}
    assert any("never picks it up" in w for w in r["warnings"])


def test_a_second_upload_posts_nothing_twice(client, db_session, seed_accounts):
    first = _upload(client, BY_NAME)
    assert first["time_entries"] == 26
    again = _upload(client, BY_NAME)
    assert again["time_entries"] == 0 and again["duplicates_skipped"] == 26
    assert len(_imported(db_session)) == 26
    # Chen's two identical GIS QC rows are two entries, as QuickBooks has them
    assert (
        db_session.query(TimeEntry).filter(TimeEntry.notes == "GIS layer QC").count()
        == 2
    )


def test_the_by_job_by_name_report_reads_the_same(client, db_session, seed_accounts):
    r = _upload(client, BY_JOB)
    assert r["detected"] == "time" and r["time_entries"] == 26, r
    entries = _imported(db_session)
    assert {e.employee.last_name for e in entries} == {"Alvarez", "Chen", "Okafor"}
    assert {e.customer_job for e in entries} == LABELS
    assert sum(Decimal(str(e.hours_regular)) for e in entries) == Decimal("104.75")


def test_a_csv_export_with_minutes_durations_reads_the_same(
    client, db_session, seed_accounts
):
    text = _csv_of(BY_NAME)
    assert text.startswith(",,,,Date,Item,Billing Status,Duration,Notes")
    # QuickBooks' minutes format: 0:30 is 0.5, 0:15 is 0.25
    text = text.replace(",Not Billable,0.5,", ",Not Billable,0:30,").replace(
        ",Not Billable,0.25,", ",Not Billable,0:15,"
    )
    assert "0:30" in text and "0:15" in text
    r = _upload(client, BY_NAME, data=text.encode("utf-8"), name="Time by Name.csv")
    assert r["detected"] == "time" and r["errors"] == [], r
    assert r["time_entries"] == 26
    hours = sum(Decimal(str(e.hours_regular)) for e in _imported(db_session))
    assert hours == Decimal("104.75")


def test_a_total_that_does_not_match_refuses_the_file_whole(
    client, db_session, seed_accounts
):
    text = _csv_of(BY_NAME).replace(
        ",,Total Northline Overhead,,,,,2.75,", ",,Total Northline Overhead,,,,,3.75,"
    )
    assert "3.75" in text
    r = _upload(client, BY_NAME, data=text.encode("utf-8"), name="t.csv")
    assert r["time_entries"] == 0
    assert any(
        "Alvarez, M:Northline Overhead" in e and "cut short or edited" in e
        for e in r["errors"]
    ), r["errors"]
    assert db_session.query(TimeEntry).count() == 0
    assert db_session.query(Employee).count() == 0  # nothing written


def test_a_report_without_headings_says_which_report_to_export(
    client, db_session, seed_accounts
):
    rows = list(csv.reader(io.StringIO(_csv_of(BY_NAME))))
    kept = [row for row in rows if not any(cell.strip() for cell in row[:4])]
    text = "\n".join(",".join(row) for row in kept) + "\n"
    r = _upload(client, BY_NAME, data=text.encode("utf-8"), name="flat.csv")
    assert r["time_entries"] == 0
    assert any("Time by Name by Job" in e for e in r["errors"]), r["errors"]


def test_people_already_on_the_employees_list_are_matched_by_name(
    client, db_session, seed_accounts
):
    db_session.add(Employee(first_name="M", last_name="Alvarez"))
    db_session.add(Employee(first_name="R", last_name="Chen"))
    db_session.commit()
    r = _upload(client, BY_NAME)
    assert r["time_entries"] == 26
    assert db_session.query(Employee).count() == 3  # Okafor added, the two matched
    assert any(
        "1 person(s)" in w and "'Okafor, T'" in w and "Alvarez" not in w
        for w in r["warnings"]
    ), r["warnings"]


def test_imported_time_is_never_swept_into_a_pay_run(client, db_session, seed_accounts):
    """The pay run's sweep (approved, unpaid time in the period) and its
    waiting-for-approval count both leave imported history alone."""
    _upload(client, BY_NAME)
    from app.routes.payroll.runs import _unapproved_time

    emp = db_session.query(Employee).filter(Employee.last_name == "Alvarez").one()
    count, hours = _unapproved_time(
        db_session, emp.id, date(2024, 1, 1), date(2024, 12, 31)
    )
    assert (count, hours) == (0, 0)
    payable = (
        db_session.query(TimeEntry)
        .filter(
            TimeEntry.employee_id == emp.id,
            TimeEntry.status == TimeEntryStatus.APPROVED,
            TimeEntry.pay_run_id.is_(None),
            TimeEntry.source.is_(None),
        )
        .count()
    )
    assert payable == 0 and len(_imported(db_session)) == 26
    runs = (
        Path(__file__).resolve().parents[1] / "app/routes/payroll/runs.py"
    ).read_text(encoding="utf-8")
    assert runs.count("TimeEntry.source.is_(") == 2


def test_the_pages_list_and_count_them(client, db_session, seed_accounts):
    _upload(client, BY_NAME)
    listed = [
        e for e in client.get("/api/time-entries").json() if e["source"] == SOURCE
    ]
    assert len(listed) == 26
    assert listed[0]["service"] and listed[0]["customer_job"] and listed[0]["billing"]
    root = Path(__file__).resolve().parents[1]
    hr = (root / "app/static/js/time_entries.js").read_text(encoding="utf-8")
    assert "en.customer_job" in hr and "QuickBooks" in hr
    iif = (root / "app/static/js/iif.js").read_text(encoding="utf-8")
    assert "time_entries: 'Time entries'" in iif
    assert "(result.time_entries || 0)" in iif and "Time by Name by Job detail" in iif
