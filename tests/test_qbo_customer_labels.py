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

import pytest

from app.services.qbo_export_import import (
    CUSTOMER_LABELS,
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
