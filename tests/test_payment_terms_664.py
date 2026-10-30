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


def test_the_list_starts_with_the_terms_the_forms_offered(client):
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
        '"15th of following month" isn\'t on the terms list, so the due date '
        "is 30 days from the invoice date. Add it under Settings > Payment "
        "Terms to give it its own rule."
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
    _term(
        client,
        name="15th of following month",
        kind="date_driven",
        due_day=15,
        cutoff_days=31,
    )
    feb = _invoice(client, seed_customer.id, "Due end of month", on="2026-02-03")
    assert feb["due_date"] == "2026-02-28"  # a short month's last day
    for on in ("2026-10-01", "2026-10-14", "2026-10-31"):
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
        cutoff_days=31,
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
    assert r.status_code == 400
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
    assert "is on 1 invoices and bills, so its rule stays" in r.json()["detail"]
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
    term = _term(client, name="Net 20", net_days=20)
    r = client.put(
        f"/api/payment-terms/{term['id']}",
        json={
            "name": "Net 21",
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
    _term(client, name="Net 10th prox", kind="date_driven", due_day=10, cutoff_days=31)
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
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, net_days FROM payment_terms ORDER BY net_days")
        ).all()
        vendors = conn.execute(text("SELECT terms FROM vendors ORDER BY name")).all()
        cols = {c["name"] for c in inspect(conn).get_columns("credit_memos")}
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
    engine.dispose()

    command.downgrade(cfg, "d7b3f9a1c5e2")
    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    assert "payment_terms" not in insp.get_table_names()
    assert "discount_bill_payment_id" not in {
        c["name"] for c in insp.get_columns("vendor_credits")
    }
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
    assert "PaymentTerms.dates(dateEl.value, termsEl.value, 'invoice')" in inv
    assert "PaymentTerms.viewHtml(inv)" in inv
    assert "PaymentTerms.dates(form.date.value, form.terms.value, 'bill')" in _js(
        "bills.js"
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
