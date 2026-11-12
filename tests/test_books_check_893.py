"""Books Check (#893): every subledger beside its control account.

Reports → Books Check sets each subledger figure beside its control
account's ledger balance as of a date: the A/R and A/P agings and the
customer and vendor balances, the payments waiting to be deposited, the
inventory valuation, the unpaid payroll liabilities, sales tax collected
less paid, construction mode's retainage, and the journal's own checks
(every transaction balances, no line on a missing account, a voided
document posts nothing live, no posted document lacks its entry). Each row
is PASS or a difference with a sentence and the documents behind it.

The books every pair agrees on are the bakery's (tests/bakery_books.py),
entered through the API, with its pay run processed and part paid, a sales
tax payment, a voided payment and bill, and its job cost entry (which
credits Accounts Payable with no vendor, a real difference the check names)
voided. Every seeded difference is a direct database write, never the API,
so the check finds what the posting code would refuse to make.

PDFs are read as the HTML they are rendered from (render_pdf replaced), so
nothing here needs WeasyPrint.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceStatus
from app.models.items import Item
from app.models.transactions import Transaction, TransactionLine
from app.models.users import ROLE_ACCOUNTANT, ROLE_READONLY, User
from app.services import auth as auth_service
from app.services import dashboard_widgets, pdf_service
from app.services.books_check import books_check

ROOT = Path(__file__).resolve().parents[1]
REPORTS_JS = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
DASHBOARD_JS = (ROOT / "app/static/js/dashboard.js").read_text(encoding="utf-8")
TODAY = date.today()


def _ok(r, codes=(200, 201)):
    assert r.status_code in codes, r.text
    return r.json()


@pytest.fixture
def agreeing(client, books, db_session, seed_accounts):
    """The bakery's books with every pair in agreement."""
    a = {n: acct.id for n, acct in seed_accounts.items()}
    _ok(client.post(f"/api/job-costs/{books['job_cost']}/void"))
    _ok(client.post(f"/api/payroll/{books['pay_run']}/process"))
    # part of the federal withholding deposited
    _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-09-30",
                "description": "941 deposit",
                "lines": [
                    {"account_id": a["2310"], "debit": "40.00", "credit": "0"},
                    {"account_id": a["1000"], "debit": "0", "credit": "40.00"},
                ],
            },
        )
    )
    _ok(
        client.post(
            "/api/reports/sales-tax/pay",
            json={
                "date": "2026-09-30",
                "amount": "1.00",
                "pay_from_account_id": a["1000"],
            },
        )
    )
    paid = _ok(
        client.post(
            "/api/payments",
            json={
                "customer_id": books["customer2"],
                "date": "2026-09-24",
                "amount": 20,
                "deposit_to_account_id": a["1000"],
                "allocations": [{"invoice_id": books["invoice2"], "amount": 20}],
            },
        )
    )
    _ok(client.post(f"/api/payments/{paid['id']}/void"))
    bill = _ok(
        client.post(
            "/api/bills",
            json={
                "vendor_id": books["vendor"],
                "date": "2026-09-26",
                "bill_number": "VOID-1",
                "lines": [
                    {
                        "account_id": a["6000"],
                        "description": "x",
                        "quantity": 1,
                        "rate": 75,
                    }
                ],
            },
        )
    )
    _ok(client.post(f"/api/bills/{bill['id']}/void"))
    # an inventory sale, voided: the stock and its cost come back
    sale = _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": books["customer"],
                "date": "2026-09-27",
                "tax_rate": 0.0825,
                "lines": [
                    {
                        "item_id": books["loaf"],
                        "description": "Sourdough Loaf",
                        "quantity": 4,
                        "rate": 8.5,
                        "is_taxable": True,
                    }
                ],
            },
        )
    )
    _ok(client.post(f"/api/invoices/{sale['id']}/void"))
    # a second pay run, processed and voided
    run = _ok(
        client.post(
            "/api/payroll",
            json={
                "period_start": "2026-09-15",
                "period_end": "2026-09-28",
                "pay_date": "2026-10-03",
                "stubs": [{"employee_id": books["employee"]}],
            },
        )
    )
    _ok(client.post(f"/api/payroll/{run['id']}/process"))
    _ok(client.post(f"/api/payroll/{run['id']}/void"))
    db_session.expire_all()
    return books


def _rows(db_session, as_of=None):
    db_session.expire_all()
    return {r["key"]: r for r in books_check(db_session, as_of)["rows"]}


def _entry(db_session, lines, source_type="manual", when=date(2026, 9, 27)):
    """A journal transaction written straight to the database."""
    txn = Transaction(date=when, description="seeded", source_type=source_type)
    db_session.add(txn)
    db_session.flush()
    for account_id, debit, credit in lines:
        db_session.add(
            TransactionLine(
                transaction_id=txn.id,
                account_id=account_id,
                debit=Decimal(debit),
                credit=Decimal(credit),
            )
        )
    db_session.commit()
    return txn


def _only_difference(rows, *keys):
    """The rows named differ; every other row that was checked agrees."""
    for k, r in rows.items():
        if k in keys:
            assert r["status"] == "difference", r
        else:
            assert r["status"] in ("pass", "not_checked"), r


def _names(row, kind):
    return [d for d in row["documents"] if d["type"] == kind]


# ── every pair agrees ────────────────────────────────────────────────────


def test_every_pair_agrees_on_the_bakery_books(client, agreeing, db_session):
    data = books_check(db_session)
    rows = {r["key"]: r for r in data["rows"]}
    assert set(rows) == {
        "ar_aging",
        "customer_balances",
        "ap_aging",
        "vendor_balances",
        "undeposited_funds",
        "inventory",
        "payroll_liabilities",
        "sales_tax",
        "journal_balanced",
        "journal_accounts",
        "journal_voids",
        "journal_entries",
    }
    for r in data["rows"]:
        assert r["status"] == "pass", r
        assert r["ok"] is True and r["documents"] == []
        assert r["sentence"]
    assert data["differences"] == 0
    assert data["summary"] == "Your books agree with themselves as of today."
    # the figures are the reports' own, and are not zero: something was tied
    assert rows["ar_aging"]["subledger"] == rows["ar_aging"]["control"] > 0
    assert rows["payroll_liabilities"]["control"] > 0
    assert rows["inventory"]["subledger"] > 0
    ar = client.get(f"/api/reports/ar-aging?as_of_date={TODAY}").json()
    assert rows["ar_aging"]["subledger"] == ar["totals"]["total"]
    ap = client.get(f"/api/reports/ap-aging?as_of_date={TODAY}").json()
    assert rows["ap_aging"]["subledger"] == ap["totals"]["total"]
    # the widget says the same
    widget = dashboard_widgets.build(db_session, ["books_check"])["books_check"]
    assert widget["differences"] == 0
    assert widget["summary"] == "Your books agree with themselves as of today."


def test_the_bakery_job_cost_entry_on_accounts_payable_is_named(
    client, books, db_session
):
    """Before the fixture voids it: the job cost entry credits 2000 with no
    vendor, so the A/P Aging is $300 short of Accounts Payable, and the row
    names the entry."""
    rows = _rows(db_session)
    ap = rows["ap_aging"]
    assert ap["status"] == "difference" and ap["difference"] == -300.0
    named = _names(ap, "job_cost")
    assert [d["id"] for d in named] == [books["job_cost"]]
    assert named[0]["amount"] == 300.0
    assert "job cost" in named[0]["why"]


def test_a_past_date_ties_the_dated_pairs_and_says_why_the_rest_wait(
    agreeing, db_session
):
    rows = _rows(db_session, date(2026, 9, 25))
    for key in (
        "customer_balances",
        "vendor_balances",
        "undeposited_funds",
        "inventory",
    ):
        assert rows[key]["status"] == "not_checked"
        assert "today only" in rows[key]["sentence"]
    for key in ("ar_aging", "ap_aging", "sales_tax", "journal_balanced"):
        assert rows[key]["status"] == "pass", rows[key]


# ── one seeded difference per pair ───────────────────────────────────────


def test_a_journal_entry_on_accounts_receivable_is_named(
    agreeing, db_session, seed_accounts
):
    a = seed_accounts
    txn = _entry(db_session, [(a["1100"].id, "100", "0"), (a["4000"].id, "0", "100")])
    rows = _rows(db_session)
    _only_difference(rows, "ar_aging", "customer_balances")
    ar = rows["ar_aging"]
    assert ar["difference"] == -100.0 and ar["control"] - ar["subledger"] == 100.0
    assert "Look at the documents below" in ar["sentence"]
    assert [(d["type"], d["id"], d["amount"]) for d in ar["documents"]] == [
        ("journal", txn.id, 100.0)
    ]
    assert _names(rows["customer_balances"], "journal")


def test_an_invoice_whose_balance_drifted_is_named(agreeing, db_session):
    inv = db_session.get(Invoice, agreeing["invoice2"])
    inv.balance_due = Decimal(str(inv.balance_due)) + Decimal("50")
    db_session.commit()
    rows = _rows(db_session)
    _only_difference(rows, "ar_aging", "customer_balances")
    named = _names(rows["ar_aging"], "invoice")
    assert [(d["id"], d["amount"]) for d in named] == [(inv.id, 50.0)]
    assert rows["ar_aging"]["difference"] == 50.0
    # the customers' balances read the same balance due, so they agree with
    # the aging and differ from the ledger: that row names the invoice too
    assert [d["id"] for d in _names(rows["customer_balances"], "invoice")] == [inv.id]


def test_a_journal_entry_on_accounts_payable_is_named(
    agreeing, db_session, seed_accounts
):
    a = seed_accounts
    txn = _entry(db_session, [(a["6000"].id, "60", "0"), (a["2000"].id, "0", "60")])
    rows = _rows(db_session)
    _only_difference(rows, "ap_aging", "vendor_balances")
    ap = rows["ap_aging"]
    assert ap["difference"] == -60.0
    assert [(d["type"], d["id"], d["amount"]) for d in ap["documents"]] == [
        ("journal", txn.id, 60.0)
    ]


def test_a_purchase_line_on_undeposited_funds_is_named(
    agreeing, db_session, seed_accounts
):
    a = seed_accounts
    txn = _entry(
        db_session,
        [(a["1200"].id, "35", "0"), (a["1000"].id, "0", "35")],
        source_type="expense",
    )
    rows = _rows(db_session)
    _only_difference(rows, "undeposited_funds")
    uf = rows["undeposited_funds"]
    assert uf["difference"] == -35.0
    assert [(d["type"], d["id"]) for d in uf["documents"]] == [("expense", txn.id)]


def test_an_item_whose_count_drifted_from_its_movements_is_named(agreeing, db_session):
    item = db_session.get(Item, agreeing["loaf"])
    item.quantity_on_hand = Decimal(str(item.quantity_on_hand)) + Decimal("5")
    db_session.commit()
    rows = _rows(db_session)
    _only_difference(rows, "inventory")
    inv = rows["inventory"]
    assert inv["subledger"] > inv["control"]
    assert [d["id"] for d in _names(inv, "item")] == [item.id]


def test_a_credit_to_a_payroll_liability_outside_a_pay_run_is_named(
    agreeing, db_session, seed_accounts
):
    a = seed_accounts
    txn = _entry(db_session, [(a["6000"].id, "25", "0"), (a["2310"].id, "0", "25")])
    rows = _rows(db_session)
    _only_difference(rows, "payroll_liabilities")
    pr = rows["payroll_liabilities"]
    assert pr["difference"] == -25.0
    assert [(d["type"], d["id"], d["amount"]) for d in pr["documents"]] == [
        ("journal", txn.id, 25.0)
    ]


def test_a_pay_run_whose_entry_disagrees_with_its_stubs_is_named(
    agreeing, db_session, seed_accounts
):
    from app.models.payroll import PayRun

    run = db_session.get(PayRun, agreeing["pay_run"])
    line = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == run.transaction_id,
            TransactionLine.account_id == seed_accounts["2310"].id,
        )
        .one()
    )
    line.credit = Decimal(str(line.credit)) - Decimal("10")
    wages = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == run.transaction_id,
            TransactionLine.debit > 0,
        )
        .first()
    )
    wages.debit = Decimal(str(wages.debit)) - Decimal("10")  # still balances
    db_session.commit()
    rows = _rows(db_session)
    pr = rows["payroll_liabilities"]
    assert pr["status"] == "difference" and pr["difference"] == 10.0
    assert [(d["type"], d["id"], d["amount"]) for d in pr["documents"]] == [
        ("pay_run", run.id, -10.0)
    ]


def test_a_journal_entry_on_sales_tax_payable_is_named(
    agreeing, db_session, seed_accounts
):
    a = seed_accounts
    txn = _entry(db_session, [(a["1000"].id, "7", "0"), (a["2200"].id, "0", "7")])
    rows = _rows(db_session)
    _only_difference(rows, "sales_tax")
    st = rows["sales_tax"]
    assert st["difference"] == -7.0
    assert [(d["type"], d["id"]) for d in st["documents"]] == [("journal", txn.id)]


def test_an_invoice_whose_tax_entry_differs_is_named(
    agreeing, db_session, seed_accounts
):
    receipt = db_session.get(Invoice, agreeing["receipt"])
    assert receipt.tax_amount > 0
    line = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == receipt.transaction_id,
            TransactionLine.account_id == seed_accounts["2200"].id,
        )
        .one()
    )
    income = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == receipt.transaction_id,
            TransactionLine.account_id == seed_accounts["4000"].id,
        )
        .first()
        or db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == receipt.transaction_id,
            TransactionLine.credit > 0,
            TransactionLine.id != line.id,
        )
        .first()
    )
    line.credit = Decimal(str(line.credit)) - Decimal("1")
    income.credit = Decimal(str(income.credit)) + Decimal("1")
    db_session.commit()
    rows = _rows(db_session)
    st = rows["sales_tax"]
    assert st["status"] == "difference" and st["difference"] == 1.0
    assert [d["id"] for d in _names(st, "invoice")] == [receipt.id]


def test_construction_retainage_off_its_documents_names_the_job(
    client, agreeing, db_session
):
    from app.services.accounting import ensure_construction_accounts
    from app.services.settings_service import set_setting

    set_setting(db_session, "company_type", "construction")
    accts = ensure_construction_accounts(db_session)
    db_session.commit()
    rows = _rows(db_session)
    assert rows["retainage_receivable"]["status"] == "pass"
    assert rows["retainage_payable"]["status"] == "pass"

    income = db_session.query(Account).filter(Account.account_number == "4000").one()
    txn = _entry(db_session, [(accts["1150"].id, "90", "0"), (income.id, "0", "90")])
    txn.job_id = agreeing["job"]
    db_session.commit()
    rows = _rows(db_session)
    _only_difference(rows, "retainage_receivable")
    rr = rows["retainage_receivable"]
    assert rr["control"] == 90.0 and rr["subledger"] == 0.0
    assert [(d["type"], d["id"], d["amount"]) for d in rr["documents"]] == [
        ("job", agreeing["job"], 90.0)
    ]


def test_retainage_rows_are_construction_only(agreeing, db_session):
    assert "retainage_receivable" not in _rows(db_session)


# ── the journal's own checks ─────────────────────────────────────────────


def test_an_unbalanced_transaction_is_named(agreeing, db_session, seed_accounts):
    a = seed_accounts
    txn = _entry(db_session, [(a["6000"].id, "12", "0"), (a["1000"].id, "0", "10")])
    rows = _rows(db_session)
    _only_difference(rows, "journal_balanced")
    jb = rows["journal_balanced"]
    assert jb["difference"] == 2.0
    assert [(d["type"], d["id"], d["amount"]) for d in jb["documents"]] == [
        ("journal", txn.id, 2.0)
    ]
    assert "One document is out of balance" in jb["sentence"]


def test_a_line_on_a_deleted_account_is_named(agreeing, db_session, seed_accounts):
    a = seed_accounts
    gone = Account(
        account_number="6999", name="Gone", account_type=a["6000"].account_type
    )
    db_session.add(gone)
    db_session.flush()
    txn = _entry(db_session, [(gone.id, "15", "0"), (a["1000"].id, "0", "15")])
    gone_id = gone.id
    db_session.execute(Account.__table__.delete().where(Account.id == gone_id))
    db_session.commit()
    rows = _rows(db_session)
    ja = rows["journal_accounts"]
    assert ja["status"] == "difference" and ja["difference"] == 15.0
    assert [(d["type"], d["id"]) for d in ja["documents"]] == [("journal", txn.id)]
    assert str(gone_id) in ja["documents"][0]["why"]


def test_a_voided_invoice_with_a_live_line_is_named(client, agreeing, db_session):
    inv = db_session.get(Invoice, agreeing["invoice2"])
    inv.status = InvoiceStatus.VOID  # void with no reversing entry
    db_session.commit()
    rows = _rows(db_session)
    jv = rows["journal_voids"]
    assert jv["status"] == "difference"
    assert [(d["type"], d["id"]) for d in jv["documents"]] == [("invoice", inv.id)]
    assert "still leave" in jv["documents"][0]["why"]
    # the aging drops a void invoice and the ledger still has it
    assert rows["ar_aging"]["status"] == "difference"


def test_a_posted_invoice_with_no_entry_is_named(agreeing, db_session):
    inv = db_session.get(Invoice, agreeing["invoice2"])
    inv.transaction_id = None
    db_session.commit()
    rows = _rows(db_session)
    je = rows["journal_entries"]
    assert je["status"] == "difference"
    assert [(d["type"], d["id"]) for d in je["documents"]] == [("invoice", inv.id)]


def test_the_widget_counts_the_differences(agreeing, db_session, seed_accounts):
    a = seed_accounts
    _entry(db_session, [(a["6000"].id, "12", "0"), (a["1000"].id, "0", "10")])
    _entry(db_session, [(a["1000"].id, "7", "0"), (a["2200"].id, "0", "7")])
    db_session.expire_all()
    w = dashboard_widgets.build(db_session, ["books_check"])["books_check"]
    assert w["differences"] == 2
    assert w["summary"] == "2 differences — open Books Check."
    assert len(w["failing"]) == 2


# ── the routes ───────────────────────────────────────────────────────────


@pytest.fixture
def as_html(monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


def _login_as(client, username, password, role, db_session):
    db_session.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password(password),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    signed_in = TestClient(app)
    r = signed_in.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return signed_in


def test_the_three_routes_answer_every_reading_role(
    client, seed_accounts, db_session, as_html
):
    tok = _ok(
        client.post("/api/tokens", json={"label": "keeper", "role": "bookkeeper"})
    )
    keeper = TestClient(app)
    keeper.headers["Authorization"] = f"Bearer {tok['token']}"
    readers = {
        "administrator": client,
        "bookkeeper token": keeper,
        "accountant": _login_as(
            client, "cpa", "cpa-password-123", ROLE_ACCOUNTANT, db_session
        ),
        "read-only": _login_as(
            client, "viewer", "viewer-password-1", ROLE_READONLY, db_session
        ),
    }
    for who, c in readers.items():
        for path in ("", "/pdf", "/csv"):
            r = c.get(f"/api/reports/books-check{path}?as_of={TODAY}")
            assert r.status_code == 200, (who, path, r.text)
    # a misspelled filter is refused with the accepted names
    r = client.get("/api/reports/books-check?as_of_date=2026-01-01")
    assert r.status_code == 422 and "as_of" in r.json()["detail"]
    # the Print page beside the PDF (#683)
    assert (
        client.get(f"/api/reports/books-check/print?as_of={TODAY}").status_code == 200
    )


def test_the_pdf_and_csv_carry_the_rows(
    client, agreeing, db_session, seed_accounts, as_html
):
    a = seed_accounts
    txn = _entry(db_session, [(a["1100"].id, "100", "0"), (a["4000"].id, "0", "100")])
    data = client.get("/api/reports/books-check").json()
    html = client.get("/api/reports/books-check/pdf").content.decode()
    csv_text = client.get("/api/reports/books-check/csv").text
    assert "Books Check" in html and "Books Check" in csv_text
    for r in data["rows"]:
        assert r["name"].replace("&", "&amp;") in html
        assert r["name"] in csv_text
    assert "PASS" in html and "Difference" in html
    assert "PASS" in csv_text and "Difference" in csv_text
    # the named document is in both
    assert f"entry {txn.id}" in html and f"entry {txn.id}" in csv_text
    assert data["summary"] in csv_text


def test_the_route_reads_today_by_default(client, seed_accounts):
    data = client.get("/api/reports/books-check").json()
    assert data["as_of"] == TODAY.isoformat() and data["basis"] == "accrual"
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert (
        client.get(f"/api/reports/books-check?as_of={yesterday}").json()["as_of"]
        == yesterday
    )


# ── the widget and the page ──────────────────────────────────────────────


def test_the_widget_is_in_the_catalog_and_every_default_layout():
    from app.services.terminology import Terms

    for mode in ("business", "nonprofit", "construction"):
        t = Terms(mode)
        assert "books_check" in [w["id"] for w in dashboard_widgets.catalog(t)]
        assert "books_check" in dashboard_widgets.default_layout(t)
    title = next(w for w in dashboard_widgets.catalog() if w["id"] == "books_check")[
        "title"
    ]
    assert title == "Books Check"
    assert "books_check(d)" in DASHBOARD_JS
    assert "DashboardPage._tileLink('books-check')" in DASHBOARD_JS


def test_the_report_center_registers_the_view_and_the_card():
    assert (
        "'books-check':          { label: 'Books Check',        open: (p) => ReportsPage.booksCheck(p), asOf: true }"
        in REPORTS_JS
    )
    assert "card('books-check', 'Books Check'" in REPORTS_JS
    assert "/reports/books-check?as_of=" in REPORTS_JS
    # PASS in green, a difference in the theme's warning colour
    assert 'var(--text-success);">PASS' in REPORTS_JS
    assert 'var(--text-warning);">Difference' in REPORTS_JS


def test_none_of_the_other_programs_names_are_used():
    """The feature has a name of its own (owner's rule)."""
    src = (ROOT / "app/services/books_check.py").read_text(encoding="utf-8")
    route = (ROOT / "app/routes/reports/books.py").read_text(encoding="utf-8")
    for text in (src, route):
        for word in ("Verify Data", "Rebuild Data", "Audit Log"):
            assert word not in text
