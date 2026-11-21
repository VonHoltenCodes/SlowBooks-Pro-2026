"""Books Check and documents dated after today (round 29, W-242).

A customer payment dated next week was waiting on the Make Deposits list
and not yet in Undeposited Funds as of today, so the row read "The payments
waiting to be deposited add up to $100.00 and 1200 Undeposited Funds is
$0.00, $100.00 apart" on books that agreed.

The rule every row keeps now: two figures are set side by side only when
both are taken over the same documents, so a document dated after the day
the check is run as of is in both figures or in neither. A dated row (the
agings, sales tax, payroll liabilities, the open accruals) reads both to
the as-of day: neither. A row kept for today only (customer balances,
vendor balances, the payments waiting to be deposited, the inventory
valuation) sets the app's running state beside the whole ledger: both, and
its sentence says how much of the figure is dated after today.

A real difference still reads "apart", with the same documents, a
post-dated document present or not. Every seeded difference is a direct
database write, as in tests/test_books_check_893.py.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.bills import Bill
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice, InvoiceStatus
from app.models.items import Item
from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import books_check

TODAY = date.today()
AHEAD = TODAY + timedelta(days=9)
PAST = TODAY - timedelta(days=20)
TODAY_ONLY = ("customer_balances", "vendor_balances", "undeposited_funds", "inventory")


def _ok(r):
    assert r.status_code in (200, 201), (str(r.request.url), r.status_code, r.text)
    return r.json()


@pytest.fixture
def co(client, db_session, seed_accounts):
    """A fresh company: a customer, a vendor and ten of a stock item."""
    customer = Customer(name="Dockside Marina", is_active=True)
    vendor = Vendor(name="Brookside Nursery", is_active=True)
    db_session.add_all([customer, vendor])
    db_session.commit()
    item = _ok(
        client.post(
            "/api/items",
            json={
                "name": "Cedar Planter",
                "item_type": "product",
                "rate": 20,
                "cost": 5,
                "is_taxable": True,
                "track_inventory": True,
                "quantity_on_hand": 10,
            },
        )
    )["id"]
    return {
        "client": client,
        "db": db_session,
        "a": {n: acct.id for n, acct in seed_accounts.items()},
        "customer": customer.id,
        "vendor": vendor.id,
        "item": item,
    }


def _rows(co, as_of=None):
    """Books Check as the page reads it."""
    url = "/api/reports/books-check"
    if as_of is not None:
        url += f"?as_of={as_of.isoformat()}"
    return {r["key"]: r for r in _ok(co["client"].get(url))["rows"]}


def _apart(rows):
    return {k: r["sentence"] for k, r in rows.items() if r["status"] == "difference"}


def _only_difference(rows, *keys):
    for k, r in rows.items():
        if k in keys:
            assert r["status"] == "difference", r
        else:
            assert r["status"] in ("pass", "not_checked"), r


# ── the documents, each through the API as a person enters it ─────────────


def _invoice(co, when, amount=100, lines=None, **extra):
    return _ok(
        co["client"].post(
            "/api/invoices",
            json={
                "customer_id": co["customer"],
                "date": when.isoformat(),
                "due_date": (when + timedelta(days=30)).isoformat(),
                "lines": lines
                or [{"description": "Dock repair", "quantity": 1, "rate": amount}],
                **extra,
            },
        )
    )["id"]


def _taxed_invoice(co, when):
    return _invoice(
        co,
        when,
        tax_rate=0.08,
        lines=[
            {
                "description": "Dock repair",
                "quantity": 1,
                "rate": 100,
                "is_taxable": True,
            }
        ],
    )


def _stock_invoice(co, when):
    return _invoice(
        co,
        when,
        lines=[
            {
                "item_id": co["item"],
                "description": "Cedar Planter",
                "quantity": 2,
                "rate": 20,
            }
        ],
    )


def _payment(co, when, amount=100, invoice_id=None, bank=False):
    body = {"customer_id": co["customer"], "date": when.isoformat(), "amount": amount}
    if invoice_id is not None:
        body["allocations"] = [{"invoice_id": invoice_id, "amount": amount}]
    if bank:
        body["deposit_to_account_id"] = co["a"]["1000"]
    return _ok(co["client"].post("/api/payments", json=body))["id"]


def _credit_memo(co, when):
    return _ok(
        co["client"].post(
            "/api/credit-memos",
            json={
                "customer_id": co["customer"],
                "date": when.isoformat(),
                "tax_rate": 0.08,
                "lines": [
                    {
                        "description": "Returned cleat",
                        "quantity": 1,
                        "rate": 45,
                        "is_taxable": True,
                    }
                ],
            },
        )
    )["id"]


def _sales_receipt(co, when):
    return _ok(
        co["client"].post(
            "/api/sales-receipts",
            json={
                "customer_id": co["customer"],
                "date": when.isoformat(),
                "tax_rate": 0.08,
                "method": "card",
                "lines": [
                    {
                        "item_id": co["item"],
                        "description": "Cedar Planter",
                        "quantity": 3,
                        "rate": 20,
                        "is_taxable": True,
                    }
                ],
            },
        )
    )["invoice"]["id"]


def _deposit(co, when, total=None, line_ids=None):
    body = {"deposit_to_account_id": co["a"]["1000"], "date": when.isoformat()}
    if total is not None:
        body["total"] = str(total)
    if line_ids is not None:
        body["line_ids"] = line_ids
    return _ok(co["client"].post("/api/deposits", json=body))["transaction_id"]


def _bill(co, when, amount=70, lines=None):
    return _ok(
        co["client"].post(
            "/api/bills",
            json={
                "vendor_id": co["vendor"],
                "date": when.isoformat(),
                "due_date": (when + timedelta(days=30)).isoformat(),
                "lines": lines
                or [
                    {
                        "account_id": co["a"]["6000"],
                        "amount": amount,
                        "description": "Mulch",
                    }
                ],
            },
        )
    )["id"]


def _stock_bill(co, when):
    """An inventory receipt: four planters at $5."""
    return _bill(
        co,
        when,
        lines=[
            {
                "item_id": co["item"],
                "description": "Cedar Planter",
                "quantity": 4,
                "rate": 5,
            }
        ],
    )


def _bill_payment(co, when, bill_id, amount=70):
    return _ok(
        co["client"].post(
            "/api/bill-payments",
            json={
                "vendor_id": co["vendor"],
                "date": when.isoformat(),
                "amount": amount,
                "pay_from_account_id": co["a"]["1000"],
                "allocations": [{"bill_id": bill_id, "amount": amount}],
            },
        )
    )["id"]


def _vendor_credit(co, when):
    return _ok(
        co["client"].post(
            "/api/vendor-credits",
            json={
                "vendor_id": co["vendor"],
                "date": when.isoformat(),
                "lines": [
                    {
                        "description": "Short-shipped bag",
                        "quantity": 1,
                        "rate": 38.5,
                        "account_id": co["a"]["5000"],
                    }
                ],
            },
        )
    )["id"]


def _expense(co, when):
    return _ok(
        co["client"].post(
            "/api/expenses",
            json={
                "date": when.isoformat(),
                "expense_account_id": co["a"]["6000"],
                "paid_from_account_id": co["a"]["1000"],
                "amount": "30.30",
                "vendor_id": co["vendor"],
            },
        )
    )["id"]


def _card_charge(co, when):
    return _ok(
        co["client"].post(
            "/api/cc-charges",
            json={
                "date": when.isoformat(),
                "payee": "Fuel dock",
                "amount": "40",
                "account_id": co["a"]["6000"],
            },
        )
    )["transaction_id"]


def _journal(co, when, debit, credit, amount=55):
    a = co["a"]
    return _ok(
        co["client"].post(
            "/api/journal",
            json={
                "date": when.isoformat(),
                "description": "Entered ahead",
                "lines": [
                    {"account_id": a[debit], "debit": str(amount), "credit": "0"},
                    {"account_id": a[credit], "debit": "0", "credit": str(amount)},
                ],
            },
        )
    )["id"]


def _pay_run(co, when):
    client = co["client"]
    employee = _ok(
        client.post(
            "/api/employees",
            json={
                "first_name": "Lena",
                "last_name": "Ortiz",
                "pay_frequency": "biweekly",
                "filing_status": "single",
                "state": "OR",
                "work_state": "OR",
                "residence_state": "OR",
                "role": "employee",
                "pay_type": "salary",
                "pay_rate": 56000,
            },
        )
    )["id"]
    run = _ok(
        client.post(
            "/api/payroll",
            json={
                "period_start": (TODAY - timedelta(days=14)).isoformat(),
                "period_end": TODAY.isoformat(),
                "pay_date": when.isoformat(),
                "stubs": [{"employee_id": employee}],
            },
        )
    )["id"]
    _ok(client.post(f"/api/payroll/{run}/process"))
    return run


def _seeded(db, lines, source_type, when):
    """A journal transaction written straight to the database: what the
    posting code would refuse to make."""
    txn = Transaction(date=when, description="seeded", source_type=source_type)
    db.add(txn)
    db.flush()
    for account_id, debit, credit in lines:
        db.add(
            TransactionLine(
                transaction_id=txn.id,
                account_id=account_id,
                debit=Decimal(debit),
                credit=Decimal(credit),
            )
        )
    db.commit()
    return txn


# One post-dated document of every kind. Each leaves the books agreeing, so
# no row may read "apart".
def _doc_payment_waiting(co):
    _payment(co, AHEAD)


def _doc_payment_on_an_invoice_due(co):
    _payment(co, AHEAD, invoice_id=_invoice(co, PAST))


def _doc_payment_to_the_bank(co):
    _payment(co, AHEAD, invoice_id=_invoice(co, PAST), bank=True)


def _doc_deposit_of_todays_payment(co):
    _payment(co, TODAY)
    _deposit(co, AHEAD, total=100)


def _doc_deposit_of_ticked_payments(co):
    _payment(co, TODAY)
    _payment(co, AHEAD, amount=30)
    pending = _ok(co["client"].get("/api/deposits/pending"))
    _deposit(co, AHEAD, line_ids=[p["transaction_line_id"] for p in pending])


def _doc_bill_payment(co):
    _bill_payment(co, AHEAD, _bill(co, PAST))


def _doc_journal_on_undeposited_funds(co):
    _journal(co, AHEAD, "1200", "4000")


def _doc_journal_on_payroll_liabilities(co):
    _journal(co, AHEAD, "6000", "2310")


def _doc_journal_on_sales_tax(co):
    _journal(co, AHEAD, "6000", "2200")


POST_DATED = {
    "customer payment, waiting": _doc_payment_waiting,
    "customer payment on an invoice due": _doc_payment_on_an_invoice_due,
    "customer payment to the bank": _doc_payment_to_the_bank,
    "invoice": lambda co: _taxed_invoice(co, AHEAD),
    "invoice selling stock": lambda co: _stock_invoice(co, AHEAD),
    "credit memo": lambda co: _credit_memo(co, AHEAD),
    "sales receipt": lambda co: _sales_receipt(co, AHEAD),
    "deposit of today's payment": _doc_deposit_of_todays_payment,
    "deposit of ticked payments": _doc_deposit_of_ticked_payments,
    "bill": lambda co: _bill(co, AHEAD),
    "bill receiving stock": lambda co: _stock_bill(co, AHEAD),
    "bill payment": _doc_bill_payment,
    "vendor credit": lambda co: _vendor_credit(co, AHEAD),
    "expense": lambda co: _expense(co, AHEAD),
    "card charge": lambda co: _card_charge(co, AHEAD),
    "journal entry on Undeposited Funds": _doc_journal_on_undeposited_funds,
    "journal entry on a payroll liability": _doc_journal_on_payroll_liabilities,
    "journal entry on Sales Tax Payable": _doc_journal_on_sales_tax,
    "pay run": lambda co: _pay_run(co, AHEAD),
}


@pytest.mark.parametrize("kind", sorted(POST_DATED))
def test_a_post_dated_document_of_any_kind_leaves_every_row_agreeing(co, kind):
    POST_DATED[kind](co)
    rows = _rows(co)
    assert _apart(rows) == {}
    for key in TODAY_ONLY:
        assert rows[key]["status"] == "pass", rows[key]


def test_every_kind_at_once_leaves_every_row_agreeing(co):
    for kind in sorted(POST_DATED):
        if kind != "pay run":  # one employee is enough for the module
            POST_DATED[kind](co)
    assert _apart(_rows(co)) == {}


# ── the payments waiting to be deposited = Undeposited Funds ──────────────


def test_w242_a_post_dated_payment_is_in_both_figures(co):
    _payment(co, AHEAD)
    uf = _rows(co)["undeposited_funds"]
    assert uf["status"] == "pass", uf
    # the list's own figure (the payment is waiting and can be deposited)
    # and the account over the whole ledger
    assert (uf["subledger"], uf["control"], uf["difference"]) == (100.0, 100.0, 0.0)
    assert uf["sentence"] == (
        "The payments waiting to be deposited add up to 1200 Undeposited "
        "Funds, counting $100.00 dated after today."
    )


@pytest.mark.parametrize(
    "kind,waiting",
    [
        ("customer payment, waiting", 100.0),
        ("customer payment on an invoice due", 100.0),
        ("sales receipt", 64.8),
        ("journal entry on Undeposited Funds", 55.0),
        ("deposit of today's payment", 0.0),
        ("deposit of ticked payments", 0.0),
    ],
)
def test_each_post_dated_document_on_undeposited_funds_agrees(co, kind, waiting):
    POST_DATED[kind](co)
    uf = _rows(co)["undeposited_funds"]
    assert uf["status"] == "pass", uf
    assert uf["subledger"] == uf["control"] == waiting
    # the figure is what the Make Deposits page adds up to
    pending = _ok(co["client"].get("/api/deposits/pending"))
    assert round(sum(p["amount"] for p in pending), 2) == waiting


def test_a_post_dated_deposit_says_what_it_takes_off_the_account(co):
    _payment(co, TODAY)
    _deposit(co, AHEAD, total=100)
    uf = _rows(co)["undeposited_funds"]
    # the Balance Sheet as of today still shows the $100.00 on 1200
    assert uf["sentence"] == (
        "The payments waiting to be deposited add up to 1200 Undeposited "
        "Funds, counting $100.00 taken off it by entries dated after today."
    )


def test_nothing_post_dated_leaves_the_sentence_as_it_was(co):
    _payment(co, TODAY)
    uf = _rows(co)["undeposited_funds"]
    assert uf["sentence"] == (
        "The payments waiting to be deposited add up to 1200 Undeposited Funds."
    )


def test_a_purchase_line_on_undeposited_funds_is_still_named(co):
    """The difference the row exists to catch, a line on 1200 that is not a
    customer's payment, with a post-dated payment present as well."""
    a = co["a"]
    stray = _seeded(
        co["db"], [(a["1200"], "35", "0"), (a["1000"], "0", "35")], "expense", PAST
    )
    before = _rows(co)["undeposited_funds"]
    _payment(co, AHEAD)
    rows = _rows(co)
    _only_difference(rows, "undeposited_funds")
    uf = rows["undeposited_funds"]
    assert (uf["subledger"], uf["control"], uf["difference"]) == (100.0, 135.0, -35.0)
    assert [(d["type"], d["id"], d["amount"]) for d in uf["documents"]] == [
        ("expense", stray.id, 35.0)
    ]
    # the same difference and the same documents as without the payment
    assert before["status"] == "difference" and before["difference"] == -35.0
    assert uf["documents"] == before["documents"]
    assert uf["sentence"].startswith(
        "The payments waiting to be deposited add up to $100.00 and 1200 "
        "Undeposited Funds is $135.00, counting $100.00 dated after today, "
        "$35.00 apart. Look at the lines below"
    )


def test_a_post_dated_purchase_line_on_undeposited_funds_is_named_too(co):
    """A real difference that is itself dated after today is in the
    account's figure like every other entry, and is named now, not on the
    day it comes due."""
    a = co["a"]
    stray = _seeded(
        co["db"], [(a["1200"], "35", "0"), (a["1000"], "0", "35")], "cc_charge", AHEAD
    )
    rows = _rows(co)
    _only_difference(rows, "undeposited_funds")
    uf = rows["undeposited_funds"]
    assert uf["difference"] == -35.0
    assert [(d["type"], d["id"]) for d in uf["documents"]] == [("cc_charge", stray.id)]


# ── customer balances = Accounts Receivable ───────────────────────────────


def test_the_customer_row_counts_a_post_dated_invoice_on_both_sides(co):
    _invoice(co, PAST, amount=60)
    _invoice(co, AHEAD, amount=300)
    rows = _rows(co)
    assert _apart(rows) == {}
    row = rows["customer_balances"]
    # what the customer's own page shows: both invoices
    assert (row["subledger"], row["control"]) == (360.0, 360.0)
    assert row["sentence"] == (
        "The customers' balances add up to 1100 Accounts Receivable, "
        "counting $300.00 dated after today."
    )
    listed = _ok(co["client"].get("/api/customers"))
    listed = listed["customers"] if isinstance(listed, dict) else listed
    balance = next(c["balance"] for c in listed if c["id"] == co["customer"])
    assert float(balance) == 360.0
    # the dated row beside it reads both figures to today: neither
    aging = rows["ar_aging"]
    assert (aging["subledger"], aging["control"]) == (60.0, 60.0)


@pytest.mark.parametrize(
    "kind,balance,counting",
    [
        (
            "customer payment to the bank",
            0.0,
            ", counting $100.00 taken off it by entries dated after today",
        ),
        (
            "credit memo",
            -48.6,
            ", counting $48.60 taken off it by entries dated after today",
        ),
        ("invoice", 108.0, ", counting $108.00 dated after today"),
    ],
)
def test_each_post_dated_document_on_the_customer_row_agrees(
    co, kind, balance, counting
):
    POST_DATED[kind](co)
    row = _rows(co)["customer_balances"]
    assert row["status"] == "pass", row
    assert row["subledger"] == row["control"] == balance
    assert row["sentence"] == (
        f"The customers' balances add up to 1100 Accounts Receivable{counting}."
    )


def test_an_invoice_whose_balance_drifted_is_still_named(co):
    inv_id = _invoice(co, PAST, amount=60)
    inv = co["db"].get(Invoice, inv_id)
    inv.status = InvoiceStatus.SENT
    co["db"].commit()
    inv.balance_due = Decimal(str(inv.balance_due)) + Decimal("50")
    co["db"].commit()
    before = _rows(co)
    _invoice(co, AHEAD, amount=300)
    _payment(co, AHEAD, amount=20)
    rows = _rows(co)
    _only_difference(rows, "customer_balances", "ar_aging")
    row = rows["customer_balances"]
    assert row["difference"] == 50.0 == before["customer_balances"]["difference"]
    assert row["documents"] == before["customer_balances"]["documents"]
    assert [(d["type"], d["id"]) for d in row["documents"]] == [("invoice", inv_id)]
    assert ", counting $280.00 dated after today, $50.00 apart." in row["sentence"]
    assert rows["ar_aging"]["documents"] == before["ar_aging"]["documents"]


# ── vendor balances = Accounts Payable ────────────────────────────────────


def test_the_vendor_row_counts_a_post_dated_bill_on_both_sides(co):
    _bill(co, PAST, amount=45)
    _bill(co, AHEAD, amount=70)
    rows = _rows(co)
    assert _apart(rows) == {}
    row = rows["vendor_balances"]
    assert (row["subledger"], row["control"]) == (115.0, 115.0)
    assert row["sentence"] == (
        "The vendors' balances add up to 2000 Accounts Payable, counting "
        "$70.00 dated after today."
    )
    aging = rows["ap_aging"]
    assert (aging["subledger"], aging["control"]) == (45.0, 45.0)


@pytest.mark.parametrize(
    "kind,balance,counting",
    [
        (
            "bill payment",
            0.0,
            ", counting $70.00 taken off it by entries dated after today",
        ),
        (
            "vendor credit",
            -38.5,
            ", counting $38.50 taken off it by entries dated after today",
        ),
        ("bill receiving stock", 20.0, ", counting $20.00 dated after today"),
    ],
)
def test_each_post_dated_document_on_the_vendor_row_agrees(co, kind, balance, counting):
    POST_DATED[kind](co)
    row = _rows(co)["vendor_balances"]
    assert row["status"] == "pass", row
    assert row["subledger"] == row["control"] == balance
    assert row["sentence"] == (
        f"The vendors' balances add up to 2000 Accounts Payable{counting}."
    )


def test_a_bill_whose_balance_drifted_is_still_named(co):
    bill_id = _bill(co, PAST, amount=45)
    bill = co["db"].get(Bill, bill_id)
    bill.balance_due = Decimal(str(bill.balance_due)) + Decimal("15")
    co["db"].commit()
    before = _rows(co)
    _bill(co, AHEAD, amount=70)
    rows = _rows(co)
    _only_difference(rows, "vendor_balances", "ap_aging")
    row = rows["vendor_balances"]
    assert row["difference"] == 15.0 == before["vendor_balances"]["difference"]
    assert row["documents"] == before["vendor_balances"]["documents"]
    assert [(d["type"], d["id"]) for d in row["documents"]] == [("bill", bill_id)]
    assert ", counting $70.00 dated after today, $15.00 apart." in row["sentence"]


# ── inventory valuation = Inventory ───────────────────────────────────────


@pytest.mark.parametrize(
    "kind,value,counting",
    [
        ("bill receiving stock", 70.0, ", counting $20.00 dated after today"),
        (
            "invoice selling stock",
            40.0,
            ", counting $10.00 taken off it by entries dated after today",
        ),
        (
            "sales receipt",
            35.0,
            ", counting $15.00 taken off it by entries dated after today",
        ),
    ],
)
def test_the_inventory_row_says_what_is_dated_after_today(co, kind, value, counting):
    POST_DATED[kind](co)
    row = _rows(co)["inventory"]
    assert row["status"] == "pass", row
    # the stock is on hand (or gone) now, and the ledger is read whole
    assert row["subledger"] == row["control"] == value
    assert row["sentence"] == (
        f"The inventory valuation equals the ledger's Inventory{counting}."
    )


def test_an_item_whose_count_drifted_is_still_named(co):
    # the receipt first: a movement entered after the drift would carry the
    # drifted count forward, whatever its date
    _stock_bill(co, AHEAD)
    item = co["db"].get(Item, co["item"])
    item.quantity_on_hand = Decimal(str(item.quantity_on_hand)) + Decimal("5")
    co["db"].commit()
    rows = _rows(co)
    _only_difference(rows, "inventory")
    row = rows["inventory"]
    assert (row["subledger"], row["control"], row["difference"]) == (95.0, 70.0, 25.0)
    assert [(d["type"], d["id"], d["amount"]) for d in row["documents"]] == [
        ("item", co["item"], 25.0)
    ]
    assert ", counting $20.00 dated after today, $25.00 apart." in row["sentence"]


# ── the dated rows, and a past day ────────────────────────────────────────


def test_the_dated_rows_leave_a_post_dated_document_out_of_both_figures(co):
    _taxed_invoice(co, PAST)
    _taxed_invoice(co, AHEAD)
    _bill(co, AHEAD)
    _journal(co, AHEAD, "6000", "2310")
    rows = _rows(co)
    assert _apart(rows) == {}
    for key, figure in (
        ("ar_aging", 108.0),
        ("ap_aging", 0.0),
        ("sales_tax", 8.0),
        ("payroll_liabilities", 0.0),
    ):
        assert (rows[key]["subledger"], rows[key]["control"]) == (figure, figure)
        assert "dated after today" not in rows[key]["sentence"]


def test_a_real_difference_on_a_dated_row_is_named_beside_a_post_dated_one(co):
    """A journal entry on Sales Tax Payable dated last month is a
    difference; the same entry dated next week is neither figure's yet."""
    a = co["a"]
    stray = _seeded(
        co["db"], [(a["6000"], "12", "0"), (a["2200"], "0", "12")], "manual", PAST
    )
    _seeded(co["db"], [(a["6000"], "7", "0"), (a["2200"], "0", "7")], "manual", AHEAD)
    _taxed_invoice(co, AHEAD)
    rows = _rows(co)
    _only_difference(rows, "sales_tax")
    tax = rows["sales_tax"]
    assert (tax["subledger"], tax["control"]) == (0.0, 12.0)
    assert [(d["type"], d["id"]) for d in tax["documents"]] == [("journal", stray.id)]


def test_a_past_day_reads_as_it_did(co):
    """As of a day gone by: the dated rows read both figures to that day,
    and the rows kept for today only are not compared and say why, in the
    words they always had."""
    _taxed_invoice(co, PAST)
    _bill(co, PAST)
    _payment(co, TODAY)  # after the as-of day
    _payment(co, AHEAD)
    _invoice(co, AHEAD, amount=300)
    _stock_bill(co, AHEAD)
    as_of = TODAY - timedelta(days=3)
    rows = _rows(co, as_of)
    assert _apart(rows) == {}
    for key, figure in (("ar_aging", 108.0), ("ap_aging", 70.0), ("sales_tax", 8.0)):
        assert rows[key]["status"] == "pass", rows[key]
        assert (rows[key]["subledger"], rows[key]["control"]) == (figure, figure)
    assert rows["ar_aging"]["sentence"] == (
        f"The A/R Aging total equals 1100 Accounts Receivable as of "
        f"{as_of.isoformat()}."
    )
    what = {
        "customer_balances": "A customer's balance",
        "vendor_balances": "A vendor's balance",
        "undeposited_funds": "The list of payments waiting to be deposited",
        "inventory": "The inventory valuation (quantity on hand × average cost)",
    }
    for key in TODAY_ONLY:
        row = rows[key]
        assert row["status"] == "not_checked", row
        assert (row["subledger"], row["control"], row["difference"]) == (
            None,
            None,
            None,
        )
        assert row["sentence"] == (
            f"{what[key]} is kept for today only, so it cannot be set beside "
            f"the ledger as of {as_of.isoformat()}. Run Books Check as of "
            "today to compare them."
        )
    # the service the page calls answers the same
    co["db"].expire_all()
    assert books_check(co["db"], as_of)["rows"] == list(rows.values())


def test_a_real_difference_on_a_past_day_is_named_as_before(co):
    a = co["a"]
    stray = _seeded(
        co["db"], [(a["1100"], "60", "0"), (a["4000"], "0", "60")], "manual", PAST
    )
    _payment(co, AHEAD)
    rows = _rows(co, TODAY - timedelta(days=3))
    _only_difference(rows, "ar_aging")
    aging = rows["ar_aging"]
    assert (aging["subledger"], aging["control"]) == (0.0, 60.0)
    assert [(d["type"], d["id"], d["amount"]) for d in aging["documents"]] == [
        ("journal", stray.id, 60.0)
    ]
