"""#1237: payments deposited to a liability account.

A professional-services firm holds retainers in a liability ("Retainer
Payable") and applies them to invoices as payments deposited to it; a
construction company's pay application does the same with its deposit
applied to 2210 Customer Deposits. The server has always taken such a
payment and posts it DR the liability / CR A/R.

Edit (Replace) opened Record Payment with the payment's own Deposit To, but
the form listed bank accounts only and a fixed set of methods, so Save sent
a blank Deposit To and no method: the replacement sat in Undeposited Funds
and the liability was overstated. The form now keeps both
(tests/js/retainer_deposit_to_1237.test.cjs renders it); these replay what
it sends and check the books.
"""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.payments import Payment
from app.models.transactions import TransactionLine
from app.services.undeposited_funds import waiting_items


def _ok(r, codes=(200, 201)):
    assert r.status_code in codes, r.text
    return r.json()


def _liability(client, number, name):
    return _ok(
        client.post(
            "/api/accounts",
            json={"account_number": number, "name": name, "account_type": "liability"},
        )
    )["id"]


def _invoice(client, customer_id, rate="300.00"):
    return _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": customer_id,
                "date": "2026-10-15",
                "tax_rate": 0,
                "lines": [{"description": "Design work", "quantity": 1, "rate": rate}],
            },
        )
    )


def _hold(client, seed_accounts, account_id, amount="500.00"):
    """Money taken up front: DR Checking / CR the liability."""
    _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-10-01",
                "description": "Retainer received",
                "lines": [
                    {
                        "account_id": seed_accounts["1000"].id,
                        "debit": amount,
                        "credit": "0",
                    },
                    {"account_id": account_id, "debit": "0", "credit": amount},
                ],
            },
        )
    )


def _lines(db_session, payment):
    return {
        (ln.account_id, Decimal(str(ln.debit)), Decimal(str(ln.credit)))
        for ln in db_session.query(TransactionLine).filter_by(
            transaction_id=payment.transaction_id
        )
    }


# --- Commit 1: Edit keeps the payment's own Deposit To and Method ---------


@pytest.mark.parametrize(
    "number,name,method",
    [
        ("2290", "Retainer Payable", "Retainer"),
        ("2210", "Customer Deposits", "deposit"),
    ],
)
def test_an_edit_keeps_a_liability_deposit_to_and_its_method(
    client, db_session, seed_accounts, seed_customer, number, name, method
):
    """The server never changed (it keeps what it is sent), so this passes
    with or without commit 1: it pins the books for what the corrected form
    sends. The form itself is pinned by the node test, which is the real
    guard against a regression."""
    acct = _liability(client, number, name)
    _hold(client, seed_accounts, acct)
    inv = _invoice(client, seed_customer.id)
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": "300.00",
        "method": method,
        "deposit_to_account_id": acct,
        "allocations": [{"invoice_id": inv["id"], "amount": "300.00"}],
    }
    first = _ok(client.post("/api/payments", json=body))
    # what the corrected form sends on Save: every field as Edit filled it
    # in, the account and method kept, the memo fixed
    body["notes"] = "typo fixed"
    new = _ok(client.post(f"/api/payments/{first['id']}/replace", json=body))
    assert new["id"] != first["id"]
    assert new["deposit_to_account_id"] == acct
    assert new["method"] == method
    db_session.expire_all()
    pay = db_session.get(Payment, new["id"])
    assert _lines(db_session, pay) == {
        (acct, Decimal("300.00"), Decimal("0.00")),
        (seed_accounts["1100"].id, Decimal("0.00"), Decimal("300.00")),
    }
    # nothing waits on Make Deposits, and the liability still holds 200
    assert not [t for _ln, t in waiting_items(db_session) if t.id == pay.transaction_id]
    assert _ok(client.get("/api/deposits/pending")) == []
    assert db_session.get(Account, acct).balance == Decimal("200.00")


def test_what_the_old_form_sent_moved_it_to_undeposited_funds(
    client, db_session, seed_accounts, seed_customer
):
    """The server keeps whatever it is sent: the blank the old form sent
    is Undeposited Funds. This is why the form must send the account."""
    acct = _liability(client, "2290", "Retainer Payable")
    _hold(client, seed_accounts, acct)
    inv = _invoice(client, seed_customer.id)
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": "300.00",
        "method": "Retainer",
        "deposit_to_account_id": acct,
        "allocations": [{"invoice_id": inv["id"], "amount": "300.00"}],
    }
    first = _ok(client.post("/api/payments", json=body))
    body.update(deposit_to_account_id=None, method=None)
    new = _ok(client.post(f"/api/payments/{first['id']}/replace", json=body))
    assert new["deposit_to_account_id"] is None
    db_session.expire_all()
    assert db_session.get(Account, acct).balance == Decimal("500.00")
    assert len(_ok(client.get("/api/deposits/pending"))) == 1


# --- Commit 2: Settings → Deposits → Customer deposits and retainers ------


def _list(client, ids):
    return client.put("/api/settings", json={"customer_deposit_account_ids": ids})


def test_the_setting_is_empty_by_default(client, seed_accounts):
    assert _ok(client.get("/api/settings"))["customer_deposit_account_ids"] == "[]"


def test_a_plain_liability_is_accepted_and_stored_as_a_list(
    client, db_session, seed_accounts
):
    a = _liability(client, "2290", "Retainer Payable")
    b = _liability(client, "2280", "Client Deposits Held")
    _ok(_list(client, [b, a, a]))
    assert _ok(client.get("/api/settings"))["customer_deposit_account_ids"] == (
        f"[{min(a, b)}, {max(a, b)}]"
    )
    # its JSON text too, and the empty value is the empty list
    _ok(_list(client, f"[{a}]"))
    _ok(_list(client, ""))
    assert _ok(client.get("/api/settings"))["customer_deposit_account_ids"] == "[]"


@pytest.mark.parametrize(
    "number,why",
    [
        ("1000", "is not a liability account"),
        ("4000", "is not a liability account"),
        ("2100", "is a bank or credit card account"),
        ("2000", "is a control account"),
        ("2200", "is a control account"),
        ("2300", "is a payroll account"),
        ("2330", "is a payroll account"),
        ("2390", "is a payroll account"),
    ],
)
def test_anything_but_a_plain_liability_is_refused_with_a_sentence(
    client, seed_accounts, number, why
):
    r = _list(client, [seed_accounts[number].id])
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Customer deposits and retainers: ")
    assert seed_accounts[number].name in detail
    assert why in detail
    assert detail.endswith("Nothing was saved.")
    assert _ok(client.get("/api/settings"))["customer_deposit_account_ids"] == "[]"


def test_an_inactive_unknown_or_malformed_account_is_refused(
    client, db_session, seed_accounts
):
    old = _liability(client, "2295", "Old Retainers")
    db_session.get(Account, old).is_active = False
    db_session.commit()
    r = _list(client, [old])
    assert r.status_code == 422 and "Old Retainers is inactive" in r.text, r.text
    r = _list(client, [999999])
    assert r.status_code == 422 and "no account 999999" in r.text, r.text
    for bad in ("nonsense", ["x"], [True], {"a": 1}):
        r = _list(client, bad)
        assert r.status_code == 422, (bad, r.text)
        assert "Customer deposits and retainers must be a list" in r.text
    assert _ok(client.get("/api/settings"))["customer_deposit_account_ids"] == "[]"


def _retainer_setup(client, seed_accounts, seed_customer, listed=True):
    acct = _liability(client, "2290", "Retainer Payable")
    _hold(client, seed_accounts, acct)
    if listed:
        _ok(_list(client, [acct]))
    inv = _invoice(client, seed_customer.id, rate="1000.00")
    return acct, inv


def _pay(client, seed_customer, acct, inv, amount, applied, **extra):
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": amount,
        "method": "Retainer",
        "deposit_to_account_id": acct,
        "allocations": (
            [{"invoice_id": inv["id"], "amount": applied}] if applied else []
        ),
    }
    body.update(extra)
    return client.post("/api/payments", json=body)


def test_unlisted_the_api_takes_what_it_took_before(
    client, db_session, seed_accounts, seed_customer
):
    """An empty list tightens nothing: a liability Deposit To with money
    left over, or more than the account holds, is taken as before."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer, listed=False)
    assert _pay(client, seed_customer, acct, inv, "300.00", "200.00").status_code == 201
    assert _pay(client, seed_customer, acct, inv, "700.00", "700.00").status_code == 201


def test_listed_a_payment_must_be_applied_in_full(
    client, db_session, seed_accounts, seed_customer
):
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    for applied in ("200.00", None):
        r = _pay(client, seed_customer, acct, inv, "300.00", applied)
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert "Retainer Payable, a customer deposit or retainer account" in detail
        assert "is not applied" in detail and "Nothing was recorded." in detail
    # the override is only about the balance
    r = _pay(client, seed_customer, acct, inv, "300.00", "200.00", allow_overdraw=True)
    assert r.status_code == 422, r.text
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert db_session.get(Account, acct).balance == Decimal("500.00")


def test_listed_more_than_the_account_holds_needs_the_override(
    client, db_session, seed_accounts, seed_customer
):
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    r = _pay(client, seed_customer, acct, inv, "600.00", "600.00")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith(
        "Retainer Payable holds $500.00, less than the $600.00 applied from it."
    )
    # in words a form user can act on, never the API's field name
    assert "allow_overdraw" not in detail
    assert "confirm applying more than it holds" in detail
    assert db_session.query(Payment).count() == 0
    r = _pay(client, seed_customer, acct, inv, "600.00", "600.00", allow_overdraw=True)
    assert r.status_code == 201, r.text
    db_session.expire_all()
    assert db_session.get(Account, acct).balance == Decimal("-100.00")


def test_listed_a_payment_applied_in_full_posts_as_before(
    client, db_session, seed_accounts, seed_customer
):
    """No posting change: DR the liability / CR A/R, off Make Deposits."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    pay = _ok(_pay(client, seed_customer, acct, inv, "500.00", "500.00"))
    db_session.expire_all()
    p = db_session.get(Payment, pay["id"])
    assert _lines(db_session, p) == {
        (acct, Decimal("500.00"), Decimal("0.00")),
        (seed_accounts["1100"].id, Decimal("0.00"), Decimal("500.00")),
    }
    assert _ok(client.get("/api/deposits/pending")) == []
    assert db_session.get(Account, acct).balance == Decimal("0.00")
    # an edit of it counts what its void gives back: the whole 500 again
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": "500.00",
        "method": "Retainer",
        "deposit_to_account_id": acct,
        "notes": "memo fixed",
        "allocations": [{"invoice_id": inv["id"], "amount": "500.00"}],
    }
    new = _ok(client.post(f"/api/payments/{pay['id']}/replace", json=body))
    assert new["deposit_to_account_id"] == acct and new["method"] == "Retainer"
    db_session.expire_all()
    assert db_session.get(Account, acct).balance == Decimal("0.00")


def test_listed_other_accounts_are_checked_as_before(
    client, db_session, seed_accounts, seed_customer
):
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    other = _liability(client, "2291", "Other Held")
    # a bank, Undeposited Funds and an unlisted liability keep a remainder
    for deposit_to in (seed_accounts["1000"].id, None, other):
        r = _pay(client, seed_customer, deposit_to, inv, "50.00", "10.00")
        assert r.status_code == 201, (deposit_to, r.text)


# --- Review round: what the list must not do --------------------------------


def test_an_account_a_payroll_setting_names_is_refused(
    client, db_session, seed_accounts
):
    """Not only the seeded 23xx numbers: any account Settings → Payroll
    Accounts posts withholding to."""
    held = _liability(client, "2295", "Withholding Held")
    _ok(
        client.put(
            "/api/settings", json={"payroll_federal_withholding_account_id": held}
        )
    )
    r = _list(client, [held])
    assert r.status_code == 422, r.text
    assert "Withholding Held is a payroll account" in r.json()["detail"]
    # the refusal names no example account
    assert "such as" not in r.json()["detail"]
    assert _ok(client.get("/api/settings"))["customer_deposit_account_ids"] == "[]"


@pytest.mark.parametrize("change", ["bank", "credit_card", "payroll"])
def test_a_listed_account_that_no_longer_qualifies_takes_ordinary_deposits(
    client, db_session, seed_accounts, seed_customer, change
):
    """Ticked, then retyped to a bank, made a credit card or named by a
    payroll setting: the saved list is not trusted on its own, so Receive
    Payment, Sales Receipts and the bank feed deposit into it as before."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    if change == "bank":
        _ok(
            client.put(
                f"/api/accounts/{acct}",
                json={"account_type": "asset", "bank_kind": "bank"},
            )
        )
    elif change == "credit_card":
        _ok(client.put(f"/api/accounts/{acct}", json={"bank_kind": "credit_card"}))
    else:
        # Settings now refuses this overlap (tests/test_retainer_account_
        # guards.py); a company saved with it before then still has it
        from app.services.settings_service import set_setting

        set_setting(db_session, "payroll_suta_account_id", str(acct))
        db_session.commit()
    db_session.expire_all()
    # money left over, and more than the account holds: both as before
    r = _pay(client, seed_customer, acct, inv, "900.00", "300.00")
    assert r.status_code == 201, r.text
    r = _pay(client, seed_customer, acct, inv, "700.00", "700.00")
    assert r.status_code == 201, r.text


def test_an_edit_of_a_payment_saved_before_the_account_was_listed_saves(
    client, db_session, seed_accounts, seed_customer
):
    """A $400 payment to Retainer Payable with $300 applied, saved while
    nothing was listed; the account is ticked; an edit changing only the
    memo keeps its account and saves, as it did before the setting."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer, listed=False)
    first = _ok(_pay(client, seed_customer, acct, inv, "400.00", "300.00"))
    _ok(_list(client, [acct]))
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": "400.00",
        "method": "Retainer",
        "deposit_to_account_id": acct,
        "notes": "memo fixed",
        "allocations": [{"invoice_id": inv["id"], "amount": "300.00"}],
    }
    new = _ok(client.post(f"/api/payments/{first['id']}/replace", json=body))
    assert new["deposit_to_account_id"] == acct
    # a new payment to it is still held to the rule
    r = _pay(client, seed_customer, acct, inv, "400.00", "300.00")
    assert r.status_code == 422, r.text
    # and an edit moving another payment onto it is a new use of it
    bank = _ok(
        _pay(client, seed_customer, seed_accounts["1000"].id, inv, "50.00", "10.00")
    )
    body.update(
        amount="50.00", allocations=[{"invoice_id": inv["id"], "amount": "10.00"}]
    )
    r = client.post(f"/api/payments/{bank['id']}/replace", json=body)
    assert r.status_code == 422, r.text


def test_a_pay_application_applies_its_deposit_past_a_listed_2210_balance(
    client, db_session, seed_accounts, seed_customer
):
    """_apply_deposit sends the override: the job's own deposit is what
    the application checks, not 2210's company-wide balance. Settings no
    longer lets 2210 be ticked (tests/test_retainer_account_guards.py); a
    company that ticked it before then still has it on the list."""
    from app.services.settings_service import set_setting

    from tests.test_pay_applications import (
        _acct,
        _approve,
        _deposit,
        _edit,
        _gl,
        _new_app,
        _two_line_job,
    )

    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    assert _deposit(client, job["id"], 10000, bank.id).status_code == 201
    cd = _acct(db_session, "2210")
    set_setting(db_session, "customer_deposit_account_ids", f"[{cd.id}]")
    db_session.commit()
    # 2210 now holds less than the job's deposit: 9,000 moved out by hand
    _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-05-02",
                "description": "Deposit refunded",
                "lines": [
                    {"account_id": cd.id, "debit": "9000.00", "credit": "0"},
                    {"account_id": bank.id, "debit": "0", "credit": "9000.00"},
                ],
            },
        )
    )
    db_session.expire_all()
    assert db_session.get(Account, cd.id).balance == Decimal("1000.00")
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    assert _gl(db_session, "2210") == Decimal("9000.00")


# --- Final verification round ------------------------------------------------


def _replace(client, seed_customer, payment_id, acct, inv, amount, applied, **extra):
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": amount,
        "method": "Retainer",
        "deposit_to_account_id": acct,
        "allocations": (
            [{"invoice_id": inv["id"], "amount": applied}] if applied else []
        ),
    }
    body.update(extra)
    return client.post(f"/api/payments/{payment_id}/replace", json=body)


def test_an_edit_keeping_a_listed_account_cannot_leave_money_unapplied(
    client, db_session, seed_accounts, seed_customer
):
    """The verified hole: $300 applied in full, then an edit keeping the
    account raised the amount to $5,000 with $300 applied, and $4,700 sat as
    a customer credit taken out of the liability. Refused as a new payment
    would be, and the payment is left as it was."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    first = _ok(_pay(client, seed_customer, acct, inv, "300.00", "300.00"))
    r = _replace(client, seed_customer, first["id"], acct, inv, "5000.00", "300.00")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "Retainer Payable, a customer deposit or retainer account" in detail
    assert "$4,700.00 is not applied" in detail
    # the override is only about the balance
    r = _replace(
        client,
        seed_customer,
        first["id"],
        acct,
        inv,
        "5000.00",
        "300.00",
        allow_overdraw=True,
    )
    assert r.status_code == 422, r.text
    db_session.expire_all()
    assert db_session.get(Payment, first["id"]).is_voided is False
    assert db_session.query(Payment).count() == 1
    assert db_session.get(Account, acct).balance == Decimal("200.00")


def test_an_edit_keeping_a_listed_account_asks_before_taking_more_than_it_holds(
    client, db_session, seed_accounts, seed_customer
):
    """$500 held, $300 applied from it: the edit's void gives the 300 back,
    so up to $500 saves unasked; $600 is asked, and saves with the override."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    first = _ok(_pay(client, seed_customer, acct, inv, "300.00", "300.00"))
    r = _replace(client, seed_customer, first["id"], acct, inv, "600.00", "600.00")
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith(
        "Retainer Payable holds $500.00, less than the $600.00 applied from it."
    )
    db_session.expire_all()
    assert db_session.get(Payment, first["id"]).is_voided is False
    assert db_session.get(Account, acct).balance == Decimal("200.00")
    second = _ok(
        _replace(client, seed_customer, first["id"], acct, inv, "500.00", "500.00")
    )
    db_session.expire_all()
    assert db_session.get(Account, acct).balance == Decimal("0.00")
    third = _ok(
        _replace(
            client,
            seed_customer,
            second["id"],
            acct,
            inv,
            "600.00",
            "600.00",
            allow_overdraw=True,
        )
    )
    db_session.expire_all()
    assert db_session.get(Payment, third["id"]).deposit_to_account_id == acct
    assert db_session.get(Account, acct).balance == Decimal("-100.00")


def test_an_edit_of_an_overdrawn_payment_that_takes_no_more_saves_unasked(
    client, db_session, seed_accounts, seed_customer
):
    """Applied past the balance with the override once: a memo fixed on it
    takes no more than it did, so it is not asked again."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    first = _ok(
        _pay(client, seed_customer, acct, inv, "700.00", "700.00", allow_overdraw=True)
    )
    new = _ok(
        _replace(
            client,
            seed_customer,
            first["id"],
            acct,
            inv,
            "700.00",
            "700.00",
            notes="memo fixed",
        )
    )
    assert new["notes"] == "memo fixed"
    # one cent more is asked
    r = _replace(client, seed_customer, new["id"], acct, inv, "700.01", "700.01")
    assert r.status_code == 422, r.text
    assert "holds $500.00, less than the $700.01" in r.json()["detail"]


def test_an_old_payment_kept_on_its_account_may_not_leave_more_unapplied(
    client, db_session, seed_accounts, seed_customer
):
    """$400 with $300 applied, saved before the account was ticked: an edit
    may keep or lower the $100 left over, not raise it."""
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer, listed=False)
    first = _ok(_pay(client, seed_customer, acct, inv, "400.00", "300.00"))
    _ok(_list(client, [acct]))
    r = _replace(client, seed_customer, first["id"], acct, inv, "500.00", "300.00")
    assert r.status_code == 422, r.text
    assert "$200.00 is not applied" in r.json()["detail"]
    lower = _ok(
        _replace(client, seed_customer, first["id"], acct, inv, "350.00", "300.00")
    )
    assert Decimal(str(lower["unapplied"])) == Decimal("50.00")


def test_a_sales_receipt_to_a_listed_account_posts_as_before(
    client, db_session, seed_accounts, seed_customer
):
    """Sales receipts are not Receive Payment: one deposited to a ticked
    account for more than it holds posts as it did before the setting."""
    acct, _inv = _retainer_setup(client, seed_accounts, seed_customer)
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-20",
            "tax_rate": "0",
            "method": "Cash",
            "deposit_to_account_id": acct,
            "lines": [
                {
                    "description": "Design work",
                    "quantity": "1",
                    "rate": "800.00",
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    db_session.expire_all()
    pay = db_session.get(Payment, r.json()["payment"]["id"])
    assert pay.deposit_to_account_id == acct
    assert db_session.get(Account, acct).balance == Decimal("-300.00")


def test_a_negative_balance_reads_with_the_sign_before_the_dollar(
    client, db_session, seed_accounts, seed_customer
):
    acct, inv = _retainer_setup(client, seed_accounts, seed_customer)
    _ok(_pay(client, seed_customer, acct, inv, "600.00", "600.00", allow_overdraw=True))
    r = _pay(client, seed_customer, acct, inv, "50.00", "50.00")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith(
        "Retainer Payable holds -$100.00, less than the $50.00 applied from it."
    )
    assert "$-" not in detail


def test_an_inactive_account_is_refused_with_how_to_fix_it(
    client, db_session, seed_accounts
):
    old = _liability(client, "2295", "Old Retainers")
    db_session.get(Account, old).is_active = False
    db_session.commit()
    r = _list(client, [old])
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "Old Retainers is inactive" in detail
    assert "Make it active" in detail and "untick it" in detail
    assert "Choose a liability account" not in detail


def test_a_control_account_is_refused_in_plain_words(client, seed_accounts):
    r = _list(client, [seed_accounts["2000"].id])
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "is a control account, which the app posts to on its own" in detail
    assert "its own documents keep" not in detail
