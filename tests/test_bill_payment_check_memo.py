"""A bill's memo rides onto the check that pays it (#602, from discussion
#594). Bookkeepers put the account or invoice number the vendor wants on the
check's memo line; the bill carried it, the check printed blank. A payment of
one bill takes that bill's memo, a payment of several leaves it to be typed,
and a memo typed on the payment is never overwritten. Print Check saves the
memo as edited before the check prints.

The check route is called with the PDF renderer stubbed, so these run
everywhere: what matters here is the data the check is built from.

Since #800 the memo is a choice under Settings, Check Printing, Check memo,
whose default is the bill's reference; these pin the bill's memo, the
behaviour #602 built (tests/test_check_memo_choice_800.py has the rest).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.routes import checks as checks_route

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def printed(monkeypatch):
    """The check data handed to the renderer, by print."""
    seen = []

    def fake(check_data, company):
        seen.append(dict(check_data))
        return b"%PDF-stub"

    monkeypatch.setattr(checks_route, "generate_check_pdf", fake)
    return seen


@pytest.fixture(autouse=True)
def bills_memo_choice(client):
    r = client.put("/api/settings", json={"check_memo": "memo"})
    assert r.status_code == 200, r.text


@pytest.fixture
def vendor(client, seed_accounts):
    r = client.post("/api/vendors", json={"name": "Harbor Supply"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _bill(client, seed_accounts, vendor_id, number, amount="100.00", **extra):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "Supplies",
                    "amount": amount,
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
    body = {
        "vendor_id": vendor_id,
        "date": "2026-09-10",
        "amount": sum(a["amount"] for a in allocations),
        "method": "check",
        "allocations": allocations,
        **extra,
    }
    r = client.post("/api/bill-payments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _check_memo(client, printed, pid):
    r = client.get(f"/api/checks/print?bill_payment_id={pid}")
    assert r.status_code == 200, r.text
    return printed[-1]["memo"]


def test_a_payment_of_one_bill_carries_that_bills_memo(
    client, seed_accounts, vendor, printed
):
    bill = _bill(client, seed_accounts, vendor["id"], "A-1", notes="Acct 4471-22")
    pay = _pay(client, vendor["id"], [bill])
    assert pay["notes"] == "Acct 4471-22"
    assert client.get(f"/api/bill-payments/{pay['id']}").json()["notes"] == (
        "Acct 4471-22"
    )
    assert _check_memo(client, printed, pay["id"]) == "Acct 4471-22"


def test_a_payment_of_several_bills_leaves_the_memo_blank(
    client, seed_accounts, vendor, printed
):
    a = _bill(client, seed_accounts, vendor["id"], "A-1", notes="Acct 4471-22")
    b = _bill(client, seed_accounts, vendor["id"], "A-2", notes="Inv 9")
    pay = _pay(client, vendor["id"], [a, b])
    assert pay["notes"] is None
    assert _check_memo(client, printed, pay["id"]) == ""


def test_a_memo_typed_on_the_payment_wins(client, seed_accounts, vendor, printed):
    bill = _bill(client, seed_accounts, vendor["id"], "A-1", notes="Acct 4471-22")
    pay = _pay(client, vendor["id"], [bill], notes="Use PO 77")
    assert pay["notes"] == "Use PO 77"
    assert _check_memo(client, printed, pay["id"]) == "Use PO 77"


def test_a_bill_without_a_memo_leaves_the_check_memo_blank(
    client, seed_accounts, vendor, printed
):
    bill = _bill(client, seed_accounts, vendor["id"], "A-1")
    pay = _pay(client, vendor["id"], [bill])
    assert pay["notes"] is None
    assert _check_memo(client, printed, pay["id"]) == ""


def test_editing_the_memo_before_printing_persists(
    client, seed_accounts, vendor, printed
):
    bill = _bill(client, seed_accounts, vendor["id"], "A-1", notes="Acct 4471-22")
    pay = _pay(client, vendor["id"], [bill])
    r = client.put(f"/api/bill-payments/{pay['id']}/memo", json={"notes": " Acct 9 "})
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "Acct 9"
    assert client.get(f"/api/bill-payments/{pay['id']}").json()["notes"] == "Acct 9"
    assert _check_memo(client, printed, pay["id"]) == "Acct 9"
    # the memo can be cleared, and the bill's own memo does not come back
    r = client.put(f"/api/bill-payments/{pay['id']}/memo", json={"notes": ""})
    assert r.json()["notes"] is None
    assert _check_memo(client, printed, pay["id"]) == ""


def test_the_memo_of_a_payment_of_several_bills_can_be_typed(
    client, seed_accounts, vendor, printed
):
    a, b = _bill(client, seed_accounts, vendor["id"], "A-1"), _bill(
        client, seed_accounts, vendor["id"], "A-2"
    )
    pay = _pay(client, vendor["id"], [a, b])
    client.put(f"/api/bill-payments/{pay['id']}/memo", json={"notes": "Oct statement"})
    assert _check_memo(client, printed, pay["id"]) == "Oct statement"


def test_a_void_payment_or_a_missing_one_refuses_a_memo(client, seed_accounts, vendor):
    bill = _bill(client, seed_accounts, vendor["id"], "A-1")
    pay = _pay(client, vendor["id"], [bill])
    assert client.post(f"/api/bill-payments/{pay['id']}/void").status_code == 200
    r = client.put(f"/api/bill-payments/{pay['id']}/memo", json={"notes": "x"})
    assert r.status_code == 400
    assert (
        client.put("/api/bill-payments/9999/memo", json={"notes": "x"}).status_code
        == 404
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_print_check_on_a_payment_saves_the_memo_first():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "bill_payment_memo_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert 'id="bill-payment-memo"' in got["check"]
    assert got["ach"] == "" and got["void_check"] == ""
    # the memo is saved, then the check opens
    assert got["calls"][1:] == [
        ["put", "/bill-payments/5/memo", {"notes": "Acct 9"}],
        ["open", "/api/checks/print?bill_payment_id=5"],
    ]
