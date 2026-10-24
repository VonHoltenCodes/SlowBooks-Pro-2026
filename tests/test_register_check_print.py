"""A check written straight into the bank register, or as an expense,
prints (discussion #354).

Print Check sat only on bill payments. A check typed into the register
(+ Entry, the quick-entry row, a split) or entered as an Expense paid from
a bank account had the same payee, date, amount and check number, and no
way to print it. GET /api/checks/print?transaction_id= prints either on
the same 3-per-page stock, each split line on the stub; the register row
and the expense's view carry Print Check when the entry is money out of a
bank account and not void. A deposit, a card charge, a transfer, a void
and a bill payment are refused with the reason.
"""

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pdf_text(r):
    pypdf = pytest.importorskip("pypdf")
    assert r.status_code == 200, r.text[:300]
    assert r.content[:5] == b"%PDF-"
    return pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()


def _entry(client, seed_accounts, **overrides):
    body = {
        "account_id": seed_accounts["1000"].id,
        "date": "2026-09-14",
        "amount": "-250.00",
        "category_account_id": seed_accounts["6500"].id,
        "payee": "Main Street Properties",
        "description": "October rent",
        "check_number": "1072",
    }
    body.update(overrides)
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _register_row(client, account, txn_id):
    reg = client.get(f"/api/banking/check-register?account_id={account.id}").json()
    return next(e for e in reg["entries"] if e["transaction_id"] == txn_id)


def test_a_register_check_prints_with_payee_number_and_category(client, seed_accounts):
    e = _entry(client, seed_accounts)
    r = client.get(f"/api/checks/print?transaction_id={e['id']}")
    text = _pdf_text(r)
    assert "Check_1072.pdf" in r.headers["content-disposition"]
    assert "Main Street Properties" in text
    assert "1072" in text
    assert "250.00" in text
    assert "Two Hundred Fifty" in text
    # the stub names what the check paid, and the memo is the entry's
    assert "Rent or Lease" in text
    assert "October rent" in text
    assert _register_row(client, seed_accounts["1000"], e["id"])["printable"] is True


def test_a_register_check_prints_values_only_on_preprinted_stock(
    client, db_session, seed_accounts
):
    # The register/expense path reads the check-stock setting through the
    # same helper every path does (#440): on pre-printed stock the labels
    # and the signature rule are gone and the values remain. Rendered to
    # HTML so it runs without WeasyPrint's native stack.
    from app.routes.checks import register_check_data
    from app.services.pdf_service import render_check_html

    e = _entry(client, seed_accounts)
    data = register_check_data(db_session, e["id"])
    company = {"company_name": "Acme", "check_stock": "preprinted"}
    html = render_check_html(data, company)
    assert "PAY TO THE ORDER OF" not in html
    assert '<div class="signature-line"></div>' not in html
    assert "Main Street Properties" in html
    assert "October rent" in html


def test_a_split_check_lists_each_line_on_the_stub(client, seed_accounts):
    e = _entry(
        client,
        seed_accounts,
        amount="-300.00",
        category_account_id=None,
        check_number="1073",
        splits=[
            {
                "category_account_id": seed_accounts["6500"].id,
                "amount": "-275.00",
                "description": "rent",
            },
            {
                "category_account_id": seed_accounts["6400"].id,
                "amount": "-25.00",
                "description": "keys",
            },
        ],
    )
    text = _pdf_text(client.get(f"/api/checks/print?transaction_id={e['id']}"))
    assert "Rent or Lease" in text and "275.00" in text
    assert "Office Supplies" in text and "25.00" in text
    assert "300.00" in text


def test_a_refund_netted_against_a_check_prints_as_a_negative_line(
    client, db_session, seed_accounts
):
    # A check that pays Insurance 620.00 less a 120.00 refund is a 500.00
    # check (#387). The stub must list the refund as a negative so the lines
    # add up to the check, not drop it and overstate the stub at 620.00.
    from app.routes.checks import register_check_data

    e = _entry(
        client,
        seed_accounts,
        amount="-500.00",
        category_account_id=None,
        check_number="1075",
        splits=[
            {
                "category_account_id": seed_accounts["6300"].id,
                "amount": "-620.00",
                "description": "annual premium",
            },
            {
                "category_account_id": seed_accounts["6000"].id,
                "amount": "120.00",
                "description": "credit for the returned order",
            },
        ],
    )
    from decimal import Decimal

    data = register_check_data(db_session, e["id"])
    by_name = {d["description"]: d["amount"] for d in data["details"]}
    insurance = next(v for k, v in by_name.items() if k.startswith("Insurance"))
    refund = next(
        v for k, v in by_name.items() if k.startswith("Advertising & Marketing")
    )
    assert Decimal(str(insurance)) == Decimal("620.00")
    assert Decimal(str(refund)) == Decimal("-120.00")
    # the stub's lines add up to the check amount
    assert sum(Decimal(str(d["amount"])) for d in data["details"]) == Decimal("500.00")
    assert Decimal(str(data["amount"])) == Decimal("500.00")
    # and it renders: the refund prints with a minus sign
    text = _pdf_text(client.get(f"/api/checks/print?transaction_id={e['id']}"))
    assert "Insurance" in text and "620.00" in text
    assert "Advertising & Marketing" in text and "-$120.00" in text
    assert "500.00" in text


def test_a_check_without_a_number_still_prints(client, seed_accounts):
    e = _entry(client, seed_accounts, check_number=None)
    r = client.get(f"/api/checks/print?transaction_id={e['id']}")
    _pdf_text(r)
    assert "Check.pdf" in r.headers["content-disposition"]


def test_an_expense_paid_by_check_prints_to_its_vendor(client, seed_accounts):
    v = client.post("/api/vendors", json={"name": "Sweet Forest Cafe"})
    assert v.status_code == 201, v.text
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-02",
            "vendor_id": v.json()["id"],
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "30.30",
            "reference": "1074",
            "memo": "team lunch",
        },
    )
    assert r.status_code == 201, r.text
    x = r.json()
    assert x["printable"] is True
    text = _pdf_text(client.get(f"/api/checks/print?transaction_id={x['id']}"))
    assert "Sweet Forest Cafe" in text
    assert "1074" in text and "30.30" in text and "team lunch" in text
    assert _register_row(client, seed_accounts["1000"], x["id"])["printable"] is True


def test_an_expense_paid_by_card_has_no_check(client, seed_accounts):
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-02",
            "payee": "Corner Hardware",
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["2100"].id,
            "amount": "12.00",
        },
    )
    assert r.status_code == 201, r.text
    x = r.json()
    assert x["printable"] is False
    r = client.get(f"/api/checks/print?transaction_id={x['id']}")
    assert r.status_code == 400
    assert "paid by card" in r.json()["detail"]
    assert _register_row(client, seed_accounts["2100"], x["id"])["printable"] is False


def test_money_in_a_transfer_and_a_void_are_refused(client, seed_accounts):
    deposit = _entry(
        client,
        seed_accounts,
        amount="500.00",
        category_account_id=seed_accounts["4000"].id,
        payee="Walk-in sale",
        check_number=None,
    )
    r = client.get(f"/api/checks/print?transaction_id={deposit['id']}")
    assert r.status_code == 400 and "money coming in" in r.json()["detail"]
    assert (
        _register_row(client, seed_accounts["1000"], deposit["id"])["printable"]
        is False
    )

    transfer = _entry(
        client,
        seed_accounts,
        amount="-100.00",
        category_account_id=seed_accounts["1010"].id,
        payee=None,
        description="to savings",
        check_number=None,
    )
    r = client.get(f"/api/checks/print?transaction_id={transfer['id']}")
    assert r.status_code == 400 and "neither" in r.json()["detail"]
    assert (
        _register_row(client, seed_accounts["1000"], transfer["id"])["printable"]
        is False
    )

    check = _entry(client, seed_accounts, check_number="1075")
    r = client.post(f"/api/banking/entries/{check['id']}/void")
    assert r.status_code == 200, r.text
    r = client.get(f"/api/checks/print?transaction_id={check['id']}")
    assert r.status_code == 400 and "void" in r.json()["detail"]
    assert (
        _register_row(client, seed_accounts["1000"], check["id"])["printable"] is False
    )


def test_a_bill_payment_is_sent_to_its_bill_and_nothing_else_is_found(
    client, seed_accounts
):
    v = client.post("/api/vendors", json={"name": "Sign Supply"})
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v.json()["id"],
            "date": "2026-09-01",
            "due_date": "2026-09-30",
            "lines": [
                {"account_id": seed_accounts["6000"].id, "quantity": 1, "rate": 40}
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    paid = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.json()["id"],
            "date": "2026-09-05",
            "amount": "40.00",
            "check_number": "1001",
            "allocations": [{"bill_id": bill.json()["id"], "amount": "40.00"}],
        },
    )
    assert paid.status_code == 201, paid.text
    txn_id = _register_row_by_source(client, seed_accounts["1000"], "bill_payment")
    r = client.get(f"/api/checks/print?transaction_id={txn_id}")
    assert r.status_code == 400 and "prints from the bill" in r.json()["detail"]

    assert client.get("/api/checks/print?transaction_id=999999").status_code == 404
    r = client.get("/api/checks/print")
    assert r.status_code == 400 and "transaction_id" in r.json()["detail"]


def _register_row_by_source(client, account, source_type):
    reg = client.get(f"/api/banking/check-register?account_id={account.id}").json()
    return next(e for e in reg["entries"] if e["source_type"] == source_type)[
        "transaction_id"
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_register_row_and_the_expense_view_offer_print_check():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "register_check_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    print_btn = (
        '<button class="btn btn-sm btn-secondary" '
        "onclick=\"window.open('/api/checks/print?transaction_id={id}','_blank')\">"
        "Print Check</button>"
    )
    # the register: a check and an expense print, a deposit, a transfer
    # and a card charge do not, a void does not
    assert got["register"]["61"] == [print_btn.format(id=61), "Void"]
    assert got["register"]["62"] == [print_btn.format(id=62)]
    assert got["register"]["63"] == ["Void"]
    assert got["register"]["64"] == ["Void"]
    assert got["register"]["65"] == []
    # the expense's view: Print Check beside Void when it was paid by check
    assert any("transaction_id=62" in b for b in got["expense_check"])
    assert any("ExpensesPage.void(62)" in b for b in got["expense_check"])
    assert not any("checks/print" in b for b in got["expense_card"])
    assert not any("checks/print" in b for b in got["expense_void"])
