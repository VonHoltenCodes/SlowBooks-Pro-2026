"""A credit memo's lines carry their own class and job (#640, from
Discussion #633).

A memo's lines all posted with the header's class, so a credit against work
in two divisions had to be two memos. Each line now takes a class and a job
of its own, as an invoice line does: the reversal of that line's income
posts with the line's class and job, the tax and receivable lines with the
memo's, and a line left without one takes the memo's. The form draws the
invoice form's Class cell on every line.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.models.credit_memos import CreditMemo
from app.models.transactions import Transaction
from app.services.accounting import (
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
)

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
JULY = "?start_date=2026-07-01&end_date=2026-07-31"


@pytest.fixture
def classes(client, seed_accounts):
    """The memo's own class, and two the credited work was done in."""
    return {
        name: client.post("/api/classes", json={"name": name}).json()["id"]
        for name in ("Storefront", "Wholesale", "Catering")
    }


def _posting(db_session, memo_id):
    cm = db_session.get(CreditMemo, memo_id)
    return db_session.get(Transaction, cm.transaction_id)


def _by_class_income(client):
    data = client.get("/api/reports/profit-loss-by-class" + JULY).json()
    return {c["class_name"]: c["income"] for c in data["classes"]}


def _by_job_income(client):
    r = client.get("/api/reports/profit-loss-by-job" + JULY + "&include_empty=true")
    assert r.status_code == 200, r.text
    return {j["job_id"]: float(j["income"]) for j in r.json()["jobs"]}


def test_two_lines_in_two_classes_post_and_report_under_each(
    client, db_session, seed_accounts, seed_customer, classes
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-10",
            "tax_rate": 0.1,
            "class_id": classes["Storefront"],
            "lines": [
                {
                    "description": "Wholesale order",
                    "quantity": 3,
                    "rate": 100,
                    "class_id": classes["Wholesale"],
                },
                {
                    "description": "Catering",
                    "quantity": 1,
                    "rate": 200,
                    "class_id": classes["Catering"],
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    by = _by_class_income(client)
    assert (by["Wholesale"], by["Catering"]) == (300.0, 200.0)

    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-20",
            "tax_rate": 0.1,
            "class_id": classes["Storefront"],
            "lines": [
                {
                    "description": "Returned wholesale order",
                    "quantity": 2,
                    "rate": 50,
                    "class_id": classes["Wholesale"],
                },
                {
                    "description": "Catering deposit back",
                    "quantity": 1,
                    "rate": 50,
                    "class_id": classes["Catering"],
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    cm = r.json()
    assert cm["class_id"] == classes["Storefront"]
    assert [ln["class_id"] for ln in cm["lines"]] == [
        classes["Wholesale"],
        classes["Catering"],
    ]
    assert Decimal(cm["total"]) == Decimal("165.00")

    # one income reversal per line, with that line's class; the tax and the
    # receivable with the memo's
    txn = _posting(db_session, cm["id"])
    assert txn.class_id == classes["Storefront"]
    income = get_default_income_account_id(db_session)
    tax = get_sales_tax_account_id(db_session)
    ar = get_ar_account_id(db_session)
    lines = sorted(
        (ln.account_id, float(ln.debit), float(ln.credit), ln.class_id)
        for ln in txn.lines
    )
    assert lines == sorted(
        [
            (income, 100.0, 0.0, classes["Wholesale"]),
            (income, 50.0, 0.0, classes["Catering"]),
            (tax, 15.0, 0.0, classes["Storefront"]),
            (ar, 0.0, 165.0, classes["Storefront"]),
        ]
    )

    # the P&L by Class nets each line's credit against its own class, and
    # still adds up to the plain P&L
    by = _by_class_income(client)
    assert (by["Wholesale"], by["Catering"]) == (200.0, 150.0)
    assert by.get("Storefront", 0.0) == 0.0
    assert by.get("Uncategorized", 0.0) == 0.0
    data = client.get("/api/reports/profit-loss-by-class" + JULY).json()
    plain = client.get("/api/reports/profit-loss" + JULY).json()
    assert abs(data["total_income"] - plain["total_income"]) < 0.01
    assert abs(plain["total_income"] - 350.0) < 0.01

    # a void carries each line's class back, so each class nets to its sale
    assert client.post(f"/api/credit-memos/{cm['id']}/void").status_code == 200
    by = _by_class_income(client)
    assert (by["Wholesale"], by["Catering"]) == (300.0, 200.0)


def test_a_line_without_a_class_takes_the_memos(
    client, db_session, seed_accounts, seed_customer, classes
):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-21",
            "class_id": classes["Storefront"],
            "lines": [
                {"description": "Returned order", "quantity": 1, "rate": 80},
                {
                    "description": "Catering deposit back",
                    "quantity": 1,
                    "rate": 20,
                    "class_id": classes["Catering"],
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    cm = r.json()
    # stored as sent, as an invoice line is; the posting fills the memo's in
    assert [ln["class_id"] for ln in cm["lines"]] == [None, classes["Catering"]]
    txn = _posting(db_session, cm["id"])
    debits = sorted((float(ln.debit), ln.class_id) for ln in txn.lines if ln.debit)
    assert debits == [(20.0, classes["Catering"]), (80.0, classes["Storefront"])]
    by = _by_class_income(client)
    assert (by["Storefront"], by["Catering"]) == (-80.0, -20.0)
    assert by.get("Uncategorized", 0.0) == 0.0


def test_a_line_takes_its_own_job_and_only_the_customers(
    client, db_session, TestSession, seed_accounts
):
    alder = client.post("/api/customers", json={"name": "Alder Co"}).json()
    birch = client.post("/api/customers", json={"name": "Birch Co"}).json()

    def job(customer, name):
        r = client.post("/api/jobs", json={"customer_id": customer["id"], "name": name})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    kitchen, bath, roof = job(alder, "Kitchen"), job(alder, "Bath"), job(birch, "Roof")
    body = {
        "customer_id": alder["id"],
        "date": "2026-07-22",
        "job_id": kitchen,
        "lines": [
            {"description": "Cabinet credit", "quantity": 1, "rate": 100},
            {"description": "Tile credit", "quantity": 1, "rate": 40, "job_id": bath},
        ],
    }

    # another customer's job on a line is refused, naming the line, and
    # nothing is saved
    wrong = {**body, "lines": [{**body["lines"][0], "job_id": roof}]}
    r = client.post("/api/credit-memos", json=wrong)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "belongs to a different customer" in detail and "(line 1)" in detail
    with TestSession() as s:
        assert s.query(CreditMemo).count() == 0

    r = client.post("/api/credit-memos", json=body)
    assert r.status_code == 201, r.text
    cm = r.json()
    assert cm["job_id"] == kitchen
    assert [ln["job_id"] for ln in cm["lines"]] == [None, bath]
    txn = _posting(db_session, cm["id"])
    assert txn.job_id == kitchen, "the entry carries the memo's job"
    debits = sorted((float(ln.debit), ln.job_id) for ln in txn.lines if ln.debit)
    assert debits == [(40.0, bath), (100.0, kitchen)]
    by_job = _by_job_income(client)
    assert (by_job[bath], by_job[kitchen]) == (-40.0, -100.0)

    # a job on a memo line alone keeps the job with its customer
    r = client.put(f"/api/jobs/{bath}", json={"customer_id": birch["id"]})
    assert r.status_code == 400, r.text
    assert "1 credit memo" in r.json()["detail"]


def test_the_form_draws_the_invoice_forms_class_cell_on_every_line():
    src = (JS / "credit_memos.js").read_text(encoding="utf-8")
    assert "if (CreditMemosPage.lineClasses()) await Nonprofit.loadFunds();" in src
    head = src[src.index('<table class="line-items-table">') :]
    head = head[: head.index("</thead>")]
    assert "${CreditMemosPage.lineClasses() ? Nonprofit.classHeadHtml() : ''}" in head
    row = src[src.index("lineRowHtml(idx) {") :]
    row = row[: row.index("\n    },")]
    cell = "${CreditMemosPage.lineClasses() ? Nonprofit.classCellHtml('line-class', null) : ''}"
    assert cell in row
    # after Description and before Qty, where the invoice form puts it
    assert (
        row.index('class="line-desc"') < row.index(cell) < row.index('class="line-qty"')
    )
    save = src[src.index("async save(e) {") :]
    save = save[: save.index("\n    },")]
    assert "class_id: CreditMemosPage.lineClassFromRow(row)," in save
    assert "ClassWarn.ok(form, { rows: '#cm-lines tr', cls: 'line-class' })" in save
    # the same shared cell the invoice form draws, funds included
    inv = (JS / "invoices.js").read_text(encoding="utf-8")
    assert "Nonprofit.classCellHtml('line-class', line.class_id)" in inv
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert '<option value="">Same as header</option>' in utils


# the migration under test: credit_memo_lines gains class_id and job_id (#640)
CM_LINE_CLASS_REVISION = "b1a26772cc4e"


def test_the_migration_adds_and_removes_the_columns(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "cm.db"
    url = "sqlite:///" + db.as_posix()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, CM_LINE_CLASS_REVISION)  # its own step, not the chain (#770)
    eng = create_engine(url)
    insp = inspect(eng)
    cols = {c["name"] for c in insp.get_columns("credit_memo_lines")}
    assert {"class_id", "job_id"} <= cols
    fks = {fk["name"] for fk in insp.get_foreign_keys("credit_memo_lines")}
    assert {"fk_credit_memo_lines_class_id", "fk_credit_memo_lines_job_id"} <= fks
    eng.dispose()
    command.downgrade(
        cfg, "b8d4f2a6c1e3"
    )  # back past this migration, whatever follows it
    eng = create_engine(url)
    cols = {c["name"] for c in inspect(eng).get_columns("credit_memo_lines")}
    assert not ({"class_id", "job_id"} & cols)
    eng.dispose()
