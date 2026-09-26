"""No sales document for nothing (2.17.3 exploratory: skytech W-M1,
macbase1 F14).

The Credit Memo and Recurring Invoice forms filled no price when an item was
picked and showed no running total, so a $0.00 credit memo was issued and a
recurring schedule generated two $0.00 invoices that then sat on the
dashboard as overdue. An invoice with one blank line saved at $0.00 too.
Every sales path now refuses a document that adds up to nothing, with a
sentence that says what to do; the generator skips (and names) an old $0.00
template instead of billing it; and the two forms price the picked item and
show their total the way the invoice form does.
"""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"

ZERO_LINE = {"description": "Sourdough Loaf", "quantity": 2, "rate": 0}
PRICED_LINE = {"description": "Sourdough Loaf", "quantity": 2, "rate": 8.5}


def _invoice_body(customer_id, line):
    return {
        "customer_id": customer_id,
        "date": "2026-09-01",
        "tax_rate": 0,
        "lines": [dict(line, line_order=0)],
    }


def _asked(r, message, question):
    """The refusal a page can turn into a question: 409, code zero_total."""
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "code": "zero_total",
        "message": message,
        "question": question,
    }


def test_an_invoice_that_adds_up_to_nothing_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice

    r = client.post("/api/invoices", json=_invoice_body(seed_customer.id, ZERO_LINE))
    _asked(
        r,
        "This invoice adds up to $0.00. Enter a rate on at least one line "
        "before saving it.",
        "This invoice adds up to $0.00. Save it anyway?",
    )
    assert db_session.query(Invoice).count() == 0


def test_editing_an_invoice_down_to_nothing_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post("/api/invoices", json=_invoice_body(seed_customer.id, PRICED_LINE))
    assert r.status_code == 201, r.text
    inv = r.json()
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": [ZERO_LINE]})
    assert r.status_code == 409, r.text
    assert "adds up to $0.00" in r.json()["detail"]["message"]
    assert Decimal(client.get(f"/api/invoices/{inv['id']}").json()["total"]) == Decimal(
        "17.00"
    )


def test_a_credit_memo_for_nothing_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.credit_memos import CreditMemo

    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-26",
            "lines": [ZERO_LINE],
        },
    )
    _asked(
        r,
        "This credit memo adds up to $0.00. Enter a rate on at least one line "
        "before saving it.",
        "This credit memo adds up to $0.00. Save it anyway?",
    )
    assert db_session.query(CreditMemo).count() == 0


def _schedule(client, customer_id, line, **extra):
    body = {
        "customer_id": customer_id,
        "frequency": "monthly",
        "start_date": "2026-09-01",
        "lines": [line],
    }
    body.update(extra)
    return client.post("/api/recurring", json=body)


def test_a_recurring_schedule_for_nothing_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.recurring import RecurringInvoice

    r = _schedule(client, seed_customer.id, ZERO_LINE)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith(
        "This recurring invoice adds up to $0.00. Enter a rate"
    )
    assert db_session.query(RecurringInvoice).count() == 0

    ok = _schedule(client, seed_customer.id, PRICED_LINE)
    assert ok.status_code == 201, ok.text
    r = client.put(f"/api/recurring/{ok.json()['id']}", json={"lines": [ZERO_LINE]})
    assert r.status_code == 400, r.text
    assert "adds up to $0.00" in r.json()["detail"]


def test_the_generator_skips_an_old_zero_schedule_and_says_so(
    client, db_session, seed_accounts, seed_customer
):
    """A template saved before the refusal existed must not bill $0.00. It
    keeps its date, so once a rate is entered the missed month is billed."""
    from app.models.invoices import Invoice
    from app.models.recurring import RecurringInvoice, RecurringInvoiceLine

    rec = RecurringInvoice(
        customer_id=seed_customer.id,
        frequency="monthly",
        start_date=date(2026, 9, 1),
        next_due=date(2026, 9, 1),
        is_active=True,
        terms="Net 30",
        tax_rate=Decimal("0"),
    )
    db_session.add(rec)
    db_session.flush()
    db_session.add(
        RecurringInvoiceLine(
            recurring_invoice_id=rec.id,
            description="Sourdough Loaf",
            quantity=Decimal("2"),
            rate=Decimal("0"),
            line_order=0,
        )
    )
    db_session.commit()
    rec_id = rec.id

    r = client.post("/api/recurring/generate?as_of=2026-09-26")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invoices_created"] == 0
    assert [s["recurring_id"] for s in body["skipped"]] == [rec_id]
    assert body["skipped"][0]["message"] == (
        "The schedule for Test Customer adds up to $0.00, so nothing was "
        "created. Open it and enter a rate on at least one line."
    )
    assert db_session.query(Invoice).count() == 0
    assert client.get(f"/api/recurring/{rec_id}").json()["next_due"] == "2026-09-01"

    r = client.put(f"/api/recurring/{rec_id}", json={"lines": [PRICED_LINE]})
    assert r.status_code == 200, r.text
    body = client.post("/api/recurring/generate?as_of=2026-09-26").json()
    assert body["invoices_created"] == 1 and body["skipped"] == []
    inv = client.get(f"/api/invoices/{body['invoice_ids'][0]}").json()
    assert inv["date"] == "2026-09-01"
    assert Decimal(inv["total"]) == Decimal("17.00")


def test_converting_an_estimate_for_nothing_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice

    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "lines": [ZERO_LINE],
        },
    )
    assert est.status_code == 201, est.text
    r = client.post(f"/api/estimates/{est.json()['id']}/convert")
    _asked(
        r,
        "This estimate adds up to $0.00. Enter a rate on at least one line "
        "before converting it.",
        "This estimate adds up to $0.00. Convert it to an invoice anyway?",
    )
    assert db_session.query(Invoice).count() == 0
    assert client.get(f"/api/estimates/{est.json()['id']}").json()["status"] == (
        "pending"
    )


def test_the_credit_memo_and_recurring_forms_price_the_item_and_show_a_total():
    for page, obj, prefix in (
        ("credit_memos.js", "CreditMemosPage", "cm"),
        ("recurring.js", "RecurringPage", "rec"),
    ):
        src = (JS / page).read_text(encoding="utf-8")
        row = src[src.index("lineRowHtml(idx") :]
        row = row[: row.index("</tr>")]
        assert f'onchange="{obj}.itemSelected(${{idx}})"' in row, page
        assert 'class="line-qty" type="number"' in row, page
        assert row.count(f'oninput="{obj}.recalc()"') == 2, page
        assert 'class="col-amount line-amount"' in row, page
        assert f"SalesLines.fillFromItem(row, {obj}._items)" in src, page
        assert f"SalesLines.totals($('#{prefix}-lines')" in src, page
        for cell in ("subtotal", "tax", "total"):
            assert f'id="{prefix}-{cell}"' in src, (page, cell)


def test_every_sales_form_uses_the_one_line_arithmetic():
    for page, prefix in (
        ("invoices.js", "inv"),
        ("estimates.js", "est"),
        ("sales_receipts.js", "sr"),
        ("credit_memos.js", "cm"),
        ("recurring.js", "rec"),
    ):
        src = (JS / page).read_text(encoding="utf-8")
        assert f"SalesLines.totals($('#{prefix}-lines')" in src, page
        # the old per-page loop summed unrounded lines
        assert "const amount = qty * rate;" not in src, page


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_forms_add_up_the_way_the_ledger_does():
    """Each line rounds half up to the cent before it is summed, and tax is
    taken on the rounded taxable lines only — the server's arithmetic, so
    the total on screen is the total that posts."""
    from types import SimpleNamespace

    from app.services.accounting import compute_line_totals

    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "sales_lines_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["cents"] == [1.01, 1.01, 2.68, 1.35, -1.01, 0.3, 10824891.75]
    assert got["amounts"] == ["$1.01", "$200.00", "$19.99"]

    server = compute_line_totals(
        [
            SimpleNamespace(quantity="3", rate="0.335", is_taxable=True),
            SimpleNamespace(quantity="2", rate="100", is_taxable=False),
            SimpleNamespace(quantity="1", rate="19.99"),
        ],
        Decimal("0.0825"),
    )
    shown = got["totals"]
    assert [Decimal(str(shown[k])) for k in ("subtotal", "tax", "total")] == [
        Decimal("221.00"),
        Decimal("1.73"),
        Decimal("222.73"),
    ]
    assert list(server) == [Decimal("221.00"), Decimal("1.73"), Decimal("222.73")]

    assert got["picked"] == {
        "found": True,
        "desc": "Sourdough Loaf",
        "rate": "8.50",
        "taxable": False,
    }
    assert got["none"] is True


def test_the_recurring_form_can_update_a_saved_schedule(
    client, seed_accounts, seed_customer
):
    """Update sent the customer and start date, which the edit API does not
    take, so every edit from the screen was refused with a 422 — including
    the one that fixes a $0.00 schedule. The form now sends them only when
    it creates a schedule."""
    rec = _schedule(client, seed_customer.id, PRICED_LINE).json()
    as_the_form_sends_it = {
        "frequency": "monthly",
        "end_date": None,
        "terms": "Net 30",
        "tax_rate": 0,
        "notes": None,
        "class_id": None,
        "lines": [dict(PRICED_LINE, rate=9, is_taxable=True, line_order=0)],
    }
    r = client.put(f"/api/recurring/{rec['id']}", json=as_the_form_sends_it)
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["rate"] == 9
    # the control: the fields the old form sent are still not accepted
    r = client.put(
        f"/api/recurring/{rec['id']}",
        json=dict(as_the_form_sends_it, customer_id=seed_customer.id),
    )
    assert r.status_code == 422

    src = (JS / "recurring.js").read_text(encoding="utf-8")
    save = src[src.index("async save(e, id)") :]
    save = save[: save.index("try {")]
    assert "customer_id" not in save[: save.index("if (!id) {")]
    assert "data.customer_id = parseInt(form.customer_id.value);" in save
    assert "data.start_date = form.start_date.value;" in save
