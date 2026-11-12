"""The CSV invoice import (#882): invoices from any program, in SlowBooks'
own column layout. The template downloads with the exact columns; a file
of two invoices posts two invoices through the invoice form's own path
(journal and customer balance as a typed invoice's); an unknown customer,
an invoice number already in the books, a bad date and a negative quantity
are refused with the row named and nothing posts; a dry run posts nothing;
a read-only or accountant sign-in is refused, a bookkeeper token is not."""

import csv
import io
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.models.invoices import Invoice
from app.models.transactions import Transaction, TransactionLine
from app.models.users import ROLE_ACCOUNTANT, ROLE_READONLY, User
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = [
    "invoice_number",
    "date",
    "due_date",
    "customer",
    "item",
    "description",
    "quantity",
    "rate",
    "taxable",
    "memo",
    "class",
    "job",
]
HEADER = ",".join(COLUMNS) + "\n"

TWO_INVOICES = HEADER + (
    "R-501,2026-10-05,,Harbor Bakery,DES-HR,,2,85.00,,Lobby sign,,\n"
    "R-501,2026-10-05,,Harbor Bakery,,Trip charge,1,25.00,no,,,\n"
    "R-502,10/07/2026,2026-10-20,Pine Street Clinic,,Window lettering,3,40,,,,\n"
)


def _upload(client, text, dry_run=True, api=None):
    return (api or client).post(
        f"/api/csv/import/invoices?dry_run={'true' if dry_run else 'false'}",
        files={"file": ("invoices.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )


def _setup(client):
    ids = {}
    for name, terms in (("Harbor Bakery", "Net 15"), ("Pine Street Clinic", None)):
        body = {"name": name, **({"terms": terms} if terms else {})}
        r = client.post("/api/customers", json=body)
        assert r.status_code in (200, 201), r.text
        ids[name] = r.json()["id"]
    r = client.post(
        "/api/items",
        json={
            "name": "Design Hour",
            "code": "DES-HR",
            "item_type": "service",
            "rate": 85,
            "description": "Design work by the hour",
        },
    )
    assert r.status_code == 201, r.text
    return ids


def _journal_sums(db_session, account):
    rows = (
        db_session.query(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .filter(TransactionLine.account_id == account.id)
        .all()
    )
    return (
        sum((Decimal(str(r.debit or 0)) for r in rows), Decimal("0")),
        sum((Decimal(str(r.credit or 0)) for r in rows), Decimal("0")),
    )


# ── the template ─────────────────────────────────────────────────────────


def test_the_template_downloads_with_the_exact_columns(client):
    r = client.get("/api/csv/import/invoices/template")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "invoices-template.csv" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == COLUMNS
    assert len(rows) > 1 and all(len(row) == len(COLUMNS) for row in rows)
    # the example shows one invoice of two lines: rows sharing a number
    numbers = [row[0] for row in rows[1:]]
    assert len(set(numbers)) < len(numbers)


def test_the_template_imports_once_its_customers_and_items_exist(client, seed_accounts):
    for name in ("Ines Halvorsen", "Orchard Lane Apartments"):
        client.post("/api/customers", json={"name": name})
    for name, code in (("Design Hour", "DES-HR"), ("Installation Labor", None)):
        body = {"name": name, "item_type": "service", "rate": 50}
        client.post("/api/items", json={**body, **({"code": code} if code else {})})
    text = client.get("/api/csv/import/invoices/template").content.decode("utf-8-sig")
    r = _upload(client, text)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["errors"] == [], out
    assert out["invoices"] == 2 and out["lines"] == 3


# ── a clean file posts ───────────────────────────────────────────────────


def test_two_invoices_post_with_their_lines_and_totals(
    client, seed_accounts, db_session
):
    ids = _setup(client)
    r = _upload(client, TWO_INVOICES, dry_run=False)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["errors"] == [], out
    assert out["posted"] is True and out["dry_run"] is False
    assert out["invoices"] == 2 and out["lines"] == 3
    assert out["customers_matched"] == 2 and out["items_matched"] == 1
    assert out["total"] == "315.00"

    by_number = {i["invoice_number"]: i for i in client.get("/api/invoices").json()}
    assert set(by_number) == {"R-501", "R-502"}
    first = client.get(f"/api/invoices/{by_number['R-501']['id']}").json()
    assert first["customer_id"] == ids["Harbor Bakery"]
    assert first["date"] == "2026-10-05"
    # no due_date: the customer's own terms (Net 15)
    assert first["due_date"] == "2026-10-20" and first["terms"] == "Net 15"
    assert first["notes"] == "Lobby sign"
    assert Decimal(first["total"]) == Decimal("195.00")
    lines = sorted(first["lines"], key=lambda ln: ln["line_order"])
    assert [ln["description"] for ln in lines] == [
        "Design work by the hour",  # the item's description, as the form fills it
        "Trip charge",
    ]
    assert [Decimal(ln["amount"]) for ln in lines] == [
        Decimal("170.00"),
        Decimal("25.00"),
    ]
    assert lines[0]["item_id"] is not None and lines[1]["item_id"] is None
    assert lines[1]["is_taxable"] is False

    second = client.get(f"/api/invoices/{by_number['R-502']['id']}").json()
    assert second["date"] == "2026-10-07" and second["due_date"] == "2026-10-20"
    assert Decimal(second["total"]) == Decimal("120.00")

    # the journal: A/R debited the totals, income credited them (the
    # description-only lines on the default income account, 4000)
    ar_debit, _ = _journal_sums(db_session, seed_accounts["1100"])
    _, income_credit = _journal_sums(db_session, seed_accounts["4000"])
    assert ar_debit == Decimal("315.00")
    assert income_credit == Decimal("315.00")
    # each invoice has its own posting, under its own number
    for inv in db_session.query(Invoice).all():
        assert inv.transaction_id is not None
        txn = db_session.get(Transaction, inv.transaction_id)
        assert inv.invoice_number in (txn.reference or "")

    # the customer balances agree with a typed invoice's
    bakery = client.get(f"/api/customers/{ids['Harbor Bakery']}").json()
    clinic = client.get(f"/api/customers/{ids['Pine Street Clinic']}").json()
    assert Decimal(str(bakery["balance"])) == Decimal("195.00")
    assert Decimal(str(clinic["balance"])) == Decimal("120.00")


def test_the_import_posts_what_the_invoice_form_posts(
    client, seed_accounts, db_session
):
    """The same line typed on the form and imported: the same totals, the
    same tax, the same journal amounts."""
    client.put("/api/settings", json={"default_tax_rate": "8.25"})
    ids = _setup(client)
    typed = client.post(
        "/api/invoices",
        json={
            "customer_id": ids["Pine Street Clinic"],
            "date": "2026-10-07",
            "tax_rate": "0.0825",
            "lines": [{"description": "Window lettering", "quantity": 3, "rate": 40}],
        },
    )
    assert typed.status_code == 201, typed.text
    r = _upload(
        client,
        HEADER + "R-9,2026-10-07,,Pine Street Clinic,,Window lettering,3,40,,,,\n",
        dry_run=False,
    )
    assert r.json()["errors"] == [], r.json()
    imported = next(
        i for i in client.get("/api/invoices").json() if i["invoice_number"] == "R-9"
    )
    for field in ("subtotal", "tax_amount", "total", "balance_due"):
        assert Decimal(str(imported[field])) == Decimal(str(typed.json()[field])), field
    _, tax_credit = _journal_sums(db_session, seed_accounts["2200"])
    assert tax_credit == 2 * Decimal(str(typed.json()["tax_amount"])) > 0


def test_class_and_job_are_found_by_name(client, seed_accounts, db_session):
    ids = _setup(client)
    r = client.post("/api/classes", json={"name": "Signs"})
    assert r.status_code in (200, 201), r.text
    class_id = r.json()["id"]
    r = client.post(
        "/api/jobs", json={"customer_id": ids["Harbor Bakery"], "name": "Storefront"}
    )
    assert r.status_code in (200, 201), r.text
    job_id = r.json()["id"]
    text = HEADER + (
        "R-1,2026-10-01,,Harbor Bakery,,Paint,1,10,,,signs,storefront\n"
        "R-2,2026-10-01,,Pine Street Clinic,,Paint,1,10,,,,Storefront\n"
        "R-3,2026-10-01,,Pine Street Clinic,,Paint,1,10,,,Nope,\n"
    )
    errors = _upload(client, text).json()["errors"]
    assert any(e.startswith("Row 3:") and "jobs" in e for e in errors), errors
    assert any(e.startswith("Row 4:") and "class" in e for e in errors), errors
    out = _upload(
        client, text.splitlines()[0] + "\n" + text.splitlines()[1] + "\n", dry_run=False
    ).json()
    assert out["posted"] is True, out
    inv = db_session.query(Invoice).one()
    assert (inv.class_id, inv.job_id) == (class_id, job_id)
    assert (inv.lines[0].class_id, inv.lines[0].job_id) == (class_id, job_id)


# ── refusals, each with its row ──────────────────────────────────────────


def test_an_unknown_customer_is_refused_and_nothing_posts(client, seed_accounts):
    _setup(client)
    text = TWO_INVOICES + "R-503,2026-10-08,,Nobody Known,,Paint,1,10,,,,\n"
    out = _upload(client, text, dry_run=False).json()
    assert out["posted"] is False
    assert out["customers_unknown"] == ["Nobody Known"]
    assert any(e.startswith("Row 5:") and "Nobody Known" in e for e in out["errors"])
    assert client.get("/api/invoices").json() == []


def test_a_number_already_in_the_books_is_refused_by_number(client, seed_accounts):
    _setup(client)
    assert _upload(client, TWO_INVOICES, dry_run=False).json()["posted"] is True
    again = HEADER + (
        "R-502,2026-10-07,,Pine Street Clinic,,Window lettering,3,40,,,,\n"
        "R-600,2026-10-09,,Pine Street Clinic,,Repairs,1,50,,,,\n"
    )
    out = _upload(client, again, dry_run=False).json()
    assert out["posted"] is False
    assert any(
        e.startswith("Row 2:") and "R-502" in e and "already in the books" in e
        for e in out["errors"]
    ), out["errors"]
    numbers = sorted(i["invoice_number"] for i in client.get("/api/invoices").json())
    assert numbers == ["R-501", "R-502"]  # no copy, and R-600 waits for the fix


def test_a_bad_date_and_a_negative_quantity_are_refused_with_the_row(
    client, seed_accounts
):
    _setup(client)
    text = HEADER + (
        "R-1,2026-13-45,,Harbor Bakery,,Paint,1,10,,,,\n"
        "R-2,2026-10-01,,Harbor Bakery,,Paint,-2,10,,,,\n"
        "R-3,2026-10-01,,Harbor Bakery,,Paint,1,ten,,,,\n"
        "R-4,2026-10-01,someday,Harbor Bakery,,Paint,1,10,,,,\n"
        "R-5,2026-10-01,,Harbor Bakery,,Paint,1,10,maybe,,,\n"
        "R-6,2026-10-01,,Harbor Bakery,NOPE,Paint,1,10,,,,\n"
    )
    out = _upload(client, text, dry_run=False).json()
    errors = out["errors"]
    assert out["posted"] is False
    for row, words in (
        (2, "is not a date"),
        (3, "negative"),
        (4, "is not a number"),
        (5, "due_date"),
        (6, "taxable"),
        (7, "Items list"),
    ):
        assert any(e.startswith(f"Row {row}:") and words in e for e in errors), (
            row,
            errors,
        )
    assert client.get("/api/invoices").json() == []


def test_rows_of_one_invoice_must_agree_on_its_customer_and_date(client, seed_accounts):
    _setup(client)
    text = HEADER + (
        "R-1,2026-10-01,,Harbor Bakery,,Paint,1,10,,,,\n"
        "R-1,2026-10-02,,Pine Street Clinic,,Paint,1,10,,,,\n"
    )
    errors = _upload(client, text).json()["errors"]
    assert any(e.startswith("Row 3: date differs from row 2") for e in errors), errors
    assert any(e.startswith("Row 3: customer differs") for e in errors), errors


def test_the_invoice_path_refusals_are_reported_with_the_row(
    client, seed_accounts, db_session
):
    """What the invoice form itself refuses (here a due date before the
    invoice's date) comes back against the invoice's first row."""
    _setup(client)
    text = HEADER + "R-1,2026-10-10,2026-10-01,Harbor Bakery,,Paint,1,10,,,,\n"
    out = _upload(client, text, dry_run=False).json()
    assert out["posted"] is False
    assert len(out["errors"]) == 1 and out["errors"][0].startswith("Row 2:"), out
    assert db_session.query(Invoice).count() == 0


def test_a_file_without_the_columns_says_so(client, seed_accounts):
    out = _upload(client, "Number,Client,Amount\n1,A,5\n").json()
    assert out["invoices"] == 0
    assert len(out["errors"]) == 1 and "invoice_number" in out["errors"][0]


# ── the dry run ──────────────────────────────────────────────────────────


def test_the_dry_run_reports_and_posts_nothing(client, seed_accounts, db_session):
    _setup(client)
    r = _upload(client, TWO_INVOICES)  # dry_run is the default
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["dry_run"] is True and out["posted"] is False
    assert out["errors"] == []
    assert (out["invoices"], out["lines"]) == (2, 3)
    assert (out["customers_matched"], out["items_matched"]) == (2, 1)
    assert out["total"] == "315.00"
    assert [d["invoice_number"] for d in out["documents"]] == ["R-501", "R-502"]
    assert [d["total"] for d in out["documents"]] == ["195.00", "120.00"]
    assert db_session.query(Invoice).count() == 0
    assert db_session.query(Transaction).count() == 0
    # and the real import afterwards posts the same
    assert _upload(client, TWO_INVOICES, dry_run=False).json()["total"] == "315.00"
    assert db_session.query(Invoice).count() == 2


def test_the_dry_run_with_no_query_is_a_dry_run(client, seed_accounts, db_session):
    _setup(client)
    r = client.post(
        "/api/csv/import/invoices",
        files={"file": ("i.csv", TWO_INVOICES.encode(), "text/csv")},
    )
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert db_session.query(Invoice).count() == 0


# ── who may run it ───────────────────────────────────────────────────────


def _login_as_role(client, db_session, username, role):
    db_session.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password("role-password-123"),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": "role-password-123"},
    )
    assert r.status_code == 200, r.text


def test_a_read_only_sign_in_is_refused(client, seed_accounts, db_session):
    _setup(client)
    _login_as_role(client, db_session, "viewer", ROLE_READONLY)
    assert _upload(client, TWO_INVOICES).status_code == 403
    assert _upload(client, TWO_INVOICES, dry_run=False).status_code == 403
    assert db_session.query(Invoice).count() == 0


def test_an_accountant_sign_in_is_refused(client, seed_accounts, db_session):
    _setup(client)
    _login_as_role(client, db_session, "cpa", ROLE_ACCOUNTANT)
    assert _upload(client, TWO_INVOICES, dry_run=False).status_code == 403
    assert db_session.query(Invoice).count() == 0


def test_a_bookkeeper_token_may_run_it(client, seed_accounts, db_session):
    _setup(client)
    r = client.post("/api/tokens", json={"label": "import-bot", "role": "bookkeeper"})
    assert r.status_code == 201, r.text
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {r.json()['token']}"
    out = _upload(client, TWO_INVOICES, dry_run=False, api=api)
    assert out.status_code == 200, out.text
    assert out.json()["posted"] is True
    assert db_session.query(Invoice).count() == 2


# ── the page and the docs ────────────────────────────────────────────────


def test_the_page_offers_invoices_and_lists_every_column():
    src = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "<option value=\"invoices\">${T('Invoices')}</option>" in src
    assert "/api/csv/import/invoices/template" in src
    start = src.index("invoices: {", src.index("_CSV_IMPORT_COLUMNS"))
    block = src[start : src.index("            ],", start)]
    for column in COLUMNS:
        assert column in block, column


def test_the_docs_name_the_routes():
    doc = (ROOT / "docs/features.md").read_text(encoding="utf-8")
    assert "/api/csv/import/invoices" in doc
    assert "/api/csv/import/invoices/template" in doc
