"""Void and Replace on an expense with a payee and no vendor (2.31.5 gate
W-101, #689).

An expense posted over the API with a payee and no vendor_id replaces as
one: the replacement carries the same payee and names no vendor, and the
replace form fills the payee in rather than asking for a vendor.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app" / "static" / "js" / "expenses.js"


def _payee_only(seed_accounts, amount="42.00", memo="parking"):
    return {
        "date": "2026-04-10",
        "payee": "City Parking Garage",
        "paid_from_account_id": seed_accounts["1000"].id,
        "expense_account_id": seed_accounts["6400"].id,
        "amount": amount,
        "memo": memo,
    }


def test_a_payee_only_expense_replaces_as_a_payee_only_expense(client, seed_accounts):
    r = client.post("/api/expenses", json=_payee_only(seed_accounts))
    assert r.status_code == 201, r.text
    orig = r.json()["id"]
    assert r.json()["vendor_id"] is None

    r = client.post(
        f"/api/expenses/{orig}/replace",
        json=_payee_only(seed_accounts, amount="45.00", memo="parking, 2 hrs"),
    )
    assert r.status_code == 200, r.text
    new = client.get(f"/api/expenses/{r.json()['id']}").json()
    assert new["payee"] == "City Parking Garage"
    assert new["vendor_id"] is None
    assert new["replaces_id"] == orig
    assert client.get(f"/api/expenses/{orig}").json()["status"] == "void"


def _fn(src, name):
    m = re.search(rf"\n    (?:async )?{name}\([^)]*\) {{\n(.*?)\n    }},\n", src, re.S)
    assert m, name
    return m.group(1)


def test_the_replace_form_sets_the_payee_and_save_sends_it():
    src = JS.read_text()
    rep = _fn(src, "replace")
    # the form keeps the expense's payee, and a payee with no vendor is
    # chosen in the vendor list as its own entry
    assert "form.dataset.replacePayee = x.payee" in rep
    assert "vendor.value = ExpensesPage.PAYEE" in rep
    save = _fn(src, "save")
    # that entry posts no vendor (no "Select a vendor" refusal) and the payee
    assert "payeeOnly ? null : await VendorQuickAdd.ensure('expense-vendor')" in save
    assert re.search(r"vendor_id: vendorId,\s*payee,", save)
