"""Writes Time_by_Name_by_Job.xlsx (and, with --by-job, Time_by_Job_by_Name
.xlsx): invented timesheets in the exact layout of QuickBooks Desktop's Time
by Name by Job detail report saved as Excel (nine columns: four label
columns, the person in the second and the customer:job in the third, then
Date, Item, Billing Status, Duration, Notes; a "Total <label>" row under each
group at both levels; a TOTAL row; the header row first and a near-empty
second row). Run it again when the entries below change. The sample company
is Northline Surveying; its people, customers and jobs are invented."""

import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import openpyxl

# (person, customer:job, date, item, billing status, hours, notes)
ENTRIES = [
    (
        "Alvarez, M",
        "Northline Overhead",
        "01/02/2024",
        "Indirect - Admin:Email/Phone",
        "Not Billable",
        0.5,
        "",
    ),
    (
        "Alvarez, M",
        "Northline Overhead",
        "01/02/2024",
        "Indirect - Admin:Timesheets",
        "Not Billable",
        0.25,
        "",
    ),
    (
        "Alvarez, M",
        "Northline Overhead",
        "01/03/2024",
        "Indirect - Admin:Training",
        "Not Billable",
        2,
        "Drone pilot recert",
    ),
    (
        "Alvarez, M",
        "Harbor Dist:Pier Survey",
        "01/03/2024",
        "Field Survey",
        "Billed",
        5.5,
        "Pier 4 control points",
    ),
    (
        "Alvarez, M",
        "Harbor Dist:Pier Survey",
        "01/04/2024",
        "Field Survey",
        "Billed",
        7,
        "Pier 4 topo",
    ),
    (
        "Alvarez, M",
        "Harbor Dist:Pier Survey",
        "01/05/2024",
        "Drafting",
        "Billed",
        3.25,
        "Topo sheet 1",
    ),
    (
        "Alvarez, M",
        "Harbor Dist:Boundary Plat",
        "01/08/2024",
        "Field Survey",
        "Unbilled",
        6,
        "Monument search",
    ),
    (
        "Alvarez, M",
        "Harbor Dist:Boundary Plat",
        "01/09/2024",
        "Drafting",
        "Unbilled",
        4,
        "Plat draft",
    ),
    (
        "Alvarez, M",
        "Town of Elm:Storm Mapping",
        "01/10/2024",
        "Field Survey",
        "Billed",
        8,
        "Inlet inventory, north half",
    ),
    (
        "Alvarez, M",
        "Town of Elm:Storm Mapping",
        "01/11/2024",
        "Field Survey",
        "Billed",
        8,
        "Inlet inventory, south half",
    ),
    (
        "Chen, R",
        "Northline Overhead",
        "01/02/2024",
        "Indirect - Admin:Email/Phone",
        "Not Billable",
        1,
        "",
    ),
    (
        "Chen, R",
        "Northline Overhead",
        "01/02/2024",
        "Indirect - Admin:Internal Mtgs",
        "Not Billable",
        1.5,
        "Stand up, Q1 planning",
    ),
    (
        "Chen, R",
        "Northline Overhead",
        "01/09/2024",
        "Indirect - Admin:Internal Mtgs",
        "Not Billable",
        1,
        "Stand up",
    ),
    (
        "Chen, R",
        "Harbor Dist:Pier Survey",
        "01/03/2024",
        "Project Management",
        "Billed",
        1.5,
        "Scope call with the district",
    ),
    (
        "Chen, R",
        "Harbor Dist:Pier Survey",
        "01/05/2024",
        "Drafting",
        "Billed",
        4.75,
        "Topo sheet 2, QC",
    ),
    (
        "Chen, R",
        "Harbor Dist:Pier Survey",
        "01/08/2024",
        "Project Management",
        "Billed",
        0.75,
        "Invoice prep",
    ),
    (
        "Chen, R",
        "Town of Elm:Storm Mapping",
        "01/10/2024",
        "Drafting",
        "Unbilled",
        6,
        "GIS layer build",
    ),
    (
        "Chen, R",
        "Town of Elm:Storm Mapping",
        "01/12/2024",
        "Drafting",
        "Unbilled",
        6,
        "GIS layer QC",
    ),
    (
        "Chen, R",
        "Town of Elm:Storm Mapping",
        "01/12/2024",
        "Drafting",
        "Unbilled",
        6,
        "GIS layer QC",
    ),  # a true duplicate row
    (
        "Okafor, T",
        "Northline Overhead",
        "01/02/2024",
        "Indirect - Admin:Timesheets",
        "Not Billable",
        0.25,
        "",
    ),
    (
        "Okafor, T",
        "Northline Overhead",
        "01/04/2024",
        "Indirect - Admin:Training",
        "Not Billable",
        3,
        "Safety course",
    ),
    (
        "Okafor, T",
        "Harbor Dist:Boundary Plat",
        "01/08/2024",
        "Field Survey",
        "Unbilled",
        6,
        "Monument search, with M",
    ),
    (
        "Okafor, T",
        "Harbor Dist:Boundary Plat",
        "01/09/2024",
        "Field Survey",
        "Unbilled",
        7.5,
        "Traverse",
    ),
    (
        "Okafor, T",
        "Harbor Dist:Boundary Plat",
        "01/10/2024",
        "Field Survey",
        "Unbilled",
        2.5,
        "Traverse close",
    ),
    (
        "Okafor, T",
        "Town of Elm:Storm Mapping",
        "02/01/2024",
        "Field Survey",
        "Billed",
        8,
        "Outfall check",
    ),
    (
        "Okafor, T",
        "Town of Elm:Storm Mapping",
        "02/02/2024",
        "Field Survey",
        "Billed",
        4.5,
        "Outfall check, east",
    ),
]
COLS = 9
PERSON, JOB = 1, 2
DATE, ITEM, BILLING, HOURS, NOTES = 4, 5, 6, 7, 8


def build(by_job: bool) -> list[list]:
    # the outer heading is always the second column, the inner the third:
    # the report indents by nesting, whichever way round it is grouped
    outer_i, inner_i = PERSON, JOB
    groups: OrderedDict = OrderedDict()
    for person, job, day, item, billing, hours, notes in ENTRIES:
        outer, inner = (job, person) if by_job else (person, job)
        groups.setdefault(outer, OrderedDict()).setdefault(inner, []).append(
            (day, item, billing, hours, notes)
        )
    rows = [[""] * COLS, [" "] + [""] * (COLS - 1)]
    rows[0][DATE : NOTES + 1] = ["Date", "Item", "Billing Status", "Duration", "Notes"]
    grand = 0
    for outer, inners in groups.items():
        row = [""] * COLS
        row[outer_i] = outer
        rows.append(row)
        outer_total = 0
        for inner, items in inners.items():
            row = [""] * COLS
            row[inner_i] = inner
            rows.append(row)
            inner_total = 0
            for day, item, billing, hours, notes in items:
                row = [""] * COLS
                row[DATE] = datetime.strptime(day, "%m/%d/%Y")
                row[ITEM], row[BILLING], row[HOURS], row[NOTES] = (
                    item,
                    billing,
                    hours,
                    notes,
                )
                rows.append(row)
                inner_total += hours
            row = [""] * COLS
            row[inner_i], row[HOURS] = f"Total {inner}", inner_total
            rows.append(row)
            outer_total += inner_total
        row = [""] * COLS
        row[outer_i], row[HOURS] = f"Total {outer}", outer_total
        rows.append(row)
        grand += outer_total
    row = [""] * COLS
    row[0], row[HOURS] = "TOTAL", grand
    rows.append(row)
    return rows


def write(by_job: bool) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    for row in build(by_job):
        sheet.append([None if cell == "" else cell for cell in row])
    name = "Time_by_Job_by_Name.xlsx" if by_job else "Time_by_Name_by_Job.xlsx"
    path = Path(__file__).with_name(name)
    book.save(path)
    return path


if __name__ == "__main__":
    for by_job in (False, True) if "--both" in sys.argv else ("--by-job" in sys.argv,):
        print(write(by_job))
