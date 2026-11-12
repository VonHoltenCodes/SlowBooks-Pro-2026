"""Line quantities keep four decimal places (2.23.0 gate, NEW-94).

``invoice_lines.quantity`` (and every other sale and purchase line's) was
Numeric(10, 2). The form kept to two places, but the IIF import and the
QuickBooks report import stored the source's 2.125, and every read gave
2.12 (SQLite) or 2.13 (PostgreSQL): the edit form showed 2.12 with a total
40 cents short, so saving any edit took them off the invoice, and the IIF
export wrote QNTY -2.12 beside AMOUNT -170.00. Now Numeric(12, 4), as unit
prices went to four places in 2.17.3 (6f57f762f464): the imports keep the
source's quantity, the API and the documents show it as entered, a finer
one is refused on input and rounded with a note on import.
"""

import io
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _invoice(client, customer_id, qty, rate="80.00", **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-09-14",
            "tax_rate": "0",
            "lines": [{"description": "Boards", "quantity": qty, "rate": rate}],
            **extra,
        },
    )
    return r


def test_a_quantity_finer_than_the_hundredth_is_kept_and_shown_as_entered(
    client, seed_accounts, seed_customer
):
    r = _invoice(client, seed_customer.id, "2.125")
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["lines"][0]["quantity"] == "2.125"
    assert float(inv["total"]) == 170.0  # 2.125 × 80, to the cent
    # an ordinary quantity reads as it always did
    plain = _invoice(client, seed_customer.id, "3").json()
    assert plain["lines"][0]["quantity"] == "3.00"
    listed = {i["id"]: i for i in client.get("/api/invoices").json()}
    assert float(listed[inv["id"]]["total"]) == 170.0


def test_an_edit_keeps_the_quantity_and_the_total(client, seed_accounts, seed_customer):
    inv = _invoice(client, seed_customer.id, "2.125").json()
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {"description": "Boards, edited", "quantity": "2.125", "rate": "80.00"}
            ]
        },
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert got["lines"][0]["quantity"] == "2.125" and float(got["total"]) == 170.0


def test_finer_than_four_places_is_refused_on_every_line_form(
    client, seed_accounts, seed_customer
):
    r = _invoice(client, seed_customer.id, "2.12345")
    assert r.status_code == 422, r.text
    assert "four decimal places" in r.text
    vendor = client.post("/api/vendors", json={"name": "Mill"}).json()
    b = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-09-14",
            "lines": [
                {
                    "description": "Lumber",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 1.00001,
                    "rate": "10.00",
                    "line_order": 0,
                }
            ],
        },
    )
    assert b.status_code == 422 and "four decimal places" in b.text
    ok = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-09-14",
            "lines": [
                {
                    "description": "Lumber",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 2.125,
                    "rate": "10.00",
                    "line_order": 0,
                }
            ],
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["lines"][0]["quantity"] == "2.125"
    assert float(ok.json()["total"]) == 21.25


def test_the_iif_round_trip_and_the_export_keep_the_quantity(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice

    inv = _invoice(client, seed_customer.id, "2.125").json()
    text = client.get("/api/iif/export/invoices").text
    spl = [
        ln
        for ln in text.replace("\r\n", "\n").split("\n")
        if ln.startswith("SPL\t") and f"\t{inv['invoice_number']}\t" in ln
    ]
    assert len(spl) == 1 and spl[0].endswith("\t\t-2.125\t80\tY"), spl
    assert "\t-170.00\t" in spl[0]
    again = text.replace(f"\t{inv['invoice_number']}\t", "\tRT-8125\t")
    r = client.post(
        "/api/iif/import",
        files={"file": ("rt.iif", io.BytesIO(again.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["invoices"] == 1 and r.json()["errors"] == []
    db_session.expire_all()
    back = db_session.query(Invoice).filter_by(invoice_number="RT-8125").one()
    assert Decimal(str(back.lines[0].quantity)) == Decimal("2.125")
    assert float(back.total) == 170.0


def test_a_finer_quantity_in_a_file_is_rounded_with_a_note(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice

    inv = _invoice(client, seed_customer.id, "2").json()
    text = client.get("/api/iif/export/invoices").text
    finer = text.replace(f"\t{inv['invoice_number']}\t", "\tRT-5P\t").replace(
        "\t-2\t80\tY", "\t-2.12345\t80\tY"
    )
    assert "-2.12345" in finer
    r = client.post(
        "/api/iif/import",
        files={"file": ("rt.iif", io.BytesIO(finer.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invoices"] == 1
    assert any("quantity 2.12345 kept as 2.1235" in w for w in body["warnings"]), body
    db_session.expire_all()
    back = db_session.query(Invoice).filter_by(invoice_number="RT-5P").one()
    assert Decimal(str(back.lines[0].quantity)) == Decimal("2.1235")
    assert float(back.total) == 160.0  # the file's amount, not 2.1235 × 80


def test_the_report_import_keeps_a_finer_quantity(client, db_session, seed_accounts):
    from app.models.invoices import Invoice
    from app.services.qb_report_import import import_invoice_report

    fixture = (ROOT / "tests/fixtures/qb_transaction_detail_invoices.csv").read_text(
        encoding="utf-8"
    )
    # 1031's door line: 3 × 400.00 = 1,200.00 becomes 2.125 × 564.71 = 1,200.00
    finer = fixture.replace(
        'Service Income,,,Accounts Receivable,-3,"400.00",,"1,200.00","-1,200.00"',
        'Service Income,,,Accounts Receivable,-2.125,"564.71",,"1,200.00","-1,200.00"',
        1,
    )
    assert finer != fixture
    client.post(
        "/api/items",
        json={
            "name": "Exterior door",
            "item_type": "service",
            "rate": "400.00",
            "income_account_id": seed_accounts["4000"].id,
        },
    )
    result = import_invoice_report(db_session, finer)
    assert result["imported"] == 3, result
    inv = db_session.query(Invoice).filter_by(invoice_number="1031").one()
    door = sorted(inv.lines, key=lambda ln: ln.line_order)[0]
    assert Decimal(str(door.quantity)) == Decimal("2.125")
    assert float(door.amount) == 1200.0 and float(inv.total) == 1350.0


def test_the_documents_print_the_quantity_as_entered():
    """The five documents with a quantity column printed it raw, which
    would read 2.1250 once the column was widened; they go through the
    qty filter, which shows it as the API does."""
    from app.services.pdf_service import _format_qty, _jinja_env

    assert _jinja_env.filters["qty"] is _format_qty
    assert [_format_qty(v) for v in (Decimal("2.1250"), Decimal("3.0000"), "1.5")] == [
        "2.125",
        "3.00",
        "1.50",
    ]
    for name in (
        "invoice_pdf",
        "estimate_pdf",
        "credit_memo_pdf",
        "bill_pdf",
        "purchase_order_pdf",
    ):
        html = (ROOT / f"app/templates/{name}.html").read_text(encoding="utf-8")
        assert (
            "{{ line.quantity | qty }}" in html and "{{ line.quantity }}" not in html
        ), name


def test_a_rounded_quantity_keeps_the_line_consistent(
    client, db_session, seed_accounts, seed_customer
):
    """Round 4 rounded 1.00005 to 1.0001 and kept the file's 1,000.05 beside
    PRICE 1000, so the edit form worked out 1,000.10 and the export wrote
    QNTY -1.0001 PRICE 1000 AMOUNT -1000.05 (2.23.0 gate, NEW-95). The unit
    price now follows the amount: 1.0001 × 999.95 is 1,000.05."""
    from app.models.invoices import Invoice

    inv = _invoice(client, seed_customer.id, "1", rate="1000.00").json()
    text = client.get("/api/iif/export/invoices").text
    finer = (
        text.replace(f"\t{inv['invoice_number']}\t", "\tRT-8127\t")
        .replace("\t-1000.00\t", "\t-1000.05\t")
        .replace("\t-1\t1000\tY", "\t-1.00005\t1000\tY")
    )
    assert "-1.00005" in finer and "-1000.05" in finer
    r = client.post(
        "/api/iif/import",
        files={"file": ("rt.iif", io.BytesIO(finer.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert any(
        "1.00005 kept as 1.0001" in w and "unit price" in w
        for w in r.json()["warnings"]
    )
    db_session.expire_all()
    back = db_session.query(Invoice).filter_by(invoice_number="RT-8127").one()
    line = back.lines[0]
    assert (Decimal(str(line.quantity)), Decimal(str(line.rate))) == (
        Decimal("1.0001"),
        Decimal("999.95"),
    )
    assert float(line.amount) == 1000.05 and float(back.total) == 1000.05
    assert (Decimal(str(line.quantity)) * Decimal(str(line.rate))).quantize(
        Decimal("0.01")
    ) == Decimal("1000.05")
    # the edit form's own arithmetic, saved, leaves the total where it was
    e = client.put(
        f"/api/invoices/{back.id}",
        json={
            "lines": [{"description": "Boards", "quantity": "1.0001", "rate": "999.95"}]
        },
    )
    assert e.status_code == 200, e.text
    assert float(client.get(f"/api/invoices/{back.id}").json()["total"]) == 1000.05
    # and the export multiplies out
    row = [
        ln
        for ln in client.get("/api/iif/export/invoices")
        .text.replace("\r\n", "\n")
        .split("\n")
        if ln.startswith("SPL\t") and "\tRT-8127\t" in ln
    ][0]
    assert row.endswith("\t-1.0001\t999.95\tY") and "\t-1000.05\t" in row


def test_the_report_import_prices_a_line_so_quantity_times_price_is_its_amount(
    client, db_session, seed_accounts
):
    """The report path derived the rate as amount ÷ quantity to the cent:
    2.125 × 564.71 is 1,200.01, not the report's 1,200.00 (NEW-95). The
    report's Sales Price is kept when it reproduces the amount; otherwise
    the rate is the amount ÷ the quantity to four places."""
    from app.models.invoices import Invoice
    from app.services.qb_report_import import import_invoice_report

    fixture = (ROOT / "tests/fixtures/qb_transaction_detail_invoices.csv").read_text(
        encoding="utf-8"
    )
    finer = fixture.replace(
        'Service Income,,,Accounts Receivable,-3,"400.00",,"1,200.00","-1,200.00"',
        'Service Income,,,Accounts Receivable,-2.125,"564.71",,"1,200.00","-1,200.00"',
        1,
    )
    client.post(
        "/api/items",
        json={
            "name": "Exterior door",
            "item_type": "service",
            "rate": "400.00",
            "income_account_id": seed_accounts["4000"].id,
        },
    )
    assert import_invoice_report(db_session, finer)["imported"] == 3
    by_num = {i.invoice_number: i for i in db_session.query(Invoice).all()}
    door = sorted(by_num["1031"].lines, key=lambda ln: ln.line_order)[0]
    assert (Decimal(str(door.quantity)), Decimal(str(door.rate))) == (
        Decimal("2.125"),
        Decimal("564.7059"),
    )
    assert (Decimal(str(door.quantity)) * Decimal(str(door.rate))).quantize(
        Decimal("0.01")
    ) == Decimal("1200.00")
    # a line whose Sales Price reproduces its amount keeps that price
    labor = sorted(by_num["1031"].lines, key=lambda ln: ln.line_order)[1]
    assert (float(labor.quantity), float(labor.rate), float(labor.amount)) == (
        1.5,
        100.0,
        150.0,
    )
