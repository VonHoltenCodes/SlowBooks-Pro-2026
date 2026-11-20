"""A void takes coded revenue off its own cost code (NEW-262, 2.32.0 gate).

An invoice with 1,000 on cost code 03-300 and 200 uncoded, voided, left the
job's Cost Detail with $1,000.00 still on 03-300 and -$1,000.00 on "Revenue
with no cost code" (the Total $0.00, the P&L right): the invoice's void
posted its reversal without the lines' cost codes and types, where a credit
memo's void kept them. The void now carries every line's dimensions, so
each row nets to zero, for an invoice, a sales receipt and an invoice
edited after it posted. A void posted under an older build is read as the
line of the invoice it reverses, so an existing company's Cost Detail reads
right with no migration."""

from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.transactions import (
    LEDGER_BASIS_OPTION,
    READ_ALL,
    Transaction,
    TransactionLine,
)


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
                "is_taxable": False,
            },
        ),
        201,
    )
    job = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Kitchen"}
        ),
        201,
    )
    concrete = _ok(
        client.post(
            "/api/cost-codes",
            json={"code": "93-300", "name": "Concrete", "cost_type": "material"},
        ),
        201,
    )
    return {
        "customer": seed_customer.id,
        "item": item["id"],
        "job": job["id"],
        "code": concrete,
    }


def _lines(b, coded=1000, uncoded=200):
    return [
        {
            "item_id": b["item"],
            "description": "Footings",
            "quantity": 1,
            "rate": coded,
            "cost_code_id": b["code"]["id"],
            "cost_type": "labor",
        },
        {
            "item_id": b["item"],
            "description": "Cleanup",
            "quantity": 1,
            "rate": uncoded,
        },
    ]


def _tree(client, b):
    return _ok(client.get(f"/api/jobs/{b['job']}/cost-tree"))


def _coded(tree, b):
    for ty in tree["types"]:
        for node in ty["codes"]:
            if node["id"] == b["code"]["id"] and ty["cost_type"] == "labor":
                return node["figures"]["act_revenue"]
    return 0.0


def _nets_to_zero(client, b):
    tree = _tree(client, b)
    assert _coded(tree, b) == 0.0
    assert tree["unassigned_revenue"]["figures"]["act_revenue"] == 0.0
    assert tree["totals"]["act_revenue"] == 0.0
    for sec in tree["revenue_sections"]:
        assert sec["act_revenue"] == 0.0, sec


def books_agree(client):
    """The trial balance foots on both bases and Books Check finds no
    difference."""
    for basis in ("accrual", "cash"):
        tb = _ok(
            client.get(
                "/api/reports/trial-balance?start_date=2000-01-01"
                f"&end_date=2099-12-31&basis={basis}"
            )
        )
        assert tb["total_debit"] == tb["total_credit"] and tb["difference"] == 0
    check = _ok(client.get("/api/reports/books-check"))
    assert check["differences"] == 0, [r for r in check["rows"] if r["ok"] is False]


def _void_lines(db_session, invoice_id):
    return (
        db_session.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Transaction.source_type == "invoice_void",
            Transaction.source_id == invoice_id,
        )
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    )


def test_a_voided_invoice_leaves_nothing_on_its_cost_code(client, db_session, books):
    b = books
    inv = _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": b["customer"],
                "date": "2026-07-02",
                "job_id": b["job"],
                "tax_rate": 0,
                "lines": _lines(b),
            },
        ),
        201,
    )
    before = _tree(client, b)
    assert _coded(before, b) == 1000.0
    assert before["unassigned_revenue"]["figures"]["act_revenue"] == 200.0
    _ok(client.post(f"/api/invoices/{inv['id']}/void"))
    _nets_to_zero(client, b)
    coded = [ln for ln in _void_lines(db_session, inv["id"]) if ln.cost_code_id]
    assert [(ln.cost_code_id, ln.cost_type, ln.debit) for ln in coded] == [
        (b["code"]["id"], "labor", Decimal("1000.00"))
    ]
    books_agree(client)


def test_a_voided_sales_receipt_leaves_nothing_on_its_cost_code(
    client, db_session, books
):
    b = books
    sr = _ok(
        client.post(
            "/api/sales-receipts",
            json={
                "customer_id": b["customer"],
                "date": "2026-07-04",
                "job_id": b["job"],
                "tax_rate": 0,
                "method": "Cash",
                "lines": _lines(b),
            },
        ),
        201,
    )
    assert _coded(_tree(client, b), b) == 1000.0
    _ok(client.post(f"/api/payments/{sr['payment']['id']}/void"))
    _ok(client.post(f"/api/invoices/{sr['invoice']['id']}/void"))
    _nets_to_zero(client, b)
    books_agree(client)


def test_an_invoice_edited_after_it_posted_then_voided_nets_to_zero(
    client, db_session, books
):
    b = books
    inv = _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": b["customer"],
                "date": "2026-07-02",
                "job_id": b["job"],
                "tax_rate": 0,
                "lines": _lines(b),
            },
        ),
        201,
    )
    _ok(client.put(f"/api/invoices/{inv['id']}", json={"lines": _lines(b, 1400, 50)}))
    tree = _tree(client, b)
    assert _coded(tree, b) == 1400.0
    assert tree["unassigned_revenue"]["figures"]["act_revenue"] == 50.0
    _ok(client.post(f"/api/invoices/{inv['id']}/void"))
    _nets_to_zero(client, b)
    books_agree(client)


def test_a_void_posted_before_the_fix_reads_right_with_no_migration(
    client, db_session, books
):
    """The reversal an older build posted: no code and no type on any
    line. The Cost Detail reads each line as the invoice line it
    reverses."""
    b = books
    inv = _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": b["customer"],
                "date": "2026-07-02",
                "job_id": b["job"],
                "tax_rate": 0,
                # two coded lines alike but for the type, and one uncoded
                "lines": _lines(b)
                + [
                    {
                        "item_id": b["item"],
                        "description": "Footings",
                        "quantity": 1,
                        "rate": 1000,
                        "cost_code_id": b["code"]["id"],
                        "cost_type": "material",
                    }
                ],
            },
        ),
        201,
    )
    _ok(client.post(f"/api/invoices/{inv['id']}/void"))
    for ln in _void_lines(db_session, inv["id"]):
        ln.cost_code_id = None
        ln.cost_type = None
    db_session.commit()
    tree = _tree(client, b)
    _nets_to_zero(client, b)
    for ty in tree["types"]:
        for node in ty["codes"]:
            assert node["figures"]["act_revenue"] == 0.0, (ty["cost_type"], node)
    books_agree(client)
