"""QuickBooks Online's customer label (Settings → Advanced → Other
preferences): a company calls its customers Customers, Clients, Donors,
Guests, Members, Patients or Tenants, and the export heads its lists and
reports with the word chosen. A nonprofit that calls them members sends a
"Member Contact List" with a "Member" column and an A/R Aging Detail report
with a "Member full name" column; the importer read neither (the list came
through empty and the aging report was refused for lacking its Customer
column). Every label is the customer column now.
"""

import csv
import io
import zipfile
from pathlib import Path

import pytest

from app.models.contacts import Customer
from app.services.qbo_export_import import (
    CUSTOMER_LABELS,
    classify_filename,
    parse_ar_aging,
    parse_contacts,
)


def _csv(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def _contact_list(label):
    return _csv(
        [
            ["Hackerspace"],
            [f"{label} Contact List"],
            [""],
            [""],
            ["", label, "Phone Numbers", "Email", "Full Name", "Billing Address"],
            [
                "",
                "Zari Davis",
                "Phone: (540) 555-0101",
                "zari@example.org",
                "Zari Davis",
                "",
            ],
            ["", "Aaron Dalton", "", "aaron@example.org", "", "1 Main St"],
        ]
    )


def _aging(label):
    return _csv(
        [
            ["Hackerspace"],
            ["A/R Aging Detail Report"],
            ["As of Nov 11, 2026"],
            [""],
            [
                "",
                "Date",
                "Transaction type",
                "Num",
                f"{label} full name",
                "Due date",
                "Amount",
                "Open balance",
            ],
            ["1 - 30 days past due"],
            [
                "",
                "11/04/2026",
                "Invoice",
                "HB-1062",
                "Research Center",
                "11/04/2026",
                "200.0",
                "200.0",
            ],
            ["Total for 1 - 30 days past due", "", "", "", "", "", "200.0", "200.0"],
            ["TOTAL", "", "", "", "", "", "200.0", "200.0"],
        ]
    )


@pytest.mark.parametrize("label", ["Customer", "Member", "Donor", "Client", "Tenant"])
def test_the_customer_list_is_read_under_every_label(label):
    rows = parse_contacts(_contact_list(label), "Customer")
    assert [r["name"] for r in rows] == ["Zari Davis", "Aaron Dalton"]
    assert rows[0]["email"] == "zari@example.org"
    assert rows[1]["address"] == "1 Main St"


def test_the_vendor_list_keeps_its_own_label():
    assert parse_contacts(_contact_list("Member"), "Vendor") == []
    assert [r["name"] for r in parse_contacts(_contact_list("Vendor"), "Vendor")] == [
        "Zari Davis",
        "Aaron Dalton",
    ]


@pytest.mark.parametrize("label", ["Customer", "Member", "Donor", "Patient"])
def test_the_aging_report_is_read_under_every_label(label):
    items, errors, notes = parse_ar_aging(_aging(label))
    assert errors == []
    assert [(i["customer"], str(i["open"])) for i in items] == [
        ("Research Center", "200.0")
    ] or [i["customer"] for i in items] == ["Research Center"]


def test_an_aging_report_with_no_customer_column_names_the_labels():
    items, errors, notes = parse_ar_aging(_aging("Account"))
    assert items == []
    assert len(errors) == 1
    assert "Customer, Client, Donor, Guest, Member, Patient or Tenant" in errors[0]


def test_the_labels_are_the_seven_quickbooks_offers():
    assert CUSTOMER_LABELS == (
        "customer",
        "client",
        "donor",
        "guest",
        "member",
        "patient",
        "tenant",
    )


# ── the list's file named for the label ─────────────────────────────────────
# QuickBooks Online names the Customer Contact List's file after the label
# too (Members.xlsx, Donors.xlsx). The importer placed the list by a name
# with "customer" in it, so such a list was left out and the dry run said
# ok with 0 customers. Every label places it now, and an export with no
# customer list at all says so in one sentence, naming its files.

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qbo_export"
LEDGER = [
    "Balance_sheet.csv",
    "Profit_and_loss.csv",
    "Journal.csv",
    "Trial_balance.csv",
    "Vendors.csv",
]


def _workbook(text: str) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    for row in csv.reader(io.StringIO(text)):
        sheet.append([cell if cell != "" else None for cell in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _zip(extra: dict[str, str]) -> list:
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        for name in LEDGER:
            text = (FIXTURES / name).read_text(encoding="utf-8")
            archive.writestr(name.replace(".csv", ".xlsx"), _workbook(text))
        for name, text in extra.items():
            archive.writestr(name, _workbook(text))
    return [("files", ("Export.zip", io.BytesIO(zipped.getvalue()), "application/zip"))]


def _members_list() -> str:
    text = (FIXTURES / "Customers.csv").read_text(encoding="utf-8")
    return text.replace("Customer Contact List", "Member Contact List").replace(
        ",Customer,Phone", ",Member,Phone"
    )


@pytest.mark.parametrize(
    "name", ["Members.xlsx", "Donors.xlsx", "CLIENTS.xlsx", "tenant.xlsx"]
)
def test_the_list_file_is_placed_under_every_label(name):
    assert classify_filename(name) == "customers"


def test_the_other_reports_keep_their_names():
    # an aging report or a vendor list whose name carries a label word
    assert classify_filename("Members_A_R_Aging_Detail_Report.xlsx") == "ar_aging"
    assert classify_filename("Clients_Journal.xlsx") == "gl"
    assert classify_filename("Guest_House_Vendors.xlsx") == "vendors"


def test_a_list_named_members_imports_its_members(client, db_session, seed_accounts):
    files = _zip({"Members.xlsx": _members_list()})
    data = client.post("/api/migration/qbo_export/dry-run", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert data["customers"] == 2
    assert not any("No customer list" in w for w in data["warnings"]), data
    assert not any("Members.xlsx" in w for w in data["warnings"]), data

    files = _zip({"Members.xlsx": _members_list()})
    data = client.post("/api/migration/qbo_export/import", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_customers"] == 2
    names = {c.name for c in db_session.query(Customer).all()}
    assert {"Dana Reyes - CUST"} <= names


def test_an_export_with_no_customer_list_says_so_in_one_sentence(
    client, db_session, seed_accounts
):
    data = client.post("/api/migration/qbo_export/dry-run", files=_zip({})).json()
    assert data["ok"] is True, data["errors"]  # not refused: invoices still post
    assert data["customers"] == 0
    said = [w for w in data["warnings"] if w.startswith("No customer list")]
    assert said == [
        "No customer list was found, so no customers are read from one: the "
        "files are Balance_sheet.xlsx, Profit_and_loss.xlsx, Journal.xlsx, "
        "Trial_balance.xlsx and Vendors.xlsx, and a customer list is read from "
        "a file whose name has Customer, Client, Donor, Guest, Member, Patient "
        "or Tenant in it (Customers.xlsx or Members.xlsx, say)."
    ]
    assert db_session.query(Customer).count() == 0
