"""Payment terms beyond Net N (#664).

Terms were free text with one rule: "Net N", else 30 days, said to no one.
A terms list now gives each term a rule — standard (due in N days) or
date-driven (due on a day of the month) — with an optional early-payment
discount, and every document that takes terms reads it through one parser
(app/services/payment_terms.py). Terms it doesn't understand keep the old
30 days and the document says so. The discount is taken in Receive
Payments and Pay Bills, as a credit memo or vendor credit tied to the
payment, posting to the account the person picks.
"""

import calendar
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.transactions import Transaction, TransactionLine
from app.models.vendor_credits import VendorCredit, VendorCreditStatus

D = Decimal
ROOT = Path(__file__).resolve().parents[1]


def _term(client, **body):
    r = client.post("/api/payment-terms", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(client, customer_id, terms, on="2026-10-05", amount="100.00"):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": on,
            "terms": terms,
            "lines": [{"description": "Service", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(client, name="Gulf Supply", terms="Net 30"):
    expense = next(
        a["id"]
        for a in client.get("/api/accounts").json()
        if a["account_number"] == "6000"
    )
    r = client.post(
        "/api/vendors",
        json={"name": name, "terms": terms, "default_expense_account_id": expense},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, terms, on="2026-10-05", amount="500.00", number="B-1"):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": on,
            "terms": terms,
            "lines": [{"description": "Lumber", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _account_id(db, number):
    return db.query(Account).filter(Account.account_number == number).one().id


def _gl(db, account_id):
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    return sum((r.debit for r in rows), D("0")) - sum((r.credit for r in rows), D("0"))


def _balanced(db):
    for txn in db.query(Transaction).all():
        dr = sum((ln.debit for ln in txn.lines), D("0"))
        cr = sum((ln.credit for ln in txn.lines), D("0"))
        assert dr == cr, f"transaction {txn.id}: {dr} != {cr}"


TWO_TEN = {
    "name": "2% 10 Net 30",
    "net_days": 30,
    "discount_percent": "2",
    "discount_days": 10,
}


# ── the list and the parser ─────────────────────────────────────────────


def test_the_list_starts_with_the_terms_the_forms_offered(client, db_session):
    # what a new company is given (company_service, scripts/seed_database.py)
    from app.services.payment_terms import ensure_seeded

    ensure_seeded(db_session)
    db_session.commit()
    rows = client.get("/api/payment-terms").json()
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"Due on Receipt", "Net 15", "Net 30", "Net 45", "Net 60"}
    assert by_name["Net 30"]["net_days"] == 30
    assert by_name["Net 30"]["description"] == "Due in 30 days"
    assert by_name["Due on Receipt"]["net_days"] == 0


def test_existing_terms_keep_their_due_dates(client, seed_accounts, seed_customer):
    for terms, due in (
        ("Net 15", "2026-10-20"),
        ("Net 30", "2026-11-04"),
        ("Net 60", "2026-12-04"),
        ("Due on Receipt", "2026-10-05"),
        ("net 45", "2026-11-19"),  # the list matches as the old parser did
        ("Net 10", "2026-10-15"),  # Net N off the list still reads
    ):
        inv = _invoice(client, seed_customer.id, terms)
        assert inv["due_date"] == due, terms
        assert inv["terms_note"] is None
        assert inv["discount_date"] is None


def test_a_negative_count_is_still_refused(client, seed_accounts, seed_customer):
    """docs/features.md: terms that read as a negative count need a due date
    given, or the invoice is refused with the date it would have had."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "terms": "-0001",
            "lines": [{"description": "Service", "quantity": 1, "rate": "10"}],
        },
    )
    assert r.status_code == 400
    assert "The due date (Oct 4, 2026) is before the invoice date" in r.json()["detail"]


def test_terms_nobody_understands_keep_30_days_and_say_so(
    client, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, "15th of following month")
    assert inv["due_date"] == "2026-11-04"
    assert inv["terms_note"] == (
        '"15th of following month" isn\'t on the terms list; its due date was '
        "set 30 days from the invoice date. An administrator can add it under "
        "Settings > Payment Terms to give it its own rule."
    )
    # the read and the list say it too
    assert client.get(f"/api/invoices/{inv['id']}").json()["terms_note"]
    listed = client.get(f"/api/invoices?customer_id={seed_customer.id}").json()
    assert listed[0]["terms_note"]

    bill = _bill(client, _vendor(client)["id"], "2% 10 Net 30")
    assert bill["due_date"] == "2026-11-04"
    assert "from the bill date" in bill["terms_note"]


def test_a_standard_term_with_an_early_payment_discount(
    client, seed_accounts, seed_customer
):
    term = _term(client, **TWO_TEN)
    assert term["description"] == "Due in 30 days; 2% off if paid within 10 days"
    # the free text from before the list now reads by its rule
    inv = _invoice(client, seed_customer.id, "2% 10 net 30")
    assert inv["due_date"] == "2026-11-04"
    assert inv["discount_percent"] == "2.00"
    assert inv["discount_date"] == "2026-10-15"
    assert inv["discount_line"] == "2% discount if paid by Oct 15, 2026"
    assert inv["terms_note"] is None


@pytest.mark.parametrize(
    "on, due",
    [
        ("2026-10-05", "2026-11-10"),  # 5 days before the 10th: next month's
        ("2026-09-20", "2026-10-10"),  # past the 10th: next month's
        ("2026-09-29", "2026-10-10"),  # 11 days before it: this one
    ],
)
def test_a_date_driven_term_is_due_on_its_day(
    client, seed_accounts, seed_customer, on, due
):
    _term(client, name="Net 10th", kind="date_driven", due_day=10, cutoff_days=10)
    assert _invoice(client, seed_customer.id, "Net 10th", on=on)["due_date"] == due


def test_end_of_month_and_the_following_month(client, seed_accounts, seed_customer):
    _term(client, name="Due end of month", kind="date_driven", due_day=31)
    # "15th of following month" is a 15-day cutoff: the next 15th, or the
    # one after when dated fewer than 15 days before it. (Pass 3, B3: this
    # test pinned it as cutoff 31, which only passed because the cutoff was
    # never applied past the date's own month.)
    _term(
        client,
        name="15th of following month",
        kind="date_driven",
        due_day=15,
        cutoff_days=15,
    )
    feb = _invoice(client, seed_customer.id, "Due end of month", on="2026-02-03")
    assert feb["due_date"] == "2026-02-28"  # a short month's last day
    for on in ("2026-10-01", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-31"):
        inv = _invoice(client, seed_customer.id, "15th of following month", on=on)
        assert inv["due_date"] == "2026-11-15", on


def test_a_date_driven_discount_by_a_day_of_the_month(
    client, seed_accounts, seed_customer
):
    _term(
        client,
        name="2% 10th Net 25th",
        kind="date_driven",
        due_day=25,
        cutoff_days=28,
        discount_percent="2",
        discount_day=10,
    )
    inv = _invoice(client, seed_customer.id, "2% 10th Net 25th", on="2026-10-20")
    assert inv["due_date"] == "2026-11-25"
    assert inv["discount_date"] == "2026-11-10"


def test_the_dates_the_form_shows_are_the_ones_the_save_makes(
    client, seed_accounts, seed_customer
):
    _term(client, **TWO_TEN)
    r = client.get(
        "/api/payment-terms/dates",
        params={"date": "2026-10-05", "terms": "2% 10 Net 30"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "due_date": "2026-11-04",
        "discount_percent": "2.00",
        "discount_date": "2026-10-15",
        "discount_line": "2% discount if paid by Oct 15, 2026",
        "note": None,
    }
    odd = client.get(
        "/api/payment-terms/dates",
        params={"date": "2026-10-05", "terms": "Net 10th", "document": "bill"},
    ).json()
    assert odd["due_date"] == "2026-11-04"
    assert "from the bill date" in odd["note"]


@pytest.mark.parametrize(
    "body, sentence",
    [
        ({"name": "X", "discount_percent": "2", "net_days": 30}, "needs both"),
        ({"name": "X", "net_days": 30, "due_day": 10}, "date-driven term"),
        ({"name": "X"}, "number of days until it's due"),
        ({"name": "X", "kind": "date_driven"}, "day of the month it's due"),
        ({"name": "X", "kind": "date_driven", "due_day": 32}, "1 to 31"),
        (
            {
                "name": "X",
                "net_days": 30,
                "discount_percent": "100",
                "discount_days": 5,
            },
            "under 100",
        ),
        (
            {"name": "X", "net_days": 10, "discount_percent": "2", "discount_days": 15},
            "within the days until it's due",
        ),
        ({"name": "X", "net_days": 30, "cutoff_days": 5}, "next month"),
    ],
)
def test_a_rule_that_cannot_work_is_refused_in_a_sentence(client, body, sentence):
    r = client.post("/api/payment-terms", json=body)
    assert r.status_code == 422, r.text
    assert sentence in r.json()["detail"]


def test_a_name_already_on_the_list_or_an_unknown_field_is_refused(client):
    r = client.post("/api/payment-terms", json={"name": "net 30", "net_days": 30})
    assert r.status_code == 409
    assert r.json()["detail"] == '"net 30" is already on the terms list.'
    r = client.post(
        "/api/payment-terms", json={"name": "Net 20", "net_days": 20, "days": 20}
    )
    assert r.status_code == 422


def test_a_term_on_a_document_keeps_its_rule_and_name(
    client, seed_accounts, seed_customer
):
    term = _term(client, **TWO_TEN)
    _invoice(client, seed_customer.id, "2% 10 Net 30")
    r = client.put(f"/api/payment-terms/{term['id']}", json={"net_days": 45})
    assert r.status_code == 409
    assert (
        "is on 1 invoices and bills saved under it, so its rule stays"
        in r.json()["detail"]
    )
    r = client.put(f"/api/payment-terms/{term['id']}", json={"name": "2/10 n30"})
    assert r.status_code == 409
    assert "so its name stays" in r.json()["detail"]
    # inactive: no longer offered, still read
    r = client.put(f"/api/payment-terms/{term['id']}", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert "2% 10 Net 30" not in [
        t["name"] for t in client.get("/api/payment-terms").json()
    ]
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    assert inv["discount_date"] == "2026-10-15"


def test_a_term_nothing_carries_can_change(client):
    term = _term(client, name="Twenty", net_days=20)
    r = client.put(
        f"/api/payment-terms/{term['id']}",
        json={
            "name": "Twenty-one",
            "net_days": 21,
            "discount_percent": "1",
            "discount_days": 5,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "Due in 21 days; 1% off if paid within 5 days"
    # a standard term turned date-driven drops its days
    r = client.put(
        f"/api/payment-terms/{term['id']}",
        json={"kind": "date_driven", "due_day": 1, "discount_percent": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["net_days"] is None and r.json()["discount_days"] is None


def test_a_recurring_invoice_due_on_receipt_is_due_that_day(
    db_session, seed_accounts, seed_customer
):
    """The recurring generator had its own parser, which read Due on
    Receipt as 30 days."""
    from app.models.invoices import Invoice
    from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
    from app.services.recurring_service import generate_due_invoices

    rec = RecurringInvoice(
        customer_id=seed_customer.id,
        frequency="monthly",
        start_date=date(2026, 1, 1),
        next_due=date(2026, 1, 1),
        is_active=True,
        terms="Due on Receipt",
        tax_rate=D("0"),
    )
    db_session.add(rec)
    db_session.flush()
    db_session.add(
        RecurringInvoiceLine(
            recurring_invoice_id=rec.id, quantity=1, rate=D("50"), description="Fee"
        )
    )
    db_session.commit()
    ids = generate_due_invoices(db_session, as_of=date(2026, 1, 1))
    assert db_session.get(Invoice, ids[0]).due_date == date(2026, 1, 1)


def test_a_bill_and_a_memorized_bill_read_the_list(client, db_session, seed_accounts):
    _term(client, name="Net 10th prox", kind="date_driven", due_day=10, cutoff_days=10)
    vendor = _vendor(client, terms="Net 10th prox")
    bill = _bill(client, vendor["id"], "Net 10th prox", on="2026-10-05")
    assert bill["due_date"] == "2026-11-10"

    mem = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "Rent",
            "mode": "remind",
            "frequency": "monthly",
            "start_date": "2026-10-20",
            "vendor_id": vendor["id"],
            "terms": "Net 10th prox",
            "lines": [
                {
                    "account_id": _account_id(db_session, "6500"),
                    "description": "Rent",
                    "amount": 400,
                }
            ],
        },
    )
    assert mem.status_code == 201, mem.text
    r = client.post(f"/api/memorized/{mem.json()['id']}/enter")
    assert r.status_code == 200, r.text
    entered = client.get(f"/api/bills/{r.json()['document_id']}").json()
    assert entered["due_date"] == "2026-11-10"


def test_a_converted_estimate_takes_the_customers_term_from_the_list(
    client, seed_accounts
):
    _term(client, name="Due end of month", kind="date_driven", due_day=31)
    cust = client.post(
        "/api/customers", json={"name": "Shore Homes", "terms": "Due end of month"}
    ).json()
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": cust["id"],
            "date": "2026-10-05",
            "lines": [{"description": "Deck", "quantity": 1, "rate": "900"}],
        },
    ).json()
    r = client.post(f"/api/estimates/{est['id']}/convert")
    assert r.status_code in (200, 201), r.text
    inv = r.json()
    today = date.today()
    last = calendar.monthrange(today.year, today.month)[1]
    assert inv["due_date"] == date(today.year, today.month, last).isoformat()


def test_a_csv_import_takes_a_term_on_the_list(client):
    _term(client, **TWO_TEN)
    r = client.post(
        "/api/csv/import/vendors",
        files={"file": ("v.csv", b"Name,Terms\nBay Lumber,2% 10 net 30\n", "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []
    assert client.get("/api/vendors").json()[0]["terms"] == "2% 10 Net 30"


# ── the early-payment discount in Receive Payments ───────────────────────


@pytest.fixture
def discounted(client, db_session, seed_accounts, seed_customer):
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    return {"invoice": inv, "customer_id": seed_customer.id, "income": "4900"}


def _receive(client, customer_id, invoice_id, amount, discount, account_id, **extra):
    return client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-10-12",
            "amount": amount,
            "allocations": [
                {"invoice_id": invoice_id, "amount": amount, "discount": discount}
            ],
            "discount_account_id": account_id,
            **extra,
        },
    )


def test_the_customer_takes_the_discount_and_the_invoice_is_paid(
    client, db_session, discounted
):
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    r = _receive(client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct)
    assert r.status_code == 201, r.text
    pay = r.json()
    assert [
        (d["invoice_id"], d["amount"], d["account_id"]) for d in pay["discounts"]
    ] == [(inv["id"], "2.00", acct)]
    after = client.get(f"/api/invoices/{inv['id']}").json()
    assert after["status"] == "paid"
    assert after["balance_due"] == "0.00"
    cm = db_session.query(CreditMemo).filter_by(discount_payment_id=pay["id"]).one()
    assert cm.total == D("2.00") and not cm.is_write_off
    assert _gl(db_session, acct) == D("2.00")  # the discount, a debit
    assert _gl(db_session, _account_id(db_session, "1100")) == D("0.00")
    _balanced(db_session)
    # the single read lists it too
    assert (
        client.get(f"/api/payments/{pay['id']}").json()["discounts"][0]["memo_number"]
        == cm.memo_number
    )


def test_voiding_the_payment_voids_its_discount(client, db_session, discounted):
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    pay = _receive(
        client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct
    ).json()
    cm = db_session.query(CreditMemo).filter_by(discount_payment_id=pay["id"]).one()
    # the memo alone goes with its payment
    r = client.post(f"/api/credit-memos/{cm.id}/void")
    assert r.status_code == 409
    assert "void that payment to undo it" in r.json()["detail"]

    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["discounts"] == []
    after = client.get(f"/api/invoices/{inv['id']}").json()
    assert after["balance_due"] == "100.00"
    assert after["amount_paid"] == "0.00"
    db_session.expire_all()
    assert db_session.get(CreditMemo, cm.id).status == CreditMemoStatus.VOID
    assert _gl(db_session, acct) == D("0.00")
    _balanced(db_session)


@pytest.mark.parametrize(
    "number, discount, sentence",
    [
        (None, "2.00", "Pick the account the discount posts to"),
        ("1000", "2.00", "income, expense or cost of goods account"),
        ("4900", "0", "no invoice or bill on this payment takes one"),
        ("4900", "-1", "can't be less than zero"),
        ("4900", "3.00", "is more than the $100.00 still due"),
    ],
)
def test_a_discount_that_cannot_post_is_refused(
    client, db_session, discounted, number, discount, sentence
):
    inv = discounted["invoice"]
    acct = _account_id(db_session, number) if number else None
    r = _receive(client, discounted["customer_id"], inv["id"], "98.00", discount, acct)
    assert r.status_code == 400, r.text
    assert sentence in r.json()["detail"]
    assert client.get(f"/api/invoices/{inv['id']}").json()["balance_due"] == "100.00"


def test_applying_a_remainder_takes_no_discount(client, db_session, discounted):
    inv = discounted["invoice"]
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": discounted["customer_id"],
            "date": "2026-10-12",
            "amount": "98.00",
            "allocations": [],
        },
    ).json()
    r = client.post(
        f"/api/payments/{pay['id']}/apply",
        json={
            "allocations": [{"invoice_id": inv["id"], "amount": "98", "discount": "2"}]
        },
    )
    assert r.status_code == 400
    assert "taken when the payment is received" in r.json()["detail"]


def test_a_discount_is_no_sale_reversed_on_the_sales_tax_report(
    client, db_session, discounted
):
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    _receive(client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct)
    rep = client.get(
        "/api/reports/sales-tax",
        params={"start_date": "2026-10-01", "end_date": "2026-10-31"},
    ).json()
    assert rep["total_sales"] == 100.0
    assert [i["type"] for i in rep["items"]] == ["invoice"]


def test_the_invoice_prints_its_discount_date(client, discounted):
    inv = discounted["invoice"]
    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "2% discount if paid by Oct 15, 2026" in html


# ── the early-payment discount in Pay Bills ─────────────────────────────


@pytest.fixture
def discounted_bill(client, seed_accounts):
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    return _bill(client, vendor["id"], "2% 10 Net 30")


def _pay(client, bill, amount, discount, account_id):
    return client.post(
        "/api/bill-payments",
        json={
            "vendor_id": bill["vendor_id"],
            "date": "2026-10-12",
            "amount": amount,
            "allocations": [
                {"bill_id": bill["id"], "amount": amount, "discount": discount}
            ],
            "discount_account_id": account_id,
        },
    )


def test_we_take_the_vendors_discount_and_the_bill_is_paid(
    client, db_session, discounted_bill
):
    assert discounted_bill["discount_date"] == "2026-10-15"
    acct = _account_id(db_session, "4900")
    r = _pay(client, discounted_bill, 490.0, "10.00", acct)
    assert r.status_code == 201, r.text
    pay = r.json()
    assert [(d["bill_id"], d["amount"]) for d in pay["discounts"]] == [
        (discounted_bill["id"], "10.00")
    ]
    after = client.get(f"/api/bills/{discounted_bill['id']}").json()
    assert after["status"] == "paid" and after["balance_due"] == "0.00"
    assert _gl(db_session, acct) == D("-10.00")  # the discount, a credit
    assert _gl(db_session, _account_id(db_session, "2000")) == D("0.00")
    _balanced(db_session)

    vc = (
        db_session.query(VendorCredit)
        .filter_by(discount_bill_payment_id=pay["id"])
        .one()
    )
    r = client.post(f"/api/vendor-credits/{vc.id}/void")
    assert r.status_code == 409
    r = client.post(f"/api/bill-payments/{pay['id']}/void")
    assert r.status_code == 200, r.text
    after = client.get(f"/api/bills/{discounted_bill['id']}").json()
    assert after["balance_due"] == "500.00" and after["status"] == "unpaid"
    db_session.expire_all()
    assert db_session.get(VendorCredit, vc.id).status == VendorCreditStatus.VOID
    assert _gl(db_session, acct) == D("0.00")
    _balanced(db_session)


def test_a_bill_discount_needs_an_account(client, db_session, discounted_bill):
    r = _pay(client, discounted_bill, 490.0, "10.00", None)
    assert r.status_code == 400
    assert "Pick the account the discount posts to" in r.json()["detail"]
    r = _pay(client, discounted_bill, 495.0, "10.00", _account_id(db_session, "4900"))
    assert r.status_code == 400
    assert "is more than the $500.00 still due on bill B-1" in r.json()["detail"]


def test_the_bill_prints_its_discount_date(client, discounted_bill):
    html = client.get(f"/api/bills/{discounted_bill['id']}/print-preview").text
    assert "2% discount if paid by Oct 15, 2026" in html


# ── the migration ───────────────────────────────────────────────────────


def test_the_migration_seeds_the_list_from_the_terms_in_use(tmp_path):
    """Up from main's head: the five form terms, plus a Net N anything
    already carries; other text stays as it is. Down drops the list and the
    discount links; up again."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text

    db = tmp_path / "terms.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["e5c1a7d3f9b2"]
    assert script.get_revision("e5c1a7d3f9b2").down_revision == "d7b3f9a1c5e2"

    command.upgrade(cfg, "d7b3f9a1c5e2")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO vendors (name, terms, is_active) VALUES "
                "('A', 'net 10', 1), ('B', '2% 10 Net 30', 1)"
            )
        )
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('default_terms', 'Net 20')")
        )
        conn.execute(
            text("INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)")
        )
        # an invoice is tied where main's own parser read its exact text as
        # that entry's days: spaces after "net" and a leading zero were
        # read; a tab or a no-break space was not (main gave those 30 days)
        for inv_id, terms in (
            (1, "net  10"),
            (2, "2% 10 Net 30"),
            (3, "Net 30"),
            (4, "net\t10"),
            (5, "net\u00a045"),
            (6, "Net 030"),
        ):
            conn.execute(
                text(
                    "INSERT INTO invoices (id, invoice_number, customer_id, date, "
                    "status, terms, retainage_held, retainage_released, "
                    "is_sales_receipt, is_pledge) VALUES (:id, :num, 1, "
                    "'2026-09-01', 'SENT', :terms, 0, 0, 0, 0)"
                ),
                {"id": inv_id, "num": str(1000 + inv_id), "terms": terms},
            )
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, net_days FROM payment_terms ORDER BY net_days")
        ).all()
        vendors = conn.execute(text("SELECT terms FROM vendors ORDER BY name")).all()
        cols = {c["name"] for c in inspect(conn).get_columns("credit_memos")}
        tied = conn.execute(
            text(
                "SELECT i.id, t.name FROM invoices i LEFT JOIN payment_terms t "
                "ON t.id = i.payment_term_id ORDER BY i.id"
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("Due on Receipt", 0),
        ("Net 10", 10),
        ("Net 15", 15),
        ("Net 20", 20),
        ("Net 30", 30),
        ("Net 45", 45),
        ("Net 60", 60),
    ]
    assert [r[0] for r in vendors] == ["net 10", "2% 10 Net 30"]  # untouched
    assert "discount_payment_id" in cols
    assert [tuple(r) for r in tied] == [
        (1, "Net 10"),
        (2, None),
        (3, "Net 30"),
        (4, None),
        (5, None),
        (6, "Net 30"),
    ]
    engine.dispose()

    command.downgrade(cfg, "d7b3f9a1c5e2")
    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    assert "payment_terms" not in insp.get_table_names()
    assert "discount_bill_payment_id" not in {
        c["name"] for c in insp.get_columns("vendor_credits")
    }
    assert "payment_term_id" not in {c["name"] for c in insp.get_columns("invoices")}
    engine.dispose()
    command.upgrade(cfg, "head")


# ── the pages ───────────────────────────────────────────────────────────

JS = ROOT / "app" / "static" / "js"


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_every_page_loads_the_terms_list_before_the_forms():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    at = index.index('<script src="/static/js/payment_terms.js"></script>')
    assert at < index.index('<script src="/static/js/invoices.js"></script>')
    assert at < index.index('<script src="/static/js/customers.js"></script>')


def test_no_form_keeps_its_own_terms_or_days():
    """The forms had five terms hard-coded, and the invoice form its own
    table of days; both are the list and the server's parser now."""
    import re

    fixed = re.compile(r"""['"]Net 15['"],\s*['"]Net 30['"]""")
    for path in JS.glob("*.js"):
        assert not fixed.search(path.read_text(encoding="utf-8")), path.name
    inv = _js("invoices.js")
    assert "daysMap" not in inv
    # the invoice being edited answers by its own rule while its terms are
    # unchanged (#665)
    assert (
        "PaymentTerms.dates(dateEl.value, termsEl.value, 'invoice', "
        "InvoicesPage._formInvoiceId)" in inv
    )
    assert "PaymentTerms.viewHtml(inv)" in inv
    assert (
        "PaymentTerms.dates(form.date.value, form.terms.value, 'bill', "
        "BillsPage._formBillId)" in _js("bills.js")
    )
    for name, call in (
        ("customers.js", "PaymentTerms.optionsHtml(c.terms)"),
        ("vendors.js", "PaymentTerms.optionsHtml(v.terms)"),
        ("recurring.js", "PaymentTerms.optionsHtml(rec.terms)"),
        ("memorized.js", 'PaymentTerms.optionsHtml(terms, "The vendor\'s terms")'),
        ("bill_quick_entry.js", "PaymentTerms.optionsHtml('Net 30')"),
        ("app.js", "PaymentTerms.optionsHtml('Net 30')"),
        ("settings.js", "PaymentTerms.optionsHtml(s.default_terms || 'Net 30')"),
    ):
        assert call in _js(name), name


def test_pay_bills_suggests_and_sends_the_discount():
    bills = _js("bills.js")
    assert "PaymentTerms.suggestedCents(bill, payDate)" in bills
    # a refused discount is asked about, then sent with the override (#665)
    assert "if (!PaymentTerms.asksOverride(err, body)) throw err;" in bills
    assert "has a discount but no payment. Pay some of it, or clear its discount." in (
        bills
    )
    assert "discount_account_id: v.discounted ? discountAccountId : null" in bills
    assert (
        "if (discount > 0) { row.discount = discount; v.discounted = true; }" in bills
    )
    assert "Pick the account the discount posts to." in bills


def test_settings_keeps_the_terms_list():
    settings = _js("settings.js")
    assert "API.get('/payment-terms?include_inactive=true')" in settings
    assert "API.post('/payment-terms', data)" in settings
    assert "API.put(`/payment-terms/${id}`, { is_active: active })" in settings


# ── the skeptic passes (#665 review): what they found ───────────────────


def _receive_one(
    client, customer_id, invoice_id, amount, discount, account_id, **extra
):
    return client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": extra.pop("date", "2026-10-12"),
            "amount": amount,
            "allocations": [
                {"invoice_id": invoice_id, "amount": amount, "discount": discount}
            ],
            "discount_account_id": account_id,
            **extra,
        },
    )


def test_a_term_added_later_leaves_documents_saved_before_it(
    client, db_session, seed_accounts, seed_customer
):
    """Both passes, B1/2: a term created over text old documents carry gave
    them a rule (and a discount) they were never made under, then locked it.
    A document is tied to its term when saved; one saved before keeps the
    old reading."""
    old = _invoice(client, seed_customer.id, "Net 10th", on="2026-09-15")
    assert old["due_date"] == "2026-10-15"
    term = _term(
        client,
        name="Net 10th",
        kind="date_driven",
        due_day=10,
        cutoff_days=10,
        discount_percent="1.5",
        discount_day=5,
    )
    assert term["documents"] == 0
    got = client.get(f"/api/invoices/{old['id']}").json()
    assert got["discount_line"] is None and got["discount_date"] is None
    assert got["terms_note"] == (
        'This invoice\'s terms were first saved before "Net 10th" was on the '
        "terms list, so it keeps their old reading, not that term's rule; its "
        "due date was set 30 days from the invoice date."
    )
    html = client.get(f"/api/invoices/{old['id']}/print-preview").text
    assert "discount if paid by" not in html
    # nothing was saved under it, so its rule can still be corrected
    r = client.put(
        f"/api/payment-terms/{term['id']}",
        json={"discount_percent": None, "discount_day": None},
    )
    assert r.status_code == 200, r.text
    # and Receive Payments offers the old invoice no discount
    r = _receive_one(
        client,
        seed_customer.id,
        old["id"],
        "98.00",
        "1.00",
        _account_id(db_session, "4900"),
        date="2026-09-20",
    )
    assert r.status_code == 400
    assert "terms (Net 10th) offer no early-payment discount" in r.json()["detail"]


def test_the_409_names_documents_truly_saved_under_the_term(
    client, seed_accounts, seed_customer
):
    term = _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    assert client.get(f"/api/payment-terms/{term['id']}").json()["documents"] == 1
    r = client.put(f"/api/payment-terms/{term['id']}", json={"net_days": 45})
    assert r.status_code == 409
    assert "saved under it, so its rule stays as they were made under it" in (
        r.json()["detail"]
    )
    # an edit that changes the terms ties it to the new ones
    r = client.put(f"/api/invoices/{inv['id']}", json={"terms": "Net 30"})
    assert r.status_code == 200, r.text
    assert r.json()["discount_line"] is None
    assert client.get(f"/api/payment-terms/{term['id']}").json()["documents"] == 0


def test_free_text_edited_onto_a_listed_term_takes_its_rule(
    client, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, "2/10 n30 (old)")
    assert inv["terms_note"]
    _term(client, **TWO_TEN)
    r = client.put(f"/api/invoices/{inv['id']}", json={"terms": "2% 10 Net 30"})
    assert r.status_code == 200, r.text
    assert r.json()["discount_line"] == "2% discount if paid by Oct 15, 2026"
    assert r.json()["terms_note"] is None


@pytest.mark.parametrize(
    "body, meaning",
    [
        ({"name": "COD", "net_days": 45}, '"COD" already means due on receipt'),
        ({"name": "Net 33", "net_days": 45}, '"Net 33" already means due in 33 days'),
        (
            {"name": "net 46", "kind": "date_driven", "due_day": 10},
            '"net 46" already means due in 46 days',
        ),
    ],
)
def test_a_name_the_old_parser_reads_keeps_its_meaning(client, body, meaning):
    """Air B2: a term named COD with 45 days changed every COD document and
    template from due-on-receipt."""
    r = client.post("/api/payment-terms", json=body)
    assert r.status_code == 409
    assert r.json()["detail"].startswith(meaning)


def test_a_rule_in_use_is_locked_whatever_whitespace_its_text_has(
    client, seed_accounts, seed_customer
):
    """Both passes, B2: the lock compared lower(trim(terms)), the parser
    collapsed inner whitespace, so "net   21" read under Net 21 without
    counting toward it, and the rule changed with a 200."""
    term = _term(client, name="Net 21", net_days=21)
    for text in ("net   21", "Net\t21", "net 21"):
        inv = _invoice(client, seed_customer.id, text)
        assert inv["due_date"] == "2026-10-26", text
    assert client.get(f"/api/payment-terms/{term['id']}").json()["documents"] == 3
    r = client.put(f"/api/payment-terms/{term['id']}", json={"net_days": 25})
    assert r.status_code == 409


# ── finding 3: the discount the terms offer, or the override ─────────────


@pytest.mark.parametrize(
    "terms, date, discount, sentence",
    [
        (
            "Net 30",
            "2026-10-12",
            "2.00",
            "terms (Net 30) offer no early-payment discount",
        ),
        (
            "2% 10 Net 30",
            "2026-10-30",
            "2.00",
            "discount had to be taken by Oct 15, 2026; this payment is dated "
            "Oct 30, 2026",
        ),
        (
            "2% 10 Net 30",
            "2026-10-12",
            "2.01",
            "terms offer 2% of its $100.00 total, $2.00; $2.01 is more",
        ),
    ],
)
def test_a_discount_the_terms_dont_offer_is_refused_unless_overridden(
    client, db_session, seed_accounts, seed_customer, terms, date, discount, sentence
):
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, terms)
    acct = _account_id(db_session, "4900")
    r = _receive_one(
        client, seed_customer.id, inv["id"], "10.00", discount, acct, date=date
    )
    assert r.status_code == 400
    assert sentence in r.json()["detail"]
    assert r.json()["detail"].endswith(
        "Send allow_discount_override to take it anyway."
    )
    r = _receive_one(
        client,
        seed_customer.id,
        inv["id"],
        "10.00",
        discount,
        acct,
        date=date,
        allow_discount_override=True,
    )
    assert r.status_code == 201, r.text
    assert r.json()["discounts"][0]["amount"] == discount


def test_a_second_discount_on_one_invoice_is_refused(client, db_session, discounted):
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    r = _receive_one(
        client, discounted["customer_id"], inv["id"], "10.00", "1.00", acct
    )
    assert r.status_code == 201, r.text
    r = _receive_one(
        client, discounted["customer_id"], inv["id"], "10.00", "1.00", acct
    )
    assert r.status_code == 400
    assert "already taken on invoice" in r.json()["detail"]


def test_the_override_with_no_discount_is_refused(client, seed_accounts, seed_customer):
    inv = _invoice(client, seed_customer.id, "Net 30")
    r = _receive_one(
        client,
        seed_customer.id,
        inv["id"],
        "10.00",
        "0",
        None,
        allow_discount_override=True,
    )
    assert r.status_code == 400
    assert "allow_discount_override takes a discount" in r.json()["detail"]


def test_a_bill_discount_after_its_date_needs_the_override(
    client, db_session, discounted_bill
):
    acct = _account_id(db_session, "4900")
    body = {
        "vendor_id": discounted_bill["vendor_id"],
        "date": "2026-11-01",
        "amount": 490.0,
        "allocations": [
            {"bill_id": discounted_bill["id"], "amount": 490.0, "discount": "10.00"}
        ],
        "discount_account_id": acct,
    }
    r = client.post("/api/bill-payments", json=body)
    assert r.status_code == 400
    assert "Bill B-1's discount had to be taken by Oct 15, 2026" in r.json()["detail"]
    r = client.post(
        "/api/bill-payments", json={**body, "allow_discount_override": True}
    )
    assert r.status_code == 201, r.text


# ── finding 4: a discount comes with a payment on its document ───────────


def test_a_discount_with_nothing_paid_names_the_invoice(
    client, db_session, discounted, seed_customer
):
    inv = discounted["invoice"]
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-12",
            "amount": "50",
            "allocations": [{"invoice_id": inv["id"], "amount": "0", "discount": "2"}],
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        f"Invoice {inv['invoice_number']} has a $2.00 discount but nothing paid on "
        "it; a discount is taken with a payment. Pay some of it on this payment, "
        "or clear its discount."
    )


def test_a_bill_discount_with_nothing_paid_is_refused_too(
    client, db_session, discounted_bill
):
    """Air B6: Pay Bills stored an allocation of 0.00 beside the discount."""
    r = _pay(client, discounted_bill, 0, "10.00", _account_id(db_session, "4900"))
    assert r.status_code == 400
    assert "Bill B-1 has a $10.00 discount but nothing paid on it" in r.json()["detail"]


# ── finding 5: cash basis and Sales Tax after a discount on a taxed invoice


def test_a_discount_reads_the_same_on_both_bases_and_sales_tax(
    client, db_session, seed_accounts, seed_customer
):
    """Omarchy B4: $500 + 7% tax, 2% 10 Net 30, paid 524.30 and a 10.70
    discount. Cash basis ignored the discount memo, so the 0.70 of tax in it
    vanished: cash net 490.00 against accrual 489.30, cash tax 34.30 against
    35.00 in the ledger."""
    _term(client, **TWO_TEN)
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "terms": "2% 10 Net 30",
            "tax_rate": "0.07",
            "lines": [
                {"description": "Svc", "quantity": 1, "rate": "500", "is_taxable": True}
            ],
        },
    ).json()
    acct = _account_id(db_session, "4900")
    pay = _receive_one(client, seed_customer.id, inv["id"], "524.30", "10.70", acct)
    assert pay.status_code == 201, pay.text
    period = {"start_date": "2026-10-01", "end_date": "2026-10-31"}

    def read(basis):
        pl = client.get("/api/reports/profit-loss", params={**period, "basis": basis})
        tax = client.get("/api/reports/sales-tax", params={**period, "basis": basis})
        return (
            pl.json()["net_income"],
            tax.json()["total_tax"],
            tax.json()["total_taxable"],
        )

    assert read("accrual") == (489.3, 35.0, 500.0)
    assert read("cash") == (489.3, 35.0, 500.0)
    _balanced(db_session)
    # the void takes the discount's cash lines with it
    assert client.post(f"/api/payments/{pay.json()['id']}/void").status_code == 200
    assert read("cash")[:2] == (0.0, 0.0)
    assert read("accrual")[0] == 500.0


def test_a_bill_discount_reads_on_the_cash_basis(client, db_session, discounted_bill):
    acct = _account_id(db_session, "4900")
    pay = _pay(client, discounted_bill, 490.0, "10.00", acct)
    assert pay.status_code == 201
    period = {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    for basis in ("accrual", "cash"):
        pl = client.get(
            "/api/reports/profit-loss", params={**period, "basis": basis}
        ).json()
        rows = {
            r["account_number"]: r["amount"]
            for section in ("expenses", "other_income")
            for r in pl[section]
        }
        # the whole bill an expense, the discount income, on both bases
        assert rows == {"6000": 500.0, "4900": 10.0}, basis
        assert pl["net_income"] == -490.0, basis
    # the void takes the vendor credit's cash lines with it (pass 2, G9)
    assert client.post(f"/api/bill-payments/{pay.json()['id']}/void").status_code == 200
    cash = client.get(
        "/api/reports/profit-loss", params={**period, "basis": "cash"}
    ).json()
    # its cash lines net to nothing on their own (DR expense, CR discount),
    # so the rows themselves must be gone, not just the total
    left = [
        (r["account_number"], r["amount"])
        for section in ("expenses", "other_income")
        for r in cash[section]
        if r["amount"]
    ]
    assert left == []
    _balanced(db_session)


# ── findings 6 to 10 ────────────────────────────────────────────────────


def test_the_note_says_the_due_date_the_invoice_has(
    client, seed_accounts, seed_customer
):
    """Omarchy B5: a due date given with free text was said to be 30 days."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-01",
            "due_date": "2026-11-15",
            "terms": "15th of following month",
            "lines": [{"description": "Svc", "quantity": 1, "rate": "10"}],
        },
    )
    assert r.json()["terms_note"] == (
        '"15th of following month" isn\'t on the terms list; its due date '
        "(Nov 15, 2026) is the one saved with it. An administrator can add it "
        "under Settings > Payment Terms to give it its own rule."
    )


@pytest.mark.parametrize(
    "params, sentence",
    [
        (
            {"date": "9999-12-31", "terms": "Net 30"},
            "Date 9999-12-31 is out of range; the year must be between 1900 and 2199.",
        ),
        (
            {"date": "2026-10-01", "bogus": "1"},
            "'bogus' is not a parameter this accepts.",
        ),
    ],
)
def test_the_dates_endpoint_refuses_in_a_sentence(client, params, sentence):
    r = client.get("/api/payment-terms/dates", params=params)
    assert r.status_code == 422
    assert r.json()["detail"] == sentence


def test_the_dates_endpoint_echoes_no_more_than_a_terms_column(client):
    r = client.get(
        "/api/payment-terms/dates", params={"date": "2026-10-01", "terms": "x" * 51}
    )
    assert r.status_code == 422


def test_net_n_past_ten_years_still_reads_as_main_read_it(client):
    d = client.get(
        "/api/payment-terms/dates", params={"date": "2026-10-01", "terms": "Net 5000"}
    ).json()
    assert d["due_date"] == "2040-06-09" and d["note"] is None


def test_a_discount_finer_than_a_cent_is_refused(client, db_session, discounted):
    inv = discounted["invoice"]
    r = _receive_one(
        client,
        discounted["customer_id"],
        inv["id"],
        "10.00",
        "0.001",
        _account_id(db_session, "4900"),
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "A discount is in whole cents; 0.001 isn't."


@pytest.mark.parametrize("pct", ["2.555", "0.001"])
def test_a_percent_past_two_places_is_named_so(client, pct):
    r = client.post(
        "/api/payment-terms",
        json={
            "name": "T",
            "net_days": 30,
            "discount_percent": pct,
            "discount_days": 10,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith(
        f"The discount percent is kept to two decimal places; {pct}% has more."
    )


def test_whole_numbers_are_strict_and_one_day_is_singular(client):
    for bad in (
        {"net_days": True},
        {"net_days": "30"},
        {"net_days": 30, "is_active": "yes"},
    ):
        r = client.post("/api/payment-terms", json={"name": "T", **bad})
        assert r.status_code == 422, bad
    r = client.post("/api/payment-terms", json={"name": "One", "net_days": 1})
    assert r.json()["description"] == "Due in 1 day"


# ── the weaknesses ──────────────────────────────────────────────────────


def test_a_discounted_invoice_keeps_its_terms_and_date(client, db_session, discounted):
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    assert (
        _receive_one(
            client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct
        ).status_code
        == 201
    )
    for change in ({"terms": "Net 30"}, {"date": "2026-08-01"}):
        r = client.put(f"/api/invoices/{inv['id']}", json=change)
        assert r.status_code == 409, change
        assert "has an early-payment discount (CM-" in r.json()["detail"]
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"lines": [{"description": "Svc", "quantity": 1, "rate": "90"}]},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Invoice total cannot be less than the amount already paid ($100.00, of "
        "which $2.00 is an early-payment discount)"
    )


def test_a_discounted_bill_keeps_its_terms_and_date(
    client, db_session, discounted_bill
):
    assert (
        _pay(
            client, discounted_bill, 490.0, "10.00", _account_id(db_session, "4900")
        ).status_code
        == 201
    )
    r = client.put(f"/api/bills/{discounted_bill['id']}", json={"date": "2026-10-01"})
    assert r.status_code == 409
    assert "Bill B-1 has an early-payment discount (VC-" in r.json()["detail"]


def test_a_foreign_currency_invoice_offers_no_discount(
    client, db_session, seed_accounts, seed_customer
):
    """Omarchy W4: the PDF and Receive Payments offered a discount the save
    then refused."""
    _term(client, **TWO_TEN)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "terms": "2% 10 Net 30",
            "currency": "EUR",
            "exchange_rate": "1.10",
            "lines": [{"description": "Svc", "quantity": 1, "rate": "100"}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["discount_line"] is None and inv["discount_date"] is None
    assert (
        "discount if paid"
        not in client.get(f"/api/invoices/{inv['id']}/print-preview").text
    )
    # and the refusal stands for one sent anyway
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-12",
            "amount": "98.00",
            "currency": "EUR",
            "exchange_rate": "1.10",
            "allocations": [{"invoice_id": inv["id"], "amount": "98", "discount": "2"}],
            "discount_account_id": _account_id(db_session, "4900"),
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 400
    assert "is in EUR; an early-payment discount is taken in the home currency" in (
        r.json()["detail"]
    )


def test_a_foreign_currency_bill_discount_is_refused(client, db_session, seed_accounts):
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "E-1",
            "date": "2026-10-05",
            "terms": "2% 10 Net 30",
            "currency": "EUR",
            "exchange_rate": "1.10",
            "lines": [{"description": "Lumber", "quantity": 1, "rate": "100"}],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    assert bill["discount_line"] is None
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-10-12",
            "amount": 98.0,
            "currency": "EUR",
            "exchange_rate": "1.10",
            "allocations": [{"bill_id": bill["id"], "amount": 98.0, "discount": "2"}],
            "discount_account_id": _account_id(db_session, "4900"),
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 400
    assert "Bill E-1 is in EUR" in r.json()["detail"]


def test_an_inactive_discount_account_is_refused(client, db_session, discounted):
    acct = db_session.query(Account).filter(Account.account_number == "4900").one()
    acct.is_active = False
    db_session.commit()
    inv = discounted["invoice"]
    r = _receive_one(
        client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct.id
    )
    assert r.status_code == 400
    assert "is inactive; pick an active account for the discount" in r.json()["detail"]


def test_a_discount_is_no_sale_reversed_by_item_or_in_lifetime_sales(
    client, db_session, discounted
):
    """Omarchy: both exclusions survived their own deletion."""
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    _receive_one(client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct)
    period = {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    sbi = client.get("/api/reports/sales-by-item", params=period).json()
    assert sbi["total_amount"] == 100.0
    lines = client.get(
        "/api/reports/sales-by-item/lines", params={**period, "item_id": "none"}
    )
    assert lines.status_code == 200, lines.text
    assert [ln["doc_type"] for ln in lines.json()["lines"]] == ["Invoice"]
    past = client.get("/api/customers/past").json()
    assert past["rows"][0]["lifetime_sales"] == 100.0


def test_a_discount_we_took_is_no_purchase_returned(
    client, db_session, discounted_bill
):
    """Omarchy W3: the vendor-credit discount showed on Purchases by Item as
    a negative "no item" row."""
    _pay(client, discounted_bill, 490.0, "10.00", _account_id(db_session, "4900"))
    period = {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    pbi = client.get("/api/reports/purchases-by-item", params=period).json()
    assert pbi["total_amount"] == 500.0
    lines = client.get(
        "/api/reports/purchases-by-item/lines", params={**period, "item_id": "none"}
    ).json()["lines"]
    assert [ln["doc_type"] for ln in lines] == ["Bill"]


def test_a_date_driven_discount_before_its_due_day_says_the_month_before(client):
    term = _term(
        client,
        name="2% 25th Net 10th",
        kind="date_driven",
        due_day=10,
        cutoff_days=28,
        discount_percent="2",
        discount_day=25,
    )
    assert term["description"] == (
        "Due on day 10 of the month, the next month's when dated fewer than 28 "
        "days before it; 2% off if paid by day 25 of the month before it's due"
    )


def test_listing_the_terms_writes_nothing(client, db_session):
    """Both passes, W7: the first GET seeded the list, for any role."""
    from app.models.payment_terms import PaymentTerm

    assert client.get("/api/payment-terms").json() == []
    assert db_session.query(PaymentTerm).count() == 0


def test_refusals_in_the_house_style(client):
    term = _term(client, name="Net 20", net_days=20)
    r = client.delete(f"/api/payment-terms/{term['id']}")
    assert r.status_code == 405
    assert "Make it inactive: PUT /api/payment-terms/" in r.json()["detail"]
    r = client.patch(f"/api/payment-terms/{term['id']}", json={})
    assert r.status_code == 405
    assert r.json()["detail"].startswith("Change a payment term with PUT")
    r = client.put(f"/api/payment-terms/{term['id']}", json={"name": None})
    assert r.json()["detail"] == "Name can't be blank; leave it out instead."
    r = client.get("/api/payment-terms", params={"active": "1"})
    assert r.status_code == 422


def test_the_list_says_where_else_a_term_is_carried(client, seed_accounts):
    term = _term(client, name="Net 20", net_days=20)
    client.post("/api/customers", json={"name": "Shore", "terms": "net  20"})
    client.post("/api/vendors", json={"name": "Bay", "terms": "Net 20"})
    got = client.get(f"/api/payment-terms/{term['id']}").json()
    assert got["documents"] == 0 and got["elsewhere"] == 2


def test_a_csv_refusal_names_ten_terms_at_most(client):
    for n in range(1, 9):
        _term(client, name=f"Custom {n}", net_days=n)
    r = client.post(
        "/api/csv/import/vendors",
        files={"file": ("v.csv", b"Name,Terms\nBay,Bogus\n", "text/csv")},
    )
    (error,) = r.json()["errors"]
    assert " and 3 more)" in error


# ── the second pass (#665, Omarchy, on 5c3b55dd) ──────────────────────


def _put(client, path, body):
    r = client.put(path, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_an_old_invoice_saved_with_the_same_terms_stays_untied(
    client, seed_accounts, seed_customer
):
    """Pass 2, B1: any save that sent a legacy invoice's terms back tied it
    to a term made later; the invoice form always sends them."""
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")  # before the term
    term = _term(client, **TWO_TEN)
    for _ in range(2):
        got = _put(
            client,
            f"/api/invoices/{inv['id']}",
            {"terms": "2% 10 Net 30", "notes": "memo"},
        )
        assert got["discount_line"] is None
        assert got["terms_note"].startswith(
            "This invoice's terms were first saved before"
        )
    assert client.get(f"/api/payment-terms/{term['id']}").json()["documents"] == 0
    # picking other terms, then these, is choosing them: now it is tied
    _put(client, f"/api/invoices/{inv['id']}", {"terms": "Net 30"})
    got = _put(client, f"/api/invoices/{inv['id']}", {"terms": "2% 10 Net 30"})
    assert got["discount_line"] == "2% discount if paid by Oct 15, 2026"


def test_an_old_bill_saved_with_the_same_terms_keeps_its_rule_and_due_date(
    client, seed_accounts
):
    """Pass 2, B1 and guard G12: a bill edit's due date comes from the
    bill's own rule (the old reading), never a term made since."""
    vendor = _vendor(client, terms="Net 30")
    bill = _bill(client, vendor["id"], "net10th", on="2026-10-05")
    assert bill["due_date"] == "2026-11-04"  # main's 30 days
    _term(client, name="net10th", kind="date_driven", due_day=10, cutoff_days=10)
    got = _put(
        client, f"/api/bills/{bill['id']}", {"terms": "net10th", "date": "2026-10-06"}
    )
    assert got["due_date"] == "2026-11-05"  # still the old 30 days, from the new date
    assert got["terms_note"].startswith("This bill's terms were first saved before")


def test_copies_and_schedules_keep_the_tie_of_their_source(
    client, db_session, seed_accounts, seed_customer
):
    """Pass 2, B1: a duplicate, a recurring invoice's next one and a
    memorized bill's next one follow the term their source was saved
    under, not one added since."""
    from app.services.recurring_service import generate_due_invoices

    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    rec = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-10-01",
            "terms": "2% 10 Net 30",
            "lines": [{"description": "Fee", "quantity": 1, "rate": "50"}],
        },
    )
    assert rec.status_code == 201, rec.text
    vendor = _vendor(client)
    mem = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "Rent",
            "mode": "remind",
            "frequency": "monthly",
            "start_date": "2026-10-20",
            "vendor_id": vendor["id"],
            "terms": "2% 10 Net 30",
            "lines": [
                {
                    "account_id": _account_id(db_session, "6500"),
                    "description": "Rent",
                    "amount": 400,
                }
            ],
        },
    )
    assert mem.status_code == 201, mem.text
    _term(client, **TWO_TEN)  # added after all three were saved

    dup = client.post(f"/api/invoices/{inv['id']}/duplicate", json={})
    assert dup.status_code == 201, dup.text
    assert dup.json()["discount_line"] is None
    ids = generate_due_invoices(db_session, as_of=date(2026, 10, 1))
    made = client.get(f"/api/invoices/{ids[0]}").json()
    assert made["discount_line"] is None and made["terms_note"]
    entered = client.post(f"/api/memorized/{mem.json()['id']}/enter").json()
    bill = client.get(f"/api/bills/{entered['document_id']}").json()
    assert bill["discount_line"] is None

    # a schedule saved again with its terms unchanged stays as it was;
    # changed to them, it follows the term
    rec_id = rec.json()["id"]
    _put(client, f"/api/recurring/{rec_id}", {"terms": "2% 10 Net 30"})
    from app.models.recurring import RecurringInvoice

    db_session.expire_all()
    assert db_session.get(RecurringInvoice, rec_id).payment_term_id is None
    _put(client, f"/api/recurring/{rec_id}", {"terms": "Net 30"})
    _put(client, f"/api/recurring/{rec_id}", {"terms": "2% 10 Net 30"})
    db_session.expire_all()
    assert db_session.get(RecurringInvoice, rec_id).payment_term_id is not None


def test_ap_aging_ages_an_old_bill_by_its_own_rule(client, db_session, seed_accounts):
    """Pass 2, B2: a bill with no due date aged through the list, so a
    term added or changed later moved it between buckets."""
    from app.models.bills import Bill

    vendor = _vendor(client)
    bill = _bill(client, vendor["id"], "Net 10th", on="2026-09-15")
    row = db_session.get(Bill, bill["id"])
    row.due_date = None  # as main's IIF import leaves a bill with no DUEDATE
    db_session.commit()

    def aging():
        # Oct 10: by its old reading (30 days) the bill is due Oct 15 and
        # current; a term read through the list would make it overdue
        rows = client.get(
            "/api/reports/ap-aging", params={"as_of_date": "2026-10-10"}
        ).json()
        return json.dumps(rows, sort_keys=True)

    before = aging()
    term = _term(client, name="Net 10th", kind="date_driven", due_day=30)
    assert aging() == before
    _put(client, f"/api/payment-terms/{term['id']}", {"due_day": 5, "cutoff_days": 10})
    assert aging() == before


def test_a_line_edit_after_a_discount_keeps_cash_sales_tax_on_its_ledger(
    client, db_session, seed_accounts, seed_customer
):
    """Pass 2, B4: the payment's cash lines were re-prorated on an edit and
    the discount's were not; the cash Sales Tax report was 0.11 off 2200."""
    _term(client, **TWO_TEN)
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "terms": "2% 10 Net 30",
            "tax_rate": "0.07",
            "lines": [
                {"description": "Svc", "quantity": 1, "rate": "500", "is_taxable": True}
            ],
        },
    ).json()
    acct = _account_id(db_session, "4900")
    assert (
        _receive_one(
            client, seed_customer.id, inv["id"], "524.30", "10.70", acct
        ).status_code
        == 201
    )
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {
                    "description": "Svc",
                    "quantity": 1,
                    "rate": "500",
                    "is_taxable": True,
                },
                {
                    "description": "Extra",
                    "quantity": 1,
                    "rate": "100",
                    "is_taxable": False,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    tax = client.get(
        "/api/reports/sales-tax",
        params={"start_date": "2026-10-01", "end_date": "2026-12-31", "basis": "cash"},
    ).json()
    assert tax["ledger"]["difference"] == 0.0
    pl = client.get(
        "/api/reports/profit-loss",
        params={"start_date": "2026-10-01", "end_date": "2026-12-31", "basis": "cash"},
    ).json()
    income = {r["account_number"]: r["amount"] for r in pl["income"]}
    assert income["4000"] == tax["total_sales"]
    _balanced(db_session)


def test_one_invoice_twice_with_a_discount_names_no_memo(
    client, db_session, discounted
):
    """Pass 2, B5: the refusal named the rolled-back CM-0001, which the next
    real discount then took."""
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    r = client.post(
        "/api/payments",
        json={
            "customer_id": discounted["customer_id"],
            "date": "2026-10-12",
            "amount": "100",
            "allocations": [
                {"invoice_id": inv["id"], "amount": "49", "discount": "1.00"},
                {"invoice_id": inv["id"], "amount": "49", "discount": "1.00"},
            ],
            "discount_account_id": acct,
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        f"Invoice {inv['invoice_number']} is on this payment more than once with a "
        "discount. Put it on one row, with its whole payment and its discount."
    )
    assert "CM-" not in r.json()["detail"]
    r = _receive_one(
        client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct
    )
    assert r.json()["discounts"][0]["memo_number"] == "CM-0001"


# ── pass 2 weaknesses ───────────────────────────────────────────────────


def test_an_override_is_recorded_and_an_administrators_alone(
    client, db_session, seed_accounts, seed_customer
):
    """W1: a bookkeeper took a 90% "early-payment discount" with the
    override, and nothing recorded it."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.audit import AuditLog

    inv = _invoice(client, seed_customer.id, "Net 30")
    acct = _account_id(db_session, "4900")
    token = client.post("/api/tokens", json={"label": "keeper", "role": "bookkeeper"})
    keeper = TestClient(app)
    keeper.headers["Authorization"] = f"Bearer {token.json()['token']}"
    r = _receive_one(
        keeper,
        seed_customer.id,
        inv["id"],
        "10.00",
        "90.00",
        acct,
        allow_discount_override=True,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == (
        f"Invoice {inv['invoice_number']}'s terms (Net 30) offer no early-payment "
        "discount. Only an administrator can take a discount the terms don't "
        "offer; ask one to record it."
    )
    r = _receive_one(
        client,
        seed_customer.id,
        inv["id"],
        "10.00",
        "90.00",
        acct,
        allow_discount_override=True,
    )
    assert r.status_code == 201, r.text
    pay = r.json()
    assert pay["discount_override"] is True
    assert "Discount taken against the terms by" in pay["discount_override_note"]
    assert "offer no early-payment discount" in pay["discount_override_note"]
    assert pay["discounts"][0]["override"] is True
    cm = db_session.query(CreditMemo).filter_by(discount_payment_id=pay["id"]).one()
    assert cm.discount_override is True
    assert "taken against its terms" in cm.notes
    logged = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "payments", AuditLog.record_id == pay["id"])
        .all()
    )
    assert any((row.new_values or {}).get("discount_override") for row in logged)


def test_a_bill_override_is_recorded_too(client, db_session, discounted_bill):
    acct = _account_id(db_session, "4900")
    body = {
        "vendor_id": discounted_bill["vendor_id"],
        "date": "2026-11-20",
        "amount": 490.0,
        "allocations": [
            {"bill_id": discounted_bill["id"], "amount": 490.0, "discount": "10.00"}
        ],
        "discount_account_id": acct,
        "allow_discount_override": True,
    }
    pay = client.post("/api/bill-payments", json=body).json()
    assert pay["discount_override"] is True and pay["discounts"][0]["override"] is True
    vc = (
        db_session.query(VendorCredit)
        .filter_by(discount_bill_payment_id=pay["id"])
        .one()
    )
    assert vc.discount_override is True


def test_a_discount_after_a_short_payment_is_taken_with_that_payment(
    client, db_session, discounted
):
    """Air 6 (still open after pass 1): 98 of 100 paid in the window; the
    2 could only go on as an ordinary credit memo."""
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": discounted["customer_id"],
            "date": "2026-10-12",
            "amount": "98.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "98.00"}],
        },
    ).json()
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={"invoice_id": inv["id"], "discount": "2.00", "discount_account_id": acct},
    )
    assert r.status_code == 200, r.text
    assert r.json()["discounts"][0]["amount"] == "2.00"
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"
    # it is no sale reversed, and it goes with its payment
    rep = client.get(
        "/api/reports/sales-tax",
        params={"start_date": "2026-10-01", "end_date": "2026-10-31"},
    ).json()
    assert rep["total_sales"] == 100.0
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    assert client.get(f"/api/invoices/{inv['id']}").json()["balance_due"] == "100.00"
    _balanced(db_session)


def test_a_late_discount_is_checked_like_any_other(client, db_session, discounted):
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": discounted["customer_id"],
            "date": "2026-10-30",
            "amount": "98.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "98.00"}],
        },
    ).json()
    url = f"/api/payments/{pay['id']}/discount"
    body = {"invoice_id": inv["id"], "discount": "2.00", "discount_account_id": acct}
    r = client.post(url, json=body)
    assert r.status_code == 400
    assert "had to be taken by Oct 15, 2026; this payment is dated Oct 30" in (
        r.json()["detail"]
    )
    other = _invoice(client, discounted["customer_id"], "2% 10 Net 30")
    r = client.post(url, json={**body, "invoice_id": other["id"]})
    assert r.status_code == 400
    assert "This payment paid nothing on invoice" in r.json()["detail"]
    r = client.post(url, json={**body, "allow_discount_override": True})
    assert r.status_code == 200, r.text
    assert r.json()["discount_override"] is True


def test_a_bill_discount_after_a_short_payment(client, db_session, discounted_bill):
    acct = _account_id(db_session, "4900")
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": discounted_bill["vendor_id"],
            "date": "2026-10-12",
            "amount": 490.0,
            "allocations": [{"bill_id": discounted_bill["id"], "amount": 490.0}],
        },
    ).json()
    r = client.post(
        f"/api/bill-payments/{pay['id']}/discount",
        json={
            "bill_id": discounted_bill["id"],
            "discount": "10.00",
            "discount_account_id": acct,
        },
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/api/bills/{discounted_bill['id']}").json()["status"] == "paid"


@pytest.mark.parametrize("name", ["​Net 30", "Net 30﻿", "Ｎｅｔ 30"])
def test_a_name_that_only_looks_different_is_the_same_name(client, db_session, name):
    """W2: a zero-width space made a second "Net 30" on every list."""
    from app.services.payment_terms import ensure_seeded

    ensure_seeded(db_session)
    db_session.commit()
    r = client.post("/api/payment-terms", json={"name": name, "net_days": 45})
    assert r.status_code == 409
    assert r.json()["detail"] == '"Net 30" is already on the terms list.'


def test_a_name_main_read_as_days_keeps_them(client):
    """W2: "Net 1_0" was 10 days on main (int() takes the underscore)."""
    r = client.post("/api/payment-terms", json={"name": "Net 1_0", "net_days": 45})
    assert r.status_code == 409
    assert "already means due in 10 days" in r.json()["detail"]


def test_the_convert_and_send_responses_carry_the_discount(
    client, seed_accounts, seed_customer
):
    """W5: duplicate, convert, void and send answered with no discount."""
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    r = client.post(f"/api/invoices/{inv['id']}/send")
    assert r.status_code == 200, r.text
    assert r.json()["discount_line"] == "2% discount if paid by Oct 15, 2026"
    dup = client.post(f"/api/invoices/{inv['id']}/duplicate", json={}).json()
    assert dup["discount_line"] is not None


def test_the_pages_take_a_late_discount_and_show_an_override():
    pay = _js("payments.js")
    assert "PaymentsPage.showTakeDiscount(${p.id}, ${a.invoice_id})" in pay
    assert "API.post(`/payments/${paymentId}/discount`, body)" in pay
    assert "escapeHtml(p.discount_override_note)" in pay
    bills = _js("bills.js")
    assert "BillsPage.showTakeDiscount(${p.id}, ${a.bill_id})" in bills
    assert "API.post(`/bill-payments/${paymentId}/discount`, body)" in bills
    assert "escapeHtml(p.discount_override_note)" in bills
    assert "taken against the terms" in _js("credit_memos.js")
    assert "taken against the terms" in _js("vendor_credits.js")


@pytest.mark.parametrize(
    "text, due",
    [
        ("net\t45", "2026-10-31"),  # main: no space after "net", so 30 days
        ("net\u00a045", "2026-10-31"),
        ("Net 1_0", "2026-10-11"),  # main's int() took the underscore
        ("+15", "2026-10-16"),
        ("Net 30.0", "2026-10-31"),
    ],
)
def test_untied_text_reads_exactly_as_main_read_it(client, text, due):
    """Pass 2, B3: the reader collapsed every whitespace, so "net<TAB>45"
    read as 45 days where main gave it 30. With no list entry, the text
    alone decides."""
    d = client.get(
        "/api/payment-terms/dates", params={"date": "2026-10-01", "terms": text}
    ).json()
    assert d["due_date"] == due, text


# ── the third pass (#665, Omarchy, on e474ac95) ───────────────────────


@pytest.mark.parametrize(
    "on, due",
    [
        ("2026-01-01", "2026-02-05"),  # Jan 5 is 4 days away: the month after
        ("2026-01-26", "2026-02-05"),  # 10 days before Feb 5: not fewer
        ("2026-01-27", "2026-03-05"),  # 9 days before Feb 5: the month after
        ("2026-01-31", "2026-03-05"),
        ("2026-12-28", "2027-02-05"),  # across the year
    ],
)
def test_the_cutoff_moves_a_month_across_a_month_end(client, on, due):
    """Pass 3, B3: dated Jan 27 under "5th, 10-day cutoff" was due Feb 5;
    the rule says fewer than 10 days before the due day moves it a month."""
    _term(client, name="5th prox", kind="date_driven", due_day=5, cutoff_days=10)
    d = client.get(
        "/api/payment-terms/dates", params={"date": on, "terms": "5th prox"}
    ).json()
    assert d["due_date"] == due, on


def test_any_other_text_picked_ties_anew(
    client, db_session, seed_accounts, seed_customer
):
    """Pass 3, B2 and W2: picking "Net 45" over a bill saved as "net<TAB>45"
    (main's 30 days) was taken for the same terms, so the bill stayed 30
    days out where main moved it to 45; a case-only pick of a listed term
    was ignored without a word."""
    from app.services.payment_terms import ensure_seeded

    vendor = _vendor(client)
    # saved before the list held Net 45, as main's books were
    bill = _bill(client, vendor["id"], "net\t45", on="2026-09-15")
    assert bill["due_date"] == "2026-10-15"  # main read it as 30 days
    ensure_seeded(db_session)
    db_session.commit()
    got = _put(client, f"/api/bills/{bill['id']}", {"terms": "Net 45"})
    assert got["due_date"] == "2026-10-30" and got["terms_note"] is None
    # a case-only pick of a listed term is a pick: tied, its rule applies
    inv = _invoice(client, seed_customer.id, "net30")
    term = _term(
        client, name="NET30", net_days=30, discount_percent="1", discount_days=5
    )
    got = _put(client, f"/api/invoices/{inv['id']}", {"terms": "NET30"})
    assert got["terms_note"] is None
    assert got["discount_line"] == "1% discount if paid by Oct 10, 2026"
    assert client.get(f"/api/payment-terms/{term['id']}").json()["documents"] == 1


def test_a_copy_says_its_terms_keep_their_first_reading(
    client, seed_accounts, seed_customer
):
    """Pass 3, B4: a duplicate made after the term existed said it "was
    saved before" it."""
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    _term(client, **TWO_TEN)
    dup = client.post(f"/api/invoices/{inv['id']}/duplicate", json={}).json()
    assert dup["terms_note"].startswith(
        'This invoice\'s terms were first saved before "2% 10 Net 30" was on the '
        "terms list, so it keeps their old reading"
    )
    assert "was saved before" not in dup["terms_note"]


def _short_paid(client, discounted):
    return client.post(
        "/api/payments",
        json={
            "customer_id": discounted["customer_id"],
            "date": "2026-10-12",
            "amount": "98.00",
            "allocations": [
                {"invoice_id": discounted["invoice"]["id"], "amount": "98.00"}
            ],
        },
    ).json()


def test_a_late_discount_on_a_void_payment_or_a_closed_period_is_refused(
    client, db_session, discounted
):
    """Pass 3, W1: both refusals survived their deletion."""
    inv = discounted["invoice"]
    body = {
        "invoice_id": inv["id"],
        "discount": "2.00",
        "discount_account_id": _account_id(db_session, "4900"),
    }
    pay = _short_paid(client, discounted)
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    r = client.post(f"/api/payments/{pay['id']}/discount", json=body)
    assert r.status_code == 400
    assert r.json()["detail"] == "This payment is void, so no discount goes with it."
    pay = _short_paid(client, discounted)
    assert (
        client.put("/api/settings", json={"closing_date": "2026-10-31"}).status_code
        == 200
    )
    # the closed period is said first, before the discount is weighed (the
    # journal refuses it again underneath, on purpose): $5.00 is more than
    # is due, but the answer is the closing date
    r = client.post(
        f"/api/payments/{pay['id']}/discount", json={**body, "discount": "5.00"}
    )
    assert r.status_code == 403
    assert "closing date" in r.json()["detail"]


def test_a_late_bill_discount_on_a_void_payment_or_a_closed_period_is_refused(
    client, db_session, discounted_bill
):
    def paid():
        return client.post(
            "/api/bill-payments",
            json={
                "vendor_id": discounted_bill["vendor_id"],
                "date": "2026-10-12",
                "amount": 490.0,
                "allocations": [{"bill_id": discounted_bill["id"], "amount": 490.0}],
            },
        ).json()

    body = {
        "bill_id": discounted_bill["id"],
        "discount": "10.00",
        "discount_account_id": _account_id(db_session, "4900"),
    }
    pay = paid()
    assert client.post(f"/api/bill-payments/{pay['id']}/void").status_code == 200
    r = client.post(f"/api/bill-payments/{pay['id']}/discount", json=body)
    assert r.status_code == 400
    assert r.json()["detail"] == "This payment is void, so no discount goes with it."
    pay = paid()
    assert (
        client.put("/api/settings", json={"closing_date": "2026-10-31"}).status_code
        == 200
    )
    r = client.post(
        f"/api/bill-payments/{pay['id']}/discount", json={**body, "discount": "50.00"}
    )
    assert r.status_code == 403
    assert "closing date" in r.json()["detail"]


def test_a_memorized_schedule_is_retied_only_when_its_terms_change(
    client, db_session, seed_accounts
):
    """Pass 3, W1: the memorized PUT's re-tie survived its deletion."""
    from app.models.memorized import MemorizedTransaction

    vendor = _vendor(client)
    mem = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "Rent",
            "mode": "remind",
            "frequency": "monthly",
            "start_date": "2026-10-20",
            "vendor_id": vendor["id"],
            "terms": "2% 10 Net 30",
            "lines": [
                {
                    "account_id": _account_id(db_session, "6500"),
                    "description": "Rent",
                    "amount": 400,
                }
            ],
        },
    ).json()
    term = _term(client, **TWO_TEN)
    _put(client, f"/api/memorized/{mem['id']}", {"terms": "2% 10 Net 30"})
    db_session.expire_all()
    assert db_session.get(MemorizedTransaction, mem["id"]).payment_term_id is None
    _put(client, f"/api/memorized/{mem['id']}", {"terms": "2% 10 net 30"})
    db_session.expire_all()
    assert db_session.get(MemorizedTransaction, mem["id"]).payment_term_id == term["id"]


def test_text_past_the_calendar_reads_as_nothing(client):
    """Pass 3, W1: the 99,999-day cap. Net 100000 from 2026 lands in 2300,
    which main gave it; past the cap the date would leave the calendar for
    some dates, so the text reads as nothing (30 days and a note)."""
    d = client.get(
        "/api/payment-terms/dates", params={"date": "2026-10-01", "terms": "Net 100000"}
    ).json()
    assert d["due_date"] == "2026-10-31" and d["note"]
    d = client.get(
        "/api/payment-terms/dates", params={"date": "2026-10-01", "terms": "Net 99999"}
    ).json()
    assert d["due_date"] == "2300-07-16" and d["note"] is None


def test_a_csv_import_refuses_negative_terms(client):
    """Pass 3, W3: "Net -5" came in, then every invoice for that customer
    was refused for being due before its date."""
    r = client.post(
        "/api/csv/import/customers",
        files={
            "file": ("c.csv", b"Name,Terms\nNeg Co,Net -5\nOk Co,Net 99\n", "text/csv")
        },
    )
    out = r.json()
    assert out["created"] == 1
    assert out["errors"] == [
        'Row 2: Terms "Net -5" would make an invoice due before its date. Use a '
        "number of days of 0 or more."
    ]


@pytest.mark.parametrize(
    "name, sentence",
    [
        ("ﬀ" * 50, "A term name is at most 50 characters; this one is 100."),
        ("‎‏", "A term needs a name."),
    ],
)
def test_a_name_refusal_is_a_sentence(client, name, sentence):
    """Pass 3, W4: "At most 50 name are allowed." """
    r = client.post("/api/payment-terms", json={"name": name, "net_days": 3})
    assert r.status_code == 422
    assert r.json()["detail"][0]["message"] == sentence


def test_joiners_stay_in_a_name_but_not_in_its_comparison(client):
    """Pass 3, W3: stripping every format character took ZWJ out of emoji
    and ZWNJ out of Persian text."""
    term = _term(client, name="fam \U0001f468‍\U0001f469", net_days=3)
    assert "‍" in term["name"]
    r = client.post(
        "/api/payment-terms", json={"name": "fam \U0001f468\U0001f469", "net_days": 3}
    )
    assert r.status_code == 409


def test_a_void_document_offers_no_discount(
    client, db_session, seed_accounts, seed_customer
):
    """Pass 3, W5: a void invoice still advertised its discount."""
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["discount_line"] is None
    assert (
        "discount if paid"
        not in client.get(f"/api/invoices/{inv['id']}/print-preview").text
    )
    vendor = _vendor(client, terms="2% 10 Net 30")
    bill = _bill(client, vendor["id"], "2% 10 Net 30")
    assert client.post(f"/api/bills/{bill['id']}/void").status_code == 200
    assert client.get(f"/api/bills/{bill['id']}").json()["discount_line"] is None
    assert (
        "discount if paid"
        not in client.get(f"/api/bills/{bill['id']}/print-preview").text
    )


# ── pass 3, B1: the late-discount endpoints under concurrency ─────────────

from tests.test_memorized_transactions import _race, file_company  # noqa: E402,F401


def _post_after_all_read(monkeypatch, name):
    """Hold every racer at the posting until the others have read too, or
    a second and a half has passed: without the claim all four read "no
    discount yet" and then each posts one; with it, the first holds the
    write lock here and the rest wait at the claim, then read its memo."""
    import threading

    from app.services import early_pay_discounts

    gate = threading.Barrier(4, timeout=1.5)
    real = getattr(early_pay_discounts, name)

    def post(*args, **kw):
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return real(*args, **kw)

    monkeypatch.setattr(early_pay_discounts, name, post)


def test_four_late_discounts_at_once_post_one(file_company, monkeypatch):  # noqa: F811
    """Pass 3, B1: four simultaneous /discount calls posted four credit
    memos (A/R -$4.00, Sales Discounts $6.00 on a $2.00 discount)."""
    from fastapi import HTTPException

    from app.models.credit_memos import CreditMemo
    from app.models.invoices import Invoice
    from app.routes import payments as payments_routes
    from app.schemas.payments import PaymentDiscountAdd

    client, Session, ids = file_company
    _term(client, **TWO_TEN)
    cust = client.post("/api/customers", json={"name": "Shore"}).json()
    inv = _invoice(client, cust["id"], "2% 10 Net 30")
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": cust["id"],
            "date": "2026-10-12",
            "amount": "98.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "98.00"}],
        },
    ).json()
    _post_after_all_read(monkeypatch, "post_customer_discount")
    body = PaymentDiscountAdd(
        invoice_id=inv["id"], discount="2.00", discount_account_id=ids["4900"].id
    )

    def take():
        with Session() as db:
            return payments_routes.add_discount(pay["id"], body, None, None, db)

    out = _race(take, take, take, take)
    refused = [x for x in out if isinstance(x, HTTPException)]
    assert len(refused) == 3, out
    # each loser read the winner's memo once it held the lock: the balance
    # it left (nothing), refused in a sentence
    assert {x.status_code for x in refused} == {400}, [x.detail for x in refused]
    assert all(
        "still due on invoice" in x.detail or "already taken" in x.detail
        for x in refused
    ), [x.detail for x in refused]
    with Session() as db:
        assert (
            db.query(CreditMemo).filter_by(discount_payment_id=pay["id"]).count() == 1
        )
        row = db.get(Invoice, inv["id"])
        assert row.balance_due == 0 and row.amount_paid == 100


def test_four_late_bill_discounts_at_once_post_one(
    file_company, monkeypatch  # noqa: F811
):
    from fastapi import HTTPException

    from app.models.vendor_credits import VendorCredit
    from app.routes import bill_payments as bill_payment_routes
    from app.schemas.bills import BillPaymentDiscountAdd

    client, Session, ids = file_company
    _term(client, **TWO_TEN)
    vendor = client.post(
        "/api/vendors",
        json={
            "name": "Gulf Supply",
            "terms": "2% 10 Net 30",
            "default_expense_account_id": ids["6000"].id,
        },
    ).json()
    bill = _bill(client, vendor["id"], "2% 10 Net 30", amount="100.00")
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-10-12",
            "amount": 98.0,
            "allocations": [{"bill_id": bill["id"], "amount": 98.0}],
        },
    ).json()
    _post_after_all_read(monkeypatch, "post_vendor_discount")
    body = BillPaymentDiscountAdd(
        bill_id=bill["id"], discount="2.00", discount_account_id=ids["4900"].id
    )

    def take():
        with Session() as db:
            return bill_payment_routes.add_discount(pay["id"], body, None, None, db)

    out = _race(take, take, take, take)
    assert sum(isinstance(x, HTTPException) for x in out) == 3, out
    with Session() as db:
        assert (
            db.query(VendorCredit).filter_by(discount_bill_payment_id=pay["id"]).count()
            == 1
        )


def test_a_late_discount_sent_again_with_its_key_answers_the_first(
    client, db_session, discounted
):
    """Pass 3, B1: an agent's retry after a lost response posted twice."""
    inv = discounted["invoice"]
    pay = _short_paid(client, discounted)
    body = {
        "invoice_id": inv["id"],
        "discount": "2.00",
        "discount_account_id": _account_id(db_session, "4900"),
    }
    url = f"/api/payments/{pay['id']}/discount"
    first = client.post(url, json=body, headers={"Idempotency-Key": "take-2"})
    again = client.post(url, json=body, headers={"Idempotency-Key": "take-2"})
    assert first.status_code == 200 and again.status_code == 200
    assert again.headers.get("X-Replayed") == "true"
    assert again.json() == first.json()
    assert (
        db_session.query(CreditMemo).filter_by(discount_payment_id=pay["id"]).count()
        == 1
    )


# ── the fourth pass (#665, Mac Studio, on 4995ac7f) ───────────────────


def _invoice_on(client, customer_id, on):
    return _invoice(client, customer_id, "2% 10 Net 30", on=on)


def test_no_discount_on_a_payment_dated_before_its_invoice(
    client, db_session, seed_accounts, seed_customer
):
    """Pass 4, B1: an August payment took October's 2%, posted in August,
    override or not."""
    _term(client, **TWO_TEN)
    inv = _invoice_on(client, seed_customer.id, "2026-10-28")
    acct = _account_id(db_session, "4900")
    for override in (False, True):
        r = _receive_one(
            client,
            seed_customer.id,
            inv["id"],
            "98.00",
            "2.00",
            acct,
            date="2026-08-01",
            allow_discount_override=override,
        )
        assert r.status_code == 400, override
        assert r.json()["detail"] == (
            f"This payment is dated Aug 1, 2026, before invoice "
            f"{inv['invoice_number']} (Oct 28, 2026); an early-payment discount "
            "is taken with a payment made on or after the invoice's date."
        )
    assert db_session.query(CreditMemo).count() == 0


def test_no_bill_discount_on_a_payment_dated_before_its_bill(
    client, db_session, seed_accounts
):
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    bill = _bill(client, vendor["id"], "2% 10 Net 30", on="2026-10-28", amount="100.00")
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-08-03",
            "amount": 98.0,
            "allocations": [
                {"bill_id": bill["id"], "amount": 98.0, "discount": "2.00"}
            ],
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 400
    assert "This payment is dated Aug 3, 2026, before bill B-1" in r.json()["detail"]


def test_a_remainder_applied_later_takes_no_late_discount(
    client, db_session, seed_accounts, seed_customer
):
    """Pass 4, B1: /apply then /discount went round the apply refusal."""
    _term(client, **TWO_TEN)
    inv = _invoice_on(client, seed_customer.id, "2026-10-05")
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-06",
            "amount": "98.00",
            "allocations": [],
        },
    ).json()
    r = client.post(
        f"/api/payments/{pay['id']}/apply",
        json={"allocations": [{"invoice_id": inv["id"], "amount": "98"}]},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": inv["id"],
            "discount": "2.00",
            "discount_account_id": _account_id(db_session, "4900"),
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 400
    assert "was applied later from its remainder" in r.json()["detail"]


def test_a_second_instalment_in_the_window_gets_the_whole_percent(
    client, db_session, discounted
):
    """Pass 4, W5: 49 + 49 in the window; the second's discount was 2% of
    the 51 left and needed an administrator."""
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    first = client.post(
        "/api/payments",
        json={
            "customer_id": discounted["customer_id"],
            "date": "2026-10-12",
            "amount": "49.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "49.00"}],
        },
    )
    assert first.status_code == 201
    r = _receive_one(
        client, discounted["customer_id"], inv["id"], "49.00", "2.00", acct
    )
    assert r.status_code == 201, r.text
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"


@pytest.mark.parametrize("name", ["Net\t81", "Net\r\n81", "Net \x0b81", " Net  81 "])
def test_a_name_with_odd_whitespace_reads_as_its_words(client, name):
    """Pass 4, B2: "Net<TAB>81" passed the name guard (main gave that text
    30 days), then took over plain "Net 81" with a 12-day rule."""
    r = client.post("/api/payment-terms", json={"name": name, "net_days": 12})
    assert r.status_code == 409
    assert r.json()["detail"].startswith('"Net 81" already means due in 81 days')
    made = client.post("/api/payment-terms", json={"name": name, "net_days": 81})
    assert made.status_code == 201, made.text
    assert made.json()["name"] == "Net 81"
    again = client.post("/api/payment-terms", json={"name": "Net 81", "net_days": 81})
    assert again.status_code == 409


@pytest.mark.parametrize(
    "path, body",
    [
        (
            "payments",
            {"allocations": [{"invoice_id": 1, "amount": "1", "discount": "1e30"}]},
        ),
        (
            "bill-payments",
            {"allocations": [{"bill_id": 1, "amount": 1, "discount": "1e30"}]},
        ),
    ],
)
def test_a_huge_discount_is_a_sentence_on_create(client, path, body):
    """Pass 4, B3: "1e30" was a 500 on all four discount endpoints."""
    who = {"customer_id": 1} if path == "payments" else {"vendor_id": 1}
    r = client.post(
        f"/api/{path}", json={**who, "date": "2026-10-12", "amount": "1", **body}
    )
    assert r.status_code == 422
    assert (
        r.json()["detail"][0]["message"]
        == "Allocation 1: A discount is at most $9,999,999,999,999.99."
    )


def test_a_huge_late_discount_is_a_sentence(client, db_session, discounted):
    pay = _short_paid(client, discounted)
    for amount in ("1e30", "123456789012345678901234567890"):
        r = client.post(
            f"/api/payments/{pay['id']}/discount",
            json={
                "invoice_id": discounted["invoice"]["id"],
                "discount": amount,
                "discount_account_id": _account_id(db_session, "4900"),
            },
        )
        assert r.status_code == 422, amount
        assert r.json()["detail"][0]["message"] == (
            "A discount is at most $9,999,999,999,999.99."
        )
    r = client.post(
        "/api/bill-payments/1/discount",
        json={"bill_id": 1, "discount": "1e30", "discount_account_id": 1},
    )
    assert r.status_code == 422


def test_a_cutoff_fits_inside_february(client):
    """Pass 4, B4: a 29 to 31 day cutoff moved one month, so in February a
    document could be due sooner than its own cutoff."""
    r = client.post(
        "/api/payment-terms",
        json={
            "name": "1st cut31",
            "kind": "date_driven",
            "due_day": 1,
            "cutoff_days": 29,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "The days before the due day must be 0 to 28, so the cutoff fits inside February."
    )
    _term(client, name="1st cut28", kind="date_driven", due_day=1, cutoff_days=28)
    for on, due in (("2027-02-01", "2027-03-01"), ("2027-02-02", "2027-04-01")):
        d = client.get(
            "/api/payment-terms/dates", params={"date": on, "terms": "1st cut28"}
        ).json()
        assert d["due_date"] == due, on


# ── pass 4 weaknesses ───────────────────────────────────────────────────


def test_a_late_bill_discount_sent_again_with_its_key_answers_the_first(
    client, db_session, discounted_bill
):
    """Pass 4, W1: without finish, a keyed bill-side retry answered 409
    "being processed" for seven days instead of replaying."""
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": discounted_bill["vendor_id"],
            "date": "2026-10-12",
            "amount": 490.0,
            "allocations": [{"bill_id": discounted_bill["id"], "amount": 490.0}],
        },
    ).json()
    url = f"/api/bill-payments/{pay['id']}/discount"
    body = {
        "bill_id": discounted_bill["id"],
        "discount": "10.00",
        "discount_account_id": _account_id(db_session, "4900"),
    }
    first = client.post(url, json=body, headers={"Idempotency-Key": "vc-1"})
    again = client.post(url, json=body, headers={"Idempotency-Key": "vc-1"})
    assert first.status_code == 200 and again.status_code == 200, again.text
    assert again.headers.get("X-Replayed") == "true"
    assert again.json() == first.json()
    assert (
        db_session.query(VendorCredit)
        .filter_by(discount_bill_payment_id=pay["id"])
        .count()
        == 1
    )


def test_a_locked_claim_is_a_sentence(client, db_session, discounted, monkeypatch):
    """Pass 4, W1: the claim's "locked" branch had no test."""
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Query

    pay = _short_paid(client, discounted)

    def locked(self, *a, **kw):
        raise OperationalError("UPDATE payments", {}, Exception("database is locked"))

    monkeypatch.setattr(Query, "update", locked)
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": discounted["invoice"]["id"],
            "discount": "2.00",
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Another discount on this payment is being saved; try again in a moment."
    )


def test_a_late_discount_reads_the_payment_and_document_afresh(
    file_company, monkeypatch  # noqa: F811
):
    """Pass 4, W1: the populate_existing reads had no test. A session that
    already holds a payment or a bill reads them again once it has the
    claim: a void, or a discount taken meanwhile, is seen."""
    from fastapi import HTTPException

    from app.models.bills import Bill, BillPayment
    from app.models.payments import Payment
    from app.routes import bill_payments as bill_payment_routes
    from app.routes import payments as payments_routes
    from app.schemas.bills import BillPaymentDiscountAdd
    from app.schemas.payments import PaymentDiscountAdd

    client, Session, ids = file_company
    _term(client, **TWO_TEN)
    cust = client.post("/api/customers", json={"name": "Shore"}).json()
    inv = _invoice(client, cust["id"], "2% 10 Net 30")
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": cust["id"],
            "date": "2026-10-12",
            "amount": "98.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "98.00"}],
        },
    ).json()
    held = Session()
    assert held.get(Payment, pay["id"]).is_voided is False  # held, as read
    with Session() as other:
        payments_routes.void_payment(pay["id"], False, other)
    body = PaymentDiscountAdd(
        invoice_id=inv["id"], discount="2.00", discount_account_id=ids["4900"].id
    )
    with pytest.raises(HTTPException) as e:
        payments_routes.add_discount(pay["id"], body, None, None, held)
    assert e.value.detail == "This payment is void, so no discount goes with it."
    held.close()

    vendor = client.post(
        "/api/vendors",
        json={
            "name": "Gulf Supply",
            "terms": "2% 10 Net 30",
            "default_expense_account_id": ids["6000"].id,
        },
    ).json()
    bill = _bill(client, vendor["id"], "2% 10 Net 30", amount="100.00")
    bp = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-10-12",
            "amount": 98.0,
            "allocations": [{"bill_id": bill["id"], "amount": 98.0}],
        },
    ).json()
    held = Session()
    assert held.get(Bill, bill["id"]).balance_due == 2  # held, as read
    held.get(BillPayment, bp["id"])
    bbody = BillPaymentDiscountAdd(
        bill_id=bill["id"], discount="2.00", discount_account_id=ids["4900"].id
    )
    with Session() as other:
        bill_payment_routes.add_discount(bp["id"], bbody, None, None, other)
    over = BillPaymentDiscountAdd(
        bill_id=bill["id"],
        discount="2.00",
        discount_account_id=ids["4900"].id,
        allow_discount_override=True,
    )
    with pytest.raises(HTTPException) as e:
        bill_payment_routes.add_discount(bp["id"], over, None, None, held)
    assert "still due on bill" in e.value.detail
    held.close()
    with Session() as db:
        assert db.get(Bill, bill["id"]).balance_due == 0


def test_the_form_hint_for_an_old_bill_says_what_it_offers(client, seed_accounts):
    """Pass 4, W2: the bill form promised a list term's discount to an
    untied bill that doesn't offer it."""
    vendor = _vendor(client)
    bill = _bill(client, vendor["id"], "2% 10 Net 30", on="2026-09-15")
    _term(client, **TWO_TEN)
    params = {"date": "2026-09-15", "terms": "2% 10 Net 30", "document": "bill"}
    listed = client.get("/api/payment-terms/dates", params=params).json()
    assert listed["discount_line"]  # a new bill would get it
    own = client.get(
        "/api/payment-terms/dates", params={**params, "bill_id": bill["id"]}
    ).json()
    assert own["discount_line"] is None and own["due_date"] == "2026-10-15"
    got = client.get(f"/api/bills/{bill['id']}").json()
    for k in ("due_date", "discount_percent", "discount_date", "discount_line"):
        assert own[k] == got[k], k
    assert own["note"].startswith("This bill's terms were first saved before")


def test_new_writes_refuse_unknown_query_parameters(client, db_session, discounted):
    """Pass 4, W7: ?bogus=1 was ignored on the new writes."""
    pay = _short_paid(client, discounted)
    r = client.post(
        f"/api/payments/{pay['id']}/discount?bogus=1",
        json={
            "invoice_id": discounted["invoice"]["id"],
            "discount": "2.00",
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "'bogus' is not a parameter this accepts."
    r = client.post("/api/payment-terms?x=1", json={"name": "Twelve", "net_days": 12})
    assert r.status_code == 422


def test_the_in_use_counts_are_said_one_way(client, seed_accounts, seed_customer):
    """Pass 4, W8: the rename and rule refusals gave two totals."""
    term = _term(client, name="Net 22", net_days=22)
    _invoice(client, seed_customer.id, "Net 22")
    client.post("/api/customers", json={"name": "Bay", "terms": "Net 22"})
    rename = client.put(f"/api/payment-terms/{term['id']}", json={"name": "Twenty-two"})
    rule = client.put(f"/api/payment-terms/{term['id']}", json={"net_days": 23})
    assert rename.json()["detail"].startswith(
        "Net 22 is on 1 invoices and bills saved under it, and 1 customers, "
        "vendors, schedules or settings carry its name, so its name stays."
    )
    assert rule.json()["detail"].startswith(
        "Net 22 is on 1 invoices and bills saved under it, so its rule stays"
    )


def test_the_dates_length_refusal_is_a_sentence(client):
    """Pass 4, W10: pydantic's "String should have at most 50 characters"."""
    r = client.get(
        "/api/payment-terms/dates", params={"date": "2026-10-01", "terms": "x" * 5000}
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "Terms are at most 50 characters; these are 5000."
