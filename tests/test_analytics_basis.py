"""Analytics says what its revenue counts (2.17.3 exploratory test, W-L12).

September's "Revenue $742.20" sat beside a $10.0M Profit & Loss for the
same month, unlabelled. The analytics figure is invoices dated in the
period and paid in full — neither the P&L's accrual income nor cash
received in the period. The screen and the PDF now say so; the first test
pins what the code computes, so the label cannot drift from it.
"""

from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "analytics.js"
PERIOD = "start_date=2026-09-01&end_date=2026-09-30"


def _invoice(client, customer_id, when, rate):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": when,
            "lines": [{"description": "work", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, customer_id, inv, when):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": when,
            "amount": inv["total"],
            "allocations": [{"invoice_id": inv["id"], "amount": inv["total"]}],
        },
    )
    assert r.status_code == 201, r.text


def test_revenue_is_paid_invoices_dated_in_the_period(
    client, seed_accounts, seed_customer
):
    cid = seed_customer.id
    paid_sept = _invoice(client, cid, "2026-09-10", "742.20")
    _pay(client, cid, paid_sept, "2026-09-12")
    _invoice(client, cid, "2026-09-11", "10000.00")  # issued, unpaid
    paid_in_sept_for_aug = _invoice(client, cid, "2026-08-20", "300.00")
    _pay(client, cid, paid_in_sept_for_aug, "2026-09-02")

    dash = client.get(f"/api/analytics/dashboard?{PERIOD}").json()
    assert sum(dash["revenue_by_customer"].values()) == 742.20
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert float(pl["total_income"]) == 10742.20  # accrual: the two differ


def test_the_screen_labels_the_basis():
    js = JS.read_text(encoding="utf-8")
    assert "Terms.text('Revenue (paid invoices)')" in js
    assert ">Expenses (paid bills)<" in js
    assert "${escapeHtml(Terms.text(this.BASIS_NOTE))}" in js
    assert "Profit & Loss report counts every invoice and bill when it is dated" in js


def test_the_pdf_labels_the_basis():
    from app.services.pdf_service import _jinja_env
    from app.services.terminology import terms_for

    html = _jinja_env.get_template("analytics_pdf.html").render(
        dashboard={
            "revenue_by_customer": {"Acme": 742.2},
            "expenses_by_category": {},
            "revenue_trend": {},
            "ar_aging": {},
            "ap_aging": {},
            "dso": 0,
            "cash_forecast": [],
            "customer_profit": {},
        },
        period={"name": "month", "start": "2026-09-01", "end": "2026-09-30"},
        company={"company_name": "Explore Signs & Co"},
        terms=terms_for({}),
        company_logo_data_uri="",
    )
    assert "Revenue (paid invoices)" in html
    assert "Expenses (paid bills)" in html
    assert "a paid basis" in html
