"""Reports on a cash basis, step 4 (#483, docs/design/cash-basis.md): the
Sales Tax Liability report on a basis, retainage held on a bill, and payroll
staying on both bases."""

from decimal import Decimal

from app.services import cash_basis
from tests.test_cash_basis import (
    _account_id,
    _amount,
    _assert_cash_invariant,
    _bill_payment,
    _bs,
    _bs_amount,
    _each_entry_balances_on_every_basis,
    _invoice,
    _payment,
    _pl,
    _strip_cash_lines,
    _vendor,
)
from tests.test_payroll import _create_employee

D = Decimal
PERIOD = "start_date=2026-07-01&end_date=2026-07-31"


# ---------------------------------------------------------------------------
# Sales Tax Liability on a basis
# ---------------------------------------------------------------------------


def _taxed_invoices(client, cust):
    """One taxed invoice nothing was paid on (tax 10.00) and one paid
    half (tax 20.00, of which 10.00 collected)."""
    unpaid = _invoice(client, cust, "2026-07-02", [{"rate": 100}], tax_rate=0.10)
    part = _invoice(client, cust, "2026-07-05", [{"rate": 200}], tax_rate=0.10)
    assert D(str(part["total"])) == D("220.00")
    _payment(client, cust, "2026-07-20", 110, [(part["id"], 110)])
    return unpaid, part


def _tax(client, basis=None, extra=""):
    qs = PERIOD + (f"&basis={basis}" if basis else "") + extra
    r = client.get(f"/api/reports/sales-tax?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def test_sales_tax_cash_basis_counts_tax_on_money_received(
    client, seed_accounts, seed_customer
):
    _taxed_invoices(client, seed_customer.id)

    accrual = _tax(client, "accrual")
    assert accrual["basis"] == "accrual" and accrual["total_tax"] == 30.0
    assert accrual["ledger"]["balance"] == 30.0
    # the accrual reading is what it was without the parameter
    plain = _tax(client)
    assert {k: v for k, v in plain.items() if k != "basis"} == {
        k: v for k, v in accrual.items() if k != "basis"
    }

    cash = _tax(client, "cash")
    assert cash["basis"] == "cash"
    assert cash["total_tax"] == 10.0 and cash["tax_on_sales"] == 10.0
    assert cash["total_sales"] == 100.0 and cash["total_taxable"] == 100.0
    assert [i["date"] for i in cash["items"]] == ["2026-07-20"]
    assert cash["ledger"]["tax_posted"] == 10.0
    assert cash["ledger"]["difference"] == 0.0
    assert cash["ledger"]["balance"] == 10.0

    # a remittance is both: it takes the cash balance down, not the accrual's
    # tax collected
    r = client.post(
        "/api/reports/sales-tax/pay",
        json={
            "date": "2026-07-31",
            "amount": 4,
            "pay_from_account_id": _account_id(client, "1000"),
        },
    )
    assert r.status_code == 200, r.text
    cash = _tax(client, "cash")
    assert cash["ledger"]["payments"] == 4.0
    assert cash["ledger"]["tax_posted"] == 10.0
    assert cash["ledger"]["balance"] == 6.0
    assert _tax(client, "accrual")["ledger"]["balance"] == 26.0

    # 2200 on the cash Balance Sheet is the same figure
    assert _bs_amount(_bs(client, "2026-07-31", "cash"), "2200") == D("6.00")


def test_sales_tax_cash_basis_on_a_retainage_invoice_matches_its_cash_lines(
    client, db_session, seed_accounts, seed_customer
):
    """A pay application's invoice holds retainage back, so its amount due is
    less than its total. The payment's cash lines prorate on the amount due;
    the report prorated on the total and read less tax than 2200 took
    (2.30.0 gate NEW-184). Both now move together, to the cent."""
    from tests.test_pay_applications import (
        _approve,
        _edit,
        _new_app,
        _taxed_estimate_job,
    )

    job, sov = _taxed_estimate_job(client, seed_customer.id, rate="0.08")
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 5000, "stored": 0}],
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    inv = client.get(f"/api/invoices/{r.json()['invoice_id']}").json()
    # 5,000 at 8%: tax 400, total 5,400, 500 held back, 4,900 due
    assert D(str(inv["total"])) == D("5400.00")
    assert D(str(inv["balance_due"])) == D("4900.00")
    # half the amount due: half the tax, as the cash lines book it
    _payment(client, seed_customer.id, "2026-07-10", 2450, [(inv["id"], 2450)])

    cash = _tax(client, "cash")
    assert cash["total_tax"] == 200.0 and cash["tax_on_sales"] == 200.0
    assert cash["total_sales"] == 2500.0 and cash["total_taxable"] == 2500.0
    assert cash["ledger"]["tax_posted"] == 200.0
    assert cash["ledger"]["difference"] == 0.0
    assert cash["ledger"]["balance"] == 200.0
    assert _bs_amount(_bs(client, "2026-07-31", "cash"), "2200") == D("200.00")
    # the rest of the amount due collects the rest of the tax
    _payment(client, seed_customer.id, "2026-07-20", 2450, [(inv["id"], 2450)])
    cash = _tax(client, "cash")
    assert cash["total_tax"] == 400.0
    assert cash["ledger"]["difference"] == 0.0


def test_sales_tax_default_follows_the_company_basis(
    client, seed_accounts, seed_customer
):
    _taxed_invoices(client, seed_customer.id)
    assert _tax(client)["basis"] == "accrual"
    client.put("/api/settings", json={"report_basis": "cash"})
    by_default = _tax(client)
    assert by_default["basis"] == "cash" and by_default["total_tax"] == 10.0
    assert _tax(client, "accrual")["total_tax"] == 30.0
    r = client.get(f"/api/reports/sales-tax?{PERIOD}&basis=modified")
    assert r.status_code == 422


def test_sales_tax_cash_basis_ignores_a_voided_payment(
    client, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-07-05", [{"rate": 200}], tax_rate=0.10)
    pay = _payment(client, cust, "2026-07-20", 220, [(inv["id"], 220)])
    assert _tax(client, "cash")["total_tax"] == 20.0
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    cash = _tax(client, "cash")
    assert cash["total_tax"] == 0.0 and cash["ledger"]["balance"] == 0.0


def test_the_sales_tax_screen_offers_the_basis():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "reports.js"
    ).read_text(encoding="utf-8")
    body = js[js.index("async salesTax(prefill)") :]
    body = body[: body.index("view: 'sales-tax'")]
    assert "_basisOpts(prefill)" in body and "_basisQs(params)" in body


# ---------------------------------------------------------------------------
# Retainage held on a bill
# ---------------------------------------------------------------------------


def _retainage_bill(client, db_session, seed_accounts, vendor_id, held=100):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": "2026-06-01",
            "retainage_held": held,
            "lines": [
                {
                    "description": "Framing",
                    "amount": 700,
                    "account_id": seed_accounts["5100"].id,
                },
                {
                    "description": "Trim",
                    "amount": 300,
                    "account_id": seed_accounts["6000"].id,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _released_commitment(client, seed_accounts, vendor_id):
    """A commitment holding 10% on Framing 700 (5100) and Trim 300 (6000),
    billed whole on 2026-06-01: 1,000, of which 100 held on 2050."""
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor_id,
            "date": "2026-06-01",
            "retainage_pct": 10,
            "lines": [
                {"description": "Framing", "quantity": 1, "rate": 700},
                {"description": "Trim", "quantity": 1, "rate": 300},
            ],
        },
    ).json()
    po = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    po = po.json()
    framing, trim = po["lines"]
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={
            "lines": [
                {"line_id": framing["id"], "account_id": seed_accounts["5100"].id},
                {"line_id": trim["id"], "account_id": seed_accounts["6000"].id},
            ]
        },
    )
    assert r.status_code == 200, r.text
    return po, client.get(f"/api/bills/{r.json()['bill_id']}").json()


def test_retainage_is_not_expensed_until_it_is_released(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _vendor(client)
    po, bill = _released_commitment(client, seed_accounts, vendor["id"])
    assert D(str(bill["balance_due"])) == D("900.00")
    _bill_payment(client, vendor["id"], "2026-06-10", 900, [(bill["id"], 900)])

    # net paid: nine tenths of each expense line, nothing held yet
    june, bs_june, _ = _assert_cash_invariant(client, "2026-06-01", "2026-06-30")
    assert _amount(june, "5100") == D("630.00")
    assert _amount(june, "6000") == D("270.00")
    assert D(str(june["net_income"])) == D("-900.00")
    assert _bs_amount(bs_june, "2000") == 0
    assert _bs_amount(bs_june, "2050") == 0
    # accrual: the whole bill, the held part on 2050
    acc = _pl(client, "2026-06-01", "2026-06-30")
    assert D(str(acc["net_income"])) == D("-1000.00")
    assert _bs_amount(_bs(client, "2026-06-30"), "2050") == D("100.00")

    # the release bill, paid, recognizes the held tenth on the bill's lines
    r = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage",
        json={"date": "2026-07-15"},
    )
    assert r.status_code == 200, r.text
    release = r.json()["bill_id"]
    paid = _bill_payment(client, vendor["id"], "2026-07-15", 100, [(release, 100)])
    july, bs_july, _ = _assert_cash_invariant(client, "2026-07-01", "2026-07-31")
    assert _amount(july, "5100") == D("70.00")
    assert _amount(july, "6000") == D("30.00")
    assert _amount(july, "6990") == 0
    assert D(str(july["net_income"])) == D("-100.00")
    assert _bs_amount(bs_july, "2000") == 0 and _bs_amount(bs_july, "2050") == 0
    assert _bs_amount(_bs(client, "2026-07-31"), "2050") == 0  # accrual: released
    # over the two months the cash expense is the accrual expense
    whole = _pl(client, "2026-06-01", "2026-07-31", "cash")
    assert _amount(whole, "5100") == D("700.00")
    assert _amount(whole, "6000") == D("300.00")

    # voiding the payment and the release takes the cash expense back and
    # the held part returns to 2050 on the accrual view
    assert client.post(f"/api/bill-payments/{paid['id']}/void").status_code == 200
    assert client.post(f"/api/bills/{release}/void").status_code == 200
    july, bs_july, _ = _assert_cash_invariant(client, "2026-07-01", "2026-07-31")
    assert D(str(july["net_income"])) == 0
    assert _bs_amount(bs_july, "2050") == 0
    assert _bs_amount(_bs(client, "2026-07-31"), "2050") == D("100.00")
    _each_entry_balances_on_every_basis(db_session)


def test_retainage_bill_paid_in_two_parts_and_the_backfill_is_idempotent(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _vendor(client)
    bill = _retainage_bill(client, db_session, seed_accounts, vendor["id"])
    _bill_payment(client, vendor["id"], "2026-06-10", 450, [(bill["id"], 450)])
    _bill_payment(client, vendor["id"], "2026-07-10", 450, [(bill["id"], 450)])
    june = _pl(client, "2026-06-01", "2026-06-30", "cash")
    july = _pl(client, "2026-07-01", "2026-07-31", "cash")
    assert D(str(june["net_income"])) == D("-450.00") == D(str(july["net_income"]))
    assert _amount(june, "5100") == D("315.00")
    assert _bs_amount(_bs(client, "2026-07-31", "cash"), "2000") == 0

    # a file from before the basis column: the backfill gives back the same
    _strip_cash_lines(db_session)
    assert (
        _pl(client, "2026-06-01", "2026-06-30", "cash")["net_income"]
        != june["net_income"]
    )
    first = cash_basis.backfill(db_session)
    assert first["bill_payments"] == 2 and first["failed"] == 0
    assert _pl(client, "2026-06-01", "2026-06-30", "cash") == june
    assert _pl(client, "2026-07-01", "2026-07-31", "cash") == july
    again = cash_basis.backfill(db_session)
    assert again["bill_payments"] == 0 and again["tagged"] == 0
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# Payroll stays on both bases
# ---------------------------------------------------------------------------


def test_a_pay_run_reads_the_same_on_both_bases(client, db_session, seed_accounts):
    emp = _create_employee(
        client, pay_type="salary", pay_rate=78000, pay_frequency="biweekly"
    )
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"]}],
        },
    ).json()
    assert client.post(f"/api/payroll/{run['id']}/process").status_code == 200

    accrual = _pl(client, "2026-04-01", "2026-04-30", "accrual")
    cash = _pl(client, "2026-04-01", "2026-04-30", "cash")
    assert accrual["net_income"] == cash["net_income"] < 0
    assert accrual["expenses"] == cash["expenses"]

    bs_cash = _bs(client, "2026-04-30", "cash")
    bs_accrual = _bs(client, "2026-04-30", "accrual")
    assert bs_cash["liabilities"] == bs_accrual["liabilities"]
    payroll_liabilities = [
        ln for ln in bs_cash["liabilities"] if D(str(ln["amount"])) != 0
    ]
    assert payroll_liabilities, "withholdings stay on the cash Balance Sheet"
    # the totals are JSON floats: compare them to the cent
    assert round(bs_cash["total_assets"], 2) == round(
        bs_cash["total_liabilities"] + bs_cash["total_equity"], 2
    )
    _assert_cash_invariant(client, "2026-04-01", "2026-04-30")
