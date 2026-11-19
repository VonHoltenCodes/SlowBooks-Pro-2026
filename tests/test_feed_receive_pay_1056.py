"""Receive a customer payment or pay bills from a To review line (#1056).

A deposit in the feed that paid open invoices had to be recorded in
Receive Payments first and then matched; a payment out that paid bills,
in Pay Bills. Now a money-in line posts one customer payment for its
whole amount, on its date, into the feed's account, applied to the
invoices chosen in full or in part, and is matched to it; a money-out
line does the same with the vendor's open bills. What is not applied
stays unapplied, as Receive Payments and Pay Bills leave it. Each path
leaves the ledger balanced and the agings equal to their control
accounts (Books Check), and the register shows the payment once."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.banking import BankTransaction
from app.models.bills import Bill, BillPayment
from app.models.invoices import Invoice
from app.models.payments import Payment
from app.services.books_check import books_check
from app.services.ofx_import import import_transactions

import secrets

# made at run time: no password, even a made-up one, is written in this repo
_PW0 = secrets.token_urlsafe(9) + "-Aa1!"
_PW1 = secrets.token_urlsafe(9) + "-Aa1!"

OVERRIDE_PW = _PW0


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


@pytest.fixture
def books(client, db_session, seed_accounts, seed_customer):
    feed = _ok(
        client.post(
            "/api/banking/accounts",
            json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
        )
    )
    invoices = [
        _ok(
            client.post(
                "/api/invoices",
                json={
                    "customer_id": seed_customer.id,
                    "date": day,
                    "lines": [{"description": "Cake", "quantity": 1, "rate": rate}],
                },
            )
        )
        for day, rate in (("2026-08-01", 300), ("2026-08-15", 200))
    ]
    vendor = _ok(
        client.post(
            "/api/vendors",
            json={
                "name": "Flour Mill",
                "default_expense_account_id": seed_accounts["6000"].id,
            },
        )
    )
    bills = [
        _ok(
            client.post(
                "/api/bills",
                json={
                    "vendor_id": vendor["id"],
                    "bill_number": f"FM-{i}",
                    "date": day,
                    "lines": [{"description": "Flour", "quantity": 1, "rate": rate}],
                },
            )
        )
        for i, (day, rate) in enumerate((("2026-08-02", 120), ("2026-08-20", 80)))
    ]
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": f"P{i}",
                "date": date(2026, 9, 10 + i),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (a, p) in enumerate(
                [
                    ("450.00", "MOBILE DEPOSIT"),
                    ("600.00", "DEPOSIT 2"),
                    ("-150.00", "CHECK 1201"),
                    ("-200.00", "ACH FLOUR MILL"),
                ]
            )
        ],
    )
    # the check number a bank's OFX carries (CHECKNUM); this import has none
    db_session.query(BankTransaction).filter_by(
        payee="CHECK 1201"
    ).one().check_number = "1201"
    db_session.commit()
    lines = {
        r["payee"]: r
        for r in _ok(
            client.get(
                f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
            )
        )
    }
    return {
        "feed": feed,
        "invoices": invoices,
        "vendor": vendor,
        "bills": bills,
        "lines": lines,
    }


def _books_agree(db_session):
    db_session.expire_all()
    rows = books_check(db_session)["rows"]
    assert [(r["name"], r["sentence"]) for r in rows if r["ok"] is False] == []


def _register(client, seed_accounts):
    reg = _ok(
        client.get(f"/api/banking/check-register?account_id={seed_accounts['1000'].id}")
    )
    return reg["entries"]


def _receive(client, line, customer_id, allocations, **extra):
    return client.post(
        f"/api/banking/transactions/{line['id']}/receive-payment",
        json={"customer_id": customer_id, "allocations": allocations, **extra},
    )


def _pay(client, line, vendor_id, allocations, **extra):
    return client.post(
        f"/api/banking/transactions/{line['id']}/pay-bills",
        json={"vendor_id": vendor_id, "allocations": allocations, **extra},
    )


def test_a_deposit_pays_one_invoice_in_full_and_one_in_part(
    client, db_session, seed_accounts, seed_customer, books
):
    first, second = books["invoices"]
    line = books["lines"]["MOBILE DEPOSIT"]
    out = _ok(
        _receive(
            client,
            line,
            seed_customer.id,
            [
                {"invoice_id": first["id"], "amount": "300.00"},
                {"invoice_id": second["id"], "amount": "150.00"},
            ],
        )
    )
    assert out["match_status"] == "added"
    payment = db_session.query(Payment).one()
    assert (payment.amount, payment.date, payment.deposit_to_account_id) == (
        Decimal("450.00"),
        date(2026, 9, 10),
        seed_accounts["1000"].id,
    )
    assert out["transaction_id"] == payment.transaction_id
    db_session.expire_all()
    assert db_session.get(Invoice, first["id"]).balance_due == Decimal("0")
    assert db_session.get(Invoice, second["id"]).balance_due == Decimal("50.00")
    # one entry in the register for the deposit, not two
    deposits = [e for e in _register(client, seed_accounts) if e["amount"] == 450.0]
    assert len(deposits) == 1
    # the line has left To review
    waiting = _ok(
        client.get(
            f"/api/banking/transactions?bank_account_id={books['feed']['id']}&status=unmatched"
        )
    )
    assert line["id"] not in {r["id"] for r in waiting}
    _books_agree(db_session)


def test_what_is_left_over_is_a_credit_on_the_customer(
    client, db_session, seed_customer, books
):
    first, second = books["invoices"]
    _ok(
        _receive(
            client,
            books["lines"]["DEPOSIT 2"],
            seed_customer.id,
            [{"invoice_id": first["id"], "amount": "300.00"}],
        )
    )
    payment = db_session.query(Payment).one()
    assert payment.amount == Decimal("600.00")
    credits = _ok(client.get(f"/api/customers/{seed_customer.id}/credits"))
    assert "300" in str(credits)
    _books_agree(db_session)


def test_a_payment_out_pays_the_vendors_bills(client, db_session, seed_accounts, books):
    first, second = books["bills"]
    line = books["lines"]["CHECK 1201"]
    out = _ok(
        _pay(
            client,
            line,
            books["vendor"]["id"],
            [
                {"bill_id": first["id"], "amount": "120.00"},
                {"bill_id": second["id"], "amount": "30.00"},
            ],
        )
    )
    assert out["match_status"] == "added"
    bp = db_session.query(BillPayment).one()
    assert (bp.amount, bp.check_number, bp.pay_from_account_id) == (
        Decimal("150.00"),
        "1201",
        seed_accounts["1000"].id,
    )
    db_session.expire_all()
    assert db_session.get(Bill, first["id"]).balance_due == Decimal("0")
    assert db_session.get(Bill, second["id"]).balance_due == Decimal("50.00")
    paid = [e for e in _register(client, seed_accounts) if e["amount"] == -150.0]
    assert len(paid) == 1
    _books_agree(db_session)


def test_a_payment_out_with_some_left_over_is_a_prepayment(client, db_session, books):
    first, _ = books["bills"]
    _ok(
        _pay(
            client,
            books["lines"]["ACH FLOUR MILL"],
            books["vendor"]["id"],
            [{"bill_id": first["id"], "amount": "120.00"}],
        )
    )
    assert db_session.query(BillPayment).one().amount == Decimal("200.00")
    _books_agree(db_session)


def test_voiding_the_payment_gives_the_line_back_to_to_review(
    client, db_session, seed_customer, books
):
    first, _ = books["invoices"]
    line = books["lines"]["MOBILE DEPOSIT"]
    _ok(
        _receive(
            client,
            line,
            seed_customer.id,
            [{"invoice_id": first["id"], "amount": "300.00"}],
        )
    )
    payment = db_session.query(Payment).one()
    _ok(client.post(f"/api/payments/{payment.id}/void"))
    db_session.expire_all()
    bt = db_session.get(BankTransaction, line["id"])
    assert bt.match_status == "unmatched" and bt.transaction_line_id is None
    _books_agree(db_session)


def test_the_refusals_say_why_and_record_nothing(
    client, db_session, seed_customer, books
):
    first, second = books["invoices"]
    deposit, check = books["lines"]["MOBILE DEPOSIT"], books["lines"]["CHECK 1201"]
    too_much = _receive(
        client,
        deposit,
        seed_customer.id,
        [
            {"invoice_id": first["id"], "amount": "300.00"},
            {"invoice_id": second["id"], "amount": "200.00"},
        ],
    )
    assert too_much.status_code == 400
    assert too_much.json()["detail"] == (
        "The amounts applied add up to $500.00, more than this line's $450.00. "
        "Lower one of them."
    )
    over_invoice = _receive(
        client,
        deposit,
        seed_customer.id,
        [{"invoice_id": second["id"], "amount": "250.00"}],
    )
    assert over_invoice.status_code == 400
    assert "still due on invoice" in over_invoice.json()["detail"]
    wrong_side = _receive(
        client, check, seed_customer.id, [{"invoice_id": first["id"], "amount": "10"}]
    )
    assert (
        wrong_side.status_code == 400 and "Use Pay bills" in wrong_side.json()["detail"]
    )
    nothing = _pay(client, check, books["vendor"]["id"], [])
    assert nothing.status_code == 400
    assert "at least one of the vendor's open bills" in nothing.json()["detail"]
    zero = _pay(
        client,
        check,
        books["vendor"]["id"],
        [{"bill_id": books["bills"][0]["id"], "amount": "0"}],
    )
    assert zero.status_code in (400, 422)
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert db_session.query(BillPayment).count() == 0
    assert db_session.get(Invoice, second["id"]).balance_due == Decimal("200.00")
    assert db_session.get(BankTransaction, deposit["id"]).match_status == "unmatched"
    # a line already in the books
    _ok(
        _receive(
            client,
            deposit,
            seed_customer.id,
            [{"invoice_id": first["id"], "amount": "300"}],
        )
    )
    again = _receive(
        client, deposit, seed_customer.id, [{"invoice_id": second["id"], "amount": "1"}]
    )
    assert again.status_code == 400
    assert again.json()["detail"] == "This statement line is already in the books."
    _books_agree(db_session)


def test_a_closed_period_is_refused(client, db_session, seed_customer, books):
    _ok(
        client.put(
            "/api/settings",
            json={"closing_date": "2026-12-31", "closing_date_password": OVERRIDE_PW},
        )
    )
    r = _receive(
        client,
        books["lines"]["MOBILE DEPOSIT"],
        seed_customer.id,
        [{"invoice_id": books["invoices"][0]["id"], "amount": "300"}],
    )
    assert r.status_code in (400, 403, 409), r.text
    assert "clos" in str(r.json()["detail"]).lower()
    assert db_session.query(Payment).count() == 0


@pytest.mark.parametrize("role", ["readonly", "accountant"])
def test_a_read_only_or_accountant_sign_in_cannot_post_them(
    client, db_session, seed_customer, books, role
):
    from app.models.users import User
    from app.services import auth as auth_service

    db_session.add(
        User(
            username=f"{role}-1056",
            display_name=role.title(),
            password_hash=auth_service.hash_password(_PW1),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    _ok(
        client.post(
            "/api/auth/login",
            json={"username": f"{role}-1056", "password": _PW1},
        )
    )
    r = _receive(
        client,
        books["lines"]["MOBILE DEPOSIT"],
        seed_customer.id,
        [{"invoice_id": books["invoices"][0]["id"], "amount": "300"}],
    )
    assert r.status_code == 403
    r = _pay(
        client,
        books["lines"]["CHECK 1201"],
        books["vendor"]["id"],
        [{"bill_id": books["bills"][0]["id"], "amount": "120"}],
    )
    assert r.status_code == 403
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert db_session.query(BillPayment).count() == 0
