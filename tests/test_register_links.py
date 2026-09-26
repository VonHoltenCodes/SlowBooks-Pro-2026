"""Every line in the bank register opens something (explore 2.17.3
integration, I18).

The register links each posting to the document behind it
(app/services/bank_register.py source_link), and the report drill-downs use
the same links. Only a vendor credit's link had a screen: a deposit's
#/deposits/{id} — and an invoice's, a bill's, a payment's, a bill
payment's, an expense's, a card charge's, a transfer's, a journal entry's —
said "Page not found". Each now opens its list with the document over it;
a deposit and a bill payment have views of their own (GET /api/deposits/{id}
lists the payments a deposit took, with Void; GET /api/bill-payments/{id}
the bills a payment paid, with Print Check and Void). A line with no
document link (a void, a sales-tax or payroll payment) opens its journal
entry.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.models.contacts import Customer, Vendor
from app.services import bank_register

ROOT = Path(__file__).resolve().parents[1]

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _node(script, *args):
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── the links resolve ────────────────────────────────────────────────────


@needs_node
def test_every_register_link_opens_a_view():
    links = {
        kind: pattern.format(id=7, txn=7).removeprefix("/")
        for kind, pattern in bank_register._LINKS.items()
    }
    shown = _node("app_routes_probe.js", json.dumps(sorted(set(links.values()))))
    for kind, link in links.items():
        page = shown[link]
        assert page["page"] != "<p>Page not found</p>", (kind, link)
        viewers = [
            c
            for c in page["calls"]
            if c[0].rsplit(".", 1)[-1] in ("view", "showDetail", "viewPayment")
        ]
        assert viewers and viewers[-1][1:] == ["7"], (kind, link, page["calls"])


@needs_node
def test_each_document_opens_over_its_own_list():
    shown = _node(
        "app_routes_probe.js",
        json.dumps(["#/deposits/31", "#/bill-payments/41", "#/banking/transfers/9"]),
    )
    assert shown["#/deposits/31"]["calls"] == [
        ["DepositsPage.render"],
        ["DepositsPage.view", "31"],
    ]
    assert shown["#/bill-payments/41"]["calls"] == [
        ["BillsPage.render"],
        ["BillsPage.viewPayment", "41"],
    ]
    # a transfer is its journal entry, over the Banking page
    assert shown["#/banking/transfers/9"]["calls"] == [
        ["BankingPage.render"],
        ["JournalPage.view", "9"],
    ]


# ── a deposit, by its id ─────────────────────────────────────────────────


def _payment(client, customer_id, amount, check):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-09-10",
            "amount": amount,
            "method": "Check",
            "check_number": check,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_deposit_opens_with_the_payments_it_took(client, db_session, seed_accounts):
    acme = Customer(name="Acme Diner", is_active=True)
    db_session.add(acme)
    db_session.commit()
    _payment(client, acme.id, 332.30, "4420")
    _payment(client, acme.id, 479.90, "4421")
    waiting = client.get("/api/deposits/pending").json()
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-09-12",
            "total": 812.20,
            "reference": "SLIP-9",
            "line_ids": [w["transaction_line_id"] for w in waiting],
        },
    )
    assert r.status_code == 200, r.text
    dep = r.json()["transaction_id"]

    # the register's link names this id
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    [line] = [e for e in reg["entries"] if e["source_type"] == "deposit"]
    assert line["source_link"] == f"/#/deposits/{dep}"

    d = client.get(f"/api/deposits/{dep}").json()
    assert (d["id"], d["reference"], d["account_name"]) == (dep, "SLIP-9", "Checking")
    assert float(d["amount"]) == 812.20
    assert (d["items"], d["voided"], d["reconciled"]) == (2, False, False)
    assert [
        (p["received_from"], p["check_number"], p["amount"]) for p in d["payments"]
    ] == [
        ("Acme Diner", "4420", 332.30),
        ("Acme Diner", "4421", 479.90),
    ]

    # voided, it has given its payments back to the list
    assert client.post(f"/api/deposits/{dep}/void").status_code == 200
    d = client.get(f"/api/deposits/{dep}").json()
    assert (d["voided"], d["payments"], d["items"]) == (True, [], None)


def test_only_a_deposit_opens_as_one(client, seed_accounts):
    assert client.get("/api/deposits/999999").status_code == 404
    e = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-01",
            "amount": 12.5,
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "payee": "Office Depot",
        },
    )
    assert e.status_code == 201, e.text
    r = client.get(f"/api/deposits/{e.json()['id']}")
    assert r.status_code == 404
    assert r.json()["detail"] == "Deposit not found"


# ── a bill payment, by its id ────────────────────────────────────────────


def test_a_bill_payment_opens_by_its_id(client, db_session, seed_accounts):
    v = Vendor(name="Sign Supply", is_active=True)
    db_session.add(v)
    db_session.commit()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "bill_number": "SS-1050",
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "Vinyl",
                    "quantity": 1,
                    "rate": 150,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": "2026-09-15",
            "amount": 150,
            "method": "check",
            "check_number": "1050",
            "pay_from_account_id": seed_accounts["1000"].id,
            "allocations": [{"bill_id": bill.json()["id"], "amount": 150}],
        },
    )
    assert pay.status_code == 201, pay.text
    pid = pay.json()["id"]

    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    [line] = [e for e in reg["entries"] if e["source_type"] == "bill_payment"]
    assert line["source_link"] == f"/#/bill-payments/{pid}"

    p = client.get(f"/api/bill-payments/{pid}").json()
    assert (p["vendor_name"], p["check_number"], p["is_voided"]) == (
        "Sign Supply",
        "1050",
        False,
    )
    assert [(a["bill_id"], float(a["amount"])) for a in p["allocations"]] == [
        (bill.json()["id"], 150.0)
    ]
    assert client.get("/api/bill-payments/999999").status_code == 404


# ── the views ────────────────────────────────────────────────────────────


@needs_node
def test_the_views_the_links_open():
    got = _node("document_views_probe.js")

    dep = got["deposit 31"]
    assert [r[1] for r in dep["rows"]] == ["Salt &amp; Pine", "Walk-in"]
    assert dep["rows"][0][2] == "Payment from Salt &amp; Pine for Invoice #1002"
    assert dep["rows"][0][3] == "Check 4420"
    assert 'onclick="DepositsPage.voidDeposit(31)">Void</button>' in dep["buttons"][0]
    # void: no payments, no Void, and why
    void = got["deposit 32"]
    assert void["rows"] == [] and len(void["buttons"]) == 1
    assert "its payments went back on the Make Deposits list" in void["text"]
    # reconciled: no Void, and why
    rec = got["deposit 33"]
    assert len(rec["buttons"]) == 1
    assert "on a reconciled bank statement, so it can't be voided" in rec["text"]

    bp = got["bill payment 41"]
    assert [r[0] for r in bp["rows"]] == ["SS-1050", "SS-1051"]
    assert "$25.00 paid ahead, not applied to a bill." in bp["text"]
    assert any("checks/print?bill_payment_id=41" in b for b in bp["buttons"])
    assert any("BillsPage.voidBillPayment(41, 8)" in b for b in bp["buttons"])
    ach = got["bill payment 42"]
    assert not any("checks/print" in b for b in ach["buttons"])
    assert any("voidBillPayment(42, 8)" in b for b in ach["buttons"])
    gone = got["bill payment 43"]
    assert not any(
        "checks/print" in b or "voidBillPayment" in b for b in gone["buttons"]
    )
    assert "paid ahead" not in gone["text"]

    # a register line with no document of its own opens its journal entry
    assert got["register"] == [
        '<td><a href="#/journal/58">View entry</a></td>',
        '<td><a href="#/journal/57">Sales tax payment</a></td>',
        '<td><a href="/#/deposits/31">Deposit to Checking</a></td>',
    ]
