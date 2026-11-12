"""One inventory adjustment of several items (#801).

A count that moved twenty items was twenty adjustments, in the window and
through the API. POST /api/inventory/adjustments takes a line per item and
posts one journal entry for the lot (source type inventory_adjustment) and
one movement per item, each naming the adjustment. A wrong line refuses the
whole adjustment with a sentence naming the line and the item, and nothing
is posted. The one-item route, POST /api/items/{id}/adjust, is unchanged.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.items import InventoryAdjustment, InventoryMovement, Item
from app.models.transactions import Transaction, TransactionLine
from app.models.users import User
from app.services import auth as auth_service

USER_PW = "long-enough-pw-801"
SOURCE = "inventory_adjustment"


def _item(client, seed_accounts, name, qty="10", cost="4.00", track=True):
    body = {
        "name": name,
        "item_type": "product",
        "rate": "25.00",
        "cost": cost,
        "track_inventory": track,
        "quantity_on_hand": qty,
        "asset_account_id": seed_accounts["1300"].id,
        "is_taxable": False,
    }
    r = client.post("/api/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(name="stock")
def stock_fixture(client, seed_accounts):
    """Three tracked items: 10 Widgets at 4.00, 20 Bolts at 0.50, 5 Gears
    at 12.00, and a service item that is not tracked."""
    return {
        "widget": _item(client, seed_accounts, "Widget", "10", "4.00"),
        "bolt": _item(client, seed_accounts, "Bolt", "20", "0.50"),
        "gear": _item(client, seed_accounts, "Gear", "5", "12.00"),
        "labor": _item(client, seed_accounts, "Labor hour", "0", "0", track=False),
    }


def _count(db_session, item_id):
    db_session.expire_all()
    return Decimal(str(db_session.get(Item, item_id).quantity_on_hand))


def _adjustment_entries(db_session):
    return db_session.query(Transaction).filter_by(source_type=SOURCE).all()


def _post(client, lines, memo="Year-end count", **kw):
    return client.post(
        "/api/inventory/adjustments", json={"memo": memo, "lines": lines}, **kw
    )


def test_one_adjustment_of_three_items_posts_one_entry_that_foots(
    client, db_session, stock
):
    w, b, g = stock["widget"], stock["bolt"], stock["gear"]
    r = _post(
        client,
        [
            {"item_id": w["id"], "quantity_delta": 5, "unit_cost": 6, "memo": "found"},
            {"item_id": b["id"], "quantity_delta": -4},
            {"item_id": g["id"], "new_quantity": 2, "memo": "two broken"},
        ],
    )
    assert r.status_code == 201, r.text
    adj = r.json()
    assert adj["line_count"] == 3 and adj["memo"] == "Year-end count"
    assert adj["date"] == date.today().isoformat()
    lines = adj["lines"]
    assert [ln["item_name"] for ln in lines] == ["Widget", "Bolt", "Gear"]
    assert [Decimal(ln["quantity_delta"]) for ln in lines] == [5, -4, -3]
    # a cost of its own where given, else the average
    assert [Decimal(ln["unit_cost"]) for ln in lines] == [6, Decimal("0.5"), 12]
    assert [Decimal(ln["amount"]) for ln in lines] == [
        Decimal("30.00"),
        Decimal("-2.00"),
        Decimal("-36.00"),
    ]
    assert Decimal(adj["net_amount"]) == Decimal("-8.00")
    assert [ln["memo"] for ln in lines] == ["found", None, "two broken"]
    assert [Decimal(ln["balance_qty"]) for ln in lines] == [15, 16, 2]

    # one entry for the lot, and it foots
    [txn] = _adjustment_entries(db_session)
    assert txn.id == adj["transaction_id"] and txn.source_id == adj["id"]
    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert len(rows) == 6
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows) == Decimal("68.00")
    inventory = {
        r.description: (r.debit, r.credit)
        for r in rows
        if r.account_id
        == db_session.query(Account).filter_by(account_number="1300").one().id
    }
    assert inventory == {
        "Inventory adj: Widget": (Decimal("30.00"), Decimal("0")),
        "Inventory adj: Bolt": (Decimal("0"), Decimal("2.00")),
        "Inventory adj: Gear": (Decimal("0"), Decimal("36.00")),
    }
    assert _count(db_session, w["id"]) == 15
    assert _count(db_session, b["id"]) == 16
    assert _count(db_session, g["id"]) == 2


def test_the_adjustment_account_is_on_the_entry(client, db_session, stock):
    acc = Account(
        account_number="5900",
        name="Inventory Adjustments",
        account_type=AccountType.COGS,
        balance=Decimal("0"),
    )
    db_session.add(acc)
    db_session.commit()
    w, b = stock["widget"], stock["bolt"]
    r = _post(
        client,
        [
            {"item_id": w["id"], "quantity_delta": -1},
            {"item_id": b["id"], "quantity_delta": 2},
        ],
    )
    assert r.status_code == 201, r.text
    [txn] = _adjustment_entries(db_session)
    on_5900 = {
        r.description: (r.debit, r.credit)
        for r in db_session.query(TransactionLine).filter_by(
            transaction_id=txn.id, account_id=acc.id
        )
    }
    assert on_5900 == {
        "Inventory adjustment loss: Widget": (Decimal("4.00"), Decimal("0")),
        "Inventory adjustment gain: Bolt": (Decimal("0"), Decimal("1.00")),
    }


def test_one_movement_per_item_names_the_adjustment(client, db_session, stock):
    w, b = stock["widget"], stock["bolt"]
    adj = _post(
        client,
        [
            {"item_id": w["id"], "quantity_delta": 3},
            {"item_id": b["id"], "new_quantity": 25},
        ],
    ).json()
    for item, qty in ((w, 3), (b, 5)):
        history = client.get(f"/api/items/{item['id']}/movements").json()
        mine = [m for m in history if m["source_type"] == SOURCE]
        assert len(mine) == 1
        assert mine[0]["source_id"] == adj["id"]
        assert mine[0]["movement_type"] == "adjustment"
        assert Decimal(mine[0]["quantity"]) == qty
        assert mine[0]["transaction_id"] == adj["transaction_id"]
    # the valuation reads them, and agrees with the ledger
    val = client.get("/api/items/valuation").json()
    assert val["total_value"] == pytest.approx(13 * 4 + 25 * 0.5 + 5 * 12)
    assert val["ledger_balance"] == pytest.approx(val["total_value"])
    assert val["ledger_note"] is None


def test_the_adjustment_reads_back_with_every_line(client, stock):
    w, g = stock["widget"], stock["gear"]
    made = _post(
        client,
        [
            {"item_id": w["id"], "quantity_delta": 1},
            {"item_id": g["id"], "quantity_delta": -1},
        ],
    ).json()
    one = client.get(f"/api/inventory/adjustments/{made['id']}")
    assert one.status_code == 200 and one.json() == made
    listed = client.get("/api/inventory/adjustments").json()
    assert [a["id"] for a in listed] == [made["id"]]
    missing = client.get("/api/inventory/adjustments/9999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Inventory adjustment not found"


def test_it_is_one_entry_on_the_journal_page_and_not_voided_there(
    client, db_session, stock
):
    w, b = stock["widget"], stock["bolt"]
    adj = _post(
        client,
        [
            {"item_id": w["id"], "quantity_delta": 2},
            {"item_id": b["id"], "quantity_delta": -2},
        ],
    ).json()
    entries = client.get("/api/journal").json()["entries"]
    mine = [e for e in entries if e["source_type"] == SOURCE]
    assert len(mine) == 1
    [entry] = mine
    assert entry["id"] == adj["transaction_id"]
    assert entry["description"].startswith(f"Inventory adjustment #{adj['id']}")
    assert entry["total_debit"] == entry["total_credit"] == pytest.approx(9.0)
    assert entry["voidable"] is False
    r = client.post(f"/api/journal/{entry['id']}/void")
    assert r.status_code == 400
    assert r.json()["detail"] == (
        f"This is inventory adjustment #{adj['id']}'s entry. It can't be voided "
        "here, since the counts it moved would stay; correct them with another "
        "inventory adjustment."
    )
    assert len(_adjustment_entries(db_session)) == 1
    assert db_session.query(Transaction).filter_by(source_id=entry["id"]).count() == 0


def test_a_residue_goes_in_the_same_entry(client, db_session, stock):
    """A count up into a count below zero fills the hole first; the difference
    from the average of the time goes to cost of goods in the one entry, and
    the ledger still reads quantity x average."""
    w, b = stock["widget"], stock["bolt"]
    r = client.post(f"/api/items/{w['id']}/adjust", json={"quantity_delta": -13})
    assert r.status_code == 200, r.text
    assert _count(db_session, w["id"]) == -3
    r = _post(
        client,
        [
            {"item_id": w["id"], "quantity_delta": 5, "unit_cost": 7},
            {"item_id": b["id"], "quantity_delta": 1},
        ],
    )
    assert r.status_code == 201, r.text
    [txn] = _adjustment_entries(db_session)
    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert len(rows) == 6  # two lines per item and the residue's two
    assert sum(r.debit for r in rows) == sum(r.credit for r in rows)
    cogs = db_session.query(Account).filter_by(account_type=AccountType.COGS).first().id
    residue = [r for r in rows if r.description == "COGS: Widget"]
    assert [(r.account_id, r.debit) for r in residue] == [(cogs, Decimal("9.00"))]
    val = client.get("/api/items/valuation").json()
    assert val["ledger_balance"] == pytest.approx(val["total_value"])
    # no entry of its own for the residue
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type.in_((SOURCE, "adjustment")))
        .count()
        == 2
    )


def _nothing_posted(db_session, stock):
    assert _adjustment_entries(db_session) == []
    assert db_session.query(InventoryAdjustment).count() == 0
    assert (
        db_session.query(InventoryMovement).filter_by(source_type=SOURCE).count() == 0
    )
    assert _count(db_session, stock["widget"]["id"]) == 10
    assert _count(db_session, stock["bolt"]["id"]) == 20


def test_a_wrong_line_refuses_the_whole_adjustment(client, db_session, stock):
    w, b, labor = stock["widget"], stock["bolt"], stock["labor"]
    r = _post(
        client,
        [
            {"item_id": w["id"], "quantity_delta": 5},
            {"item_id": labor["id"], "quantity_delta": 1},
            {"item_id": 99999, "quantity_delta": 1},
            {"item_id": w["id"], "quantity_delta": 1},
            {"item_id": b["id"], "quantity_delta": 0},
        ],
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Nothing was posted. "
        "Line 2 (Labor hour): this item is not inventory-tracked, so it has no "
        "count to adjust. "
        "Line 3: there is no item with id 99999. "
        "Line 4 (Widget): Widget is on line 1 already; put its whole change on "
        "one line. "
        "Line 5 (Bolt): the change is zero; take the line out or enter the change."
    )
    _nothing_posted(db_session, stock)


@pytest.mark.parametrize(
    "line, sentence",
    [
        (
            {"quantity_delta": 1, "new_quantity": 3},
            "Line 1 (Widget): give the change or the new quantity, one of the two.",
        ),
        ({}, "Line 1 (Widget): give the change or the new quantity, one of the two."),
        (
            {"new_quantity": -1},
            "Line 1 (Widget): a new quantity cannot be below zero.",
        ),
        (
            {"new_quantity": 10},
            "Line 1 (Widget): the count is 10 already; take the line out or enter "
            "the new count.",
        ),
        (
            {"quantity_delta": 2, "unit_cost": -1},
            "Line 1 (Widget): a unit cost cannot be below zero.",
        ),
    ],
)
def test_each_sentence_names_the_line_and_the_item(
    client, db_session, stock, line, sentence
):
    r = _post(client, [{"item_id": stock["widget"]["id"], **line}])
    assert r.status_code == 400
    assert r.json()["detail"] == "Nothing was posted. " + sentence
    _nothing_posted(db_session, stock)


def test_no_lines_and_too_many_lines_are_refused(client, db_session, stock):
    r = _post(client, [])
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "An adjustment needs at least one line: an item and its change or new "
        "quantity."
    )
    r = client.post("/api/inventory/adjustments", json={"memo": "nothing"})
    assert r.status_code == 400
    many = [{"item_id": stock["widget"]["id"], "quantity_delta": 1}] * 501
    r = _post(client, many)
    assert r.status_code == 400
    assert "at most 500 lines; this one has 501" in r.json()["detail"]
    _nothing_posted(db_session, stock)


def test_a_closed_period_refuses_it_and_posts_nothing(client, db_session, stock):
    r = client.put("/api/settings", json={"closing_date": date.today().isoformat()})
    assert r.status_code == 200, r.text
    r = _post(client, [{"item_id": stock["widget"]["id"], "quantity_delta": 1}])
    assert r.status_code in (400, 403, 409), r.text
    assert "closing date" in r.json()["detail"]
    _nothing_posted(db_session, stock)


def test_one_item_at_the_top_level_is_one_line(client, db_session, stock):
    w = stock["widget"]
    r = client.post(
        "/api/inventory/adjustments",
        json={"item_id": w["id"], "quantity_delta": -2, "memo": "spoiled"},
    )
    assert r.status_code == 201, r.text
    adj = r.json()
    assert adj["line_count"] == 1 and adj["memo"] == "spoiled"
    assert adj["lines"][0]["memo"] == "spoiled"
    assert _count(db_session, w["id"]) == 8
    both = client.post(
        "/api/inventory/adjustments",
        json={
            "item_id": w["id"],
            "quantity_delta": 1,
            "lines": [{"item_id": w["id"], "quantity_delta": 1}],
        },
    )
    assert both.status_code == 400
    assert both.json()["detail"] == (
        "Send the items as lines, or one item at the top level, not both."
    )
    nameless = client.post("/api/inventory/adjustments", json={"quantity_delta": 1})
    assert nameless.status_code == 400
    assert "Name the item to adjust" in nameless.json()["detail"]
    unknown = client.post(
        "/api/inventory/adjustments", json={"lines": [], "extra": True}
    )
    assert unknown.status_code == 422


def test_the_one_item_route_is_unchanged(client, db_session, stock):
    w = stock["widget"]
    r = client.post(
        f"/api/items/{w['id']}/adjust",
        json={"quantity_delta": -3, "memo": "shrinkage"},
    )
    assert r.status_code == 200, r.text
    mv = r.json()
    assert set(mv) == {
        "id",
        "item_id",
        "date",
        "movement_type",
        "quantity",
        "unit_cost",
        "balance_qty",
        "balance_avg_cost",
        "source_type",
        "source_id",
        "transaction_id",
        "memo",
        "reverses_movement_id",
    }
    assert (mv["source_type"], mv["source_id"]) == ("adjustment", w["id"])
    assert Decimal(mv["quantity"]) == -3 and mv["memo"] == "shrinkage"
    txn = db_session.get(Transaction, mv["transaction_id"])
    assert (txn.source_type, txn.source_id) == ("adjustment", w["id"])
    assert txn.description == "Inventory adjustment — Widget"
    assert _adjustment_entries(db_session) == []
    assert db_session.query(InventoryAdjustment).count() == 0
    # its refusals are as they were
    r = client.post(f"/api/items/{w['id']}/adjust", json={"quantity_delta": 0})
    assert r.status_code == 400
    assert r.json()["detail"] == "No adjustment recorded (zero delta?)"
    labor = stock["labor"]
    r = client.post(f"/api/items/{labor['id']}/adjust", json={"quantity_delta": 1})
    assert r.json()["detail"] == "Item is not inventory-tracked"


def _users(db_session):
    for role in ("readonly", "accountant", "bookkeeper"):
        db_session.add(
            User(
                username=role + "-801",
                display_name=role.title(),
                password_hash=auth_service.hash_password(USER_PW),
                role=role,
                is_active=True,
            )
        )
    db_session.commit()


def _login(client, role):
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": role + "-801", "password": USER_PW}
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("role", ["readonly", "accountant"])
def test_a_read_only_or_accountant_sign_in_reads_but_does_not_post(
    client, db_session, stock, role
):
    made = _post(client, [{"item_id": stock["bolt"]["id"], "quantity_delta": 1}]).json()
    _users(db_session)
    _login(client, role)
    r = _post(client, [{"item_id": stock["widget"]["id"], "quantity_delta": 1}])
    assert r.status_code == 403
    assert r.json()["detail"]
    assert db_session.query(InventoryAdjustment).count() == 1
    assert _count(db_session, stock["widget"]["id"]) == 10
    one = client.get(f"/api/inventory/adjustments/{made['id']}")
    assert one.status_code == 200 and one.json()["line_count"] == 1
    assert client.get("/api/inventory/adjustments").status_code == 200


def test_a_bookkeeper_posts_one(client, db_session, stock):
    _users(db_session)
    _login(client, "bookkeeper")
    r = _post(
        client,
        [
            {"item_id": stock["widget"]["id"], "quantity_delta": 1},
            {"item_id": stock["bolt"]["id"], "quantity_delta": 1},
        ],
    )
    assert r.status_code == 201, r.text
    assert r.json()["line_count"] == 2


def test_the_migration_adds_the_table_and_walks_back(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    db = tmp_path / "adj.db"
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("b7d2e4f6a8c1").down_revision == "c7e3a9d5f1b4"

    def tables():
        with sqlite3.connect(db) as conn:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

    command.upgrade(cfg, "c7e3a9d5f1b4")  # the step below it (#795)
    assert "inventory_adjustments" not in tables()
    command.upgrade(cfg, "b7d2e4f6a8c1")  # its own step, not the chain (#770)
    assert "inventory_adjustments" in tables()
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(inventory_adjustments)")}
    assert cols == {"id", "date", "memo", "transaction_id", "created_at"}
    command.downgrade(cfg, "c7e3a9d5f1b4")
    assert "inventory_adjustments" not in tables()
