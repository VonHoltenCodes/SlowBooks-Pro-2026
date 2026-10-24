"""The dashboard Payables card nets vendor credits (#495).

The card summed open Bill.balance_due by itself, so a $100 bill with a $30
unapplied vendor credit read $100 against an A/P Aging total of $70. Both
now read the A/P Aging report's TOTAL row, as the Receivables card already
reads A/R Aging, so the card and the report can't disagree.
"""

from datetime import date

import pytest

from app.models.contacts import Vendor


@pytest.fixture
def supplier(db_session):
    v = Vendor(name="Supplier Co", is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


def _expense_account(seed_accounts):
    return next(a for a in seed_accounts.values() if a.account_type.value == "expense")


def _bill(client, vendor_id, account_id, amount, number, date="2026-03-14"):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": date,
            "lines": [
                {
                    "description": "Parked",
                    "quantity": 1,
                    "rate": amount,
                    "account_id": account_id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _vendor_credit(client, vendor_id, account_id, amount, date="2026-03-14"):
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor_id,
            "date": date,
            "lines": [
                {
                    "description": "Credit",
                    "quantity": 1,
                    "rate": amount,
                    "account_id": account_id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_payables_card_nets_vendor_credit(client, db_session, seed_accounts, supplier):
    from app.routes.reports.payables_tax import ap_aging_report
    from app.services.dashboard_widgets import payables

    expense = _expense_account(seed_accounts)
    _bill(client, supplier.id, expense.id, 100, "B-100")
    _vendor_credit(client, supplier.id, expense.id, 30)

    card_total = payables(db_session)["total"]
    ap_total = float(ap_aging_report(db_session, date.today())["totals"]["total"])

    assert card_total == pytest.approx(70.0)
    assert card_total == pytest.approx(ap_total)
