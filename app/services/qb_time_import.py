# ============================================================================
# QuickBooks Desktop's timesheets, as time entries (2.23.4, #323).
#
# QuickBooks Desktop does not export timesheets to IIF (it only imports
# them), so what a company has is a report: Reports > Jobs, Time & Mileage >
# Time by Name by Job detail, or Time by Job by Name detail, saved as CSV or
# Excel. Both have the same rows: a heading row for each person (or each
# customer:job), the other one indented a column under it, then one row per
# time entry with its Date, Item (the service, "Indirect - Admin:Email/
# Phone"), Billing Status (Not Billable, Unbilled, Billed), Duration (hours,
# as 2.25 or 2:15) and Notes; a "Total <label>" row under each group at both
# levels, and a TOTAL row at the end.
#
# Each row becomes one approved time entry under its employee, with the
# hours as regular hours, the notes as notes, the service item, the billing
# status and the customer or job label as printed (linked to the job on the
# Jobs list when the label is one). A person not on the Employees list is
# added from the name, and named in the result, for their pay to be set.
# Imported time is history: it is marked as QuickBooks', and a pay run never
# picks it up. Before anything is written the whole file is checked, every
# group's hours against the total QuickBooks printed for it; a file that
# fails is refused whole. A second upload posts nothing twice.
#
# Built against a real company's report (half a year of a consultancy's
# timesheets: 6,277 entries, 50 people, 52 customer:jobs, 33 service items,
# every total matched); the fixture in tests/fixtures/qb_desktop/ is
# invented books in that layout. Nothing of the real file is here.
# ============================================================================

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.jobs import Job
from app.models.payroll import Employee
from app.models.time_entries import TimeEntry, TimeEntryStatus

SOURCE = "qb_time_report"
# The columns both reports share. Time by Name by Job detail carries the
# service as an Item column under person and customer:job headings; the real
# Time by Job by Name detail (a user's file, 2.23.5) carries the person as a
# Name column under customer:job and service-item headings, with no Item
# column at all. Either reads.
TIME_COLUMNS = ("Date", "Billing Status", "Duration")
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")
_HOW = (
    "Export it from QuickBooks Desktop as Reports > Jobs, Time & Mileage > "
    "Time by Job Detail (a Time by Name report grouped by job reads too), with "
    "the dates covering the time you want, saved as CSV or Excel."
)
_PERSON = re.compile(r"^[^,:]+,\s*[^,:]+$")  # "Last, First", QuickBooks' names
_BILLING = {
    "not billable": "not_billable",
    "unbilled": "unbilled",
    "billable": "unbilled",
    "billed": "billed",
}


def is_time_header(cols: dict | None) -> bool:
    return (
        bool(cols)
        and all(c in cols for c in TIME_COLUMNS)
        and ("Item" in cols or "Name" in cols)
    )


def _minutes(raw: str) -> int:
    """A duration as whole minutes: 2:15 (QuickBooks' minutes format) is
    135; 2.25 is 135; 0.33 (twenty minutes shown to two places) is 20.
    QuickBooks keeps time in minutes, so a decimal it prints is within
    0.005 of a whole number of minutes over sixty, and rounding recovers
    it. Totals are compared in minutes, so three 0:20 rows meet their 1:00
    (2.23.4 gate, NEW-130: in hours, 20/60 three times fell short of 1)."""
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        return 0
    negative = raw.startswith("-")
    raw = raw.lstrip("-")
    if ":" in raw:
        # 2:15, or 00:20:00 (a Minutes-format CSV opened in Excel or Numbers
        # and saved as .xlsx keeps each duration as a time of day; 2.23.4
        # gate, NEW-137)
        parts = raw.split(":")
        hours, minutes = int(parts[0] or "0"), int(parts[1] or "0")
        seconds = (
            int(Decimal(parts[2]).to_integral_value())
            if len(parts) > 2 and parts[2]
            else 0
        )
        total = hours * 60 + minutes + int(round(seconds / 60))
    else:
        total = int((Decimal(raw) * 60).to_integral_value())
    return -total if negative else total


def _hours(minutes: int) -> Decimal:
    return Decimal(minutes) / Decimal(60)


def _date(raw: str):
    raw = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unreadable date {raw!r}")


def parse_time_report(rows: list[list[str]], header_at: int, cols: dict):
    """(entries, errors): each time row as {labels, date, item, billing,
    hours, notes, row}, its labels the two headings above it (outer, inner),
    after the file's own totals are checked. With a Name column and no Item
    column (Time by Job by Name detail as QuickBooks writes it), the person
    is the row's Name, the outer heading the customer:job and the inner one
    the service item: the entry carries "person", and its item is the inner
    heading."""
    first = cols["Date"]  # the label columns are the ones before Date
    name_column = "Item" not in cols and "Name" in cols
    entries: list[dict] = []
    errors: list[str] = []
    stack: list[tuple[int, str]] = []
    totals: list[tuple[tuple, int]] = []

    def cell(row, name):
        i = cols.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        if not cell(row, "Date"):
            label = next(
                (
                    (j, row[j].strip())
                    for j in range(min(first, len(row)))
                    if row[j].strip()
                ),
                None,
            )
            if label is None:
                continue
            depth, text = label
            if text.upper() == "TOTAL" or text.startswith("Total "):
                # QuickBooks' own total: read, or the file is refused (a
                # total skipped in silence hid a deleted row; the Excel
                # export writes them as formulas, read from their computed
                # values: 2.23.4 gate, NEW-132)
                try:
                    minutes = _minutes(cell(row, "Duration"))
                except Exception:
                    errors.append(
                        f"Row {n}: the total for {text[6:] or 'the report'} could "
                        f"not be read ({cell(row, 'Duration')!r}). Save the file "
                        "from Excel again, or export it as CSV."
                    )
                    minutes = None
                if text.upper() == "TOTAL":
                    if minutes is not None:
                        totals.append(((), minutes))
                    continue
                while stack and stack[-1][0] > depth:
                    stack.pop()
                if stack and stack[-1][0] == depth:
                    if minutes is not None:
                        totals.append((tuple(name for _, name in stack), minutes))
                    stack.pop()
                continue
            while stack and stack[-1][0] >= depth:
                stack.pop()
            stack.append((depth, text))
            continue
        if len(stack) < (1 if name_column else 2):
            errors.append(
                "This report lists its time without the person and the customer "
                "or job each entry belongs to above it, so it cannot be posted. " + _HOW
            )
            return [], errors
        try:
            minutes = _minutes(cell(row, "Duration"))
            day = _date(cell(row, "Date"))
        except Exception:
            # the cells as they read, never the exception's own words
            errors.append(
                f"Row {n}: the date ({cell(row, 'Date')!r}) or the duration "
                f"({cell(row, 'Duration')!r}) could not be read"
            )
            continue
        if minutes == 0:
            continue
        labels = tuple(name for _, name in stack[:2])
        entry = {
            "labels": labels if len(labels) == 2 else (labels[0], ""),
            "date": day,
            "item": (
                stack[1][1] if name_column and len(stack) > 1 else cell(row, "Item")
            ),
            "billing": cell(row, "Billing Status"),
            "minutes": minutes,
            "hours": _hours(minutes),
            "notes": cell(row, "Notes"),
            "row": n,
        }
        if name_column:
            entry["person"] = cell(row, "Name")
        entries.append(entry)
    if errors:
        return [], errors
    # QuickBooks' own totals: a group's rows add up to what it printed, in
    # whole minutes
    for path, minutes in totals:
        mine = sum(e["minutes"] for e in entries if e["labels"][: len(path)] == path)
        if mine != minutes:
            where = ":".join(path) if path else "the whole report"
            errors.append(
                f"The time under {where} comes to {_hours(mine):,.2f} hours "
                f"({mine:,} minutes) and QuickBooks' total says "
                f"{_hours(minutes):,.2f} ({minutes:,} minutes): the file looks cut "
                "short or edited. Export it again."
            )
    return entries, errors


def _person_level(entries: list[dict], employees: dict) -> int:
    """Which heading level is the person: the one whose labels read as
    names ("Last, First"), or are on the Employees list; the outer one
    when nothing tells (Time by Name by Job, the usual report)."""
    levels = [{e["labels"][0] for e in entries}, {e["labels"][1] for e in entries}]
    score = [
        sum(1 for text in labels if _PERSON.match(text) or _key(text) in employees)
        - sum(1 for text in labels if ":" in text)
        for labels in levels
    ]
    return 1 if score[1] > score[0] else 0


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _split_name(label: str) -> tuple[str, str]:
    """ "Last, First" → (first, last); "First Last" → (first, last)."""
    if "," in label:
        last, first = (part.strip() for part in label.split(",", 1))
        return first or label, last
    parts = label.strip().split()
    if len(parts) > 1:
        return " ".join(parts[:-1]), parts[-1]
    return label.strip(), ""


def _employees(db: Session) -> dict:
    """The Employees list under every spelling a report may use."""
    out: dict = {}
    for emp in db.query(Employee).all():
        first, last = (emp.first_name or "").strip(), (emp.last_name or "").strip()
        for spelling in (f"{last}, {first}", f"{first} {last}", f"{last} {first}"):
            out.setdefault(_key(spelling), emp)
    return out


def _jobs(db: Session) -> dict:
    """The Jobs list by QuickBooks' "Customer:Job" (spaces around the
    colon aside), and by the job's own name when that is unambiguous."""
    out: dict = {}
    by_name: dict = {}
    for job in db.query(Job).all():
        full = _key(job.full_name).replace(" : ", ":").replace(": ", ":")
        out.setdefault(full, job)
        by_name.setdefault(_key(job.name), []).append(job)
    for name, jobs in by_name.items():
        if len(jobs) == 1:
            out.setdefault(name, jobs[0])
    return out


def _ref(fingerprint: str, nth: int) -> str:
    """The row's fingerprint, a SHA-256 of its fields (notes and names among
    them) cut to forty characters, and which repeat of it this is."""
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:40] + f":{nth}"


def import_time_report(
    db: Session, rows: list[list[str]], header_at: int, cols: dict
) -> dict:
    result = {
        "time_entries": 0,
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
    }
    entries, errors = parse_time_report(rows, header_at, cols)
    if errors:
        result["errors"] = errors
        return result
    if not entries:
        result["errors"].append("The report has no time in it. " + _HOW)
        return result
    employees = _employees(db)
    jobs = _jobs(db)
    # the person: the row's Name column (Time by Job by Name detail as
    # QuickBooks writes it), else the heading level that reads as names
    name_column = all("person" in e for e in entries)
    person_at = 0 if name_column else _person_level(entries, employees)
    # which report this is, for the result (W-10: the by-Job file said
    # "Time by Name by Job")
    result["report"] = (
        "Time by Job by Name detail"
        if name_column or person_at == 1
        else "Time by Name by Job detail"
    )
    added: list[str] = []
    unlinked: OrderedDict = OrderedDict()
    have = {
        ref
        for (ref,) in db.query(TimeEntry.source_ref).filter(
            TimeEntry.source == SOURCE, TimeEntry.source_ref.isnot(None)
        )
    }
    seen: dict = {}
    now = datetime.now(timezone.utc)
    for entry in entries:
        if name_column:
            person, label = entry["person"], entry["labels"][0]
        else:
            person = entry["labels"][person_at]
            label = entry["labels"][1 - person_at]
        emp = employees.get(_key(person))
        if emp is None:
            first, last = _split_name(person)
            emp = Employee(first_name=first[:100], last_name=last[:100], is_active=True)
            db.add(emp)
            db.flush()
            for spelling in (person, f"{first} {last}", f"{last}, {first}"):
                employees.setdefault(_key(spelling), emp)
            added.append(person)
        job = jobs.get(_key(label).replace(" : ", ":").replace(": ", ":"))
        if job is None:
            unlinked[label] = unlinked.get(label, 0) + 1
        billing = _BILLING.get(entry["billing"].lower(), None) or (
            re.sub(r"\s+", "_", entry["billing"].lower())[:20] or None
        )
        fingerprint = "|".join(
            [
                str(emp.id),
                entry["date"].isoformat(),
                entry["item"],
                billing or "",
                f"{entry['hours']:.4f}",
                entry["notes"],
                label,
            ]
        )
        seen[fingerprint] = seen.get(fingerprint, 0) + 1
        ref = _ref(fingerprint, seen[fingerprint])
        if ref in have:
            result["duplicates_skipped"] += 1
            continue
        db.add(
            TimeEntry(
                employee_id=emp.id,
                date=entry["date"],
                hours_regular=entry["hours"],
                hours_overtime=Decimal("0"),
                hours_doubletime=Decimal("0"),
                job_id=job.id if job is not None else None,
                notes=entry["notes"][:2000] or None,
                status=TimeEntryStatus.APPROVED,
                approved_by="QuickBooks import",
                approved_at=now,
                source=SOURCE,
                source_ref=ref,
                billing=billing,
                service=entry["item"][:200] or None,
                customer_job=label[:300] or None,
            )
        )
        have.add(ref)
        result["time_entries"] += 1
    if added:
        result["warnings"].append(
            f"{len(added)} person(s) in the report were not on the Employees list "
            "and were added from their names: "
            + "; ".join(f"'{name}'" for name in added)
            + ". Set their pay type and rate on the Employees page."
        )
    if unlinked:
        result["warnings"].append(
            f"{len(unlinked)} customer or job label(s) in the report are not a job "
            "on the Jobs list, so their entries keep the label without a link: "
            + "; ".join(f"'{label}' ({n})" for label, n in unlinked.items())
            + ". Import the lists IIF first (File > Utilities > Export > Lists to "
            "IIF Files) for the customer:jobs, or add the jobs and import again."
        )
    if result["time_entries"]:
        result["warnings"].append(
            "Imported time is approved and marked as QuickBooks', so a pay run "
            "never picks it up. HR > Time Entries lists it; post an entry to its "
            "job from there if the labor belongs on the job."
        )
    db.commit()
    return result
