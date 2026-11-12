"""Reports: a Print page beside Save PDF (#683).

Invoices, bills, estimates and the other documents have Print beside their
PDF: a page of the same rendering that opens the print dialog on load
(/api/bills/{id}/print-preview). A report had Save PDF only, so printing
one was Save PDF, open the file, print it from there. Every report PDF
route now has a /print beside it: the HTML the PDF is rendered from, with
the on-load print script, answered for the same query string and the same
roles as the PDF."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import reports
from app.routes._print import PRINT_SCRIPT
from app.services import pdf_service
from tests.test_reconciliation_report import completed  # noqa: F401  fixture

ROOT = Path(__file__).resolve().parents[1]
RECON = "/api/banking/reconciliations/{recon_id}/print"

# Each print route and the title its page carries (as the HTML writes it).
TITLES = {
    "/api/reports/profit-loss-by-job/print": "P&amp;L by Job",
    "/api/reports/profit-loss/print": "Profit &amp; Loss",
    "/api/reports/profit-loss-by-class/print": "P&amp;L by Class",
    "/api/reports/trial-balance/print": "Trial Balance",
    "/api/reports/general-ledger/print": "General Ledger",
    "/api/reports/account-transactions/print": "Account detail",
    "/api/reports/balance-sheet/print": "Balance Sheet",
    "/api/reports/financial-statements/print": "Trial Balance",
    "/api/reports/adjusting-entries/print": "Adjusting Journal Entries",
    "/api/reports/closing-date-exceptions/print": "Closing Date Exceptions",
    "/api/reports/customer-statement/{customer_id}/print": "STATEMENT",
    "/api/reports/statement-of-financial-position/print": (
        "Statement of Financial Position"
    ),
    "/api/reports/statement-of-activities/print": "Statement of Activities",
    "/api/reports/fund-balances/print": "Fund Balances",
    "/api/reports/functional-expenses/print": "Statement of Functional Expenses",
    "/api/reports/pledges/print": "Pledge Report",
    "/api/reports/possible-duplicates/print": "Possible Duplicates",
    "/api/reports/sales-by-item/print": "Sales by Item",
    "/api/reports/purchases-by-item/print": "Purchases by Item",
    "/api/reports/profit-loss-detail/print": "Profit &amp; Loss Detail",
    "/api/reports/wip-schedule/print": "WIP Schedule",
    "/api/reports/cost-to-complete/print": "Cost to Complete",
    "/api/reports/cost-to-complete-forecast/print": "Cost-to-Complete Forecast",
    "/api/reports/commitment-status/print": "Commitment Status",
    "/api/reports/field-ticket-log/print": "Field Ticket Log",
    "/api/reports/retainage/print": "Retainage owed to me",
    "/api/reports/compliance/print": "Sub Compliance",
    "/api/reports/change-order-log/print": "Change Order Log",
    "/api/reports/buyout/print": "Buyout Report",
    "/api/reports/job-cost-by-vendor/print": "Job Cost by Vendor",
    "/api/reports/job-profitability/print": "Job Profitability",
    "/api/reports/job-budget-vs-actual/print": "Job Budget vs Actual",
    "/api/reports/sales-tax/print": "Sales Tax Report",
    "/api/reports/books-check/print": "Books Check",
    "/api/reports/ar-aging/print": "Accounts Receivable Aging",
    "/api/reports/ap-aging/print": "Accounts Payable Aging",
    "/api/reports/estimate-closing-rate/print": "Estimate Closing Rate: by customer",
    RECON: "Reconciliation Summary",
}


# The construction reports that read only an as-of date, and answer 409 to a
# company that is not in construction mode.
AS_OF_ONLY = {
    "/api/reports/books-check/print",
    "/api/reports/wip-schedule/print",
    "/api/reports/cost-to-complete/print",
    "/api/reports/cost-to-complete-forecast/print",
    "/api/reports/commitment-status/print",
    "/api/reports/change-order-log/print",
    "/api/reports/buyout/print",
    "/api/reports/field-ticket-log/print",
    "/api/reports/retainage/print",
    "/api/reports/compliance/print",
}


@pytest.fixture
def rendered(monkeypatch):
    """The HTML each PDF is rendered from, without running WeasyPrint."""
    pages = []

    def write(html_str):
        pages.append(html_str)
        return b"%PDF-1.7 stand-in"

    monkeypatch.setattr(pdf_service, "_write_pdf", write)
    return pages


@pytest.fixture
def urls(client, seed_accounts, seed_customer, completed):  # noqa: F811
    """print path -> (print url, pdf url), with the query string each takes."""
    out = {}
    for path in TITLES:
        url = path.replace("{customer_id}", str(seed_customer.id)).replace(
            "{recon_id}", str(completed["id"])
        )
        qs = "?start_date=2026-01-01&end_date=2026-12-31"
        if "account-transactions" in url or "possible-duplicates" in url:
            qs += f"&account_id={seed_accounts['1000'].id}"
        if "customer-statement" in url:
            qs = "?as_of_date=2026-09-30"
        if path in AS_OF_ONLY:
            qs = "?as_of=2026-12-31"
        if "-aging/" in url:
            # the agings refuse a filter they do not read, by name
            qs = "?as_of_date=2026-09-30"
        base = url[: -len("/print")]
        out[path] = (base + "/print" + qs, base + "/pdf" + qs)
    return out


def _as(client, role):
    r = client.post("/api/tokens", json={"label": f"as-{role}", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def test_every_report_pdf_route_has_a_print_route_beside_it():
    assert set(reports.PRINT_ROUTES) | {RECON} == set(TITLES)


def test_each_print_page_is_the_pdfs_html_with_the_print_script(client, urls, rendered):
    for path, (print_url, pdf_url) in urls.items():
        rendered.clear()
        mode = "construction" if path in AS_OF_ONLY else "business"
        assert (
            client.put("/api/settings", json={"company_type": mode}).status_code == 200
        )
        pdf = client.get(pdf_url)
        assert pdf.status_code == 200, (pdf_url, pdf.text)
        assert pdf.headers["content-type"] == "application/pdf", pdf_url
        assert len(rendered) == 1, pdf_url
        page = client.get(print_url)
        assert page.status_code == 200, (print_url, page.text)
        assert page.headers["content-type"].startswith("text/html"), print_url
        assert PRINT_SCRIPT in page.text, print_url
        assert TITLES[path] in page.text, print_url
        # the same rendering as the PDF, the script added
        assert page.text == rendered[0].replace(
            "</body>", PRINT_SCRIPT + "</body>", 1
        ), print_url
        # the print page renders no PDF
        assert len(rendered) == 1, print_url


def test_a_print_page_is_refused_where_its_pdf_is(client, urls, rendered):
    who = {role: _as(client, role) for role in ("readonly", "bookkeeper", "accountant")}
    who["signed out"] = TestClient(app)
    for path, (print_url, pdf_url) in urls.items():
        for role, c in who.items():
            pdf, page = c.get(pdf_url), c.get(print_url)
            assert page.status_code == pdf.status_code, (role, print_url)
            if pdf.status_code == 200:
                assert PRINT_SCRIPT in page.text, (role, print_url)
            else:
                assert PRINT_SCRIPT not in page.text, (role, print_url)
    assert who["signed out"].get(urls[RECON][0]).status_code == 401
    # a record that is not there is a 404 on both
    for url in (
        "/api/reports/customer-statement/999999/print",
        "/api/banking/reconciliations/999999/print",
    ):
        assert client.get(url).status_code == 404, url
        assert client.get(url.replace("/print", "/pdf")).status_code == 404, url


def test_save_pdf_is_unchanged(client, urls, rendered):
    """Save PDF still answers a PDF, rendered through WeasyPrint."""
    pdf = client.get(urls["/api/reports/trial-balance/print"][1])
    assert pdf.content == b"%PDF-1.7 stand-in"
    assert "inline" in pdf.headers.get("content-disposition", "") or (
        "attachment" in pdf.headers.get("content-disposition", "")
    )


def test_reports_js_puts_print_beside_save_pdf():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    # the shared Save PDF / Save CSV helper
    assert (
        "window.open('/api/reports/${path}/print?${qs}','_blank')\">Print</button>"
        in js
    )
    # the one-offs: the statements pack, the customer statement and the
    # reconciliation reports
    assert "/api/reports/financial-statements/print?" in js
    assert "/api/reports/customer-statement/${cid}/print?" in js
    assert "/api/banking/reconciliations/${r.id}/print" in js
