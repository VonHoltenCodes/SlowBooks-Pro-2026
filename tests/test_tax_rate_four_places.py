"""A tax rate to four places of a percent (2.18.0).

New York City's sales tax is 8.875%, and rates like 7.0625% are common. A
document kept its rate as Numeric(5, 4), a percent to two places, and every
Tax Rate field stepped in hundredths, so 8.875% could not be typed. One sent
through the API was taxed at 8.875% when the document was saved ($88.75 on
$1,000.00), then stored as 8.88% (PostgreSQL) or read back as 8.87%
(SQLite): the next edit, duplicate, conversion or recurring run worked the
tax out again as $88.80 or $88.70, and the PDF printed 8.88%.

A document's rate is now Numeric(7, 6), a fraction to six places and a
percent to four (migration d3d40d716684). It is entered, stored, used,
shown and printed as typed on every document that has one.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.contacts import Vendor
from app.services import pdf_service

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-09-10"
PERIOD = "start_date=2026-09-01&end_date=2026-09-30"
# What the forms send: the typed percent divided by 100 in floating point.
NYC = 8.875 / 100
SEVEN = 7.0625 / 100
THOUSAND = {"description": "Catering, 40 guests", "quantity": 1, "rate": 1000}


def _ok(r):
    assert r.status_code in (200, 201), r.text
    return r.json()


def _money(value):
    return Decimal(str(value))


def _vendor(db_session, seed_accounts):
    v = Vendor(
        name="Cascade Flour Mill",
        is_active=True,
        default_expense_account_id=seed_accounts["5000"].id,
    )
    db_session.add(v)
    db_session.commit()
    return v.id


def _printed(client, monkeypatch, path):
    """The document's PDF (as the HTML it is made from) and its print
    preview."""
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    pdf = client.get(f"{path}/pdf")
    preview = client.get(f"{path}/print-preview")
    assert pdf.status_code == 200 and preview.status_code == 200
    return pdf.text + preview.text


def _bill_from(client, po_path):
    made = _ok(client.post(f"{po_path}/convert-to-bill"))
    return client.get(f"/api/bills/{made.get('bill_id') or made['id']}").json()


def _invoice(client, customer_id, rate, **extra):
    body = {"customer_id": customer_id, "date": DAY, "tax_rate": rate}
    body.update({"lines": [THOUSAND]}, **extra)
    return _ok(client.post("/api/invoices", json=body))


def test_an_invoice_at_8_875_percent_stays_at_88_75(
    client, seed_accounts, seed_customer, monkeypatch
):
    inv = _invoice(client, seed_customer.id, NYC)
    assert _money(inv["tax_amount"]) == Decimal("88.75")
    assert client.get(f"/api/invoices/{inv['id']}").json()["tax_rate"] == "0.08875"

    # Saved again, the tax is worked out from the stored rate, and the
    # ledger holds what the invoice says.
    again = _ok(client.put(f"/api/invoices/{inv['id']}", json={"lines": [THOUSAND]}))
    assert _money(again["tax_amount"]) == Decimal("88.75")
    assert _money(again["total"]) == Decimal("1088.75")
    ledger = client.get(f"/api/reports/sales-tax?{PERIOD}").json()["ledger"]
    assert ledger["tax_posted"] == 88.75 and ledger["difference"] == 0

    printed = _printed(client, monkeypatch, f"/api/invoices/{inv['id']}")
    assert printed.count("Tax (8.875%)") == 2

    # A duplicate works it out from the stored rate too.
    dup = _ok(client.post(f"/api/invoices/{inv['id']}/duplicate"))
    assert dup["tax_rate"] == "0.08875"
    assert _money(dup["tax_amount"]) == Decimal("88.75")

    # A rate with four places or fewer reads as it always has.
    plain = _invoice(client, seed_customer.id, 0.0825)
    assert plain["tax_rate"] == "0.0825"
    assert _invoice(client, seed_customer.id, 0)["tax_rate"] == "0.0000"
    assert "Tax (8.25%)" in _printed(
        client, monkeypatch, f"/api/invoices/{plain['id']}"
    )


def test_an_estimate_and_the_invoice_it_becomes(
    client, seed_accounts, seed_customer, monkeypatch
):
    body = {"customer_id": seed_customer.id, "date": DAY, "lines": [THOUSAND]}
    est = _ok(client.post("/api/estimates", json=dict(body, tax_rate=NYC)))
    assert est["tax_rate"] == "0.08875"
    assert _money(est["tax_amount"]) == Decimal("88.75")

    path = f"/api/estimates/{est['id']}"
    again = _ok(client.put(path, json={"lines": [THOUSAND]}))
    assert _money(again["tax_amount"]) == Decimal("88.75")
    # a new rate alone re-totals the stored lines: 7.0625% is $70.625, $70.63
    seven = _ok(client.put(path, json={"tax_rate": SEVEN}))
    assert seven["tax_rate"] == "0.070625"
    assert _money(seven["tax_amount"]) == Decimal("70.63")
    assert "Tax (7.0625%)" in _printed(client, monkeypatch, path)

    _ok(client.put(path, json={"tax_rate": NYC}))
    inv = _ok(client.post(f"{path}/convert"))
    assert inv["tax_rate"] == "0.08875"
    assert _money(inv["tax_amount"]) == Decimal("88.75")
    assert _money(inv["total"]) == Decimal("1088.75")


def test_a_credit_memo_gives_back_the_tax_the_invoice_charged(
    client, seed_accounts, seed_customer, monkeypatch
):
    inv = _invoice(client, seed_customer.id, NYC)
    cm = _ok(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": seed_customer.id,
                "date": DAY,
                "original_invoice_id": inv["id"],
                "lines": [THOUSAND],
            },
        )
    )
    assert cm["tax_rate"] == "0.08875"
    assert _money(cm["tax_amount"]) == Decimal("88.75")
    assert "Tax (8.875%)" in _printed(
        client, monkeypatch, f"/api/credit-memos/{cm['id']}"
    )

    own = _ok(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": seed_customer.id,
                "date": DAY,
                "tax_rate": SEVEN,
                "lines": [THOUSAND],
            },
        )
    )
    assert own["tax_rate"] == "0.070625"
    assert _money(own["tax_amount"]) == Decimal("70.63")


def test_a_bill_and_a_purchase_order_keep_the_vendors_rate(
    client, db_session, seed_accounts, monkeypatch
):
    vid = _vendor(db_session, seed_accounts)
    body = {"vendor_id": vid, "date": DAY, "lines": [THOUSAND]}
    bill = _ok(client.post("/api/bills", json=dict(body, tax_rate=NYC)))
    assert bill["tax_rate"] == "0.08875"
    assert _money(bill["tax_amount"]) == Decimal("88.75")
    assert _money(bill["total"]) == Decimal("1088.75")
    assert "Tax (8.875%)" in _printed(client, monkeypatch, f"/api/bills/{bill['id']}")

    po = _ok(client.post("/api/purchase-orders", json=dict(body, tax_rate=NYC)))
    path = f"/api/purchase-orders/{po['id']}"
    again = _ok(client.put(path, json={"lines": [THOUSAND]}))
    assert _money(again["tax_amount"]) == Decimal("88.75")
    assert "Tax (8.875%)" in _printed(client, monkeypatch, path)

    converted = _bill_from(client, path)
    assert converted["tax_rate"] == "0.08875"
    assert _money(converted["tax_amount"]) == Decimal("88.75")
    assert _money(converted["total"]) == Decimal("1088.75")


def test_a_new_rate_alone_retotals_a_purchase_order(
    client, db_session, seed_accounts, monkeypatch
):
    """A rate sent without the lines changed the order's rate and kept its
    old tax and total, and the bill the order became carried that tax."""
    vid = _vendor(db_session, seed_accounts)
    po = _ok(
        client.post(
            "/api/purchase-orders",
            json={"vendor_id": vid, "date": DAY, "tax_rate": NYC, "lines": [THOUSAND]},
        )
    )
    path = f"/api/purchase-orders/{po['id']}"
    seven = _ok(client.put(path, json={"tax_rate": SEVEN}))
    assert seven["tax_rate"] == "0.070625"
    assert _money(seven["tax_amount"]) == Decimal("70.63")
    assert _money(seven["total"]) == Decimal("1070.63")
    assert "Tax (7.0625%)" in _printed(client, monkeypatch, path)

    converted = _bill_from(client, path)
    assert converted["tax_rate"] == "0.070625"
    assert _money(converted["tax_amount"]) == Decimal("70.63")
    assert _money(converted["total"]) == Decimal("1070.63")


def test_a_recurring_template_bills_at_its_rate(client, seed_accounts, seed_customer):
    rec = _ok(
        client.post(
            "/api/recurring",
            json={
                "customer_id": seed_customer.id,
                "frequency": "monthly",
                "start_date": DAY,
                "tax_rate": NYC,
                "lines": [THOUSAND],
            },
        )
    )
    assert client.get(f"/api/recurring/{rec['id']}").json()["tax_rate"] == 0.08875
    run = _ok(client.post("/api/recurring/generate?as_of=2026-09-26"))
    made = client.get(f"/api/invoices/{run['invoice_ids'][0]}").json()
    assert made["tax_rate"] == "0.08875"
    assert _money(made["tax_amount"]) == Decimal("88.75")
    assert _money(made["total"]) == Decimal("1088.75")


def test_a_sales_receipt_and_a_vendor_credit_at_7_0625_percent(
    client, db_session, seed_accounts, seed_customer
):
    sr = _ok(
        client.post(
            "/api/sales-receipts",
            json={
                "customer_id": seed_customer.id,
                "date": DAY,
                "tax_rate": SEVEN,
                "lines": [THOUSAND],
            },
        )
    )["invoice"]
    assert sr["tax_rate"] == "0.070625"
    assert _money(sr["tax_amount"]) == Decimal("70.63")
    assert client.get(f"/api/invoices/{sr['id']}").json()["tax_rate"] == "0.070625"

    vid = _vendor(db_session, seed_accounts)
    vc = _ok(
        client.post(
            "/api/vendor-credits",
            json={
                "vendor_id": vid,
                "date": DAY,
                "tax_rate": SEVEN,
                "lines": [THOUSAND],
            },
        )
    )
    assert vc["tax_rate"] == "0.070625"
    assert _money(vc["tax_amount"]) == Decimal("70.63")
    assert _money(vc["total"]) == Decimal("1070.63")


def test_a_default_tax_rate_takes_four_places_and_no_more(
    client, seed_accounts, seed_customer
):
    for typed in ("7.0625", "8.875"):
        r = client.put("/api/settings", json={"default_tax_rate": typed})
        assert r.status_code == 200, r.text
        assert client.get("/api/settings").json()["default_tax_rate"] == typed

    r = client.put("/api/settings", json={"default_tax_rate": "8.87501"})
    assert r.status_code == 422, r.text
    assert "Default tax rate can have up to four decimal places" in r.json()["detail"]
    assert client.get("/api/settings").json()["default_tax_rate"] == "8.875"

    # A new invoice takes the default the way the form sends it.
    default = client.get("/api/settings").json()["default_tax_rate"]
    inv = _invoice(client, seed_customer.id, float(default) / 100)
    assert inv["tax_rate"] == "0.08875"
    assert _money(inv["tax_amount"]) == Decimal("88.75")


def test_the_sales_tax_report_carries_the_rate(client, seed_accounts, seed_customer):
    _invoice(client, seed_customer.id, NYC)
    rep = client.get(f"/api/reports/sales-tax?{PERIOD}").json()
    assert [(i["tax_rate"], i["tax_amount"]) for i in rep["items"]] == [
        (0.08875, 88.75)
    ]


def test_a_quickbooks_report_brings_its_rate(db_session, seed_accounts):
    """A receipt from a QuickBooks "Transaction Detail by Date" report: the
    tax row carries the rate in Sales Price."""
    from app.models.invoices import Invoice
    from app.services.qb_report_import import import_sales_receipt_report

    header = (
        ",Date,Num,Name,Memo,Item,Item Description,Account,Class,Clr,Split,"
        "Qty,Sales Price,Debit,Credit,Amount,Balance"
    )
    report = "\n".join(
        [
            header,
            ',09/10/26,41,Dana Park,,,,Checking,,,-SPLIT-,,,"1,088.75",,0.00,0.00',
            ",09/10/26,41,Dana Park,catering,Catering,catering,Sales,,,Checking,"
            '-1,"1,000.00",,"1,000.00","-1,000.00","-1,000.00"',
            ",09/10/26,41,NYS Dept of Taxation,Sales Tax,NYC,Sales Tax,"
            'Sales Tax Payable,,,Checking,,8.875%,,88.75,-88.75,"-1,088.75"',
            ",09/11/26,42,Eli Stone,,,,Checking,,,-SPLIT-,,,107.06,,0.00,0.00",
            ",09/11/26,42,Eli Stone,cake,Cake,cake,Sales,,,Checking,"
            "-1,100.00,,100.00,-100.00,-100.00",
            ",09/11/26,42,Dept of Revenue,Sales Tax,ST,Sales Tax,"
            "Sales Tax Payable,,,Checking,,7.0625%,,7.06,-7.06,-107.06",
        ]
    )
    result = import_sales_receipt_report(db_session, report)
    assert result["errors"] == [] and result["imported"] == 2, result
    db_session.expire_all()
    got = {
        r.invoice_number: (r.tax_rate, r.tax_amount, r.total)
        for r in db_session.query(Invoice).filter(Invoice.is_sales_receipt)
    }
    assert got == {
        "41": (Decimal("0.08875"), Decimal("88.75"), Decimal("1088.75")),
        "42": (Decimal("0.070625"), Decimal("7.06"), Decimal("107.06")),
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_forms_take_and_show_four_places(
    client, db_session, seed_accounts, seed_customer
):
    """Every Tax Rate field steps in ten-thousandths and opens with the rate
    as it was typed; the forms work tax out to the cent as the server does;
    the Sales Tax report and a scanned receipt keep four places."""
    _ok(client.put("/api/settings", json={"default_tax_rate": "8.875"}))
    inv = _invoice(client, seed_customer.id, NYC)
    _invoice(client, seed_customer.id, SEVEN)
    _invoice(client, seed_customer.id, 0.0825)
    body = {"customer_id": seed_customer.id, "date": DAY, "lines": [THOUSAND]}
    est = _ok(client.post("/api/estimates", json=dict(body, tax_rate=SEVEN)))
    rec = _ok(
        client.post(
            "/api/recurring",
            json={
                "customer_id": seed_customer.id,
                "frequency": "monthly",
                "start_date": DAY,
                "tax_rate": NYC,
                "lines": [THOUSAND],
            },
        )
    )
    vid = _vendor(db_session, seed_accounts)
    po = _ok(
        client.post(
            "/api/purchase-orders",
            json={"vendor_id": vid, "date": DAY, "tax_rate": NYC, "lines": [THOUSAND]},
        )
    )
    given = {
        "settings": client.get("/api/settings").json(),
        "invoice": client.get(f"/api/invoices/{inv['id']}").json(),
        "estimate": client.get(f"/api/estimates/{est['id']}").json(),
        "recurring": client.get(f"/api/recurring/{rec['id']}").json(),
        "po": client.get(f"/api/purchase-orders/{po['id']}").json(),
        "invoices": client.get(f"/api/invoices?customer_id={seed_customer.id}").json(),
        "report": client.get(f"/api/reports/sales-tax?{PERIOD}").json(),
    }
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "tax_rate_forms_probe.js")],
        cwd=ROOT,
        input=json.dumps(given),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)

    def four(value):
        return {"step": "0.0001", "value": value}

    assert got["forms"] == {
        "invoiceNew": four("8.875"),
        "invoiceEdit": four("8.875"),
        "estimateEdit": four("7.0625"),
        "recurringEdit": four("8.875"),
        "salesReceiptNew": four("8.875"),
        "creditMemoNew": four("8.875"),
        "purchaseOrderEdit": four("8.875"),
        "vendorCreditNew": four("0"),
    }
    assert got["newInvoiceTotals"] == {"subtotal": 1000, "tax": 88.75, "total": 1088.75}
    assert got["creditMemoFromInvoice"] == "8.875"
    # $175,800.00 at 1.0875% is $1,911.825 and $1,290.00 at 6.35% is
    # $81.915: half a cent, rounded up as the server rounds it
    assert got["salesTax"] == [88.75, 70.63, 1911.83, 81.92]
    # 8.25% of $102.00 is $8.415; a purchase order showed $8.41
    assert got["purchaseTax"] == ["$8.42", "$88.75", "$19.78"]
    assert got["reportRates"] == ["8.875%", "7.0625%", "8.25%"]
    assert got["percent"] == ["8.875", "7.0625", "8.25", "7.00", "0.00"]
    assert got["scanned"] == [8.875, 7.063, 8.875]
    assert got["scannedIntoForm"] == {
        "value": "8.875",
        "note": "— tax rate set to 8.875%.",
    }


def test_an_old_default_with_more_places_does_not_hold_back_settings():
    # A default saved before 2.18 with five decimals would fail the field's
    # step (0.0001) and block Save Settings for the whole page.
    import json as _json
    import shutil as _shutil
    import subprocess as _subprocess

    if _shutil.which("node") is None:
        pytest.skip("node is not installed")
    probe = (
        "const fs=require('fs'),vm=require('vm');"
        "const ctx={console,window:{},document:{addEventListener:()=>{}}};"
        "vm.createContext(ctx);"
        "vm.runInContext(fs.readFileSync('app/static/js/settings.js','utf8')"
        "+'\\nthis.S=SettingsPage;',ctx);"
        "console.log(JSON.stringify(['8.87512','8.875','7','','abc',null]"
        ".map(v=>ctx.S._ratePercent(v))));"
    )
    out = _subprocess.run(
        ["node", "-e", probe],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert _json.loads(out.stdout) == ["8.8751", "8.875", "7", "0", "0.0", "0"]
