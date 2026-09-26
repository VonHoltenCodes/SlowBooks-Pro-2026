"""Sub-cent unit prices on purchases and items (migration 4c7e2a9d1b05).

macbase1's case for four-place prices was a purchase — cake boxes at $0.045
each — but 2.18's first pass widened only the sales lines. A bill, a PO
and an item now keep $0.045; each line amount is still rounded to the
cent. The same migration gives a due date to the bills a PO made before
2.18, which were saved with terms and none."""

import sqlite3
from decimal import Decimal
from pathlib import Path

from app.models.contacts import Vendor

ROOT = Path(__file__).resolve().parents[1]


def _vendor(db_session, seed_accounts):
    v = Vendor(
        name="Blue Heron Packaging",
        is_active=True,
        terms="Net 15",
        default_expense_account_id=seed_accounts["5000"].id,
    )
    db_session.add(v)
    db_session.commit()
    return v.id


def test_a_bill_keeps_a_sub_cent_price_and_rounds_the_line(
    client, db_session, seed_accounts
):
    vid = _vendor(db_session, seed_accounts)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vid,
            "date": "2026-09-20",
            "lines": [{"description": "Cake boxes", "quantity": 1000, "rate": 0.045}],
        },
    )
    assert r.status_code == 201, r.text
    bill = client.get(f"/api/bills/{r.json()['id']}").json()
    assert bill["lines"][0]["rate"] == "0.045"
    assert Decimal(str(bill["lines"][0]["amount"])) == Decimal("45.00")
    assert Decimal(str(bill["total"])) == Decimal("45.00")


def test_a_po_and_its_bill_keep_a_sub_cent_price(client, db_session, seed_accounts):
    vid = _vendor(db_session, seed_accounts)
    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vid,
            "date": "2026-09-20",
            "lines": [
                {"description": "Cake boxes", "quantity": 333, "rate": 0.0455},
            ],
        },
    )
    assert r.status_code == 201, r.text
    po = client.get(f"/api/purchase-orders/{r.json()['id']}").json()
    assert po["lines"][0]["rate"] == "0.0455"
    r = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill")
    assert r.status_code in (200, 201), r.text
    bill_id = r.json().get("bill_id") or r.json().get("id")
    bill = client.get(f"/api/bills/{bill_id}").json()
    # 333 x 0.0455 = 15.1515 -> 15.15
    assert Decimal(str(bill["total"])) == Decimal("15.15")


def test_an_item_keeps_a_sub_cent_price_and_cost(client, seed_accounts):
    r = client.post(
        "/api/items",
        json={
            "name": "Cake box",
            "item_type": "material",
            "rate": "0.09",
            "cost": "0.045",
            "income_account_id": seed_accounts["4000"].id,
        },
    )
    assert r.status_code in (200, 201), r.text
    item = client.get(f"/api/items/{r.json()['id']}").json()
    assert item["cost"] == "0.045"
    assert item["rate"] == "0.09"


def test_the_purchase_forms_take_four_places():
    js = ROOT / "app" / "static" / "js"
    for name in ("bills.js", "purchase_orders.js", "vendor_credits.js"):
        text = (js / name).read_text(encoding="utf-8")
        assert 'class="line-rate" type="number" step="0.0001"' in text, name
    items = (js / "items.js").read_text(encoding="utf-8")
    assert '<input name="rate" type="number" step="0.0001"' in items
    assert '<input name="cost" type="number" step="0.0001"' in items


def test_the_migration_widens_prices_and_gives_old_bills_a_due_date(tmp_path):
    from alembic import command
    from alembic.config import Config

    db = tmp_path / "old.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "697f63b2975e")

    con = sqlite3.connect(db)
    con.execute("INSERT INTO vendors (name) VALUES ('Cascade Flour Mill')")
    rows = [
        ("BILL-PO-0001", "2026-08-01", "Net 15", None),
        ("BILL-PO-0002", "2026-08-01", "Due on Receipt", None),
        ("BILL-PO-0003", "2026-08-01", "whenever", None),
        ("BILL-4", "2026-08-01", "Net 30", "2026-08-20"),  # has one: untouched
    ]
    for number, day, terms, due in rows:
        con.execute(
            "INSERT INTO bills (bill_number, vendor_id, date, terms, due_date) "
            "VALUES (?, 1, ?, ?, ?)",
            (number, day, terms, due),
        )
    con.execute(
        "INSERT INTO bill_lines (bill_id, quantity, rate, amount, line_order) "
        "VALUES (1, 1000, 0.05, 50.00, 0)"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "4c7e2a9d1b05")
    con = sqlite3.connect(db)
    got = dict(con.execute("SELECT bill_number, due_date FROM bills").fetchall())
    types = {
        (t, c): next(
            col[2] for col in con.execute(f"PRAGMA table_info({t})") if col[1] == c
        )
        for t, c in (
            ("bill_lines", "rate"),
            ("purchase_order_lines", "rate"),
            ("vendor_credit_lines", "rate"),
            ("items", "rate"),
            ("items", "cost"),
        )
    }
    assert con.execute("SELECT rate FROM bill_lines").fetchone() == (0.05,)
    con.close()
    assert got == {
        "BILL-PO-0001": "2026-08-16",
        "BILL-PO-0002": "2026-08-01",
        "BILL-PO-0003": "2026-08-31",
        "BILL-4": "2026-08-20",
    }
    assert set(types.values()) == {"NUMERIC(17, 4)"}
    command.downgrade(cfg, "697f63b2975e")
