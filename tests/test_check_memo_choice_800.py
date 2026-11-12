"""The check memo is a choice (#800): Settings, Check Printing, Check memo.

A customer asked by mail (11-11): paying a bill put the bill's whole note on
the check's memo line, where the payee expects an invoice number. The choice
is the bill's reference ("Invoice 196132", the default), the bill's memo
(as #602 built it) or nothing. A memo typed on the payment wins and stays
editable before printing; a check paying several bills lists the
references; a bill SlowBooks numbered itself (its number left blank) has no
reference and falls back to its memo, then nothing. The check PDF and the
test check follow it.

The check routes are called with the PDF renderer stubbed: what matters is
the data the check is built from.
"""

from datetime import date

import pytest

from app.routes import checks as checks_route
from app.services import check_memo


@pytest.fixture
def printed(monkeypatch):
    seen = []

    def fake(check_data, company):
        seen.append(dict(check_data))
        return b"%PDF-stub"

    monkeypatch.setattr(checks_route, "generate_check_pdf", fake)
    return seen


@pytest.fixture
def vendor(client, seed_accounts):
    r = client.post("/api/vendors", json={"name": "Gin Kee"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _choose(client, which):
    r = client.put("/api/settings", json={"check_memo": which})
    assert r.status_code == 200, r.text


def _bill(client, seed_accounts, vendor_id, number, **extra):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": "2026-09-02",
            "lines": [
                {
                    "description": "Supplies",
                    "amount": "100.00",
                    "account_id": seed_accounts["6000"].id,
                }
            ],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, vendor_id, bills, **extra):
    allocations = [{"bill_id": b["id"], "amount": float(b["total"])} for b in bills]
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": "2026-09-10",
            "amount": sum(a["amount"] for a in allocations),
            "method": "check",
            "allocations": allocations,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _check_memo(client, printed, pid):
    r = client.get(f"/api/checks/print?bill_payment_id={pid}")
    assert r.status_code == 200, r.text
    return printed[-1]["memo"]


LONG_NOTE = "Called Tuesday about the short shipment; credit promised next month"


# ---- each choice ----------------------------------------------------------


def test_the_default_is_the_bills_reference(client, seed_accounts, vendor, printed):
    assert client.get("/api/settings").json()["check_memo"] == "reference"
    bill = _bill(client, seed_accounts, vendor["id"], "196132", notes=LONG_NOTE)
    pay = _pay(client, vendor["id"], [bill])
    assert pay["notes"] == "Invoice 196132"
    assert _check_memo(client, printed, pay["id"]) == "Invoice 196132"


def test_the_bills_memo_choice_is_the_old_behaviour(
    client, seed_accounts, vendor, printed
):
    _choose(client, "memo")
    bill = _bill(client, seed_accounts, vendor["id"], "196132", notes=LONG_NOTE)
    pay = _pay(client, vendor["id"], [bill])
    assert pay["notes"] == LONG_NOTE
    assert _check_memo(client, printed, pay["id"]) == LONG_NOTE


def test_nothing_leaves_the_memo_blank(client, seed_accounts, vendor, printed):
    _choose(client, "none")
    bill = _bill(client, seed_accounts, vendor["id"], "196132", notes=LONG_NOTE)
    pay = _pay(client, vendor["id"], [bill])
    assert pay["notes"] is None
    assert _check_memo(client, printed, pay["id"]) == ""


# ---- a typed memo wins ----------------------------------------------------


@pytest.mark.parametrize("which", ["reference", "memo", "none"])
def test_a_memo_typed_on_the_payment_wins(
    client, seed_accounts, vendor, printed, which
):
    _choose(client, which)
    bill = _bill(client, seed_accounts, vendor["id"], "196132", notes=LONG_NOTE)
    pay = _pay(client, vendor["id"], [bill], notes="Acct 4471-22")
    assert pay["notes"] == "Acct 4471-22"
    assert _check_memo(client, printed, pay["id"]) == "Acct 4471-22"


def test_the_filled_memo_stays_editable_before_printing(
    client, seed_accounts, vendor, printed
):
    bill = _bill(client, seed_accounts, vendor["id"], "196132", notes=LONG_NOTE)
    pay = _pay(client, vendor["id"], [bill])
    r = client.put(f"/api/bill-payments/{pay['id']}/memo", json={"notes": "Acct 9"})
    assert r.status_code == 200, r.text
    assert _check_memo(client, printed, pay["id"]) == "Acct 9"
    # cleared, the reference does not come back
    client.put(f"/api/bill-payments/{pay['id']}/memo", json={"notes": ""})
    assert _check_memo(client, printed, pay["id"]) == ""


# ---- several bills on one check -------------------------------------------


def test_several_bills_on_one_check_list_their_references(
    client, seed_accounts, vendor, printed
):
    a = _bill(client, seed_accounts, vendor["id"], "196132", notes=LONG_NOTE)
    b = _bill(client, seed_accounts, vendor["id"], "196140", notes="Inv 9")
    pay = _pay(client, vendor["id"], [a, b])
    assert pay["notes"] == "Invoices 196132, 196140"
    assert _check_memo(client, printed, pay["id"]) == "Invoices 196132, 196140"
    # Print Checks lists the same memo
    r = client.get(f"/api/checks/unprinted?bank_account_id={seed_accounts['1000'].id}")
    assert r.status_code == 200, r.text
    memos = {c["bill_payment_id"]: c["memo"] for c in r.json()["checks"]}
    assert memos[pay["id"]] == "Invoices 196132, 196140"


def test_several_bills_under_the_memo_choice_leave_it_blank(
    client, seed_accounts, vendor, printed
):
    _choose(client, "memo")
    a = _bill(client, seed_accounts, vendor["id"], "196132", notes=LONG_NOTE)
    b = _bill(client, seed_accounts, vendor["id"], "196140", notes="Inv 9")
    pay = _pay(client, vendor["id"], [a, b])
    assert pay["notes"] is None


def test_a_bill_with_no_reference_is_left_off_the_list(
    client, seed_accounts, vendor, printed
):
    a = _bill(client, seed_accounts, vendor["id"], "196132")
    unnumbered = _bill(client, seed_accounts, vendor["id"], "", notes="Delivery")
    assert unnumbered["bill_number"] == "20260902-GK"
    pay = _pay(client, vendor["id"], [a, unnumbered])
    assert pay["notes"] == "Invoice 196132"


# ---- a bill with no reference ---------------------------------------------


def test_a_bill_with_no_reference_falls_back_to_its_memo(
    client, seed_accounts, vendor, printed
):
    bill = _bill(client, seed_accounts, vendor["id"], "", notes="Acct 4471-22")
    assert bill["bill_number"] == "20260902-GK"
    pay = _pay(client, vendor["id"], [bill])
    assert pay["notes"] == "Acct 4471-22"
    assert _check_memo(client, printed, pay["id"]) == "Acct 4471-22"


def test_a_bill_with_no_reference_and_no_memo_prints_nothing(
    client, seed_accounts, vendor, printed
):
    first = _bill(client, seed_accounts, vendor["id"], "")
    second = _bill(client, seed_accounts, vendor["id"], "")
    assert second["bill_number"] == "20260902-GK-2"
    pay = _pay(client, vendor["id"], [first, second])
    assert pay["notes"] is None
    alone = _bill(client, seed_accounts, vendor["id"], "")
    assert _pay(client, vendor["id"], [alone])["notes"] is None


def test_what_reads_as_a_reference():
    class B:
        def __init__(self, number, on=date(2026, 9, 2), notes=None):
            self.bill_number, self.date, self.notes = number, on, notes

    assert check_memo.bill_reference(B("196132"), "Gin Kee") == "196132"
    assert check_memo.bill_reference(B("  "), "Gin Kee") is None
    assert check_memo.bill_reference(B("20260902-GK"), "Gin Kee") is None
    assert check_memo.bill_reference(B("20260902-GK-3"), "Gin Kee") is None
    # the same shape for another vendor or another day is the vendor's own
    assert check_memo.bill_reference(B("20260902-GK"), "Harbor Supply") == (
        "20260902-GK"
    )
    assert check_memo.bill_reference(B("20260901-GK"), "Gin Kee") == "20260901-GK"
    # one invoice number twice is listed once
    assert (
        check_memo.memo_for_bills("reference", [B("7"), B("7"), B("8")], "Gin Kee")
        == "Invoices 7, 8"
    )


# ---- the setting ----------------------------------------------------------


@pytest.mark.parametrize("bad", ["invoice", "", "REFERENCE", "true"])
def test_any_other_choice_is_refused_in_a_sentence(client, bad):
    r = client.put("/api/settings", json={"check_memo": bad})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("The check memo is the bill's reference")
    assert detail.endswith(".")
    assert client.get("/api/settings").json()["check_memo"] == "reference"


# ---- the test check -------------------------------------------------------


def test_the_test_check_follows_the_saved_choice(client, printed):
    for which, memo in (
        ("reference", "Invoice 196132"),
        ("memo", "Alignment test"),
        ("none", ""),
    ):
        _choose(client, which)
        r = client.get("/api/checks/test-print")
        assert r.status_code == 200, r.text
        assert printed[-1]["memo"] == memo


def test_the_test_check_takes_the_forms_unsaved_choice(client, printed):
    _choose(client, "memo")
    r = client.get("/api/checks/test-print?check_memo=none")
    assert r.status_code == 200, r.text
    assert printed[-1]["memo"] == ""
    r = client.get("/api/checks/test-print?check_memo=reference")
    assert printed[-1]["memo"] == "Invoice 196132"
    r = client.get("/api/checks/test-print?check_memo=banana")
    assert r.status_code == 422
    assert r.json()["detail"].startswith("The check memo is the bill's reference")
