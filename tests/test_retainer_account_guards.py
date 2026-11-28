"""Customer deposits and retainers (#1237), pre-release review.

Three ways the setting could be put in a state that misstates the books or
locks Settings:

1. 2210 Customer Deposits, where a job's deposits post and which the pay
   application applies, could be ticked; the same job deposit could then be
   applied twice, once by Record Payment and again by the pay application.
2. The construction liabilities the app posts to on its own (Retainage
   Payable, the WIP entry's two, Use Tax Payable) could be ticked.
3. One save could put an account on a payroll posting setting and on the
   list, after which every later Settings save was refused.

The form's side (no box, "(unavailable)", the help) is
tests/js/retainer_deposit_to_1237.test.cjs.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account
from app.services.accounting import (
    CONSTRUCTION_ACCOUNTS,
    JOB_DEPOSITS_NUMBER,
    ensure_construction_accounts,
)
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

ROOT = Path(__file__).resolve().parent.parent
LABEL = "Customer deposits and retainers: "


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


def _list(client, ids, **more):
    return client.put(
        "/api/settings", json={"customer_deposit_account_ids": ids, **more}
    )


def _saved(client):
    return _ok(client.get("/api/settings"))


def _invoice(client, customer_id, rate):
    return _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": customer_id,
                "date": "2026-06-15",
                "tax_rate": 0,
                "lines": [{"description": "Extra work", "quantity": 1, "rate": rate}],
            },
        )
    )


def _pay(client, customer_id, acct_id, inv, amount, applied):
    return client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-06-20",
            "amount": amount,
            "method": "deposit",
            "deposit_to_account_id": acct_id,
            "allocations": [{"invoice_id": inv["id"], "amount": applied}],
        },
    )


# --- 1. Customer Deposits (2210) ---------------------------------------------


def test_customer_deposits_is_refused_saying_the_pay_application_applies_it(
    client, db_session, seed_accounts
):
    cd = ensure_construction_accounts(db_session)[JOB_DEPOSITS_NUMBER]
    db_session.commit()
    r = _list(client, [cd.id])
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail == (
        LABEL + "Customer Deposits holds job deposits, which are applied on the "
        "pay application, not in Record Payment. Choose a liability account "
        "that holds money customers paid ahead. Nothing was saved."
    )
    assert _saved(client)["customer_deposit_account_ids"] == "[]"


def test_customer_deposits_is_found_by_name_as_the_job_deposit_finds_it(
    client, db_session, seed_accounts, seed_customer
):
    """The posting code finds it by name: renumbered, it is still the
    account a job deposit posts to, and an unrelated account on 2210 of a
    chart's own is not."""
    mine = _liability(client, "2210", "Loan From Owner")
    _ok(_list(client, [mine]))
    held = _liability(client, "2299", "Customer Deposits")
    job, _sov = _two_line_job(client, seed_customer.id)
    assert _deposit(client, job["id"], 500, _acct(db_session, "1000").id).status_code
    db_session.expire_all()
    assert db_session.get(Account, held).balance == Decimal("500.00")
    r = _list(client, [mine, held])
    assert r.status_code == 422, r.text
    assert "Customer Deposits holds job deposits" in r.json()["detail"]


def test_a_job_deposit_can_no_longer_be_applied_twice(
    client, db_session, seed_accounts, seed_customer
):
    """The reviewer's probe, end to end: 10,000 taken as a job deposit
    cleared 20,000 of receivables (Record Payment from 2210 against an
    ordinary invoice, then the pay application), leaving 2210 at a 10,000
    debit. 2210 can no longer be ticked, so Record Payment never offers it,
    and the pay application applies the deposit once."""
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    assert _deposit(client, job["id"], 10000, bank.id).status_code == 201
    cd = _acct(db_session, "2210")
    assert _gl(db_session, "2210") == Decimal("-10000.00")

    # step 2 of the probe is refused, and nothing is saved
    assert _list(client, [cd.id]).status_code == 422
    assert str(cd.id) not in _saved(client)["customer_deposit_account_ids"]

    # the pay application applies the job's deposit, once
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    assert r.status_code == 200, r.text
    assert _approve(client, app["id"]).status_code == 200
    assert _gl(db_session, "2210") == Decimal("0.00")
    figures = _ok(client.get(f"/api/jobs/{job['id']}/deposits"))
    assert figures["applied"] == 10000.0 and figures["unapplied"] == 0.0


def test_a_company_that_ticked_2210_before_is_treated_as_not_ticking_it(
    client, db_session, seed_accounts, seed_customer
):
    """Ticked while it was allowed (the stored list, written directly):
    Record Payment's retainer rules do not apply to it (the form does not
    offer it), and Settings still saves around it."""
    job, _sov = _two_line_job(client, seed_customer.id)
    assert _deposit(client, job["id"], 10000, _acct(db_session, "1000").id)
    cd = _acct(db_session, "2210")
    set_setting(db_session, "customer_deposit_account_ids", f"[{cd.id}]")
    db_session.commit()
    inv = _invoice(client, seed_customer.id, "1000.00")
    # a listed account would refuse money left unapplied: this is not listed
    r = _pay(client, seed_customer.id, cd.id, inv, "900.00", "300.00")
    assert r.status_code == 201, r.text
    assert "customer deposit or retainer account" not in r.text
    # the form sends the list back on every save: it saves
    _ok(_list(client, [cd.id], company_name="Example Builders"))
    assert _saved(client)["company_name"] == "Example Builders"
    # and unticking it takes it off
    _ok(_list(client, []))
    assert _saved(client)["customer_deposit_account_ids"] == "[]"


# --- 2. The construction accounts the app posts to on its own ---------------


@pytest.mark.parametrize("number", ["2050", "2060", "2070", "2220"])
def test_a_construction_account_the_app_posts_to_is_refused(
    client, db_session, seed_accounts, number
):
    acct = ensure_construction_accounts(db_session)[number]
    db_session.commit()
    r = _list(client, [acct.id])
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        LABEL + acct.name + " is a construction account, which the app posts to "
        "on its own. Choose a liability account that holds money customers "
        "paid ahead. Nothing was saved."
    )
    assert _saved(client)["customer_deposit_account_ids"] == "[]"


def test_retainage_payable_ticked_before_takes_no_retainer_rules(
    client, db_session, seed_accounts, seed_customer
):
    """The reviewer's 2050 case: with Retainage Payable on the list a
    payment could draw on the subs' retainage. One already on the stored
    list is treated as not ticked."""
    ret = ensure_construction_accounts(db_session)["2050"]
    set_setting(db_session, "customer_deposit_account_ids", f"[{ret.id}]")
    db_session.commit()
    from app.services.customer_deposit_accounts import not_usable_because

    assert not_usable_because(db_session, ret) is not None
    _ok(_list(client, [ret.id], company_name="Example Builders"))


def test_the_form_list_names_the_same_construction_accounts():
    """utils.js's CONSTRUCTION_POSTED_NAMES is every construction liability
    (accounting.CONSTRUCTION_ACCOUNTS): the form leaves out what the save
    refuses."""
    src = (ROOT / "app/static/js/utils.js").read_text(encoding="utf-8")
    block = re.search(r"CONSTRUCTION_POSTED_NAMES: \[(.*?)\]", src, re.S).group(1)
    in_js = set(re.findall(r"'([^']+)'", block))
    liabilities = {
        name
        for _n, name, atype, _d in CONSTRUCTION_ACCOUNTS
        if getattr(atype, "value", atype) == "liability"
    }
    assert in_js == liabilities


# --- 3. A payroll posting account and the list, in one save -----------------


def _payroll_sentence(name):
    return (
        "The Federal income tax withheld account cannot be "
        + name
        + ": it is ticked under Settings → Connections → Deposits → Customer "
        "deposits and retainers. Untick it there, or choose another account. "
        "Nothing was saved."
    )


def test_one_save_cannot_put_an_account_on_both(client, seed_accounts):
    acct = _liability(client, "2295", "Retainer Payable")
    r = _list(client, [acct], payroll_federal_withholding_account_id=str(acct))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == _payroll_sentence("Retainer Payable")
    saved = _saved(client)
    assert saved["customer_deposit_account_ids"] == "[]"
    assert saved["payroll_federal_withholding_account_id"] == ""


@pytest.mark.parametrize("resend_list", [True, False])
def test_a_ticked_account_cannot_be_chosen_for_payroll(
    client, seed_accounts, resend_list
):
    """The reviewer's probe: ticked first, then chosen for payroll with the
    list sent again (as the page does) or not at all (an API save)."""
    acct = _liability(client, "2295", "Retainer Payable")
    _ok(_list(client, [acct]))
    body = {"payroll_federal_withholding_account_id": str(acct)}
    if resend_list:
        body["customer_deposit_account_ids"] = [acct]
    r = client.put("/api/settings", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == _payroll_sentence("Retainer Payable")
    assert _saved(client)["payroll_federal_withholding_account_id"] == ""
    # the next ordinary save is not locked
    _ok(_list(client, [acct], company_name="Example Firm"))


def test_a_payroll_account_cannot_be_ticked_counting_the_same_save(
    client, seed_accounts
):
    """The other order: payroll chosen first, then ticked, is refused as
    before; ticked in the same save that moves payroll off it saves."""
    acct = _liability(client, "2295", "Withholding Held")
    _ok(
        client.put(
            "/api/settings",
            json={"payroll_federal_withholding_account_id": str(acct)},
        )
    )
    r = _list(client, [acct])
    assert r.status_code == 422, r.text
    assert "Withholding Held is a payroll account" in r.json()["detail"]
    _ok(_list(client, [acct], payroll_federal_withholding_account_id=""))
    assert _saved(client)["customer_deposit_account_ids"] == f"[{acct}]"


def test_an_overlap_saved_before_the_fix_does_not_lock_settings(
    client, db_session, seed_accounts
):
    acct = _liability(client, "2295", "Retainer Payable")
    set_setting(db_session, "customer_deposit_account_ids", f"[{acct}]")
    set_setting(db_session, "payroll_federal_withholding_account_id", str(acct))
    db_session.commit()
    # what the page sends: both settings as they are, and the change
    _ok(
        _list(
            client,
            [acct],
            payroll_federal_withholding_account_id=str(acct),
            company_name="Example Firm",
        )
    )
    assert _saved(client)["company_name"] == "Example Firm"
    # clearing either side is a save like any other
    _ok(_list(client, [], payroll_federal_withholding_account_id=str(acct)))
    _ok(_list(client, [], company_name="Example Firm Two"))


# --- An account that stopped qualifying does not lock Settings --------------


def test_an_account_deactivated_after_it_was_ticked_lets_settings_save(
    client, db_session, seed_accounts
):
    acct = _liability(client, "2295", "Old Retainers")
    _ok(_list(client, [acct]))
    db_session.get(Account, acct).is_active = False
    db_session.commit()
    _ok(_list(client, [acct], company_name="Example Firm"))
    assert _saved(client)["customer_deposit_account_ids"] == f"[{acct}]"
    # ticking it afresh is still refused, with how to fix it
    _ok(_list(client, []))
    r = _list(client, [acct])
    assert r.status_code == 422 and "Old Retainers is inactive" in r.text, r.text


def test_an_account_renamed_onto_a_construction_account_lets_settings_save(
    client, db_session, seed_accounts
):
    acct = _liability(client, "2295", "Held Deposits")
    _ok(_list(client, [acct]))
    db_session.get(Account, acct).name = "Use Tax Payable"
    db_session.commit()
    _ok(_list(client, [acct], company_name="Example Firm"))
    # but not alongside a new account that cannot be ticked
    cd = ensure_construction_accounts(db_session)["2210"]
    db_session.commit()
    r = _list(client, [acct, cd.id])
    assert r.status_code == 422 and "holds job deposits" in r.text, r.text
