"""A vendor's payment history (#569): every bill payment, direct expense
and bank-feed entry to the vendor, in a date range (year to date unless
asked otherwise) with a running total, and the same list as a CSV through
the CSV export service. The bill-payment subtotal is summed by the 1099
query itself (form_1099.paid_bill_payments), so for a 1099 vendor over a
year it is the figure on the 1099-NEC; expenses and bank entries, which the
1099 leaves out, are listed and subtotalled beside it rather than folded in.
The list's search box (#335) and the list route's `search` cover name,
company, email and phone."""

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bills import BillPayment
from app.models.contacts import Vendor
from app.services.csv_export import VENDOR_PAYMENT_COLUMNS
from app.services.form_1099 import compute_1099_data

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/vendors.js").read_text(encoding="utf-8")
YEAR = date.today().year


@pytest.fixture(autouse=True)
def _this_year():
    """The app reads its own today on every request, so the test's year is
    read when each test starts, not once at import: a run that crosses New
    Year's must not leave the later tests a year behind the app."""
    global YEAR
    YEAR = date.today().year


def _vendor(db_session, name="Blue Heron Installs", **extra):
    v = Vendor(name=name, **extra)
    db_session.add(v)
    db_session.commit()
    return v


def _bill_payment(db_session, vendor, day, amount, voided=False):
    db_session.add(
        BillPayment(
            vendor_id=vendor.id,
            date=day,
            amount=Decimal(amount),
            method="check",
            check_number=f"1{amount[:2]}",
            is_voided=voided,
        )
    )
    db_session.commit()


def _expense(client, seed_accounts, vendor, day, amount, memo="", reference=""):
    r = client.post(
        "/api/expenses",
        json={
            "date": day.isoformat(),
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": amount,
            "memo": memo,
            "reference": reference,
            "vendor_id": vendor.id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_the_bill_payment_subtotal_for_a_year_is_the_1099_figure(
    client, db_session, seed_accounts
):
    v = _vendor(db_session, is_1099_vendor=True, vendor_1099_type="NEC")
    _bill_payment(db_session, v, date(YEAR, 2, 10), "400.00")
    _bill_payment(db_session, v, date(YEAR, 6, 1), "350.00")
    _bill_payment(db_session, v, date(YEAR, 7, 1), "999.00", voided=True)
    _bill_payment(db_session, v, date(YEAR - 1, 12, 20), "100.00")  # last year
    _expense(client, seed_accounts, v, date(YEAR, 3, 3), "25.50", memo="Blades")

    nec = next(d for d in compute_1099_data(db_session, YEAR) if d["vendor_id"] == v.id)
    assert nec["total_paid"] == Decimal("750.00")

    h = client.get(
        f"/api/vendors/{v.id}/payment-history",
        params={"start_date": f"{YEAR}-01-01", "end_date": f"{YEAR}-12-31"},
    ).json()
    # the 1099 counts bill payments; the history's bill-payment subtotal is
    # that figure, and the expense is beside it, not inside it
    assert Decimal(h["totals"]["bill_payment"]) == nec["total_paid"]
    assert h["totals"]["expense"] == "25.50"
    assert h["total"] == "775.50"
    assert h["all_time"] == "875.50"  # last year's payment too
    kinds = [(r["date"], r["kind"], r["amount"], r["is_voided"]) for r in h["rows"]]
    assert kinds == [
        (f"{YEAR}-02-10", "bill_payment", "400.00", False),
        (f"{YEAR}-03-03", "expense", "25.50", False),
        (f"{YEAR}-06-01", "bill_payment", "350.00", False),
        (f"{YEAR}-07-01", "bill_payment", "999.00", True),
    ]
    # the running total skips the voided row and ends on the total
    assert [r["running_total"] for r in h["rows"]] == [
        "400.00",
        "425.50",
        "775.50",
        "775.50",
    ]
    assert h["rows"][0]["link"] == f"/#/bill-payments/{h['rows'][0]['id']}"
    assert h["rows"][1]["link"] == f"/#/expenses/{h['rows'][1]['id']}"
    assert h["rows"][1]["memo"] == "Blades" and h["rows"][1]["paid_from"]


def test_the_range_is_year_to_date_unless_asked(client, db_session, seed_accounts):
    v = _vendor(db_session)
    # January 1 is in the year to date whatever day the run is on
    _bill_payment(db_session, v, date(YEAR, 1, 1), "10.00")
    _bill_payment(db_session, v, date(YEAR - 1, 6, 5), "20.00")
    h = client.get(f"/api/vendors/{v.id}/payment-history").json()
    assert (
        h["start_date"] == f"{YEAR}-01-01" and h["end_date"] == date.today().isoformat()
    )
    assert h["total"] == "10.00" and h["all_time"] == "30.00"
    # one date leaves the other side open
    h = client.get(
        f"/api/vendors/{v.id}/payment-history", params={"end_date": f"{YEAR - 1}-12-31"}
    ).json()
    assert h["start_date"] is None and h["total"] == "20.00"
    assert client.get("/api/vendors/99999/payment-history").status_code == 404


def test_a_voided_expense_is_listed_marked_and_not_counted(
    client, db_session, seed_accounts
):
    v = _vendor(db_session, name="Harbor Hardware")
    # dated January 1 so both are in the year to date on any day of the year
    kept = _expense(
        client, seed_accounts, v, date(YEAR, 1, 1), "30.30", reference="rcpt 7"
    )
    gone = _expense(client, seed_accounts, v, date(YEAR, 1, 1), "12.00", memo="Twice")
    assert client.post(f"/api/expenses/{gone}/void").status_code == 200
    h = client.get(f"/api/vendors/{v.id}/payment-history").json()
    assert [(r["id"], r["is_voided"]) for r in h["rows"]] == [
        (kept, False),
        (gone, True),
    ]
    assert h["total"] == "30.30" and h["totals"]["expense"] == "30.30"
    assert h["rows"][0]["reference"] == "rcpt 7"


def test_the_csv_is_the_history_with_a_running_total_column(
    client, db_session, seed_accounts
):
    v = _vendor(db_session, name="Harbor Hardware")
    _bill_payment(db_session, v, date(YEAR, 2, 10), "400.00")
    _expense(client, seed_accounts, v, date(YEAR, 3, 3), "25.50", memo="Blades")
    r = client.get(
        f"/api/vendors/{v.id}/payments/csv",
        params={"start_date": f"{YEAR}-01-01", "end_date": f"{YEAR}-12-31"},
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == ["Vendor", "Harbor Hardware"]
    assert rows[1] == ["Period", f"{YEAR}-01-01 to {YEAR}-12-31"]
    assert (
        rows[3]
        == VENDOR_PAYMENT_COLUMNS
        == [
            "Date",
            "Type",
            "Method",
            "Check # / Ref",
            "Amount",
            "Running total",
            "Paid from",
            "Notes",
            "Voided",
        ]
    )
    assert rows[4][:6] == [
        f"{YEAR}-02-10",
        "Bill payment",
        "check",
        "140",
        "400.00",
        "400.00",
    ]
    assert rows[5][:2] == [f"{YEAR}-03-03", "Expense"]
    assert rows[5][4:6] == ["25.50", "425.50"] and rows[5][7] == "Blades"
    assert rows[6][3:5] == ["Total paid", "425.50"]


def test_the_page_reads_the_history_and_exports_it_through_the_same_route():
    assert "API.get(`/vendors/${id}/payment-history`)" in JS
    assert "/api/vendors/${id}/payments/csv?start_date=" in JS
    assert (
        'id="vend-hist-from-${id}"' in JS and "VendorsPage._reloadHistory(${id})" in JS
    )
    assert "Running total" in JS and "Paid this year" not in JS.split("_historyHtml")[0]
    assert "Paid ${escapeHtml(label)}" in JS


def test_the_list_search_covers_name_company_email_and_phone(client, db_session):
    for name, extra in (
        ("Cascade Flour Mill", {"company": "Cascade Milling LLC"}),
        ("Harbor Hardware", {"email": "orders@harborhw.example"}),
        ("Blue Heron Installs", {"phone": "555-0199"}),
    ):
        db_session.add(Vendor(name=name, **extra))
    db_session.commit()

    def names(q):
        return [
            v["name"] for v in client.get("/api/vendors", params={"search": q}).json()
        ]

    assert names("milling") == ["Cascade Flour Mill"]
    assert names("harborhw") == ["Harbor Hardware"]
    assert names("0199") == ["Blue Heron Installs"]
    assert names("flour") == ["Cascade Flour Mill"]
    # and the list page filters as you type on the same four fields (#335)
    assert "`${v.name} ${v.company || ''} ${v.phone || ''} ${v.email || ''}`" in JS
