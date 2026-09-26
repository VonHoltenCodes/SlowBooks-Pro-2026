"""Receive Payment applies what was received, and a payment is not a check.

Explore 2.17.3:
- macbase1 F12: Amount 200 against the customer's only invoice recorded the
  payment with nothing applied; the only cue was a small line saying the
  remainder "will be tracked as a customer credit". Typing the amount now
  fills the Apply column oldest invoice first (the user can change it), and
  money left over is kept as a credit only when the user ticks a box that
  says so. Credits the customer already holds are offered for applying.
- skytech W-M5: Receive Payment listed only sent and partial invoices, so a
  draft — which already posts to A/R — could not be paid on the normal
  screen, while Batch Payments and credit-memo Apply listed it.
- skytech W-M9 / F12: Print Check on a RECEIVED payment printed a check
  payable to the customer for the money they paid us, its stub listing
  "Invoice #1" (the internal id) and not adding up to the check.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.contacts import Vendor

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _read(name):
    return (JS / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def probe():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "receive_payment_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return {r["name"]: r for r in map(json.loads, out.stdout.splitlines())}


def test_typing_the_amount_applies_it_oldest_first(probe):
    assert probe["partial"]["apply"] == ["312.30", "87.70"]
    assert probe["partial"]["keepShown"] is False
    # more than is owed: every invoice filled, the rest left over
    assert probe["over"]["apply"] == ["312.30", "500.00"]
    # the user can still change an amount
    assert probe["edited"]["apply"] == ["312.30", "100.00"]
    assert "$487.70 not applied" in probe["edited"]["status"]


def test_money_left_over_is_kept_only_when_the_box_is_ticked(probe):
    assert probe["over"]["keepShown"] is True
    assert "Keep the $87.70 not applied as a credit" in probe["over"]["keepText"]
    unticked = probe["save-unticked"]
    assert unticked["posts"] == 0
    assert unticked["toast"][1] == "error"
    assert "tick the box to keep it as a credit" in unticked["toast"][0]
    ticked = probe["save-ticked"]
    assert ticked["posts"] == 1
    assert ticked["body"]["allocations"] == [
        {"invoice_id": 11, "amount": 312.3},
        {"invoice_id": 12, "amount": 500},
    ]
    # the box starts unticked on the form
    form = _read("payments.js")
    assert '<input type="checkbox" id="keep-credit">' in form


def test_drafts_are_listed_oldest_first_and_voids_are_not(probe):
    assert probe["open"]["ids"] == [1, 5, 3]
    js = _read("payments.js")
    load = js[js.index("async loadInvoices(") : js.index("_autoApply() {")]
    assert "status=sent" not in load and "status=partial" not in load
    assert "/invoices?customer_id=${customerId}`" in load


def test_a_draft_invoice_can_be_paid(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": 200}],
        },
    )
    inv = r.json()
    assert inv["status"] == "draft"
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-10",
            "amount": 200,
            "allocations": [{"invoice_id": inv["id"], "amount": 200}],
        },
    )
    assert r.status_code == 201, r.text
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"


def test_receive_payment_and_the_customer_page_offer_credits_to_apply():
    js = _read("payments.js")
    load = js[js.index("async loadInvoices(") : js.index("_autoApply() {")]
    assert "/customers/${customerId}/credits" in load
    assert "showApplyCredit(" in js and "/payments/${creditId}/apply" in js
    assert "/credit-memos/${creditId}/apply" in js
    view = js[js.index("async view(id)") : js.index("async void(id)")]
    assert "p.unapplied" in view and "showApplyCredit('payment'" in view
    customers = _read("customers.js")
    assert "/customers/${id}/credits" in customers
    assert "PaymentsPage.showApplyCredit(" in customers
    # the Method column read a field the API never sends
    assert "p.payment_method" not in customers


def test_a_received_payment_offers_no_check_and_names_invoices_by_number(
    client, seed_accounts, seed_customer
):
    js = _read("payments.js")
    assert "checks/print" not in js
    view = js[js.index("async view(id)") : js.index("async void(id)")]
    assert "a.invoice_number" in view

    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-10",
            "amount": 332.30,
            "method": "Check",
            "check_number": "4420",
        },
    )
    assert r.status_code == 201, r.text
    r = client.get(f"/api/checks/print?payment_id={r.json()['id']}")
    assert r.status_code == 400, r.text
    assert "payment you received, so there is no check to print" in r.json()["detail"]


def test_a_bill_payment_check_stub_names_the_bills_and_adds_up(
    client, db_session, seed_accounts, monkeypatch
):
    import app.routes.checks as checks

    seen = {}
    monkeypatch.setattr(
        checks,
        "generate_check_pdf",
        lambda data, company: seen.update(data) or b"%PDF-",
    )
    v = Vendor(name="Sign Supply", is_active=True)
    db_session.add(v)
    db_session.commit()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "bill_number": "SS-1050",
            "date": "2026-09-01",
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "x",
                    "rate": 150,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": "2026-09-05",
            "amount": 200,
            "check_number": "1050",
            "pay_from_account_id": seed_accounts["1000"].id,
            "allocations": [{"bill_id": bill["id"], "amount": 150}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.get(f"/api/checks/print?bill_payment_id={r.json()['id']}")
    assert r.status_code == 200, r.text
    assert seen["payee"] == "Sign Supply"
    assert [d["description"] for d in seen["details"]] == [
        "Bill #SS-1050",
        "Paid ahead (not applied to a bill)",
    ]
    assert sum(Decimal(str(d["amount"])) for d in seen["details"]) == Decimal("200")
