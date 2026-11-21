"""Books Check and documents dated after today (round 29, W-242).

A customer payment dated next week was waiting on the Make Deposits list
and not yet in Undeposited Funds as of today, so the row read "The payments
waiting to be deposited add up to $100.00 and 1200 Undeposited Funds is
$0.00, $100.00 apart" on books that agreed. The list has no date; the
account beside it was read to today.

The rule now: every row reads both of its figures to the as-of day, so a
document dated after it is in neither. The Undeposited Funds row was the
one that did not, and it now takes the list as it stood on that day: a
payment dated later is not waiting yet, and a deposit dated later has taken
nothing yet. When the Make Deposits page shows more than the row does, the
row says how much ("not counting $100.00 dated after today"). Inventory is
the one exception and is unchanged: it is kept for today only, valuation
and ledger both read whole.

Nothing else about Books Check moves. tests/fixtures/books_check_r30_base.json
is the whole answer as the code before this change gave it (commit
0633db03), for books with nothing dated after today and for the differences
the Undeposited Funds row exists to catch; the answer now is the same, row
for row. Every seeded difference is a direct database write, as in
tests/test_books_check_893.py.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.contacts import Customer, Vendor
from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import books_check
from app.services.undeposited_funds import waiting_balance, waiting_items

TODAY = date.today()
AHEAD = TODAY + timedelta(days=9)
PAST = TODAY - timedelta(days=20)
TODAY_ONLY = ("customer_balances", "vendor_balances", "undeposited_funds", "inventory")
BASE_ANSWERS = Path(__file__).parent / "fixtures" / "books_check_r30_base.json"
AGREE = "The payments waiting to be deposited add up to 1200 Undeposited Funds"


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


def _doc_typed_deposit_of_todays_payment(co):
    _payment(co, TODAY)
    _deposit(co, AHEAD, total=100)


def _doc_ticked_deposit_of_todays_payment(co):
    _payment(co, TODAY)
    _deposit(co, AHEAD, line_ids=_pending_line_ids(co))


def _doc_deposit_of_ticked_payments(co):
    _payment(co, TODAY)
    _payment(co, AHEAD, amount=30)
    _deposit(co, AHEAD, line_ids=_pending_line_ids(co))


def _doc_bill_payment(co):
    _bill_payment(co, AHEAD, _bill(co, PAST))


def _doc_journal_on_undeposited_funds(co):
    _journal(co, AHEAD, "1200", "4000")


def _doc_journal_on_payroll_liabilities(co):
    _journal(co, AHEAD, "6000", "2310")


def _doc_journal_on_sales_tax(co):
    _journal(co, AHEAD, "6000", "2200")


def _pending(co):
    """The Make Deposits list, as its page reads it."""
    return _ok(co["client"].get("/api/deposits/pending"))


def _pending_line_ids(co):
    return [p["transaction_line_id"] for p in _pending(co)]


POST_DATED = {
    "customer payment, waiting": _doc_payment_waiting,
    "customer payment on an invoice due": _doc_payment_on_an_invoice_due,
    "customer payment to the bank": _doc_payment_to_the_bank,
    "invoice": lambda co: _taxed_invoice(co, AHEAD),
    "invoice selling stock": lambda co: _stock_invoice(co, AHEAD),
    "credit memo": lambda co: _credit_memo(co, AHEAD),
    "sales receipt": lambda co: _sales_receipt(co, AHEAD),
    "typed deposit of today's payment": _doc_typed_deposit_of_todays_payment,
    "ticked deposit of today's payment": _doc_ticked_deposit_of_todays_payment,
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


def test_w242_a_post_dated_payment_is_in_neither_figure(co):
    _payment(co, AHEAD)
    uf = _rows(co)["undeposited_funds"]
    assert uf["status"] == "pass", uf
    # not waiting yet as of today, and not in the account as of today
    assert (uf["subledger"], uf["control"], uf["difference"]) == (0.0, 0.0, 0.0)
    assert uf["sentence"] == AGREE + ", not counting $100.00 dated after today."
    assert uf["documents"] == []


# kind -> (the row's two figures, what the Make Deposits page adds up to,
# what the row says it leaves out)
ON_UNDEPOSITED_FUNDS = {
    "customer payment, waiting": (0.0, 100.0, "$100.00"),
    "customer payment on an invoice due": (0.0, 100.0, "$100.00"),
    "sales receipt": (0.0, 64.8, "$64.80"),
    "journal entry on Undeposited Funds": (0.0, 55.0, "$55.00"),
    # a deposit dated ahead has taken nothing yet: today's payment still
    # waits as of today and is still in the account as of today
    "typed deposit of today's payment": (100.0, 0.0, None),
    "ticked deposit of today's payment": (100.0, 0.0, None),
    # ... and the post-dated payment it also took is in neither figure and
    # no longer on the Make Deposits list, so there is nothing to say
    "deposit of ticked payments": (100.0, 0.0, None),
}


@pytest.mark.parametrize("kind", sorted(ON_UNDEPOSITED_FUNDS))
def test_each_post_dated_document_on_undeposited_funds_agrees(co, kind):
    figure, on_the_page, left_out = ON_UNDEPOSITED_FUNDS[kind]
    POST_DATED[kind](co)
    uf = _rows(co)["undeposited_funds"]
    assert uf["status"] == "pass", uf
    assert (uf["subledger"], uf["control"], uf["difference"]) == (figure, figure, 0.0)
    assert round(sum(p["amount"] for p in _pending(co)), 2) == on_the_page
    clause = f", not counting {left_out} dated after today" if left_out else ""
    assert uf["sentence"] == f"{AGREE}{clause}."
    # the account's figure is the Balance Sheet's as of today
    db = co["db"]
    db.expire_all()
    on_1200 = sum(
        (
            Decimal(str(ln.debit)) - Decimal(str(ln.credit))
            for ln, txn in db.query(TransactionLine, Transaction)
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(TransactionLine.account_id == co["a"]["1200"])
            if txn.date <= TODAY
        ),
        Decimal("0"),
    )
    assert float(on_1200) == uf["control"]


def test_the_row_says_once_what_it_leaves_out_beside_what_it_counts(co):
    _payment(co, TODAY, amount=40)
    _payment(co, AHEAD, amount=100)
    _sales_receipt(co, AHEAD)
    uf = _rows(co)["undeposited_funds"]
    assert uf["status"] == "pass", uf
    assert (uf["subledger"], uf["control"]) == (40.0, 40.0)
    assert uf["sentence"] == AGREE + ", not counting $164.80 dated after today."
    assert uf["sentence"].count("dated after today") == 1
    assert round(sum(p["amount"] for p in _pending(co)), 2) == 204.8


def test_nothing_post_dated_leaves_the_sentence_as_it_was(co):
    _payment(co, TODAY)
    uf = _rows(co)["undeposited_funds"]
    assert (uf["subledger"], uf["control"]) == (100.0, 100.0)
    assert uf["sentence"] == AGREE + "."


def _line_of_new_payment(co, when, amount):
    """Receive a payment and answer its line on the Make Deposits list."""
    before = set(_pending_line_ids(co))
    _payment(co, when, amount=amount)
    (line,) = set(_pending_line_ids(co)) - before
    return line


# what the deposit dated today took (dated ahead), what still waits today
# and earlier -> the row's two figures
A_DEPOSIT_TODAY_OF_A_POST_DATED_PAYMENT = {
    "the same amount still waits": (100, [(TODAY, 100)], (100.0, 0.0)),
    "more than it took still waits": (80, [(TODAY, 100)], (100.0, 20.0)),
    "two payments still wait": (100, [(TODAY, 60), (PAST, 40)], (100.0, 0.0)),
    "nothing else waits": (100, [], (0.0, -100.0)),
}


@pytest.mark.parametrize("case", sorted(A_DEPOSIT_TODAY_OF_A_POST_DATED_PAYMENT))
def test_a_deposit_today_that_named_a_post_dated_payment_took_only_that(co, case):
    """A deposit dated today that ticked a payment dated next week has
    taken that payment and nothing else: its credit is on 1200 today and
    is not netted against another payment that waits. The row reads apart
    whether or not other money happens to wait (round 30 rework)."""
    took, others, figures = A_DEPOSIT_TODAY_OF_A_POST_DATED_PAYMENT[case]
    ahead = _line_of_new_payment(co, AHEAD, took)
    for when, amount in others:
        _payment(co, when, amount=amount)
    _deposit(co, TODAY, line_ids=[ahead])
    uf = _rows(co)["undeposited_funds"]
    assert uf["status"] == "difference", uf
    assert (uf["subledger"], uf["control"]) == figures
    # the Make Deposits page shows the same waiting payments: nothing is
    # left out, so the sentence has no clause
    assert "dated after today" not in uf["sentence"]
    assert round(sum(p["amount"] for p in _pending(co)), 2) == figures[0]
    db, uf_id = co["db"], co["a"]["1200"]
    db.expire_all()
    assert float(waiting_balance(db, uf_id, TODAY)) == figures[0]
    assert waiting_balance(db, uf_id, TODAY) == waiting_balance(db, uf_id)


# payments (day, amount), typed deposits (day, total) -> the row's status
# and waiting figure, and what the sentence says it leaves out
NOT_COUNTING = {
    # a typed deposit dated ahead takes today's payment on the Make
    # Deposits page, which then shows the post-dated one: 100 either way
    "typed deposit ahead of as much": (
        [(TODAY, 100), (AHEAD, 100)],
        [(AHEAD, 100)],
        ("pass", 100.0, None),
    ),
    # 40 of the post-dated payment is what that page still shows
    "typed deposit ahead of part of a post-dated payment": (
        [(AHEAD, 100)],
        [(AHEAD, 60)],
        ("pass", 0.0, "$40.00"),
    ),
    "typed deposit ahead with older and post-dated money": (
        [(PAST, 30), (AHEAD, 100)],
        [(AHEAD, 30)],
        ("pass", 30.0, "$70.00"),
    ),
    # the page shows less than the row: the deposit dated ahead has taken
    # more than is dated ahead, and the row does not claim to leave it out
    "typed deposit ahead of more than is post-dated": (
        [(TODAY, 100), (AHEAD, 50)],
        [(AHEAD, 100)],
        ("pass", 100.0, None),
    ),
    "typed deposit today over what waits today": (
        [(TODAY, 100), (AHEAD, 100)],
        [(TODAY, 150)],
        ("difference", 0.0, "$50.00"),
    ),
}


@pytest.mark.parametrize("case", sorted(NOT_COUNTING))
def test_what_the_row_leaves_out_is_the_rest_of_the_make_deposits_total(co, case):
    """ "not counting $X dated after today" is the Make Deposits total less
    the row's figure, and is said only when that page shows more: the two
    always add up to what the page shows (round 30 rework)."""
    payments, deposits, (status, waiting, left_out) = NOT_COUNTING[case]
    for when, amount in payments:
        _payment(co, when, amount=amount)
    for when, total in deposits:
        _deposit(co, when, total=total)
    uf = _rows(co)["undeposited_funds"]
    assert (uf["status"], uf["subledger"]) == (status, waiting), uf
    db = co["db"]
    db.expire_all()
    page_total = waiting_balance(db, co["a"]["1200"])
    if left_out is None:
        assert "dated after today" not in uf["sentence"]
        assert float(page_total) <= waiting
    else:
        assert f", not counting {left_out} dated after today" in uf["sentence"]
        assert uf["sentence"].count("dated after today") == 1
        assert f"${page_total - Decimal(str(waiting)):,.2f}" == left_out


def test_the_make_deposits_list_still_offers_a_post_dated_payment(co):
    """The dated cut is Books Check's alone: the Make Deposits page lists a
    payment dated next week and lets it be deposited, as before."""
    _payment(co, TODAY, amount=40)
    before = _pending(co)
    assert [p["amount"] for p in before] == [40.0]
    _payment(co, AHEAD, amount=100)
    listed = _pending(co)
    assert before[0] in listed
    assert sorted((p["amount"], p["date"]) for p in listed) == [
        (40.0, TODAY.isoformat()),
        (100.0, AHEAD.isoformat()),
    ]
    db, uf_id = co["db"], co["a"]["1200"]
    db.expire_all()
    # with no day given, the service answers what it always did
    assert waiting_balance(db, uf_id) == waiting_balance(db) == Decimal("140")
    assert len(waiting_items(db, uf_id)) == 2
    # and the dated list is the same list, cut at the day
    assert waiting_balance(db, uf_id, TODAY) == Decimal("40")
    assert waiting_balance(db, uf_id, AHEAD) == Decimal("140")
    assert [ln.id for ln, _ in waiting_items(db, uf_id, AHEAD)] == [
        ln.id for ln, _ in waiting_items(db, uf_id)
    ]
    # the whole of it can be deposited today
    _deposit(co, TODAY, line_ids=_pending_line_ids(co))
    assert _pending(co) == []


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
    assert (uf["subledger"], uf["control"], uf["difference"]) == (0.0, 35.0, -35.0)
    assert [(d["type"], d["id"], d["amount"]) for d in uf["documents"]] == [
        ("expense", stray.id, 35.0)
    ]
    # the same figures and documents as without the payment; the sentence
    # adds only what the Make Deposits page shows beyond the row
    said = ", not counting $100.00 dated after today"
    assert uf["sentence"].count(said) == 1
    assert {**uf, "sentence": uf["sentence"].replace(said, "")} == before
    assert uf["sentence"].startswith(
        "The payments waiting to be deposited add up to $0.00 and 1200 "
        "Undeposited Funds is $35.00, not counting $100.00 dated after today, "
        "$35.00 apart. Look at the lines below"
    )


def test_a_post_dated_purchase_line_waits_for_its_day(co):
    """A line on 1200 that is not a customer's payment, dated after today,
    is in neither figure yet, as before this change."""
    a = co["a"]
    _seeded(
        co["db"], [(a["1200"], "35", "0"), (a["1000"], "0", "35")], "cc_charge", AHEAD
    )
    rows = _rows(co)
    assert _apart(rows) == {}
    uf = rows["undeposited_funds"]
    assert (uf["subledger"], uf["control"]) == (0.0, 0.0)
    assert uf["sentence"] == AGREE + "."


# ── nothing else moves: the answers the code gave before this change ──────


def _ordinary(co):
    """A month of ordinary books, nothing dated after today."""
    inv = _invoice(co, PAST)
    _payment(co, PAST, amount=40, invoice_id=inv)
    _payment(co, TODAY, amount=25)
    _taxed_invoice(co, TODAY)
    _stock_invoice(co, PAST)
    _credit_memo(co, PAST)
    _sales_receipt(co, TODAY)
    bill = _bill(co, PAST)
    _bill(co, TODAY, amount=33)
    _stock_bill(co, PAST)
    _bill_payment(co, TODAY, bill, amount=20)
    _vendor_credit(co, PAST)
    _expense(co, TODAY)
    _card_charge(co, PAST)
    _journal(co, PAST, "6000", "1000")


def _real_differences(co):
    """A stray line on each control account, and a deposit entered by hand
    that took more than was waiting."""
    a, db = co["a"], co["db"]
    _seeded(db, [(a["1100"], "80", "0"), (a["4000"], "0", "80")], "manual", PAST)
    _seeded(db, [(a["6000"], "45", "0"), (a["2000"], "0", "45")], "manual", PAST)
    _seeded(db, [(a["1200"], "35", "0"), (a["1000"], "0", "35")], "expense", PAST)
    _seeded(db, [(a["1300"], "17", "0"), (a["1000"], "0", "17")], "manual", PAST)
    _seeded(db, [(a["1000"], "500", "0"), (a["1200"], "0", "500")], "deposit", TODAY)


def _stray_today_and_a_journal_credit_ahead(co):
    """A purchase line sitting on 1200, and a journal entry dated ahead
    that takes the same amount off the account."""
    a = co["a"]
    _seeded(co["db"], [(a["1200"], "50", "0"), (a["1000"], "0", "50")], "bill", PAST)
    _journal(co, AHEAD, "6000", "1200", amount=50)


def _stray_today_and_a_stray_credit_ahead(co):
    a = co["a"]
    _seeded(co["db"], [(a["1200"], "50", "0"), (a["1000"], "0", "50")], "bill", PAST)
    _seeded(
        co["db"], [(a["1000"], "50", "0"), (a["1200"], "0", "50")], "expense", AHEAD
    )


def _payment_today_and_a_stray_credit_ahead(co):
    """A payment received today, and a purchase line dated ahead crediting
    1200 for the same amount: not a deposit, a line to move."""
    a = co["a"]
    _payment(co, TODAY)
    _seeded(
        co["db"], [(a["6000"], "100", "0"), (a["1200"], "0", "100")], "expense", AHEAD
    )


def _agreeing(co):
    _ordinary(co)


def _apart_today(co):
    _ordinary(co)
    _real_differences(co)


# name -> (the books, the rows that read "apart")
AS_BEFORE = {
    "ordinary books": (_agreeing, ()),
    "ordinary books, differences": (
        _apart_today,
        (
            "ar_aging",
            "customer_balances",
            "ap_aging",
            "vendor_balances",
            "undeposited_funds",
            "inventory",
        ),
    ),
    "purchase line today, journal credit ahead": (
        _stray_today_and_a_journal_credit_ahead,
        ("undeposited_funds",),
    ),
    "purchase line today, purchase credit ahead": (
        _stray_today_and_a_stray_credit_ahead,
        ("undeposited_funds",),
    ),
    "payment today, purchase credit ahead": (
        _payment_today_and_a_stray_credit_ahead,
        ("undeposited_funds",),
    ),
}
COMPANIES = ("business", "nonprofit")


def _dateless(answer):
    """The answer with every date written as its distance from today, so
    the answers of two days can be set side by side."""

    def days(match):
        return f"<day {(date.fromisoformat(match.group(0)) - TODAY).days:+d}>"

    return json.loads(re.sub(r"\d{4}-\d{2}-\d{2}", days, json.dumps(answer)))


def answer_for(co, name, company):
    """The books of AS_BEFORE[name] in a company of that type, and the
    whole Books Check answer for them as the page reads it."""
    if company != "business":
        _ok(co["client"].put("/api/settings", json={"company_type": company}))
    AS_BEFORE[name][0](co)
    return _dateless(_ok(co["client"].get("/api/reports/books-check")))


@pytest.mark.parametrize("company", COMPANIES)
@pytest.mark.parametrize("name", sorted(AS_BEFORE))
def test_the_whole_answer_is_what_it_was_before_this_change(co, name, company):
    answer = answer_for(co, name, company)
    before = json.loads(BASE_ANSWERS.read_text())[f"{name} / {company}"]
    assert answer == before
    apart = {r["key"] for r in answer["rows"] if r["status"] == "difference"}
    assert apart == set(AS_BEFORE[name][1])


def test_a_stray_line_is_named_whatever_is_dated_after_it(co):
    """The reviewers' cases, read closely: the purchase line on 1200 is
    listed, and a credit dated ahead does not make the row agree over it."""
    a = co["a"]
    stray = _seeded(
        co["db"], [(a["1200"], "50", "0"), (a["1000"], "0", "50")], "bill", PAST
    )
    alone = _rows(co)["undeposited_funds"]
    assert (alone["status"], alone["subledger"], alone["control"]) == (
        "difference",
        0.0,
        50.0,
    )
    assert [(d["id"], d["amount"]) for d in alone["documents"]] == [(stray.id, 50.0)]
    _journal(co, AHEAD, "6000", "1200", amount=50)
    assert _rows(co)["undeposited_funds"] == alone
    credit = _seeded(
        co["db"], [(a["1000"], "50", "0"), (a["1200"], "0", "50")], "expense", AHEAD
    )
    uf = _rows(co)["undeposited_funds"]
    assert {**uf, "documents": alone["documents"]} == alone
    assert [(d["id"], d["amount"]) for d in uf["documents"]] == [
        (stray.id, 50.0),
        (credit.id, -50.0),
    ]


def test_a_payment_taken_by_a_purchase_credit_dated_ahead_reads_apart(co):
    _payment_today_and_a_stray_credit_ahead(co)
    rows = _rows(co)
    _only_difference(rows, "undeposited_funds")
    uf = rows["undeposited_funds"]
    assert (uf["subledger"], uf["control"], uf["difference"]) == (0.0, 100.0, -100.0)
    assert [(d["type"], d["amount"]) for d in uf["documents"]] == [("expense", -100.0)]
    assert uf["sentence"].startswith(
        "The payments waiting to be deposited add up to $0.00 and 1200 "
        "Undeposited Funds is $100.00, $100.00 apart. Look at the lines below"
    )


# ── the other rows, and a past day ────────────────────────────────────────


def test_no_other_row_says_anything_about_dates_after_today(co):
    for kind in sorted(POST_DATED):
        if kind != "pay run":
            POST_DATED[kind](co)
    rows = _rows(co)
    for key, row in rows.items():
        if key != "undeposited_funds":
            assert "dated after today" not in row["sentence"], row
    assert rows["customer_balances"]["sentence"] == (
        "The customers' balances add up to 1100 Accounts Receivable."
    )
    assert rows["vendor_balances"]["sentence"] == (
        "The vendors' balances add up to 2000 Accounts Payable."
    )
    assert rows["inventory"]["sentence"] == (
        "The inventory valuation equals the ledger's Inventory."
    )


def test_the_customer_and_vendor_rows_read_both_figures_to_today(co):
    _invoice(co, PAST, amount=60)
    _invoice(co, AHEAD, amount=300)
    _bill(co, PAST, amount=45)
    _bill(co, AHEAD, amount=70)
    rows = _rows(co)
    assert _apart(rows) == {}
    for key, figure in (
        ("customer_balances", 60.0),
        ("ar_aging", 60.0),
        ("vendor_balances", 45.0),
        ("ap_aging", 45.0),
    ):
        assert (rows[key]["subledger"], rows[key]["control"]) == (figure, figure)


def test_a_stray_entry_on_a_control_account_is_named_before_its_reversal(co):
    """A journal entry on Accounts Receivable or Accounts Payable dated
    last month is reported though its reversal is dated next week."""
    a, db = co["a"], co["db"]
    ar = _seeded(db, [(a["1100"], "80", "0"), (a["4000"], "0", "80")], "manual", PAST)
    _seeded(db, [(a["4000"], "80", "0"), (a["1100"], "0", "80")], "manual", AHEAD)
    ap = _seeded(db, [(a["6000"], "45", "0"), (a["2000"], "0", "45")], "manual", PAST)
    _seeded(db, [(a["2000"], "45", "0"), (a["6000"], "0", "45")], "manual", AHEAD)
    rows = _rows(co)
    _only_difference(
        rows, "customer_balances", "ar_aging", "vendor_balances", "ap_aging"
    )
    for key, stray, figure in (
        ("customer_balances", ar, 80.0),
        ("vendor_balances", ap, 45.0),
    ):
        assert (rows[key]["subledger"], rows[key]["control"]) == (0.0, figure)
        assert [(d["type"], d["id"]) for d in rows[key]["documents"]] == [
            ("journal", stray.id)
        ]


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
