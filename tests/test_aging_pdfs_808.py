"""The A/R and A/P Aging print (#808).

/api/reports/ar-aging/pdf was a 404 and the two aging pages offered Save CSV
only, where every other statement prints. Each aging now has a PDF in the
report theme, and a Print page beside it (#683): the as-of date, the
buckets, a row per customer or vendor and the totals, as the screen shows
them, on a letter or an A4 page.
"""

import io
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bills import Bill, BillStatus
from app.models.contacts import Customer, Vendor
from app.services.ledger_exports import aging_section
from app.services.settings_service import set_setting
from tests.test_wip_pdf_columns_760 import _runs

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-09-30"
LONG = "Northgate Print and Sign Shop of the Lower Valley Incorporated"


def _invoice(client, cid, rate, day, due):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": day,
            "due_date": due,
            "tax_rate": 0,
            "lines": [{"description": "Signage", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, cid, inv, amount, applied, day="2026-09-10"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": day,
            "amount": amount,
            "allocations": [{"invoice_id": inv["id"], "amount": applied}],
        },
    )
    assert r.status_code == 201, r.text


def _bill(db_session, vendor, amount, days_old, paid="0"):
    db_session.add(
        Bill(
            vendor_id=vendor.id,
            bill_number=f"B-{vendor.id}-{days_old}",
            date=date.fromisoformat(AS_OF) - timedelta(days=days_old),
            due_date=None,
            terms="Net 30",
            subtotal=Decimal(amount),
            tax_amount=Decimal("0"),
            total=Decimal(amount),
            amount_paid=Decimal(paid),
            balance_due=Decimal(amount) - Decimal(paid),
            status=BillStatus.PAID if Decimal(paid) else BillStatus.UNPAID,
        )
    )


@pytest.fixture
def books(client, db_session, seed_accounts):
    """Customers and vendors with money in every bucket, a credit a customer
    holds, a long unbroken-ish name with a seven-figure balance, and one
    customer and one vendor who owe nothing."""
    harbor = Customer(name="Harbor Light Bakery", is_active=True)
    north = Customer(name=LONG, is_active=True)
    quiet = Customer(name="Quiet Hollow Farm", is_active=True)
    db_session.add_all([harbor, north, quiet])
    flour = Vendor(name="Cascade Flour Mill", is_active=True)
    dairy = Vendor(name="Puget Dairy", is_active=True)
    settled = Vendor(name="Settled Paper Supply", is_active=True)
    db_session.add_all([flour, dairy, settled])
    db_session.commit()

    _invoice(client, harbor.id, 400, "2026-09-01", "2026-10-01")  # current
    late = _invoice(client, harbor.id, 300, "2026-07-01", "2026-08-15")  # 31-60
    _pay(client, harbor.id, late, 350, 300)  # 50 held as a credit
    _invoice(client, north.id, 1234567.89, "2026-04-01", "2026-05-01")  # 90+
    _invoice(client, north.id, 2500, "2026-08-01", "2026-09-10")  # 1-30
    paid = _invoice(client, quiet.id, 125, "2026-08-01", "2026-08-31")
    _pay(client, quiet.id, paid, 125, 125)  # paid in full: nothing due

    _bill(db_session, flour, "777.24", 75)  # Net 30: 45 days late
    _bill(db_session, dairy, "120.00", 10)
    _bill(db_session, dairy, "98765.43", 140)
    _bill(db_session, settled, "60.00", 20, paid="60.00")  # paid: nothing due
    db_session.commit()


def _pdf(client, db_session, side, size):
    set_setting(db_session, "pdf_paper_size", size)
    db_session.commit()
    r = client.get(f"/api/reports/{side}-aging/pdf?as_of_date={AS_OF}")
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == (
        f'inline; filename="{side}-aging_{AS_OF}.pdf"'
    )
    assert r.content[:5] == b"%PDF-"
    return r.content


def _text(pdf_bytes, pypdf):
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def _money_cells(section):
    return [c for row in section["rows"] for c in row["cells"][1:] if c]


@pytest.mark.parametrize("size", ["letter", "a4"])
@pytest.mark.parametrize("side", ["ar", "ap"])
def test_the_aging_pdf_prints_every_row_and_total_the_report_gives(
    client, db_session, books, side, size
):
    pypdf = pytest.importorskip("pypdf")
    data = client.get(f"/api/reports/{side}-aging?as_of_date={AS_OF}").json()
    section = aging_section(data, side)
    pdf = _pdf(client, db_session, side, size)
    text = _text(pdf, pypdf)
    title = "Accounts Receivable Aging" if side == "ar" else "Accounts Payable Aging"
    assert title in text
    assert f"As of {AS_OF}" in text
    for heading in ("CURRENT", "1-30", "31-60", "61-90+", "TOTAL"):
        assert heading in text, heading
    # every figure of every row, and of the totals, on a page: placed by its
    # x inside the page's width, so none ran past the right edge
    on_page = [run[1] for run in _runs(pdf, pypdf) if 0 <= run[2] < run[3]]
    for cell in _money_cells(section):
        assert cell in on_page, (cell, on_page)
    name_key = "customer_name" if side == "ar" else "vendor_name"
    for item in data["items"]:
        assert item[name_key].split()[0] in text, item[name_key]


def test_the_ar_rows_are_the_screens(client, books):
    """Current before the credits, the credits on their own, the total as the
    report gives it; a customer with nothing due is not on the sheet."""
    data = client.get(f"/api/reports/ar-aging?as_of_date={AS_OF}").json()
    section = aging_section(data, "ar")
    assert section["columns"] == [
        "Customer",
        "Current",
        "1-30",
        "31-60",
        "61-90+",
        "Credits",
        "Total",
    ]
    assert section["period"] == f"As of {AS_OF}"
    by_name = {r["cells"][0]: r["cells"] for r in section["rows"]}
    assert by_name["Harbor Light Bakery"] == [
        "Harbor Light Bakery",
        "$400.00",
        "$0.00",
        "$0.00",
        "$0.00",
        "-$50.00",
        "$350.00",
    ]
    assert by_name[LONG] == [
        LONG,
        "$0.00",
        "$2,500.00",
        "$0.00",
        "$1,234,567.89",
        "",
        "$1,237,067.89",
    ]
    assert "Quiet Hollow Farm" not in by_name
    total = section["rows"][-1]
    assert total["style"] == "grand-total"
    assert total["cells"] == [
        "Total",
        "$400.00",
        "$2,500.00",
        "$0.00",
        "$1,234,567.89",
        "-$50.00",
        "$1,237,417.89",
    ]
    assert Decimal(str(data["totals"]["total"])) == Decimal("1237417.89")


def test_the_ap_rows_are_the_screens(client, books):
    data = client.get(f"/api/reports/ap-aging?as_of_date={AS_OF}").json()
    section = aging_section(data, "ap")
    assert section["columns"] == [
        "Vendor",
        "Current",
        "1-30",
        "31-60",
        "61-90+",
        "Total",
    ]
    by_name = {r["cells"][0]: r["cells"] for r in section["rows"]}
    assert by_name["Cascade Flour Mill"] == [
        "Cascade Flour Mill",
        "$0.00",
        "$0.00",
        "$777.24",
        "$0.00",
        "$777.24",
    ]
    assert by_name["Puget Dairy"] == [
        "Puget Dairy",
        "$120.00",
        "$0.00",
        "$0.00",
        "$98,765.43",
        "$98,885.43",
    ]
    assert "Settled Paper Supply" not in by_name
    assert section["rows"][-1]["cells"] == [
        "Total",
        "$120.00",
        "$0.00",
        "$777.24",
        "$98,765.43",
        "$99,662.67",
    ]


def test_a_customer_and_a_vendor_with_nothing_due_are_not_printed(
    client, db_session, books
):
    pypdf = pytest.importorskip("pypdf")
    ar = _text(_pdf(client, db_session, "ar", "letter"), pypdf)
    ap = _text(_pdf(client, db_session, "ap", "letter"), pypdf)
    assert "Harbor Light Bakery" in ar and "Quiet Hollow" not in ar
    assert "Puget Dairy" in ap and "Settled Paper" not in ap


def test_the_print_page_is_the_pdfs_sheet(client, books):
    for side, name in (("ar", "Harbor Light Bakery"), ("ap", "Puget Dairy")):
        r = client.get(f"/api/reports/{side}-aging/print?as_of_date={AS_OF}")
        assert r.status_code == 200, r.text[:300]
        assert r.headers["content-type"].startswith("text/html")
        assert name in r.text and f"As of {AS_OF}" in r.text
        assert "window.print()" in r.text


def test_a_nonprofit_prints_its_own_words(client, books):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    page = client.get(f"/api/reports/ar-aging/print?as_of_date={AS_OF}").text
    assert "Pledges Receivable Aging" in page
    assert ">Donor<" in page


def test_a_filter_the_aging_does_not_read_is_refused_by_name(client, books):
    r = client.get(f"/api/reports/ar-aging/pdf?as_of_date={AS_OF}&customer_id=1")
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "customer_id is not a filter here. This request accepts: as_of_date."
    )


def test_signed_out_is_refused(unauthed_client):
    for path in ("ar-aging/pdf", "ap-aging/pdf", "ar-aging/print", "ap-aging/print"):
        r = unauthed_client.get(f"/api/reports/{path}?as_of_date={AS_OF}")
        assert r.status_code == 401, path


def test_the_csv_is_unchanged(client, books):
    """Save CSV writes what it wrote before: the Accountant's Package file."""
    from app.services.ledger_exports import rows_of

    r = client.get(f"/api/reports/ar-aging/csv?as_of_date={AS_OF}")
    assert r.status_code == 200
    rows = rows_of(r.text.lstrip("﻿"))
    head = next(i for i, row in enumerate(rows) if row and row[0] == "Customer")
    assert rows[head] == [
        "Customer",
        "Current",
        "1-30",
        "31-60",
        "61-90+",
        "Total",
        "Unapplied credits",
    ]
    by_name = {row[0]: row for row in rows[head + 1 :]}
    # Current net of the credit, as the file always carried it
    assert by_name["Harbor Light Bakery"] == [
        "Harbor Light Bakery",
        "350.00",
        "0.00",
        "0.00",
        "0.00",
        "350.00",
        "50.00",
    ]
    r = client.get(f"/api/reports/ap-aging/csv?as_of_date={AS_OF}")
    rows = rows_of(r.text.lstrip("﻿"))
    assert ["Vendor", "Current", "1-30", "31-60", "61-90+", "Total"] in rows


def test_both_aging_pages_offer_print_and_save_pdf():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    ar = js.split("async arAging")[1].split("async apAging")[0]
    ap = js.split("async apAging")[1].split("async trialBalance")[0]
    assert "ReportsPage._exportButtons('ar-aging', csvQs)" in ar
    assert "ReportsPage._exportButtons('ap-aging', csvQs)" in ap
