"""The cash flow statement, by the indirect method, on a real month of books.

Exploratory 2.17.3 (skytech W-M6, macbase1 F18): customer receipts routed
through Undeposited Funds showed under Investing, bill payments and payroll
withholdings under Financing, and net income never appeared (the net change
was right). The statement starts from net income now, adds back what moved
no cash, adds the change in working capital, and keeps investing and
financing for fixed assets, loans and the owners' money. Its net change is
the change in the bank accounts — checked here against the ledger itself."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func

from app.models.accounts import Account
from app.models.transactions import Transaction, TransactionLine

START, END = "2026-02-01", "2026-03-31"

JS = (Path(__file__).resolve().parents[1] / "app/static/js/reports.js").read_text(
    encoding="utf-8"
)


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


def _journal(client, day, lines, description="journal"):
    return _ok(
        client.post(
            "/api/journal",
            json={
                "date": day,
                "description": description,
                "lines": [
                    {"account_id": a.id, "debit": str(dr), "credit": str(cr)}
                    for a, dr, cr in lines
                ],
            },
        )
    )


@pytest.fixture
def books(client, db_session, seed_accounts, seed_customer):
    a = seed_accounts
    # carried in: Checking's statement balance when the feed was set up
    _ok(
        client.post(
            "/api/banking/accounts",
            json={
                "name": "Checking feed",
                "account_id": a["1000"].id,
                "opening_balance": "10000",
                "opening_date": "2026-02-01",
            },
        )
    )
    # financing: the owner puts money in, a loan, a draw
    _journal(client, "2026-02-02", [(a["1000"], 5000, 0), (a["3000"], 0, 5000)])
    _journal(client, "2026-02-03", [(a["1000"], 20000, 0), (a["2400"], 0, 20000)])
    _journal(client, "2026-03-28", [(a["3100"], 700, 0), (a["1000"], 0, 700)])

    # sales: one invoice paid into Undeposited Funds and deposited, one with
    # tax paid in part and the money still undeposited
    inv1 = _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-02-05",
                "tax_rate": 0,
                "lines": [{"description": "Sign", "quantity": 1, "rate": 1000}],
            },
        )
    )
    _ok(
        client.post(
            "/api/payments",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-02-10",
                "amount": "1000",
                "method": "check",
                "check_number": "4420",
                "allocations": [{"invoice_id": inv1["id"], "amount": "1000"}],
            },
        )
    )
    _ok(
        client.post(
            "/api/deposits",
            json={
                "deposit_to_account_id": a["1000"].id,
                "date": "2026-02-12",
                "total": "1000",
            },
        )
    )
    inv2 = _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-03-05",
                "tax_rate": 0.10,
                "lines": [{"description": "Banner", "quantity": 1, "rate": 500}],
            },
        )
    )
    _ok(
        client.post(
            "/api/payments",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-03-10",
                "amount": "300",
                "method": "check",
                "allocations": [{"invoice_id": inv2["id"], "amount": "300"}],
            },
        )
    )

    # purchases: a bill paid by check, a bill still owed, a card charge
    vendor = _ok(client.post("/api/vendors", json={"name": "Sign Supply"}))
    bills = []
    for number, amount in (("SS-1", 400), ("SS-2", 250)):
        bills.append(
            _ok(
                client.post(
                    "/api/bills",
                    json={
                        "vendor_id": vendor["id"],
                        "bill_number": number,
                        "date": "2026-02-06",
                        "lines": [
                            {
                                "description": "Vinyl",
                                "quantity": 1,
                                "rate": amount,
                                "account_id": a["6000"].id,
                            }
                        ],
                    },
                )
            )
        )
    _ok(
        client.post(
            "/api/bill-payments",
            json={
                "vendor_id": vendor["id"],
                "date": "2026-02-15",
                "amount": 400,
                "method": "check",
                "check_number": "2001",
                "pay_from_account_id": a["1000"].id,
                "allocations": [{"bill_id": bills[0]["id"], "amount": 400}],
            },
        )
    )
    _ok(
        client.post(
            "/api/cc-charges",
            json={
                "date": "2026-02-20",
                "payee": "Fuel",
                "amount": "80",
                "account_id": a["6000"].id,
            },
        )
    )

    # payroll: withholdings and employer taxes stay owed at month end
    emp = _ok(
        client.post(
            "/api/employees",
            json={
                "first_name": "Hana",
                "last_name": "Ito",
                "pay_type": "salary",
                "pay_rate": 52000,
                "pay_frequency": "biweekly",
                "filing_status": "single",
                "work_state": "WA",
            },
        )
    )
    run = _ok(
        client.post(
            "/api/payroll",
            json={
                "period_start": "2026-03-01",
                "period_end": "2026-03-14",
                "pay_date": "2026-03-20",
                "stubs": [{"employee_id": emp["id"]}],
            },
        )
    )
    _ok(client.post(f"/api/payroll/{run['id']}/process"))

    # a fixed asset bought from Checking, and a month of its depreciation
    [etype] = client.get("/api/fixed-assets/types").json()
    _ok(
        client.post(
            "/api/fixed-assets",
            json={
                "name": "Vinyl plotter",
                "asset_type_id": etype["id"],
                "purchase_date": "2026-02-10",
                "purchase_price": "6000",
                "acquisition": {"method": "paid_from", "account_id": a["1000"].id},
            },
        )
    )
    _ok(
        client.post(
            "/api/fixed-assets/run-depreciation", json={"run_date": "2026-03-31"}
        )
    )
    return {"net_pay": Decimal(str(run["stubs"][0]["net_pay"]))}


def _cash_change_in_ledger(db_session):
    """The bank accounts' change over the period, from the ledger lines,
    leaving out the opening balance that was carried in."""
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(Account.bank_kind == "bank")
        .filter(
            Transaction.date >= date(2026, 2, 1), Transaction.date <= date(2026, 3, 31)
        )
        .filter(Transaction.source_type != "opening_balance")
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


def _report(client):
    return _ok(
        client.get(
            "/api/reports/cash-flow", params={"start_date": START, "end_date": END}
        )
    )


def _names(rows):
    return {r["account_name"]: r["amount"] for r in rows}


def test_net_change_is_the_change_in_the_bank_accounts(client, db_session, books):
    report = _report(client)
    change = _cash_change_in_ledger(db_session)
    assert Decimal(str(report["net_change"])) == change
    total = (
        Decimal(str(report["total_operating"]))
        + Decimal(str(report["total_investing"]))
        + Decimal(str(report["total_financing"]))
    )
    assert total == change
    assert report["beginning_cash"] == 10000.0
    assert Decimal(str(report["ending_cash"])) == Decimal("10000") + change


def test_it_starts_from_net_income(client, books):
    report = _report(client)
    pl = client.get(
        "/api/reports/profit-loss", params={"start_date": START, "end_date": END}
    ).json()
    assert report["net_income"] == pytest.approx(pl["net_income"], abs=0.005)
    first = report["operating"][0]
    assert first["group"] == "net_income" and first["amount"] == report["net_income"]


def test_customer_bill_and_payroll_money_is_operating(client, books):
    report = _report(client)
    wc = _names(report["working_capital"])
    # receipts through Undeposited Funds: $300 still waits to be deposited
    assert wc["Undeposited Funds"] == -300.0
    assert wc["Accounts Receivable"] == -250.0  # 1,550 invoiced, 1,300 paid
    assert wc["Accounts Payable"] == 250.0  # one bill of two still owed
    assert wc["Sales Tax Payable"] == 50.0
    assert wc["Credit Card"] == 80.0
    payroll_owed = [
        name
        for name in wc
        if "Payable" in name and name not in ("Accounts Payable", "Sales Tax Payable")
    ]
    assert payroll_owed, wc  # withholdings and employer taxes, not yet paid
    # none of it anywhere else
    elsewhere = _names(report["investing"]) | _names(report["financing"])
    for name in ("Undeposited Funds", "Accounts Receivable", "Accounts Payable"):
        assert name not in elsewhere
    assert not set(payroll_owed) & set(elsewhere)
    # operating cash = the deposit less the bill paid less the net pay
    assert Decimal(str(report["total_operating"])) == (
        Decimal("1000") - Decimal("400") - books["net_pay"]
    )


def test_depreciation_is_added_back_and_the_plotter_is_investing(client, books):
    report = _report(client)
    assert _names(report["adjustments"]) == {
        "Depreciation (Accumulated Depreciation)": 100.0  # 6,000 / 60 x 1
    }
    assert _names(report["investing"]) == {"Equipment": -6000.0}
    assert report["total_investing"] == -6000.0


def test_loans_and_the_owners_money_are_financing(client, books):
    report = _report(client)
    assert _names(report["financing"]) == {
        "Owner's Equity": 5000.0,
        "Owner's Draw": -700.0,
        "Loan Payable": 20000.0,
    }
    assert report["total_financing"] == 24300.0


def test_selling_a_fixed_asset_is_investing_and_its_loss_is_added_back(
    client, db_session, seed_accounts
):
    [etype] = client.get("/api/fixed-assets/types").json()
    asset = _ok(
        client.post(
            "/api/fixed-assets",
            json={
                "name": "Old plotter",
                "asset_type_id": etype["id"],
                "purchase_date": "2025-01-01",
                "purchase_price": "3000",
                "acquisition": {
                    "method": "paid_from",
                    "account_id": seed_accounts["1000"].id,
                },
            },
        )
    )
    _ok(
        client.post(
            "/api/fixed-assets/run-depreciation", json={"run_date": "2026-01-01"}
        )
    )
    # book value 3,000 - 600 = 2,400; sold for 2,000: a 400 loss
    _ok(
        client.post(
            f"/api/fixed-assets/{asset['id']}/dispose",
            json={
                "disposal_date": "2026-02-15",
                "proceeds": 2000,
                "deposit_account_id": seed_accounts["1000"].id,
            },
        )
    )
    report = _report(client)
    assert report["net_income"] == -400.0
    assert _names(report["adjustments"]) == {"Loss on disposal of fixed assets": 400.0}
    assert _names(report["investing"]) == {
        "Proceeds from disposal of fixed assets": 2000.0
    }
    assert report["total_operating"] == 0.0
    assert Decimal(str(report["net_change"])) == _cash_change_in_ledger(db_session)


def test_the_screen_shows_net_income_the_groups_and_the_cash_balances():
    body = JS[JS.index("async cashFlow(prefill)") :]
    body = body[: body.index("\n    },\n")]
    assert "T('Net Income')" in body and "data.net_income" in body
    assert "Adjustments for non-cash items" in body and "data.adjustments" in body
    assert "Changes in working capital" in body and "data.working_capital" in body
    assert "data.beginning_cash" in body and "data.ending_cash" in body
