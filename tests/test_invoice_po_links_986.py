"""An invoice and the purchase orders that fill it (#986).

A reseller invoices a mouse bought on one vendor's PO and a monitor bought
on another's. One invoice now names many purchase orders and each PO the
invoice it was bought for: both sides read the link, it is made and undone
from either side, a PO already filling another invoice is refused with a
sentence, a PO that goes takes its link with it, and voiding the invoice
leaves the links. A link is a plain reference: no journal line is written
and no total moves. Each document's Activity says so, a read-only or
accountant sign-in reads the links and cannot change them, and the
migration adds the column to an existing company file and walks back."""

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.purchase_orders import PurchaseOrder
from app.models.transactions import TransactionLine
from app.models.users import User
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parent.parent
REVISION = "e986b4d2a7c3"
BELOW = "e904b7c3a1d5"
USER_PW = "Links-986-pass!"


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


def _message(r):
    detail = r.json()["detail"]
    return detail["message"] if isinstance(detail, dict) else detail


@pytest.fixture
def books(client, seed_accounts):
    cust = _ok(client.post("/api/customers", json={"name": "Barrs Resale"}))
    mice = _ok(client.post("/api/vendors", json={"name": "Mouse Supply"}))
    screens = _ok(client.post("/api/vendors", json={"name": "Screen Depot"}))

    def invoice(rate=250):
        return _ok(
            client.post(
                "/api/invoices",
                json={
                    "customer_id": cust["id"],
                    "date": "2026-11-16",
                    "lines": [
                        {
                            "description": "Mouse and monitor",
                            "quantity": 1,
                            "rate": rate,
                        }
                    ],
                },
            )
        )

    def po(vendor, rate, **extra):
        return _ok(
            client.post(
                "/api/purchase-orders",
                json={
                    "vendor_id": vendor["id"],
                    "date": "2026-11-16",
                    "lines": [{"description": "goods", "quantity": 1, "rate": rate}],
                    **extra,
                },
            )
        )

    return {"invoice": invoice, "po": po, "mice": mice, "screens": screens}


def _linked(client, inv_id):
    return _ok(client.get(f"/api/invoices/{inv_id}"))["linked_purchase_orders"]


def _journal_lines(db_session):
    db_session.expire_all()
    return db_session.query(TransactionLine).count()


def test_one_invoice_links_two_pos_from_different_vendors(client, books, db_session):
    inv = books["invoice"]()
    mouse = books["po"](books["mice"], 20)
    screen = books["po"](books["screens"], 140)
    lines_before = _journal_lines(db_session)

    _ok(client.post(f"/api/invoices/{inv['id']}/purchase-orders/{mouse['id']}"))
    after = _ok(
        client.post(f"/api/invoices/{inv['id']}/purchase-orders/{screen['id']}")
    )
    assert [p["po_number"] for p in after] == [mouse["po_number"], screen["po_number"]]

    linked = _linked(client, inv["id"])
    assert [(p["id"], p["vendor_name"], p["status"]) for p in linked] == [
        (mouse["id"], "Mouse Supply", "draft"),
        (screen["id"], "Screen Depot", "draft"),
    ]
    assert [Decimal(p["total"]) for p in linked] == [Decimal("20"), Decimal("140")]

    for po in (mouse, screen):
        got = _ok(client.get(f"/api/purchase-orders/{po['id']}"))
        assert got["invoice_id"] == inv["id"]
        assert got["invoice_number"] == inv["invoice_number"]
    listed = {p["id"]: p for p in _ok(client.get("/api/purchase-orders"))}
    assert listed[mouse["id"]]["invoice_number"] == inv["invoice_number"]

    # nothing posts, no total moves
    assert _journal_lines(db_session) == lines_before
    again = _ok(client.get(f"/api/invoices/{inv['id']}"))
    assert Decimal(again["total"]) == Decimal(inv["total"])
    assert Decimal(again["balance_due"]) == Decimal(inv["balance_due"])
    for po in (mouse, screen):
        got = _ok(client.get(f"/api/purchase-orders/{po['id']}"))
        assert Decimal(got["total"]) == Decimal(po["total"])


def test_a_po_linked_to_another_invoice_is_refused_with_a_sentence(client, books):
    first, second = books["invoice"](), books["invoice"]()
    po = books["po"](books["mice"], 20)
    _ok(client.post(f"/api/invoices/{first['id']}/purchase-orders/{po['id']}"))
    r = client.post(f"/api/invoices/{second['id']}/purchase-orders/{po['id']}")
    assert r.status_code == 409
    assert _message(r) == (
        f"Purchase order {po['po_number']} already fills invoice "
        f"{first['invoice_number']}. Unlink it there first."
    )
    assert _linked(client, second["id"]) == []
    # linking it again where it already is changes nothing
    again = _ok(client.post(f"/api/invoices/{first['id']}/purchase-orders/{po['id']}"))
    assert [p["id"] for p in again] == [po["id"]]


def test_unlink_from_the_invoice_and_from_the_po(client, books):
    inv = books["invoice"]()
    mouse = books["po"](books["mice"], 20)
    screen = books["po"](books["screens"], 140)
    for po in (mouse, screen):
        _ok(client.post(f"/api/invoices/{inv['id']}/purchase-orders/{po['id']}"))

    left = _ok(
        client.delete(f"/api/invoices/{inv['id']}/purchase-orders/{mouse['id']}")
    )
    assert [p["id"] for p in left] == [screen["id"]]
    assert _ok(client.get(f"/api/purchase-orders/{mouse['id']}"))["invoice_id"] is None

    # from the PO side: its Fills invoice cleared
    got = _ok(
        client.put(f"/api/purchase-orders/{screen['id']}", json={"invoice_id": None})
    )
    assert got["invoice_id"] is None and got["invoice_number"] is None
    assert _linked(client, inv["id"]) == []

    # unlinking one this invoice doesn't have says so
    r = client.delete(f"/api/invoices/{inv['id']}/purchase-orders/{mouse['id']}")
    assert r.status_code == 409
    assert _message(r) == (
        f"Purchase order {mouse['po_number']} does not fill this invoice."
    )


def test_the_po_side_links_on_create_and_on_edit(client, books):
    inv = books["invoice"]()
    made = books["po"](books["mice"], 20, invoice_id=inv["id"])  # Create PO
    assert made["invoice_id"] == inv["id"]
    assert made["invoice_number"] == inv["invoice_number"]
    other = books["po"](books["screens"], 140)
    got = _ok(
        client.put(
            f"/api/purchase-orders/{other['id']}", json={"invoice_id": inv["id"]}
        )
    )
    assert got["invoice_number"] == inv["invoice_number"]
    assert {p["id"] for p in _linked(client, inv["id"])} == {made["id"], other["id"]}

    # an edit that leaves the field out keeps the link
    _ok(client.put(f"/api/purchase-orders/{other['id']}", json={"notes": "rush"}))
    assert (
        _ok(client.get(f"/api/purchase-orders/{other['id']}"))["invoice_id"]
        == inv["id"]
    )

    r = client.put(f"/api/purchase-orders/{other['id']}", json={"invoice_id": 987654})
    assert r.status_code == 404
    assert _message(r) == "That invoice was not found."


def test_deleting_a_po_drops_its_link(client, books, db_session):
    inv = books["invoice"]()
    keep = books["po"](books["mice"], 20, invoice_id=inv["id"])
    gone = books["po"](books["screens"], 140, invoice_id=inv["id"])
    db_session.delete(db_session.get(PurchaseOrder, gone["id"]))
    db_session.commit()
    assert [p["id"] for p in _linked(client, inv["id"])] == [keep["id"]]


def test_voiding_the_invoice_keeps_its_links(client, books, db_session):
    inv = books["invoice"]()
    po = books["po"](books["mice"], 20, invoice_id=inv["id"])
    _ok(client.post(f"/api/invoices/{inv['id']}/void"))
    assert [p["id"] for p in _linked(client, inv["id"])] == [po["id"]]
    assert (
        _ok(client.get(f"/api/purchase-orders/{po['id']}"))["invoice_id"] == inv["id"]
    )
    # a void invoice takes no new link, and says why
    fresh = books["po"](books["screens"], 140)
    r = client.post(f"/api/invoices/{inv['id']}/purchase-orders/{fresh['id']}")
    assert r.status_code == 409
    assert _message(r) == (
        f"This invoice, {inv['invoice_number']}, is void, so no purchase order "
        "can be linked to it."
    )
    # the PO it already fills keeps it through an edit
    _ok(
        client.put(
            f"/api/purchase-orders/{po['id']}",
            json={"invoice_id": inv["id"], "notes": "x"},
        )
    )


def test_activity_records_link_and_unlink_on_both_documents(client, books):
    inv = books["invoice"]()
    po = books["po"](books["mice"], 20)
    made = books["po"](books["screens"], 140, invoice_id=inv["id"])
    _ok(client.post(f"/api/invoices/{inv['id']}/purchase-orders/{po['id']}"))
    _ok(client.delete(f"/api/invoices/{inv['id']}/purchase-orders/{po['id']}"))

    def texts(type_, id_):
        found = _ok(client.get(f"/api/activity?type={type_}&id={id_}&tz_offset=0"))
        return [e["text"] for e in found["events"]], [
            d for e in found["events"] for d in e["details"]
        ]

    on_invoice, _ = texts("invoice", inv["id"])
    assert any(
        t.startswith(f"Purchase order {made['po_number']} linked by")
        for t in on_invoice
    )
    assert any(
        t.startswith(f"Purchase order {po['po_number']} linked by") for t in on_invoice
    )
    assert any(
        t.startswith(f"Purchase order {po['po_number']} unlinked by")
        for t in on_invoice
    )

    _, on_po = texts("purchase_order", po["id"])
    assert f"Invoice set to {inv['invoice_number']}" in on_po
    assert f"Invoice cleared (was {inv['invoice_number']})" in on_po


def _sign_in(db_session, role):
    name = f"{role}-986"
    db_session.add(
        User(
            username=name,
            display_name=role.title(),
            password_hash=auth_service.hash_password(USER_PW),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    c = TestClient(app)
    _ok(c.post("/api/auth/login", json={"username": name, "password": USER_PW}))
    return c


@pytest.mark.parametrize("role", ["readonly", "accountant"])
def test_read_only_and_accountant_read_the_links_and_cannot_change_them(
    client, books, db_session, role
):
    inv = books["invoice"]()
    linked = books["po"](books["mice"], 20, invoice_id=inv["id"])
    loose = books["po"](books["screens"], 140)
    reader = _sign_in(db_session, role)

    assert [
        p["id"]
        for p in _ok(reader.get(f"/api/invoices/{inv['id']}"))["linked_purchase_orders"]
    ] == [linked["id"]]
    assert (
        _ok(reader.get(f"/api/purchase-orders/{linked['id']}"))["invoice_id"]
        == inv["id"]
    )

    assert (
        reader.post(
            f"/api/invoices/{inv['id']}/purchase-orders/{loose['id']}"
        ).status_code
        == 403
    )
    assert (
        reader.delete(
            f"/api/invoices/{inv['id']}/purchase-orders/{linked['id']}"
        ).status_code
        == 403
    )
    assert (
        reader.put(
            f"/api/purchase-orders/{loose['id']}", json={"invoice_id": inv["id"]}
        ).status_code
        == 403
    )
    db_session.expire_all()
    assert db_session.get(PurchaseOrder, linked["id"]).invoice_id == inv["id"]
    assert db_session.get(PurchaseOrder, loose["id"]).invoice_id is None


def test_the_migration_adds_the_column_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "links.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(REVISION).down_revision == BELOW
    assert script.get_heads() == ["e1055c0de4a1"]  # #1055 is the head above it
    command.upgrade(cfg, BELOW)  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO vendors (id, name, is_active) VALUES (1, 'V', 1)")
        conn.execute(
            "INSERT INTO purchase_orders (id, po_number, vendor_id, status, date,"
            " commitment_type, retainage_released, total)"
            " VALUES (1, 'PO-0001', 1, 'DRAFT', '2026-11-01', 'po', 0, 12.5)"
        )
    command.upgrade(cfg, REVISION)  # its own step (#770)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(purchase_orders)")}
        assert "invoice_id" in cols
        fks = [r for r in conn.execute("PRAGMA foreign_key_list(purchase_orders)")]
        assert any(r[2] == "invoices" and r[3] == "invoice_id" for r in fks)
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(purchase_orders)")}
        assert "ix_purchase_orders_invoice_id" in indexes
        assert conn.execute(
            "SELECT po_number, invoice_id, total FROM purchase_orders"
        ).fetchone() == ("PO-0001", None, 12.5)
    command.downgrade(cfg, BELOW)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(purchase_orders)")}
        assert "invoice_id" not in cols
        assert conn.execute("SELECT count(*) FROM purchase_orders").fetchone() == (1,)


def test_a_sales_receipt_takes_no_link(client, db_session, seed_accounts):
    # #1018: the window never offers a link on a sales receipt, so the API
    # refuses one from either side with a sentence
    from fastapi import HTTPException

    from app.models.contacts import Customer
    from app.models.invoices import Invoice
    from app.services.invoice_po_links import invoice_to_fill

    c = Customer(name="Walk-in", is_active=True)
    db_session.add(c)
    db_session.commit()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": c.id,
            "date": "2026-09-02",
            "due_date": "2026-09-02",
            "lines": [{"description": "Cable", "quantity": 1, "rate": 12.0}],
        },
    )
    assert r.status_code in (200, 201), r.text
    inv = db_session.get(Invoice, r.json()["id"])
    inv.is_sales_receipt = True
    db_session.commit()
    try:
        invoice_to_fill(db_session, inv.id)
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["code"] == "sales_receipt"
        assert exc.detail["message"] == (
            f"Sales receipt {inv.invoice_number} carries no balance for a purchase "
            "order to fill, so none can be linked to it."
        )
    else:
        raise AssertionError("a sales receipt took a link")
    # an invoice still does
    inv.is_sales_receipt = False
    db_session.commit()
    assert invoice_to_fill(db_session, inv.id).id == inv.id
