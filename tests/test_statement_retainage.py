"""A customer statement shows held retainage on its own line (persona acct
F1): the balance due is what A/R Aging says, and the retainage the invoices
hold back is shown apart as not yet due."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus


@pytest.fixture
def lakeside(db_session):
    c = Customer(name="Lakeside Homes", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


def _invoice(db, cust, number, day, total, held=0, released=0):
    inv = Invoice(
        invoice_number=number,
        customer_id=cust.id,
        status=InvoiceStatus.SENT,
        date=day,
        total=Decimal(total),
        subtotal=Decimal(total),
        retainage_held=Decimal(held),
        retainage_released=Decimal(released),
        balance_due=Decimal(total) - Decimal(held) + Decimal(released),
    )
    db.add(inv)
    db.commit()
    return inv


def test_retainage_is_its_own_line_and_balance_due_ties_to_aging(
    client, db_session, seed_accounts, lakeside
):
    from app.routes.reports.receivables import statement_activity

    _invoice(db_session, lakeside, "1001", date(2026, 8, 1), "10000", held="1000")
    act = statement_activity(db_session, lakeside, date(2026, 9, 30))
    assert act["lines"][0]["amount"] == Decimal("9000.00")
    assert "$1,000.00 retainage held" in act["lines"][0]["description"]
    assert act["balance_due"] == Decimal("9000.00")
    assert act["retainage_held"] == Decimal("1000.00")
    aging = client.get("/api/reports/ar-aging?as_of_date=2026-09-30").json()
    assert Decimal(str(aging["totals"]["total"])) == act["balance_due"]

    # a release invoice of 400 comes due; 600 is still held
    _invoice(db_session, lakeside, "1002", date(2026, 9, 15), "0", released="400")
    act = statement_activity(db_session, lakeside, date(2026, 9, 30))
    assert act["balance_due"] == Decimal("9400.00")
    assert act["retainage_held"] == Decimal("600.00")
    assert act["lines"][1]["amount"] == Decimal("400.00")
    aging = client.get("/api/reports/ar-aging?as_of_date=2026-09-30").json()
    assert Decimal(str(aging["totals"]["total"])) == act["balance_due"]


def test_the_pdf_prints_the_retainage_line_only_when_there_is_some(
    db_session, seed_accounts, lakeside
):
    from app.routes.reports.receivables import statement_activity
    from app.services.pdf_service import _render
    from app.services.settings_service import get_all_settings

    def html(act):
        return _render(
            "statement_pdf.html",
            get_all_settings(db_session),
            customer=lakeside,
            activity=act,
            as_of_date=date(2026, 9, 30),
        )

    plain = statement_activity(db_session, lakeside, date(2026, 9, 30))
    assert "Retainage held, not yet due" not in html(plain)
    _invoice(db_session, lakeside, "1001", date(2026, 8, 1), "10000", held="1000")
    held = statement_activity(db_session, lakeside, date(2026, 9, 30))
    assert "Retainage held, not yet due" in html(held)
