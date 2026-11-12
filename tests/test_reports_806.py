"""Reports (#806): the Trial Balance can be saved, the Sales Tax report
prints and exports, and the Trial Balance takes as_of_date.

The Trial Balance was the one statement the Saved Reports list refused;
the Sales Tax report had its JSON and its Pay route only, so it could not
be printed or saved as a PDF or a spreadsheet; and a client sending the
Balance Sheet's as_of_date to the Trial Balance got today's trial balance
without a word."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import pdf_service
from app.services.accounting import create_journal_entry
from app.services.ledger_exports import rows_of
from tests.test_sales_tax_report import PERIOD, _sales

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")


@pytest.fixture
def rendered(monkeypatch):
    """The HTML each PDF is rendered from, without running WeasyPrint."""
    pages = []

    def write(html_str):
        pages.append(html_str)
        return b"%PDF-1.7 stand-in"

    monkeypatch.setattr(pdf_service, "_write_pdf", write)
    return pages


def _activity(db_session, accounts):
    create_journal_entry(
        db_session,
        date(2026, 6, 15),
        "Cash sale",
        [
            {
                "account_id": accounts["1000"].id,
                "debit": Decimal("125.50"),
                "credit": Decimal("0"),
            },
            {
                "account_id": accounts["4000"].id,
                "debit": Decimal("0"),
                "credit": Decimal("125.50"),
            },
        ],
    )
    db_session.commit()


# ── The Trial Balance is saved and opens ─────────────────────────────────


def test_a_trial_balance_is_saved_with_its_parameters_and_opens_with_them(
    client, db_session, seed_accounts
):
    _activity(db_session, seed_accounts)
    params = {
        "period": "custom",
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "basis": "cash",
        "hide_zero": "1",
    }
    r = client.post(
        "/api/saved-reports",
        json={
            "name": "Half-year TB",
            "report_type": "trial_balance",
            "parameters": params,
        },
    )
    assert r.status_code == 201, r.text
    saved = client.get(f"/api/saved-reports/{r.json()['id']}").json()
    assert saved["report_type"] == "trial_balance"
    assert saved["parameters"] == params

    # what the Report Center opens it with: the trial-balance view, run on
    # the saved dates and basis
    p = saved["parameters"]
    tb = client.get(
        "/api/reports/trial-balance",
        params={
            "start_date": p["start_date"],
            "end_date": p["end_date"],
            "basis": p["basis"],
            "hide_zero": "true",
        },
    )
    assert tb.status_code == 200, tb.text
    body = tb.json()
    assert body["end_date"] == "2026-06-30" and body["basis"] == "cash"
    assert Decimal(str(body["total_debit"])) == Decimal("125.50")


def test_the_trial_balance_view_has_the_save_button_and_a_saved_one_opens_it():
    start = JS.index("async trialBalance(prefill)")
    body = JS[start : JS.index("async adjustingEntries(prefill)", start)]
    assert "reportType: 'trial_balance', view: 'trial-balance'" in body
    # a saved report opens the view its type names, underscores to hyphens
    assert "String(saved.report_type).replace(/_/g, '-')" in JS
    assert "'trial-balance':" in JS


# ── The Sales Tax report prints and exports ──────────────────────────────


def test_the_sales_tax_pdf_renders_with_the_screens_rows_and_totals(
    client, seed_accounts, seed_customer, rendered
):
    taxed, untaxed, cm = _sales(client, seed_customer.id)
    r = client.get(f"/api/reports/sales-tax/pdf?{PERIOD}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert (
        'filename="sales-tax_2026-07-01_2026-07-31.pdf"'
        in r.headers["content-disposition"]
    )
    page = rendered[0]
    assert "Sales Tax Report" in page
    assert "2026-07-01 — 2026-07-31" in page
    for number in (taxed["invoice_number"], untaxed["invoice_number"]):
        assert number in page
    assert f"{cm['memo_number']} (credit memo)" in page
    assert "8.25%" in page
    for figure in ("$280.00", "$80.00", "$200.00", "$8.25", "-$1.65", "$6.60"):
        assert figure in page, figure
    assert "Tax collected" in page and "2200 Sales Tax Payable: tax posted" in page


def test_the_sales_tax_pdf_names_the_cash_basis(
    client, seed_accounts, seed_customer, rendered
):
    _sales(client, seed_customer.id)
    r = client.get(f"/api/reports/sales-tax/pdf?{PERIOD}&basis=cash")
    assert r.status_code == 200, r.text
    assert "(cash basis)" in rendered[0]


def test_the_sales_tax_pdf_is_a_real_weasyprint_render(
    client, seed_accounts, seed_customer
):
    _sales(client, seed_customer.id)
    r = client.get(f"/api/reports/sales-tax/pdf?{PERIOD}")
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"


def test_the_sales_tax_print_page_is_the_pdfs_page(
    client, seed_accounts, seed_customer, rendered
):
    _sales(client, seed_customer.id)
    page = client.get(f"/api/reports/sales-tax/print?{PERIOD}")
    assert page.status_code == 200, page.text
    assert page.headers["content-type"].startswith("text/html")
    assert "Sales Tax Report" in page.text and "window.print()" in page.text


def test_the_sales_tax_csv_has_the_rows_and_the_totals(
    client, seed_accounts, seed_customer
):
    taxed, untaxed, cm = _sales(client, seed_customer.id)
    r = client.get(f"/api/reports/sales-tax/csv?{PERIOD}")
    assert r.status_code == 200, r.text
    assert "sales-tax_2026-07-01_2026-07-31.csv" in r.headers["content-disposition"]
    rows = rows_of(r.text)
    assert rows[0] == [
        "Date",
        "Type",
        "Number",
        "Customer",
        "Sales",
        "Taxable",
        "Rate",
        "Tax",
    ]
    by_number = {row[2]: row for row in rows[1:4]}
    assert by_number[taxed["invoice_number"]][1:] == [
        "Invoice",
        taxed["invoice_number"],
        "Test Customer",
        "100.00",
        "100.00",
        "8.25",
        "8.25",
    ]
    assert by_number[untaxed["invoice_number"]][6] == ""  # nothing taxed, no rate
    memo = by_number[cm["memo_number"]]
    assert memo[1] == "Credit memo" and memo[4] == "-20.00" and memo[7] == "-1.65"
    totals = {row[3]: row for row in rows[4:]}
    assert totals["Total"][4:] == ["280.00", "80.00", "", "6.60"]
    assert totals["Non-taxable sales"][4] == "200.00"
    assert totals["Tax on sales"][7] == "8.25"
    assert totals["Less tax on credit memos"][7] == "1.65"
    assert totals["Tax collected"][7] == "6.60"
    assert totals["Difference from this report"][7] == "0.00"
    assert totals["Owed at 2026-07-31"][7] == "6.60"
    # the CSV's figures are the screen's
    screen = client.get(f"/api/reports/sales-tax?{PERIOD}").json()
    assert Decimal(totals["Tax collected"][7]) == Decimal(str(screen["total_tax"]))


def test_the_sales_tax_report_has_print_save_pdf_and_save_csv():
    start = JS.index("async salesTax(prefill)")
    body = JS[start : JS.index("useTaxHtml(u, endDate)", start)]
    assert "ReportsPage._exportButtons('sales-tax'," in body


# ── The Trial Balance takes as_of_date ──────────────────────────────────


def test_the_trial_balance_with_as_of_date_is_the_trial_balance_with_end_date(
    client, db_session, seed_accounts
):
    _activity(db_session, seed_accounts)
    by_end = client.get(
        "/api/reports/trial-balance?start_date=2026-01-01&end_date=2026-06-30"
    )
    by_as_of = client.get(
        "/api/reports/trial-balance?start_date=2026-01-01&as_of_date=2026-06-30"
    )
    assert by_end.status_code == 200 and by_as_of.status_code == 200
    assert by_as_of.json() == by_end.json()
    # as_of_date alone: that day's fiscal year, as end_date alone gives
    alone = client.get("/api/reports/trial-balance?as_of_date=2026-06-30").json()
    assert alone == client.get("/api/reports/trial-balance?end_date=2026-06-30").json()
    # the exports take it too
    for ext in ("csv",):
        a = client.get(f"/api/reports/trial-balance/{ext}?as_of_date=2026-06-30")
        b = client.get(f"/api/reports/trial-balance/{ext}?end_date=2026-06-30")
        assert a.status_code == 200 and a.text == b.text


def test_two_different_days_for_the_trial_balance_end_are_refused_in_a_sentence(
    client, seed_accounts
):
    r = client.get(
        "/api/reports/trial-balance?end_date=2026-06-30&as_of_date=2026-05-31"
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "The end date and the as-of date are different days. "
        "Send one of them, or the same day in both."
    )


def test_the_api_description_says_as_of_date_is_end_date():
    from app.main import app

    spec = app.openapi()
    op = spec["paths"]["/api/reports/trial-balance"]["get"]
    param = next(p for p in op["parameters"] if p["name"] == "as_of_date")
    assert "same as end_date" in param["description"]
    assert "as_of_date" in op["description"]
