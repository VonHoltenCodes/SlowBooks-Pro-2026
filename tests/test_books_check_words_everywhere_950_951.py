"""Books Check after the 2.32.0 round-9 gate (#950, #951).

#950 (W-162): the window prefers the server's label, so an invoice-kind
document reads from its own face there too, and the PDF stops re-running the
vocabulary over document lines it already received in the company's words.

#951 (W-163): the chart's account names travel with the run in a context
variable, set and reset around the check, so two checks at once never read
each other's names or clear them mid-run."""

import contextvars
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.routes.reports.books import _sections
from app.services.books_check import _chart_labels, _words, books_check
from app.services.terminology import Terms

ROOT = Path(__file__).resolve().parents[1]


def test_the_window_takes_the_servers_label_first():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    body = js.split("_booksCheckKind = function (kind, label) {")[1].split(
        "ReportsPage.booksCheckHref"
    )[0]
    assert "return label || own[kind] || words[kind]" in body


def test_the_pdf_does_not_rerun_the_vocabulary_over_document_lines():
    py = (ROOT / "app/routes/reports/books.py").read_text(encoding="utf-8")
    assert "t.text(_document_words(doc))" not in py


def _invoice(client, customer_id, when, rate):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": when.isoformat(),
            "due_date": (when + timedelta(days=15)).isoformat(),
            "lines": [{"description": "Work", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_the_pdfs_rows_name_a_nonprofits_documents_from_their_face(
    client, db_session, seed_accounts
):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    donor = Customer(name="Grant Foundation", is_active=True)
    db_session.add(donor)
    db_session.commit()
    today = date.today()
    fee_id = _invoice(client, donor.id, today - timedelta(days=5), 83.0)
    pledge_id = _invoice(client, donor.id, today - timedelta(days=5), 120.0)
    fee, pledge = db_session.get(Invoice, fee_id), db_session.get(Invoice, pledge_id)
    for inv in (fee, pledge):
        inv.status = InvoiceStatus.SENT
    pledge.is_pledge = True
    db_session.commit()
    fee.balance_due = Decimal(str(fee.balance_due)) + Decimal("1")
    pledge.balance_due = Decimal(str(pledge.balance_due)) + Decimal("2")
    db_session.commit()
    data = books_check(db_session, None)
    section = _sections(data, Terms("nonprofit"))[0]
    cells = [row["cells"][0] for row in section["rows"]]
    assert any(c.startswith(f"Invoice {fee.invoice_number}") for c in cells), cells
    assert any(c.startswith(f"Pledge {pledge.invoice_number}") for c in cells), cells
    assert not any(c.startswith(f"Pledge {fee.invoice_number}") for c in cells)
    assert not any("Grant Costs" in c for c in cells)


def test_the_charts_names_travel_with_the_run(db_session, seed_accounts):
    assert _chart_labels.get() == ()
    books_check(db_session, None)
    # nothing left behind for the next request on this thread
    assert _chart_labels.get() == ()
    np = Terms("nonprofit")
    # another run's names never reach this one: each context sees its own
    ctx = contextvars.copy_context()
    ctx.run(lambda: _chart_labels.set(("Accrued Job Costs",)))
    assert (
        ctx.run(lambda: _words(np, "to 2010 Accrued Job Costs"))
        == "to 2010 Accrued Job Costs"
    )
    assert _words(np, "to 2010 Accrued Job Costs") == "to 2010 Accrued Grant Costs"
