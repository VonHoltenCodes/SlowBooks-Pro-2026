"""Apply Credit from the invoice.

A customer's credit — the unapplied part of a payment, a credit memo — could
be applied from Receive Payments, from the payment and from the customer
page, but not from the invoice itself, where the balance is looked at
(found integrating the 2.17.3 exploratory fixes). The invoice view now says
when the customer holds credit in the invoice's currency and applies it:
each credit with an amount, filled oldest first up to what the invoice
owes. A credit memo is in the home currency, so it pays a home-currency
invoice only, and an amount must be more than zero — the credit memo's
apply accepted both.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _invoice(client, cid, amount, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _credit_memo(client, cid, amount):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": cid,
            "date": "2026-09-05",
            "tax_rate": 0,
            "lines": [{"description": "Return", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_invoice_view_offers_and_applies_the_customers_credit():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "invoice_apply_credit_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # USD credit only on a USD invoice: $30 of a payment + a $20 credit memo
    assert got["note"] == {
        "text": "This customer has $50.00 in credit not applied to an invoice yet.",
        "button": True,
    }
    assert got["noteWhenNothingDue"] == ""
    assert got["noteForEur"] == (
        "This customer has EUR 50.00 in credit not applied to an invoice yet."
    )
    # oldest first, up to the $40.00 the invoice owes
    assert got["dialog"] == {
        "title": "Apply Credit to Invoice #1005",
        "rows": [["payment", 11, "30.00"], ["credit_memo", 3, "10.00"]],
        "status": "Applying $40.00; $0.00 still due.",
    }
    assert got["overStatus"] == "One of the amounts is more than that credit has left."
    assert got["overDue"] == "That is $10.00 more than the invoice owes."
    # each credit through its own apply
    assert got["posts"] == [
        ["/payments/11/apply", {"allocations": [{"invoice_id": 5, "amount": 30}]}],
        ["/credit-memos/3/apply", {"invoice_id": 5, "amount": 10}],
    ]
    assert got["toasts"] == [["Credit applied", "ok"]]


def test_the_view_asks_for_the_note_and_payments_share_the_apply():
    inv = (JS / "invoices.js").read_text(encoding="utf-8")
    view = inv[inv.index("    async view(id) {") :]
    view = view[: view.index("\n    },")]
    assert '<div id="inv-credit-note"></div>' in view
    assert "InvoicesPage.loadCreditNote(inv);" in view
    pay = (JS / "payments.js").read_text(encoding="utf-8")
    save = pay[pay.index("    async saveApplyCredit(") :]
    save = save[: save.index("\n    },")]
    assert "await PaymentsPage.applyCredit(kind, creditId, allocations);" in save


def test_what_the_view_sends_settles_the_invoice(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, 100)
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-02",
            "amount": 30,
            "allocations": [],
        },
    ).json()
    cm = _credit_memo(client, seed_customer.id, 20)

    r = client.post(
        f"/api/payments/{pay['id']}/apply",
        json={"allocations": [{"invoice_id": inv["id"], "amount": 30}]},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/credit-memos/{cm['id']}/apply",
        json={"invoice_id": inv["id"], "amount": 20},
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert Decimal(got["balance_due"]) == Decimal("50.00")
    assert got["status"] == "partial"
    left = client.get(f"/api/customers/{seed_customer.id}/credits").json()
    assert left["credits"] == [] and left["total"] == 0


def test_a_credit_memo_pays_a_home_currency_invoice_only(
    client, db_session, seed_accounts, seed_customer
):
    eur = _invoice(client, seed_customer.id, 850, currency="EUR", exchange_rate="1.10")
    cm = _credit_memo(client, seed_customer.id, 50)
    r = client.post(
        f"/api/credit-memos/{cm['id']}/apply",
        json={"invoice_id": eur["id"], "amount": 50},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"Invoice {eur['invoice_number']} is in EUR, and credit memo "
        f"{cm['memo_number']} is in USD. A credit pays an invoice in its own "
        "currency only."
    )
    assert Decimal(
        client.get(f"/api/invoices/{eur['id']}").json()["balance_due"]
    ) == Decimal("850.00")
    assert Decimal(
        client.get(f"/api/credit-memos/{cm['id']}").json()["balance_remaining"]
    ) == Decimal("50.00")


@pytest.mark.parametrize("amount", [0, -25])
def test_a_credit_memo_applies_an_amount_more_than_zero(
    client, db_session, seed_accounts, seed_customer, amount
):
    inv = _invoice(client, seed_customer.id, 100)
    cm = _credit_memo(client, seed_customer.id, 50)
    r = client.post(
        f"/api/credit-memos/{cm['id']}/apply",
        json={"invoice_id": inv["id"], "amount": amount},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Enter an amount more than zero to apply."
    memo = client.get(f"/api/credit-memos/{cm['id']}").json()
    assert Decimal(memo["balance_remaining"]) == Decimal("50.00")
    assert Decimal(memo["amount_applied"]) == Decimal("0")
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert Decimal(got["balance_due"]) == Decimal("100.00")
