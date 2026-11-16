"""Every receivables and payables number on Home reads the aging (#935,
2.32.0 round 7, NEW-232 / W-156): Total Receivables' overdue count, the
Overdue card's list and the A/R Aging card are one pool, so "1 overdue"
never sits beside "Nothing overdue." A customer-named journal entry is past
due from its first day, as the aging's 1-30 column has it."""

from datetime import date, timedelta
from decimal import Decimal

from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice, InvoiceStatus
from app.models.transactions import Transaction, TransactionLine


def _entry(
    db, when, debit_acct, credit_acct, amount, *, vendor_id=None, customer_id=None
):
    txn = Transaction(
        date=when, description="imported", source_type="iif_import", vendor_id=vendor_id
    )
    db.add(txn)
    db.flush()
    db.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=debit_acct,
                debit=amount,
                credit=0,
                customer_id=customer_id,
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=credit_acct,
                debit=0,
                credit=amount,
                customer_id=customer_id,
            ),
        ]
    )
    db.commit()
    return txn


def _cards(client, ids):
    r = client.get(f"/api/dashboard/data?ids={','.join(ids)}")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_count_the_list_and_the_aging_card_agree(client, db_session, seed_accounts):
    a = seed_accounts
    ada = Customer(name="Ada Okafor", is_active=True)
    db_session.add(ada)
    db_session.commit()
    today = date.today()
    # a customer-named entry 45 days old: the aging's 31-60
    _entry(
        db_session,
        today - timedelta(days=45),
        a["1100"].id,
        a["4000"].id,
        140,
        customer_id=ada.id,
    )
    d = _cards(client, ["receivables", "overdue_invoices", "ar_aging"])
    assert d["receivables"] == {"total": 140.0, "overdue_count": 1}
    assert d["overdue_invoices"]["count"] == 1
    assert d["overdue_invoices"]["items"] == [
        {"id": ada.id, "customer": "Ada Okafor", "past_due": 140.0, "oldest": "31-60"}
    ]
    assert d["ar_aging"]["d60"] == 140.0


def test_an_entry_in_the_agings_1_30_counts_and_one_dated_today_does_not(
    client, db_session, seed_accounts
):
    a = seed_accounts
    ben = Customer(name="Ben Reyes", is_active=True)
    cy = Customer(name="Cy Lindqvist", is_active=True)
    db_session.add_all([ben, cy])
    db_session.commit()
    today = date.today()
    _entry(
        db_session,
        today - timedelta(days=15),
        a["1100"].id,
        a["4000"].id,
        115,
        customer_id=ben.id,
    )
    _entry(db_session, today, a["1100"].id, a["4000"].id, 50, customer_id=cy.id)
    d = _cards(client, ["receivables", "overdue_invoices"])
    assert d["receivables"]["overdue_count"] == 1
    assert [i["customer"] for i in d["overdue_invoices"]["items"]] == ["Ben Reyes"]
    assert d["overdue_invoices"]["items"][0]["oldest"] == "1-30"


def test_a_past_due_invoice_lists_by_its_customer(client, db_session, seed_accounts):
    cust = Customer(name="Salt & Pine", is_active=True)
    db_session.add(cust)
    db_session.flush()
    today = date.today()
    db_session.add(
        Invoice(
            invoice_number="2001",
            customer_id=cust.id,
            status=InvoiceStatus.SENT,
            date=today - timedelta(days=40),
            due_date=today - timedelta(days=10),
            total=Decimal("222.00"),
            balance_due=Decimal("222.00"),
        )
    )
    db_session.commit()
    d = _cards(client, ["receivables", "overdue_invoices"])
    assert d["receivables"]["overdue_count"] == 1
    assert d["overdue_invoices"]["items"] == [
        {"id": cust.id, "customer": "Salt & Pine", "past_due": 222.0, "oldest": "1-30"}
    ]


def test_the_payables_count_is_the_vendors_the_aging_puts_past_due(
    client, db_session, seed_accounts
):
    a = seed_accounts
    vendor = Vendor(name="Brookside Nursery", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    today = date.today()
    _entry(
        db_session,
        today - timedelta(days=15),
        a["6000"].id,
        a["2000"].id,
        215,
        vendor_id=vendor.id,
    )
    assert _cards(client, ["payables"])["payables"] == {
        "total": 215.0,
        "overdue_count": 1,
    }
    _entry(
        db_session,
        today - timedelta(days=2),
        a["2000"].id,
        a["1000"].id,
        215,
        vendor_id=vendor.id,
    )
    assert _cards(client, ["payables"])["payables"] == {
        "total": 0.0,
        "overdue_count": 0,
    }


def test_the_card_opens_the_customer_and_the_aging():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1] / "app/static/js/dashboard.js"
    ).read_text()
    assert "App.navigate('#/customers/${i.id}')" in js
    assert "DashboardPage._tileLink('ar-aging')" in js
    assert (
        "InvoicesPage.view(${i.id})"
        not in js.split("overdue_invoices(d)")[1].split("bank_balances(d)")[0]
    )
