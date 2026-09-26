"""A $0.00 invoice or credit memo when the person means it.

Both QA reports of the 2.17.3 exploratory test asked for a zero-total
document to be refused "unless the user explicitly allows it"; the first fix
refused it with no way past, and no-charge warranty work is a real invoice.
An invoice (create, edit, duplicate, estimate conversion) or a credit memo
that adds up to $0.00 is now refused with a question the page asks — 409,
``code: zero_total`` — and saved when the request says ``allow_zero_total:
true``. A $0.00 invoice starts paid, so it never shows as overdue. A
recurring schedule has no such way past: a $0.00 template would bill $0.00
every period.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func

from app.models.transactions import TransactionLine

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"

FREE = {"description": "Warranty repair", "quantity": 1, "rate": 0}
PRICED = {"description": "Repair", "quantity": 1, "rate": 90}


def _invoice(client, customer_id, line, **extra):
    return client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-01-05",
            "terms": "Net 15",
            "tax_rate": 0.0825,
            "lines": [line],
            **extra,
        },
    )


def _ar(db_session, seed_accounts):
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == seed_accounts["1100"].id)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


def test_a_no_charge_invoice_is_saved_when_the_person_says_so(
    client, db_session, seed_accounts, seed_customer
):
    from app.services.dashboard_widgets import overdue_invoices

    r = _invoice(client, seed_customer.id, FREE, allow_zero_total=True)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert Decimal(inv["total"]) == Decimal(inv["balance_due"]) == Decimal("0")
    # nothing is owed on it, so it is paid — never overdue on the dashboard
    assert inv["status"] == "paid"
    assert overdue_invoices(db_session)["count"] == 0
    assert _ar(db_session, seed_accounts) == Decimal("0")
    body = client.get("/api/reports/ar-aging?as_of_date=2026-09-30").json()
    assert body["items"] == []

    # the flag is not something the invoice keeps
    r = client.put(f"/api/invoices/{inv['id']}", json={"notes": "under warranty"})
    assert r.status_code == 200, r.text
    assert "allow_zero_total" not in r.json()


def test_an_edit_down_to_nothing_goes_through_with_the_flag(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, PRICED).json()
    assert inv["status"] == "draft"
    assert _ar(db_session, seed_accounts) == Decimal("97.43")
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"lines": [FREE], "allow_zero_total": True},
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["total"]) == Decimal("0")
    assert r.json()["status"] == "paid"
    assert _ar(db_session, seed_accounts) == Decimal("0")


def test_duplicating_a_no_charge_invoice_asks_first(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice

    free = _invoice(client, seed_customer.id, FREE, allow_zero_total=True).json()
    r = client.post(f"/api/invoices/{free['id']}/duplicate")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "zero_total"
    assert r.json()["detail"]["question"] == (
        "This invoice adds up to $0.00. Duplicate it anyway?"
    )
    assert db_session.query(Invoice).count() == 1

    r = client.post(
        f"/api/invoices/{free['id']}/duplicate", json={"allow_zero_total": True}
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "paid"
    # a priced invoice needs no body at all, as before
    priced = _invoice(client, seed_customer.id, PRICED).json()
    r = client.post(f"/api/invoices/{priced['id']}/duplicate")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"
    # the body takes nothing else
    r = client.post(f"/api/invoices/{priced['id']}/duplicate", json={"force": True})
    assert r.status_code == 422


def test_converting_a_no_charge_estimate_with_the_flag(
    client, db_session, seed_accounts, seed_customer
):
    est = client.post(
        "/api/estimates",
        json={"customer_id": seed_customer.id, "date": "2026-09-01", "lines": [FREE]},
    ).json()
    r = client.post(
        f"/api/estimates/{est['id']}/convert", json={"allow_zero_total": True}
    )
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["total"]) == Decimal("0")
    assert r.json()["status"] == "paid"
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "converted"


def test_a_no_charge_credit_memo_with_the_flag(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-26",
            "lines": [FREE],
            "allow_zero_total": True,
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["total"]) == Decimal("0")
    assert _ar(db_session, seed_accounts) == Decimal("0")


def test_a_recurring_schedule_has_no_way_past(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.recurring import RecurringInvoice

    body = {
        "customer_id": seed_customer.id,
        "frequency": "monthly",
        "start_date": "2026-09-01",
        "lines": [FREE],
    }
    r = client.post("/api/recurring", json=dict(body, allow_zero_total=True))
    assert r.status_code == 422, r.text
    r = client.post("/api/recurring", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("This recurring invoice adds up to $0.00.")
    assert db_session.query(RecurringInvoice).count() == 0

    ok = client.post("/api/recurring", json=dict(body, lines=[PRICED]))
    assert ok.status_code == 201, ok.text
    r = client.put(
        f"/api/recurring/{ok.json()['id']}",
        json={"lines": [FREE], "allow_zero_total": True},
    )
    assert r.status_code == 422, r.text


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_asks_and_sends_again_with_the_flag():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "zero_total_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # yes: the question asked is the server's, then the same request with the flag
    assert got["yes"] == {
        "asked": ["This invoice adds up to $0.00. Duplicate it anyway?"],
        "sent": [False, True],
        "result": "INV-2",
    }
    # no: nothing sent again, and the caller hears null
    assert got["no"] == {"sent": [False], "result": None}
    # any other refusal is not a question
    assert got["other"] == {"asked": [], "error": "Customer not found"}
    # Duplicate on the invoice view
    assert got["duplicate"]["bodies"] == [None, {"allow_zero_total": True}]
    assert got["duplicate"]["toasts"] == ["Duplicated as Invoice #1002"]


def test_every_zero_total_form_sends_through_the_question():
    inv = (JS / "invoices.js").read_text(encoding="utf-8")
    save = inv[inv.index("    async save(e, id) {") :]
    save = save[: save.index("    async creditLimitOk(")]
    assert "SalesLines.sendAllowingZero(allow =>" in save
    assert "{ ...data, allow_zero_total: true }" in save
    assert "API.put(`/invoices/${id}`, body)" in save
    assert "API.post('/invoices', body)" in save
    assert "if (!saved) return;" in save

    cm = (JS / "credit_memos.js").read_text(encoding="utf-8")
    save = cm[cm.index("    async save(e) {") :]
    save = save[: save.index("    async showApply(")]
    assert "SalesLines.sendAllowingZero(allow =>" in save
    assert (
        "API.post('/credit-memos', allow ? { ...data, allow_zero_total: true } : data)"
        in save
    )

    est = (JS / "estimates.js").read_text(encoding="utf-8")
    convert = est[est.index("    async convert(id) {") :]
    convert = convert[: convert.index("    lineCount:")]
    assert "SalesLines.sendAllowingZero(allow =>" in convert
    assert (
        "API.post(`/estimates/${id}/convert`, allow ? { allow_zero_total: true } : undefined)"
        in convert
    )
