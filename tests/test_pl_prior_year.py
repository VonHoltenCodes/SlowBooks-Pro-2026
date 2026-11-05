"""Profit & Loss compared with the same period one year earlier
(compare=prior_year): the screen's data, the PDF and the CSV."""

from datetime import date

from app.routes.reports.financial import _year_earlier, pct_change


def _mk_invoice(client, customer_id, amount, date_):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": date_,
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": [
                {
                    "description": "Service",
                    "quantity": "1",
                    "rate": amount,
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text


def _mk_bill(client, db_session, account_id, amount, date_, number):
    from app.models.contacts import Vendor

    v = db_session.query(Vendor).filter_by(name="PY Vendor").first()
    if v is None:
        v = Vendor(name="PY Vendor", is_active=True)
        db_session.add(v)
        db_session.commit()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "bill_number": number,
            "date": date_,
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [
                {
                    "description": "Supplies",
                    "quantity": 1,
                    "rate": amount,
                    "account_id": account_id,
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text


def test_year_earlier_dates():
    assert _year_earlier(date(2026, 4, 15)) == date(2025, 4, 15)
    assert _year_earlier(date(2026, 1, 1)) == date(2025, 1, 1)
    # month ends stay month ends, so a full February compares to a full one
    assert _year_earlier(date(2025, 2, 28)) == date(2024, 2, 29)
    assert _year_earlier(date(2024, 2, 29)) == date(2023, 2, 28)
    assert _year_earlier(date(2026, 6, 30)) == date(2025, 6, 30)


def test_pct_change():
    assert pct_change(150, 100) == 50.0
    assert pct_change(50, 100) == -50.0
    assert pct_change(100, 0) is None
    assert pct_change(-50, -100) == 50.0  # a loss that shrank is an improvement


def test_pl_compare_prior_year(client, db_session, seed_accounts, seed_customer):
    # This year: $300 income, $250 expense. Last year, same months: $200
    # income and no expenses.
    _mk_invoice(client, seed_customer.id, "300.00", "2026-03-10")
    _mk_invoice(client, seed_customer.id, "200.00", "2025-03-10")
    # outside the prior window — must not count
    _mk_invoice(client, seed_customer.id, "999.00", "2025-07-01")
    _mk_bill(client, db_session, seed_accounts["6400"].id, 250.00, "2026-04-01", "B-26")

    url = "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-06-30"
    plain = client.get(url).json()
    assert "prior" not in plain
    assert all("prior_amount" not in i for i in plain["income"])

    r = client.get(url + "&compare=prior_year")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["compare"] == "prior_year"
    assert d["prior"]["start_date"] == "2025-01-01"
    assert d["prior"]["end_date"] == "2025-06-30"

    # current figures are unchanged by comparing
    assert d["total_income"] == plain["total_income"] == 300.0
    assert d["net_income"] == plain["net_income"]
    assert d["prior"]["total_income"] == 200.0
    assert d["prior"]["total_expenses"] == 0.0
    assert d["prior"]["net_income"] == 200.0

    service = next(i for i in d["income"] if i["account_name"] == "Service Income")
    assert service["amount"] == 300.0 and service["prior_amount"] == 200.0
    supplies = next(
        i for i in d["expenses"] if i["account_id"] == seed_accounts["6400"].id
    )
    assert supplies["amount"] == 250.0 and supplies["prior_amount"] == 0.0


def test_account_only_in_prior_year_still_listed(
    client, db_session, seed_accounts, seed_customer
):
    _mk_bill(client, db_session, seed_accounts["6400"].id, 80.00, "2025-02-01", "B-25")
    d = client.get(
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-03-31&compare=prior_year"
    ).json()
    row = next(i for i in d["expenses"] if i["account_id"] == seed_accounts["6400"].id)
    assert row["amount"] == 0.0 and row["prior_amount"] == 80.0
    assert d["total_expenses"] == 0.0
    assert d["prior"]["total_expenses"] == 80.0


def test_compare_rejects_unknown_value(client, seed_accounts):
    r = client.get("/api/reports/profit-loss?compare=last_month")
    assert r.status_code == 422


def test_pl_csv_with_prior_year(client, seed_accounts, seed_customer):
    _mk_invoice(client, seed_customer.id, "300.00", "2026-03-10")
    _mk_invoice(client, seed_customer.id, "200.00", "2025-03-10")
    r = client.get(
        "/api/reports/profit-loss/csv?start_date=2026-01-01&end_date=2026-06-30&compare=prior_year"
    )
    assert r.status_code == 200, r.text
    text = r.text
    assert "Current,Prior year,$ Change,% Change" in text
    assert "compared with 2025-01-01 to 2025-06-30" in text
    line = next(l for l in text.splitlines() if "Total " in l and "300.00" in l)
    assert line.endswith("300.00,200.00,100.00,50.0%")

    plain = client.get(
        "/api/reports/profit-loss/csv?start_date=2026-01-01&end_date=2026-06-30"
    ).text
    assert "Prior year" not in plain


def test_pl_pdf_with_prior_year(client, seed_accounts, seed_customer, monkeypatch):
    from app.routes.reports import financial

    captured = {}

    def fake_pdf(sections, db, filename, landscape=False):
        captured["sections"] = sections
        return {"ok": True}

    monkeypatch.setattr(financial, "_pdf_response", fake_pdf)
    _mk_invoice(client, seed_customer.id, "300.00", "2026-03-10")
    _mk_invoice(client, seed_customer.id, "200.00", "2025-03-10")
    r = client.get(
        "/api/reports/profit-loss/pdf?start_date=2026-01-01&end_date=2026-06-30&compare=prior_year"
    )
    assert r.status_code == 200, r.text
    sec = captured["sections"][0]
    assert sec["columns"] == ["", "Current", "Prior Year", "$ Change", "% Change"]
    assert "compared with 2025-01-01 — 2025-06-30" in sec["period"]
    assert all(len(row["cells"]) == 5 for row in sec["rows"])
    net = sec["rows"][-1]["cells"]
    assert net[1:] == ["$300.00", "$200.00", "$100.00", "50.0%"]


# --- the $ and % change columns can be turned off -------------------------


def test_pl_exports_without_change_columns(
    client, seed_accounts, seed_customer, monkeypatch
):
    from app.routes.reports import financial

    _mk_invoice(client, seed_customer.id, "300.00", "2026-03-10")
    _mk_invoice(client, seed_customer.id, "200.00", "2025-03-10")
    qs = "start_date=2026-01-01&end_date=2026-06-30&compare=prior_year&hide_changes=1"

    text = client.get(f"/api/reports/profit-loss/csv?{qs}").text
    assert "Current,Prior year" in text and "$ Change" not in text
    line = next(l for l in text.splitlines() if "Total " in l and "300.00" in l)
    assert line.endswith("300.00,200.00")

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/profit-loss/pdf?{qs}")
    sec = captured["s"][0]
    assert sec["columns"] == ["", "Current", "Prior Year"]
    assert all(len(r["cells"]) == 3 for r in sec["rows"])


# --- Balance Sheet: as of a date compared with the same date last year ----


def test_balance_sheet_compare_prior_year(client, seed_accounts, seed_customer):
    _mk_invoice(client, seed_customer.id, "200.00", "2025-03-10")
    _mk_invoice(client, seed_customer.id, "300.00", "2026-03-10")

    url = "/api/reports/balance-sheet?as_of_date=2026-06-30"
    plain = client.get(url).json()
    assert "prior" not in plain

    r = client.get(url + "&compare=prior_year")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["compare"] == "prior_year"
    assert d["prior"]["as_of_date"] == "2025-06-30"
    # current figures unchanged by comparing
    assert d["total_assets"] == plain["total_assets"] == 500.0
    assert d["prior"]["total_assets"] == 200.0

    ar = next(a for a in d["assets"] if a["account_number"] == "1100")
    assert ar["amount"] == 500.0 and ar["prior_amount"] == 200.0

    # the computed net-income line lines up across the two years by name,
    # and stays at the end of equity
    # (2.31.7, #708: this fiscal year's net income; the years before it are
    # Retained Earnings, the line above, so the two still add to 500)
    ni = d["equity"][-1]
    assert ni["account_id"] is None and ni["line"] == "net_income"
    assert ni["amount"] == 300.0 and ni["prior_amount"] == 200.0
    re_line = d["equity"][-2]
    assert re_line["account_id"] is None and re_line["line"] == "retained_earnings"
    assert re_line["amount"] == 200.0 and not re_line.get("prior_amount")
    assert d["prior"]["total_equity"] == 200.0


def test_balance_sheet_compare_rejects_unknown_value(client, seed_accounts):
    assert client.get("/api/reports/balance-sheet?compare=yesterday").status_code == 422


def test_balance_sheet_exports_with_prior_year(
    client, seed_accounts, seed_customer, monkeypatch
):
    from app.routes.reports import financial

    _mk_invoice(client, seed_customer.id, "200.00", "2025-03-10")
    _mk_invoice(client, seed_customer.id, "300.00", "2026-03-10")
    qs = "as_of_date=2026-06-30&compare=prior_year"

    text = client.get(f"/api/reports/balance-sheet/csv?{qs}").text
    assert "Current,Prior year,$ Change,% Change" in text
    assert "As of 2026-06-30 compared with 2025-06-30" in text
    line = next(l for l in text.splitlines() if l.startswith("Assets,,Total Assets"))
    assert line.endswith("500.00,200.00,300.00,150.0%,Assets")

    short = client.get(f"/api/reports/balance-sheet/csv?{qs}&hide_changes=1").text
    assert "$ Change" not in short

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/balance-sheet/pdf?{qs}")
    sec = captured["s"][0]
    assert sec["columns"] == ["", "Current", "Prior Year", "$ Change", "% Change"]
    assert "compared with 2025-06-30" in sec["period"]
    assert sec["rows"][-1]["cells"][1:3] == ["$500.00", "$200.00"]

    plain = client.get("/api/reports/balance-sheet/csv?as_of_date=2026-06-30").text
    assert "Prior year" not in plain


def test_a_fall_in_the_csv_is_a_number_not_guarded_text():
    """2.26.0 gate, W-29 / NEW-171: a falling % Change was written '-12.3%
    (the formula guard's apostrophe on a text cell) while a rise was 62.8%."""
    import csv
    import io

    from app.services.ledger_exports import _SafeWriter, _cmp_cells

    buf = io.StringIO()
    _SafeWriter(buf).writerow(["Rent"] + _cmp_cells(87.70, 100, True, True))
    _SafeWriter(buf).writerow(["Sales"] + _cmp_cells(162.80, 100, True, True))
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    assert rows[0] == ["Rent", "87.70", "100.00", "-12.30", "-12.3%"]
    assert rows[1] == ["Sales", "162.80", "100.00", "62.80", "62.8%"]
