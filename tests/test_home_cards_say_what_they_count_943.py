"""The Home cards after the 2.32.0 round-8 gate (#943, #944, #945).

#943 (the owner's decision, NEW-234): Total Receivables and Total Payables
keep the agings' totals and count overdue invoices and bills, the documents
themselves, with a label that says so; the Overdue Invoices card lists
invoices, the same pool as the count. A customer-named journal entry is in
the total and on the A/R Aging card, not in the count.

#944 (NEW-235): Books Check leaves an account's name as the chart has it,
"Accrued Job Costs" on a nonprofit too.

#945 (NEW-236): the Cash Position footnote in the company's words."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import books_check

JS = (Path(__file__).resolve().parents[1] / "app/static/js/dashboard.js").read_text()


def _named_entry(db, when, debit_acct, credit_acct, amount, customer_id):
    txn = Transaction(date=when, description="imported", source_type="iif_import")
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


def test_the_count_is_overdue_invoices_and_the_total_is_the_aging(
    client, db_session, seed_accounts
):
    a = seed_accounts
    cust = Customer(name="Ada Okafor", is_active=True)
    db_session.add(cust)
    db_session.flush()
    today = date.today()
    db_session.add(
        Invoice(
            invoice_number="3001",
            customer_id=cust.id,
            status=InvoiceStatus.SENT,
            date=today - timedelta(days=40),
            due_date=today - timedelta(days=10),
            total=Decimal("222.00"),
            balance_due=Decimal("222.00"),
        )
    )
    db_session.commit()
    # a customer-named entry 45 days old: in the total and the aging card,
    # not in the invoice count
    _named_entry(
        db_session, today - timedelta(days=45), a["1100"].id, a["4000"].id, 140, cust.id
    )
    d = client.get(
        "/api/dashboard/data?ids=receivables,overdue_invoices,ar_aging"
    ).json()
    assert d["receivables"] == {"total": 362.0, "overdue_count": 1}
    assert d["overdue_invoices"]["count"] == 1
    assert [i["invoice_number"] for i in d["overdue_invoices"]["items"]] == ["3001"]
    assert d["overdue_invoices"]["items"][0]["days_overdue"] == 10
    assert d["ar_aging"]["d60"] == 140.0 and d["ar_aging"]["total"] == 362.0


def test_the_labels_say_what_each_card_counts():
    rec = JS.split("receivables(d)")[1].split("active_customers(d)")[0]
    assert "word(d.overdue_count, T('invoice'), T('invoices'))" in rec
    assert "word(d.overdue_pledges, 'pledge', 'pledges')" in rec
    pay = JS.split("payables(d)")[1].split("overdue_invoices(d)")[0]
    assert "overdue ${d.overdue_count === 1 ? 'bill' : 'bills'}" in pay
    # the Overdue Invoices card lists invoices again
    over = JS.split("overdue_invoices(d)")[1].split("bank_balances(d)")[0]
    assert "InvoicesPage.view(${i.id})" in over and "i.invoice_number" in over
    assert "Assumes ${T('customers')} pay on the due date." in JS


def test_books_check_keeps_the_charts_account_names_on_a_nonprofit(
    client, db_session, seed_accounts
):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    if not db_session.query(Account).filter(Account.account_number == "2010").first():
        db_session.add(
            Account(
                account_number="2010",
                name="Accrued Job Costs",
                account_type=AccountType.LIABILITY,
                is_active=True,
            )
        )
        db_session.commit()
    rows = {r["key"]: r for r in books_check(db_session, None)["rows"]}
    accruals = next(
        (r for k, r in rows.items() if "accrued" in k or "accrual" in k), None
    )
    assert accruals is not None, list(rows)
    text = " ".join([accruals["name"], accruals["sentence"], accruals["control_label"]])
    assert "Accrued Job Costs" in text and "Grant Costs" not in text, text
    # the rest of the words are still the company's
    assert (
        rows["ar_aging"]["name"].startswith("Pledge")
        or "Pledge" in rows["ar_aging"]["name"]
    )
