"""Income by Customer and Sales by Item tie to the Profit and Loss
(2.32.0 gate round 22: W-194, W-195, W-197, W-198).

- W-194: cash-basis Sales by Item left out money received and not yet
  applied, so October read 1,140.00 against the cash P&L's 1,390.00. It now
  has a row of its own under the items, "Payments received, not yet
  applied", in the total, on screen, in the lines, the PDF and the CSV.
- W-195: accrual Income by Customer counted a security deposit (an item on
  a liability) as a sale, 2,500.00 against the P&L's 1,500.00. Sales is
  income-account lines only, on both bases; the deposit is Not income.
- W-198: accrual Income by Customer did not net credit memos (1,600.00
  against 1,550.00). A credit memo reduces its customer's Sales on its date.
- W-197: Income by Customer had no PDF or CSV. It has both, and Print,
  following the Basis switch.

The books built here hold invoices, a sales receipt, credit memos (open,
applied, voided), a voided invoice, a deposit item, money not applied and
a register deposit naming a customer (#1065). For every period and both
bases Income by Customer's Sales equals the P&L's income, Sales by Item's
total equals it less the register deposit (income no item sold, the rest
of the P&L's figure), the trial balance foots and Books Check finds no
difference."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from tests.test_cash_basis import _invoice, _payment
from tests.test_void_revenue_cost_codes_new262 import books_agree

ROOT = Path(__file__).resolve().parents[1]
PERIODS = {
    "sep": "start_date=2026-09-01&end_date=2026-09-30",
    "oct": "start_date=2026-10-01&end_date=2026-10-31",
    "nov": "start_date=2026-11-01&end_date=2026-11-30",
    "sep-nov": "start_date=2026-09-01&end_date=2026-11-30",
}
OCT = PERIODS["oct"]


def _get(client, path, qs, basis=None):
    url = f"/api/reports/{path}?{qs}" + (f"&basis={basis}" if basis else "")
    r = client.get(url)
    assert r.status_code == 200, (url, r.text)
    return r.json()


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


@pytest.fixture
def books(client, db_session, seed_accounts, seed_customer):
    deposits = Account(
        account_number="2450",
        name="Security Deposits Held",
        account_type=AccountType.LIABILITY,
        detail_type="other_current_liability",
        balance=Decimal("0"),
    )
    db_session.add(deposits)
    other = Customer(name="Second Customer", is_active=True)
    walk_in = Customer(name="Walk-in Customer", is_active=True)
    db_session.add_all([other, walk_in])
    db_session.commit()
    cid = seed_customer.id
    item = _ok(
        client.post(
            "/api/items",
            json={"name": "Gutter cleaning", "item_type": "service", "rate": 200},
        ),
        201,
    )
    deposit = _ok(
        client.post(
            "/api/items",
            json={
                "name": "Security deposit",
                "item_type": "service",
                "is_taxable": False,
                "income_account_id": deposits.id,
            },
        ),
        201,
    )
    # September: 1,000 on the item and 200 with none, 10% tax on the item
    a = _invoice(
        client,
        cid,
        "2026-09-10",
        [
            {"item_id": item["id"], "quantity": 5, "rate": 200, "is_taxable": True},
            {"rate": 200, "is_taxable": False},
        ],
        tax_rate=0.1,
    )
    # October: rent with a security deposit, for the second customer
    b = _invoice(
        client,
        other.id,
        "2026-10-01",
        [
            {"item_id": item["id"], "quantity": 1, "rate": 500, "is_taxable": False},
            {"item_id": deposit["id"], "quantity": 1, "rate": 1000},
        ],
    )
    # an October invoice voided
    gone = _invoice(client, cid, "2026-10-03", [{"rate": 300, "is_taxable": False}])
    _ok(client.post(f"/api/invoices/{gone['id']}/void"))
    # October credit memos: 100 applied to September's invoice, 50 open,
    # and 70 voided
    applied = _ok(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": cid,
                "date": "2026-10-05",
                "tax_rate": 0,
                "lines": [{"item_id": item["id"], "quantity": 1, "rate": 100}],
            },
        ),
        201,
    )
    _ok(
        client.post(
            f"/api/credit-memos/{applied['id']}/apply",
            json={"invoice_id": a["id"], "amount": 100},
        )
    )
    _ok(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": other.id,
                "date": "2026-10-06",
                "tax_rate": 0,
                "lines": [{"description": "Allowance", "quantity": 1, "rate": 50}],
            },
        ),
        201,
    )
    voided = _ok(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": cid,
                "date": "2026-10-07",
                "tax_rate": 0,
                "lines": [{"description": "Mistake", "quantity": 1, "rate": 70}],
            },
        ),
        201,
    )
    _ok(client.post(f"/api/credit-memos/{voided['id']}/void"))
    # October money: 600 on September's invoice, 250 applied to nothing
    _payment(client, cid, "2026-10-20", 600, [(a["id"], 600)])
    _payment(client, other.id, "2026-10-22", 250)
    # an October sales receipt
    _ok(
        client.post(
            "/api/sales-receipts",
            json={
                "customer_id": walk_in.id,
                "date": "2026-10-25",
                "tax_rate": 0,
                "method": "Cash",
                "lines": [
                    {"item_id": item["id"], "quantity": 2, "rate": 200},
                ],
            },
        ),
        201,
    )
    # a register deposit naming a customer (#1065), October
    bank = seed_accounts["1000"]
    _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": bank.id,
                "date": "2026-10-14",
                "amount": "75.00",
                "category_account_id": seed_accounts["4000"].id,
                "payee": "Counter sale",
                "customer_id": walk_in.id,
            },
        ),
        201,
    )
    # November: the rest of September's invoice, and the rent
    _payment(client, cid, "2026-11-05", 600, [(a["id"], 600)])
    _payment(client, other.id, "2026-11-06", 1500, [(b["id"], 1500)])
    return {
        "customer": cid,
        "other": other.id,
        "walk_in": walk_in.id,
        "item": item["id"],
        "deposit": deposit["id"],
        "bank_entry": {"oct": 75.0, "sep-nov": 75.0},
    }


@pytest.mark.parametrize("basis", ["accrual", "cash"])
def test_both_reports_tie_to_the_pl_in_every_period(client, books, basis):
    for name, qs in PERIODS.items():
        income = _get(client, "profit-loss", qs, basis)["total_income"]
        ibc = _get(client, "income-by-customer", qs, basis)
        sbi = _get(client, "sales-by-item", qs, basis)
        assert ibc["basis"] == sbi["basis"] == basis
        assert ibc["total_sales"] == income, (basis, name, ibc, income)
        no_item = books["bank_entry"].get(name, 0.0)
        assert round(sbi["total_amount"] + no_item, 2) == income, (basis, name, sbi)
        # every row adds up: Sales + Not income + Tax − Paid = Balance
        if basis == "accrual":
            for r in ibc["items"]:
                assert round(
                    r["total_sales"]
                    + r["total_other"]
                    + r["total_tax"]
                    - r["total_paid"],
                    2,
                ) == round(r["total_balance"], 2), (name, r)
    books_agree(client)


def test_the_october_figures(client, books):
    """The gate's month, by hand. Accrual: 500 rent (the deposit is Not
    income), the voided 300 nothing, the 100 and 50 credits off, 400 on the
    sales receipt and the 75 register deposit: 825. Cash: 600 of
    September's 1,300 invoice was 600/1,300 of its 1,000 + 200 income
    (553.85), plus the 250 not applied, the 400 receipt and the 75."""
    accrual = _get(client, "income-by-customer", OCT, "accrual")
    rows = {r["customer_id"]: r for r in accrual["items"]}
    assert rows[books["other"]]["total_sales"] == 450.0  # 500 rent − 50 credit
    assert rows[books["other"]]["total_other"] == 1000.0  # the deposit
    assert rows[books["customer"]]["total_sales"] == -100.0  # the applied credit
    assert rows[books["walk_in"]]["total_sales"] == 475.0
    assert accrual["total_sales"] == 825.0
    assert _get(client, "profit-loss", OCT, "accrual")["total_income"] == 825.0

    cash = _get(client, "sales-by-item", OCT, "cash")
    assert cash["unapplied_label"] == "Payments received, not yet applied"
    assert cash["unapplied_amount"] == 250.0
    assert all(r["item_id"] != "unapplied" for r in cash["items"])
    pl = _get(client, "profit-loss", OCT, "cash")["total_income"]
    assert round(cash["total_amount"] + 75.0, 2) == pl
    # the accrual Sales by Item has no such row
    accrual_sbi = _get(client, "sales-by-item", OCT, "accrual")
    assert accrual_sbi["unapplied_amount"] is None


def test_the_unapplied_row_in_the_lines_pdf_and_csv(client, books):
    from app.services.ledger_exports import sales_by_item_sections

    lines = _get(client, "sales-by-item/lines", f"{OCT}&item_id=unapplied", "cash")
    assert [(ln["doc_type"], ln["amount"]) for ln in lines["lines"]] == [
        ("Payment", 250.0)
    ]
    assert lines["lines"][0]["link"]["type"] == "payment"
    # on accrual it has no lines; narrowed to a class or a job, no row
    assert (
        _get(client, "sales-by-item/lines", f"{OCT}&item_id=unapplied", "accrual")[
            "lines"
        ]
        == []
    )
    data = _get(client, "sales-by-item", OCT, "cash")
    section = sales_by_item_sections(data)[0]
    cells = [r["cells"] for r in section["rows"]]
    assert cells[-2][0] == "Payments received, not yet applied"
    assert cells[-2][3] == "$250.00"
    assert cells[-1][0] == "TOTAL"
    csv_text = _ok_text(client, f"/api/reports/sales-by-item/csv?{OCT}&basis=cash")
    assert '"Payments received, not yet applied",,,250.00' in csv_text
    total = f"{data['total_amount']:.2f}"
    assert f",{total}," in csv_text.splitlines()[-1]
    # a customer narrowing keeps only that customer's
    other = _get(
        client, "sales-by-item", f"{OCT}&customer_id={books['customer']}", "cash"
    )
    assert other["unapplied_amount"] is None


def _ok_text(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.text
    return r.text


def test_the_screen_shows_the_row_and_the_columns():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    sbi = js[js.index("async salesByItem(prefill) {") :]
    sbi = sbi[: sbi.index("\n    },")]
    assert "data.unapplied_amount" in sbi and "data.unapplied_label" in sbi
    assert "openItemLines('sales', 'unapplied'" in sbi
    ibc = js[js.index("async incomeByCustomer(prefill) {") :]
    ibc = ibc[: ibc.index("\n    },")]
    assert "total_other" in ibc
    assert "ReportsPage._exportButtons('income-by-customer', qs)" in ibc


def test_the_other_sales_figures_leave_the_deposit_out(client, db_session, books):
    """W-195's leak, checked everywhere a sale is summed by customer: Past
    Customers' lifetime sales and the analytics' revenue by customer (both
    paid invoices only) read the rent, not the deposit."""
    from app.services.analytics import AnalyticsEngine

    past = _ok(client.get("/api/customers/past"))
    rows = {r["customer_id"]: r for r in past["rows"]}
    # 500 rent less the 50 allowance; the 1,000 deposit is not a sale
    assert rows[books["other"]]["lifetime_sales"] == 450.0
    engine = AnalyticsEngine(db_session)
    from datetime import date

    paid = engine.revenue_by_customer(date(2026, 9, 1), date(2026, 11, 30))
    assert paid["Second Customer"] == 500.0
    assert engine.customer_profit()["Second Customer"]["revenue"] == 500.0


def test_income_by_customer_pdf_and_csv_follow_the_basis(client, books, monkeypatch):
    """W-197: the PDF (its HTML, without WeasyPrint), the print page and the
    CSV carry the screen's figures for the basis asked for."""
    from app.services import pdf_service
    from app.services.ledger_exports import income_by_customer_sections

    pages = []
    monkeypatch.setattr(
        pdf_service, "_write_pdf", lambda html: pages.append(html) or b"%PDF"
    )
    for basis in ("accrual", "cash"):
        data = _get(client, "income-by-customer", OCT, basis)
        (section,) = income_by_customer_sections(data)
        assert section["period"].endswith("(cash basis)") == (basis == "cash")
        assert ("Not income" in section["columns"]) == (basis == "accrual")
        last = section["rows"][-1]
        assert last["style"] == "grand-total" and last["cells"][0] == "TOTAL"
        assert last["cells"][2] == f"${data['total_sales']:,.2f}"
        pdf = client.get(f"/api/reports/income-by-customer/pdf?{OCT}&basis={basis}")
        assert pdf.status_code == 200 and pdf.headers["content-type"] == (
            "application/pdf"
        )
        assert "Income by Customer" in pages[-1] and "TOTAL" in pages[-1]
        assert ("(cash basis)" in pages[-1]) == (basis == "cash")
        csv_text = _ok_text(
            client, f"/api/reports/income-by-customer/csv?{OCT}&basis={basis}"
        )
        lines = csv_text.splitlines()
        assert ("(cash basis)" in csv_text) == (basis == "cash")
        assert lines[-1].startswith("TOTAL,") and (
            f",{data['total_sales']:.2f}," in lines[-1]
        )
        page = client.get(f"/api/reports/income-by-customer/print?{OCT}&basis={basis}")
        assert page.status_code == 200 and "window.print()" in page.text
    # with no deposit in the period there is no Not income column
    sep = _get(client, "income-by-customer", PERIODS["sep"], "accrual")
    assert "Not income" not in income_by_customer_sections(sep)[0]["columns"]
    r = client.get(f"/api/reports/income-by-customer/csv?{OCT}&basis=modified")
    assert r.status_code == 422
