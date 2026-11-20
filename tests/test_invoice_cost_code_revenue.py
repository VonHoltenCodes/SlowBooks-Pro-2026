"""Revenue by cost code and cost type (#1055). In construction mode an
invoice line takes a cost code and a cost type, so one item ("Project
revenue") is split on the invoice per code and type. The income account is
the item's, so the Profit & Loss keeps one line; the job's Cost Detail
shows the revenue on each code and type, and only what truly has none on
its "Revenue with no cost code" row. A sales receipt takes them the same
way, a credit memo coded to a code takes revenue back from it, and the
printed invoice does not show them."""

import sqlite3
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.invoices import Invoice
from app.models.transactions import TransactionLine

ROOT = Path(__file__).resolve().parent.parent


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


@pytest.fixture
def books(client, db_session, seed_accounts, seed_customer):
    _ok(client.put("/api/settings", json={"company_type": "construction"}))
    _ok(client.post("/api/construction/setup"))
    income = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.INCOME)
        .order_by(Account.id)
        .first()
    )
    item = _ok(
        client.post(
            "/api/items",
            json={
                "name": "Project revenue",
                "item_type": "service",
                "income_account_id": income.id,
            },
        ),
        201,
    )
    job = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Clinic"}
        ),
        201,
    )
    slab = _ok(
        client.post(
            "/api/cost-codes",
            json={"code": "93-100", "name": "Slab", "cost_type": "material"},
        ),
        201,
    )
    board = _ok(
        client.post(
            "/api/cost-codes",
            json={"code": "99-100", "name": "Board", "cost_type": "subcontract"},
        ),
        201,
    )
    return {
        "customer": seed_customer.id,
        "income": income,
        "item": item["id"],
        "job": job["id"],
        "slab": slab,
        "board": board,
    }


def _line(b, rate, code=None, cost_type=None, desc="Project revenue"):
    out = {"item_id": b["item"], "description": desc, "quantity": 1, "rate": rate}
    if code is not None:
        out["cost_code_id"] = code["id"]
    if cost_type is not None:
        out["cost_type"] = cost_type
    return out


def _invoice(client, b, lines, day="2026-07-02"):
    return _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": b["customer"],
                "date": day,
                "job_id": b["job"],
                "lines": lines,
            },
        ),
        201,
    )


def _by_type(tree):
    out = {}
    for ty in tree["types"]:
        for node in ty["codes"]:
            out[(node["id"], ty["cost_type"])] = node["figures"]["act_revenue"]
    return out


def test_one_item_splits_per_code_and_type_under_one_income_line(
    client, db_session, books
):
    b = books
    inv = _invoice(
        client,
        b,
        [
            _line(b, 1000, b["slab"]),  # the code's own type: material
            _line(b, 400, b["slab"], "labor"),
            _line(b, 600, b["board"]),
            _line(b, 250),  # no code: truly unassigned
        ],
    )
    assert [ln["cost_type"] for ln in inv["lines"]] == [None, "labor", None, None]
    assert inv["lines"][1]["cost_code_id"] == b["slab"]["id"]

    # the journal: every revenue leg on the item's income account, each with
    # its code and its type (the code's own where the line names none)
    legs = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.account_id == b["income"].id,
            TransactionLine.transaction_id
            == db_session.get(Invoice, inv["id"]).transaction_id,
        )
        .order_by(TransactionLine.id)
        .all()
    )
    assert [(ln.cost_code_id, ln.cost_type, float(ln.credit)) for ln in legs] == [
        (b["slab"]["id"], "material", 1000.0),
        (b["slab"]["id"], "labor", 400.0),
        (b["board"]["id"], "subcontract", 600.0),
        (None, None, 250.0),
    ]

    # the P&L: one income line carrying it all
    pl = _ok(
        client.get("/api/reports/profit-loss?start_date=2026-07-01&end_date=2026-07-31")
    )
    income_rows = [r for r in pl["income"] if r["amount"]]
    assert [(r["account_id"], r["amount"]) for r in income_rows] == [
        (b["income"].id, 2250.0)
    ]

    # the job's Cost Detail: revenue on each code and type, the rest on its
    # own row
    tree = _ok(client.get(f"/api/jobs/{b['job']}/cost-tree"))
    by = _by_type(tree)
    assert by[(b["slab"]["id"], "material")] == 1000.0
    assert by[(b["slab"]["id"], "labor")] == 400.0
    assert by[(b["board"]["id"], "subcontract")] == 600.0
    assert tree["unassigned_revenue"]["figures"]["act_revenue"] == 250.0
    assert tree["totals"]["act_revenue"] == 2250.0
    sections = {r["cost_code_id"]: r for r in tree["revenue_sections"]}
    assert sections[None]["act_revenue"] == 250.0


def test_an_edit_moves_the_revenue_and_a_duplicate_keeps_it(client, books):
    b = books
    inv = _invoice(client, b, [_line(b, 1000, b["slab"], "labor")])
    body = {"lines": [_line(b, 1000, b["board"], "equipment")]}
    edited = _ok(client.put(f"/api/invoices/{inv['id']}", json=body))
    assert (edited["lines"][0]["cost_code_id"], edited["lines"][0]["cost_type"]) == (
        b["board"]["id"],
        "equipment",
    )
    tree = _ok(client.get(f"/api/jobs/{b['job']}/cost-tree"))
    by = _by_type(tree)
    assert by.get((b["slab"]["id"], "labor"), 0) == 0
    assert by[(b["board"]["id"], "equipment")] == 1000.0

    dup = _ok(client.post(f"/api/invoices/{inv['id']}/duplicate"), 201)
    assert (dup["lines"][0]["cost_code_id"], dup["lines"][0]["cost_type"]) == (
        b["board"]["id"],
        "equipment",
    )


def test_an_unknown_code_or_type_is_refused(client, books):
    b = books
    for line in (_line(b, 10, {"id": 999999}), _line(b, 10, b["slab"], "nonsense")):
        r = client.post(
            "/api/invoices",
            json={
                "customer_id": b["customer"],
                "date": "2026-07-02",
                "job_id": b["job"],
                "lines": [line],
            },
        )
        assert r.status_code == 422, r.text


def test_a_credit_memo_takes_revenue_back_from_its_code(client, books):
    b = books
    _invoice(client, b, [_line(b, 1000, b["slab"], "labor")])
    cm = _ok(
        client.post(
            "/api/credit-memos",
            json={
                "customer_id": b["customer"],
                "date": "2026-07-03",
                "job_id": b["job"],
                "lines": [
                    {
                        "item_id": b["item"],
                        "description": "Credit",
                        "quantity": 1,
                        "rate": 300,
                        "cost_code_id": b["slab"]["id"],
                        "cost_type": "labor",
                    }
                ],
            },
        ),
        201,
    )
    assert (cm["lines"][0]["cost_code_id"], cm["lines"][0]["cost_type"]) == (
        b["slab"]["id"],
        "labor",
    )
    tree = _ok(client.get(f"/api/jobs/{b['job']}/cost-tree"))
    assert _by_type(tree)[(b["slab"]["id"], "labor")] == 700.0
    _ok(client.post(f"/api/credit-memos/{cm['id']}/void"))
    tree = _ok(client.get(f"/api/jobs/{b['job']}/cost-tree"))
    assert _by_type(tree)[(b["slab"]["id"], "labor")] == 1000.0


def test_a_sales_receipt_takes_them_the_same_way(client, books):
    b = books
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": b["customer"],
            "date": "2026-07-04",
            "job_id": b["job"],
            "lines": [_line(b, 500, b["board"], "labor")],
        },
    )
    assert r.status_code == 201, r.text
    tree = _ok(client.get(f"/api/jobs/{b['job']}/cost-tree"))
    assert _by_type(tree)[(b["board"]["id"], "labor")] == 500.0


def test_the_printed_invoice_does_not_show_them(client, books):
    b = books
    inv = _invoice(client, b, [_line(b, 1000, b["slab"], "labor")])
    page = client.get(f"/api/invoices/{inv['id']}/print-preview")
    assert page.status_code == 200
    assert "93-100" not in page.text and "Slab" not in page.text
    assert "Project revenue" in page.text


def test_a_read_only_sign_in_cannot_post_one(client, books, db_session):
    from app.models.users import ROLE_READONLY, User
    from app.services import auth as auth_service

    b = books
    db_session.add(
        User(
            username="viewer",
            display_name="Viewer",
            password_hash=auth_service.hash_password("viewer-password-123"),
            role=ROLE_READONLY,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login",
        json={"username": "viewer", "password": "viewer-password-123"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": b["customer"],
            "date": "2026-07-02",
            "job_id": b["job"],
            "lines": [_line(b, 10, b["slab"], "labor")],
        },
    )
    assert r.status_code == 403, r.text


def test_the_migration_adds_the_columns_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "memo_lines.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [
        "c1128e5a7d0f"
    ]  # the estimate void (#1128) is the head above it
    assert script.get_revision("e1055c0de4a1").down_revision == "e986b4d2a7c3"
    command.upgrade(cfg, "e986b4d2a7c3")
    command.upgrade(cfg, "e1055c0de4a1")  # its own step (#770)

    def cols():
        with sqlite3.connect(db) as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(credit_memo_lines)")}

    assert {"cost_code_id", "cost_type"} <= cols()
    with sqlite3.connect(db) as conn:
        fks = list(conn.execute("PRAGMA foreign_key_list(credit_memo_lines)"))
    assert any(r[2] == "cost_codes" and r[3] == "cost_code_id" for r in fks)
    command.downgrade(cfg, "e986b4d2a7c3")
    assert not ({"cost_code_id", "cost_type"} & cols())


def test_the_sales_forms_draw_the_cells_while_a_job_is_chosen():
    js = ROOT / "app" / "static" / "js"
    utils = (js / "utils.js").read_text(encoding="utf-8")
    assert "const SalesCost" in utils
    assert 'aria-label="Line cost code"' in utils
    assert 'aria-label="Line cost type"' in utils
    inv = (js / "invoices.js").read_text(encoding="utf-8")
    assert "SalesCost.headHtml()" in inv and "SalesCost.cellHtml(line)" in inv
    assert "SalesCost.sync(" in inv and "SalesCost.fromRow(row, form)" in inv
    sr = (js / "sales_receipts.js").read_text(encoding="utf-8")
    assert "SalesCost.cellHtml(line)" in sr and "SalesCost.fromRow(row, form)" in sr
    cm = (js / "credit_memos.js").read_text(encoding="utf-8")
    assert "cost_code_id: row.dataset.costCodeId" in cm
