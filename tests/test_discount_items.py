"""A discount or concession item of the user's own (#639, from discussion
#633).

A negative line is a discount. Until now only the item a QuickBooks Online
discount came in on could carry one (tests/test_discount_item_in_form.py);
an item made here was refused with "rate must be non-negative; use a
credit memo for refunds". An item now says so itself (allows_negative_price,
the item form's "Discount or concession" box), one rule covers both kinds
(services/discount_items.py), the discount comes off the item's income
account on the accrual and the cash basis, and every other item is refused
as before, in the same words. The migration that added the flag marks the
items a QuickBooks Online discount mapping names."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.models.transactions import (
    BASIS_CASH,
    LEDGER_BASIS_OPTION,
    READ_ALL,
    TransactionLine,
)

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d2f7a9c4e6b1"
BEFORE = "b1a26772cc4e"  # the credit memo line classes migration (#640), which this one follows
REFUSAL = "Value error, rate must be non-negative; use a credit memo for refunds"


def _item(client, name, **extra):
    body = {"name": name, "item_type": "service", "rate": 0, **extra}
    r = client.post("/api/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _discount_account(db_session):
    """An income account of the discount item's own, so the test sees the
    discount land there and not on the sale's income account."""
    from app.models.accounts import Account, AccountType

    account = Account(
        account_number="4955",
        name="Discounts given",
        account_type=AccountType.INCOME,
        balance=Decimal("0"),
    )
    db_session.add(account)
    db_session.commit()
    return account


def _lines(db_session, transaction_id):
    return (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == transaction_id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    )


def _posted(lines):
    return sorted(
        (ln.account_id, Decimal(str(ln.debit)), Decimal(str(ln.credit))) for ln in lines
    )


def _sale_lines(discount_item_id):
    return [
        {"description": "Catering", "quantity": 1, "rate": 100, "line_order": 0},
        {
            "item_id": discount_item_id,
            "description": "Loyalty discount",
            "quantity": 1,
            "rate": -10,
            "line_order": 1,
        },
    ]


def test_a_marked_item_takes_a_negative_line_on_an_invoice(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice
    from app.services.accounting import (
        get_ar_account_id,
        get_default_income_account_id,
    )

    discounts = _discount_account(db_session)
    item = _item(
        client,
        "Loyalty discount",
        allows_negative_price=True,
        income_account_id=discounts.id,
    )
    assert item["allows_negative_price"] is True and item["is_discount"] is True

    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-01",
            "tax_rate": 0,
            "lines": _sale_lines(item["id"]),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert Decimal(str(body["subtotal"])) == Decimal("90")
    assert Decimal(str(body["total"])) == Decimal("90")
    assert Decimal(str(body["balance_due"])) == Decimal("90")

    # A/R carries the net; the sale is income in full and the discount is
    # taken off the discount item's own income account.
    invoice = db_session.get(Invoice, body["id"])
    assert _posted(_lines(db_session, invoice.transaction_id)) == sorted(
        [
            (get_ar_account_id(db_session), Decimal("90"), Decimal("0")),
            (get_default_income_account_id(db_session), Decimal("0"), Decimal("100")),
            (discounts.id, Decimal("10"), Decimal("0")),
        ]
    )


def test_a_marked_item_takes_a_negative_line_on_a_sales_receipt(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice
    from app.models.payments import Payment
    from app.services.accounting import (
        get_ar_account_id,
        get_default_income_account_id,
    )

    discounts = _discount_account(db_session)
    item = _item(
        client,
        "Loyalty discount",
        allows_negative_price=True,
        income_account_id=discounts.id,
    )
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-01",
            "tax_rate": 0,
            "method": "Cash",
            "lines": _sale_lines(item["id"]),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert Decimal(str(body["invoice"]["total"])) == Decimal("90")
    assert Decimal(str(body["invoice"]["balance_due"])) == Decimal("0")
    assert Decimal(str(body["payment"]["amount"])) == Decimal("90")

    invoice = db_session.get(Invoice, body["invoice"]["id"])
    payment = db_session.get(Payment, body["payment"]["id"])
    ar_id = get_ar_account_id(db_session)
    income_id = get_default_income_account_id(db_session)
    # The sale posts as an invoice does (accrual).
    assert _posted(_lines(db_session, invoice.transaction_id)) == sorted(
        [
            (ar_id, Decimal("90"), Decimal("0")),
            (income_id, Decimal("0"), Decimal("100")),
            (discounts.id, Decimal("10"), Decimal("0")),
        ]
    )
    # Paid in full on the spot, the payment recognizes the whole sale on
    # the cash basis: the discount keeps its sign, a debit to its account.
    cash = [
        ln
        for ln in _lines(db_session, payment.transaction_id)
        if ln.basis == BASIS_CASH and ln.recognizes_invoice_id == invoice.id
    ]
    recognized = {
        (ln.account_id, Decimal(str(ln.debit)), Decimal(str(ln.credit))) for ln in cash
    }
    assert (income_id, Decimal("0"), Decimal("100")) in recognized
    assert (discounts.id, Decimal("10"), Decimal("0")) in recognized
    assert sum((d - c for _, d, c in recognized), Decimal("0")) == Decimal("-90")


def test_any_other_item_is_refused_in_the_same_words(
    client, db_session, seed_accounts, seed_customer
):
    plain = _item(client, "Sourdough", rate=8.5)
    on_plain = {
        "item_id": plain["id"],
        "description": "Sourdough",
        "quantity": 1,
        "rate": -10,
        "line_order": 1,
    }
    on_nothing = {"description": "No item", "quantity": 1, "rate": -10, "line_order": 1}
    first = {"description": "Catering", "quantity": 1, "rate": 100, "line_order": 0}
    for line in (on_plain, on_nothing):
        r = client.post(
            "/api/invoices",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-10-01",
                "tax_rate": 0,
                "lines": [first, line],
            },
        )
        assert r.status_code == 422, r.text
        [problem] = r.json()["detail"]
        assert problem["msg"] == REFUSAL
        assert problem["loc"] == ["body", "lines", 1]
        r = client.post(
            "/api/sales-receipts",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-10-01",
                "tax_rate": 0,
                "method": "Cash",
                "lines": [first, line],
            },
        )
        assert r.status_code == 422, r.text
        assert [p["msg"] for p in r.json()["detail"]] == [REFUSAL]
    assert client.get("/api/invoices").json() == []


def test_the_flag_is_set_from_the_api_and_shows_on_the_item(
    client, db_session, seed_accounts, seed_customer
):
    item = _item(client, "Loyalty discount")
    assert item["allows_negative_price"] is False and item["is_discount"] is False
    invoice = {
        "customer_id": seed_customer.id,
        "date": "2026-10-01",
        "tax_rate": 0,
        "lines": _sale_lines(item["id"]),
    }
    assert client.post("/api/invoices", json=invoice).status_code == 422

    r = client.put(f"/api/items/{item['id']}", json={"allows_negative_price": True})
    assert r.status_code == 200, r.text
    assert r.json()["allows_negative_price"] is True and r.json()["is_discount"] is True
    listed = {i["id"]: i for i in client.get("/api/items").json()}
    assert listed[item["id"]]["is_discount"] is True
    assert client.get(f"/api/items/{item['id']}").json()["is_discount"] is True
    assert client.post("/api/invoices", json=invoice).status_code == 201

    # Unmarked, the item is refused a negative price again; the invoice it
    # already carries keeps its line (refuse_negative_lines' edit rule).
    r = client.put(f"/api/items/{item['id']}", json={"allows_negative_price": False})
    assert r.status_code == 200 and r.json()["is_discount"] is False
    assert client.post("/api/invoices", json=invoice).status_code == 422


def test_the_item_form_has_the_box():
    src = (ROOT / "app/static/js/items.js").read_text(encoding="utf-8")
    assert 'name="allows_negative_price"' in src
    assert "Discount or concession: may carry a negative price" in src
    assert "allows_negative_price: form.allows_negative_price" in src
    # offered to product and service items: the type select shows and
    # hides it, and a hidden box is cleared
    assert "const canDiscount = ['product','service'].includes(item.item_type);" in src
    assert "['product','service'].includes(typeSel.value)" in src
    assert "if (negChk && !discountable) negChk.checked = false;" in src


def test_the_items_csv_carries_the_flag(client):
    _item(client, "Loyalty discount", allows_negative_price=True)
    _item(client, "Sourdough", rate=8.5)
    exported = client.get("/api/csv/export/items").text.lstrip("﻿")
    rows = [line.split(",") for line in exported.strip().splitlines()]
    assert rows[0][-2:] == ["Taxable", "Negative Price"]
    by_name = {r[1]: r for r in rows[1:]}
    assert by_name["Loyalty discount"][-1] == "True"
    assert by_name["Sourdough"][-1] == "False"

    r = client.post(
        "/api/csv/import/items",
        files={
            "file": (
                "items.csv",
                b"Name,Type,Rate,Cost,Negative Price\n"
                b"Seasonal discount,service,0,0,yes\n"
                b"Rye,product,6,2,\n"
                b"Copy of loyalty,service,0,0,True\n",
                "text/csv",
            )
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 3 and r.json()["errors"] == []
    flags = {
        i["name"]: i["allows_negative_price"] for i in client.get("/api/items").json()
    }
    assert flags["Seasonal discount"] is True
    assert flags["Copy of loyalty"] is True
    assert flags["Rye"] is False


def test_an_iif_discount_item_comes_in_marked(db_session, seed_accounts):
    from app.models.items import Item, ItemType
    from app.services.iif_import import import_all, validate_iif

    iif = (
        "!INVITEM\tNAME\tINVITEMTYPE\tDESC\tACCNT\tPRICE\n"
        "INVITEM\tLoyalty discount\tDISC\tLoyalty discount\tService Income\t0.00\n"
        "INVITEM\tSourdough\tSERV\t\tService Income\t8.50\n"
    )
    assert not [w for w in validate_iif(iif)["warnings"] if "DISC" in w]
    result = import_all(db_session, iif)
    assert result["errors"] == [], result["errors"]
    items = {i.name: i for i in db_session.query(Item)}
    assert items["Loyalty discount"].allows_negative_price is True
    assert items["Loyalty discount"].item_type == ItemType.SERVICE
    assert items["Sourdough"].allows_negative_price is False


def test_the_migration_adds_the_flag_and_marks_the_mapped_items(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "discounts.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(REVISION).down_revision == BEFORE

    # Books from before the flag: an item a QuickBooks Online discount came
    # in on (its mapping names it) and an ordinary one.
    command.upgrade(cfg, BEFORE)
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        for item_id, name in ((1, "Discount"), (2, "Sourdough")):
            conn.execute(
                text(
                    "INSERT INTO items (id, name, item_type, is_taxable, is_active, "
                    "track_inventory, quantity_on_hand, reorder_point, avg_cost) "
                    "VALUES (:id, :name, 'SERVICE', 1, 1, 0, 0, 0, 0)"
                ),
                {"id": item_id, "name": name},
            )
        conn.execute(
            text(
                "INSERT INTO qbo_mappings (entity_type, slowbooks_id, qbo_id) "
                "VALUES ('discount_item', 1, '99')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    cols = {c["name"]: c for c in inspect(engine).get_columns("items")}
    assert cols["allows_negative_price"]["nullable"] is False
    with engine.connect() as conn:
        flags = dict(
            conn.execute(
                text("SELECT id, allows_negative_price FROM items ORDER BY id")
            ).all()
        )
    assert flags == {1: 1, 2: 0}
    engine.dispose()

    command.downgrade(cfg, BEFORE)
    engine = create_engine(f"sqlite:///{db}")
    names = {c["name"] for c in inspect(engine).get_columns("items")}
    assert "allows_negative_price" not in names and "code" in names
    engine.dispose()
