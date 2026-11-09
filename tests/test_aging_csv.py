"""Save CSV on the A/R and A/P Aging reports (#393): the file the
Accountant's Package carries, on its own, so a client who asks for the
aging as a spreadsheet does not cost the whole zip."""

import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bills import Bill, BillStatus
from app.models.contacts import Customer, Vendor
from app.services.ledger_exports import rows_of

ROOT = Path(__file__).resolve().parents[1]
BOM = "﻿"
AS_OF = "2026-09-30"
AR_HEAD = [
    "Customer",
    "Current",
    "1-30",
    "31-60",
    "61-90+",
    "Total",
    "Unapplied credits",
]
AP_HEAD = ["Vendor", "Current", "1-30", "31-60", "61-90+", "Total"]


@pytest.fixture
def customers(db_session):
    acme = Customer(name="Acme Diner", is_active=True)
    muller = Customer(name="Bäckerei Müller", is_active=True)
    db_session.add_all([acme, muller])
    db_session.commit()
    return acme.id, muller.id


def _invoice(client, cid, rate):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "due_date": "2026-10-01",
            "tax_rate": 0,
            "lines": [{"description": "Signage", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill(db_session, name, amount, days_old):
    vendor = Vendor(name=name, is_active=True)
    db_session.add(vendor)
    db_session.flush()
    db_session.add(
        Bill(
            vendor_id=vendor.id,
            bill_number=f"B-{vendor.id}",
            date=date.fromisoformat(AS_OF) - timedelta(days=days_old),
            due_date=None,
            terms="Net 30",
            subtotal=Decimal(amount),
            tax_amount=Decimal("0"),
            total=Decimal(amount),
            amount_paid=Decimal("0"),
            balance_due=Decimal(amount),
            status=BillStatus.UNPAID,
        )
    )
    db_session.commit()
    return vendor.id


def _csv(client, path, **headers):
    r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    return r


def _package_file(client, name):
    r = client.get(
        "/api/reports/accountants-package?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content)).read(name).decode("utf-8-sig")


def test_ar_aging_saves_the_package_file(client, seed_accounts, customers):
    acme, muller = customers
    _invoice(client, acme, 400)
    _invoice(client, muller, 935)

    r = _csv(client, f"/api/reports/ar-aging/csv?as_of_date={AS_OF}")
    assert (
        r.headers["content-disposition"]
        == 'attachment; filename="ar-aging_2026-09-30.csv"; '
        "filename*=UTF-8''ar-aging_2026-09-30.csv"
    )
    assert r.text.startswith(BOM)
    rows = rows_of(r.text.lstrip(BOM))
    assert rows[0] == AR_HEAD
    assert rows[1] == ["Acme Diner", "400.00", "0.00", "0.00", "0.00", "400.00", "0.00"]
    assert rows[2][0] == "Bäckerei Müller" and rows[2][5] == "935.00"
    assert rows[3] == ["Total", "1335.00", "0.00", "0.00", "0.00", "1335.00", "0.00"]
    assert r.text.lstrip(BOM).splitlines()[1] == "Report,Accounts Receivable Aging"

    # byte for byte what the package writes for the same day
    on_screen = _csv(client, "/api/reports/ar-aging/csv?as_of_date=2026-12-31")
    assert on_screen.text.lstrip(BOM) == _package_file(
        client, "ar-aging_2026-12-31.csv"
    )


def test_ap_aging_saves_the_package_file(client, seed_accounts, db_session):
    _bill(db_session, "Cascade Flour Mill", "777.24", 75)  # Net 30: 45 days late
    _bill(db_session, "Puget Dairy", "120.00", 10)

    r = _csv(client, f"/api/reports/ap-aging/csv?as_of_date={AS_OF}")
    assert (
        r.headers["content-disposition"]
        == 'attachment; filename="ap-aging_2026-09-30.csv"; '
        "filename*=UTF-8''ap-aging_2026-09-30.csv"
    )
    rows = rows_of(r.text.lstrip(BOM))
    assert rows[0] == AP_HEAD
    assert rows[1] == ["Cascade Flour Mill", "0.00", "0.00", "777.24", "0.00", "777.24"]
    assert rows[2] == ["Puget Dairy", "120.00", "0.00", "0.00", "0.00", "120.00"]
    assert rows[3] == ["Total", "120.00", "0.00", "777.24", "0.00", "897.24"]
    assert r.text.lstrip(BOM).splitlines()[1] == "Report,Accounts Payable Aging"

    on_screen = _csv(client, "/api/reports/ap-aging/csv?as_of_date=2026-12-31")
    assert on_screen.text.lstrip(BOM) == _package_file(
        client, "ap-aging_2026-12-31.csv"
    )


def test_opened_from_a_customer_or_vendor_page_the_file_is_the_whole_company(
    client, seed_accounts, db_session, customers
):
    # The screen shows the whole company with that row highlighted, so the
    # file is the same: no narrowing, no party in the file name. The page
    # asks for the file by date alone; a customer_id or vendor_id on the
    # request is a filter the aging does not read, so it is refused with
    # the one it does, not dropped in silence.
    acme, muller = customers
    _invoice(client, acme, 400)
    _invoice(client, muller, 935)
    r = _csv(client, f"/api/reports/ar-aging/csv?as_of_date={AS_OF}")
    assert r.headers["content-disposition"].endswith(
        "filename*=UTF-8''ar-aging_2026-09-30.csv"
    )
    names = [row[0] for row in rows_of(r.text.lstrip(BOM))]
    assert names[0] == "Customer" and names[-1] == "Total"
    assert "Acme Diner" in names and len(names) > 3
    narrowed = client.get(
        f"/api/reports/ar-aging/csv?as_of_date={AS_OF}&customer_id={acme}"
    )
    assert narrowed.status_code == 422
    assert narrowed.json()["detail"] == (
        "customer_id is not a filter here. This request accepts: as_of_date."
    )

    cascade = _bill(db_session, "Cascade Flour Mill", "777.24", 75)
    _bill(db_session, "Puget Dairy", "120.00", 10)
    r = _csv(client, f"/api/reports/ap-aging/csv?as_of_date={AS_OF}")
    assert r.headers["content-disposition"].endswith(
        "filename*=UTF-8''ap-aging_2026-09-30.csv"
    )
    names = [row[0] for row in rows_of(r.text.lstrip(BOM))]
    assert "Cascade Flour Mill" in names and "Puget Dairy" in names
    narrowed = client.get(
        f"/api/reports/ap-aging/csv?as_of_date={AS_OF}&vendor_id={cascade}"
    )
    assert narrowed.status_code == 422
    assert "vendor_id is not a filter here" in narrowed.json()["detail"]


def test_the_desktop_shell_gets_the_file_inline(client, seed_accounts):
    r = _csv(
        client,
        f"/api/reports/ar-aging/csv?as_of_date={AS_OF}",
        **{"X-Slowbooks-Desktop": "1"},
    )
    assert r.headers["content-disposition"].startswith("inline; filename=")


def test_signed_out_is_refused(unauthed_client):
    for path in ("ar-aging/csv", "ap-aging/csv"):
        r = unauthed_client.get(f"/api/reports/{path}?as_of_date={AS_OF}")
        assert r.status_code == 401, path


def test_both_reports_offer_save_csv():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert "ReportsPage._exportButtons('ar-aging', csvQs, ['csv'])" in js
    assert "ReportsPage._exportButtons('ap-aging', csvQs, ['csv'])" in js
    assert (
        "customer_id"
        not in js.split("async arAging")[1]
        .split("async apAging")[0]
        .split("csvQs =")[1]
        .split("\n")[0]
    )
    assert (
        "vendor_id"
        not in js.split("async apAging")[1].split("csvQs =")[1].split("\n")[0]
    )
