"""A line added from the bank feed names its payee's vendor (#491): the
vendor of that name when there is one, else the review row offers to make
it one; the entry stores the vendor, so the vendor's page, its paid-to-date
and its payments CSV include feed postings."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.banking import BankTransaction
from app.models.contacts import Vendor
from app.models.transactions import Transaction
from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parent.parent
REVISION = "d7e8f9a0b1c2"


@pytest.fixture
def feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _import(db, feed, *rows):
    import_transactions(
        db,
        feed["id"],
        [
            {
                "fitid": f"V{feed['id']}-{i}-{p}",
                "date": date(2026, 9, d),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (d, a, p) in enumerate(rows)
        ],
    )
    return {
        bt.payee: bt
        for bt in db.query(BankTransaction)
        .filter(BankTransaction.bank_account_id == feed["id"])
        .all()
    }


def _add(client, bt, category, **extra):
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={"category_account_id": category.id, **extra},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_add_names_the_vendor_whose_name_is_the_payee(
    client, db_session, seed_accounts, feed
):
    v = client.post("/api/vendors", json={"name": "NT Suites"}).json()
    lines = _import(db_session, feed, (3, "-450.00", "nt suites "))
    out = _add(client, lines["nt suites "], seed_accounts["6000"])
    assert out["vendor_id"] == v["id"] and out["vendor_name"] == "NT Suites"
    txn = db_session.query(Transaction).get(out["transaction_id"])
    assert txn.vendor_id == v["id"]
    # the review list says it too
    [row] = client.get(
        f"/api/banking/transactions?bank_account_id={feed['id']}&status=added"
    ).json()
    assert row["vendor_name"] == "NT Suites"
    # no vendor was made for a name that already had one
    assert db_session.query(Vendor).count() == 1


def test_a_payee_no_vendor_has_posts_as_text_and_the_row_makes_the_vendor(
    client, db_session, seed_accounts, feed
):
    lines = _import(db_session, feed, (4, "-120.00", "NT Suites"))
    out = _add(client, lines["NT Suites"], seed_accounts["6000"])
    assert out["vendor_id"] is None
    # before: the vendor list is empty — the gap the issue reports
    assert client.get("/api/vendors").json() == []
    r = client.post("/api/banking/transactions/vendors", json={"line_ids": [out["id"]]})
    assert r.status_code == 200, r.text
    assert [v["name"] for v in r.json()["created"]] == ["NT Suites"]
    assert r.json()["linked"] == 1
    [vendor] = client.get("/api/vendors").json()
    assert vendor["name"] == "NT Suites"
    txn = db_session.query(Transaction).get(out["transaction_id"])
    db_session.refresh(txn)
    assert txn.vendor_id == vendor["id"]
    # asked again, nothing more is made
    r = client.post("/api/banking/transactions/vendors", json={"line_ids": [out["id"]]})
    assert r.json() == {"created": [], "linked": 0}
    assert len(client.get("/api/vendors").json()) == 1


def test_attach_skips_money_in_transfers_and_lines_not_added(
    client, db_session, seed_accounts, feed
):
    lines = _import(
        db_session,
        feed,
        (5, "75.00", "REFUND CO"),
        (6, "-30.00", "TO SAVINGS"),
        (7, "-9.00", "WAITING"),
    )
    _add(client, lines["REFUND CO"], seed_accounts["4000"])
    _add(client, lines["TO SAVINGS"], seed_accounts["2100"])  # a transfer
    ids = [bt.id for bt in lines.values()]
    r = client.post("/api/banking/transactions/vendors", json={"line_ids": ids})
    assert r.json() == {"created": [], "linked": 0}
    assert db_session.query(Vendor).count() == 0


def test_an_explicit_vendor_wins_and_an_unknown_one_is_refused(
    client, db_session, seed_accounts, feed
):
    landlord = client.post("/api/vendors", json={"name": "Landlord LLC"}).json()
    client.post("/api/vendors", json={"name": "ACH DEBIT"})
    lines = _import(db_session, feed, (8, "-2000.00", "ACH DEBIT"), (9, "-5", "X"))
    out = _add(
        client, lines["ACH DEBIT"], seed_accounts["6000"], vendor_id=landlord["id"]
    )
    assert out["vendor_name"] == "Landlord LLC"
    r = client.post(
        f"/api/banking/transactions/{lines['X'].id}/add",
        json={"category_account_id": seed_accounts["6000"].id, "vendor_id": 99999},
    )
    assert r.status_code == 404
    db_session.refresh(lines["X"])
    assert lines["X"].match_status == "unmatched"


def test_add_all_names_known_vendors_and_lists_the_rest(
    client, db_session, seed_accounts, feed
):
    known = client.post("/api/vendors", json={"name": "City Water"}).json()
    lines = _import(
        db_session,
        feed,
        (2, "-10", "CITY WATER"),
        (3, "-11", "Corner Hardware"),
        (4, "40", "Deposit from Joe"),
    )
    for bt in lines.values():
        bt.category_account_id = (
            seed_accounts["4000"].id if bt.amount > 0 else seed_accounts["6000"].id
        )
    db_session.commit()
    out = client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all").json()
    assert out["added"] == 3 and out["skipped"] == []
    assert out["without_vendor"] == [lines["Corner Hardware"].id]
    water = db_session.query(Transaction).get(lines["CITY WATER"].transaction_id)
    assert water.vendor_id == known["id"]
    r = client.post(
        "/api/banking/transactions/vendors", json={"line_ids": out["without_vendor"]}
    ).json()
    assert [v["name"] for v in r["created"]] == ["Corner Hardware"]


def test_the_vendor_page_and_payments_csv_include_feed_postings(
    client, db_session, seed_accounts, feed
):
    v = client.post("/api/vendors", json={"name": "NT Suites"}).json()
    lines = _import(
        db_session, feed, (3, "-450.00", "NT Suites"), (10, "-50.00", "NT Suites ")
    )
    first = _add(client, lines["NT Suites"], seed_accounts["6000"])
    second = _add(client, lines["NT Suites "], seed_accounts["6000"])
    # the second is voided: listed as void, left out of the total
    assert (
        client.post(f"/api/banking/entries/{second['transaction_id']}/void").status_code
        == 200
    )
    entries = client.get(f"/api/vendors/{v['id']}/bank-entries").json()
    assert [e["transaction_id"] for e in entries] == [
        second["transaction_id"],
        first["transaction_id"],
    ]
    assert entries[1]["amount"] == "450.00" and not entries[1]["is_voided"]
    assert entries[1]["account_name"] == seed_accounts["1000"].name
    assert entries[1]["link"] == f"/#/journal/{first['transaction_id']}"
    assert entries[0]["is_voided"]
    csv = client.get(f"/api/vendors/{v['id']}/payments/csv").text
    assert "Bank entry" in csv and "450.00" in csv
    total = [ln for ln in csv.splitlines() if "Total paid" in ln][0]
    assert "450.00" in total
    # another vendor's page shows none of it
    other = client.post("/api/vendors", json={"name": "Other"}).json()
    assert client.get(f"/api/vendors/{other['id']}/bank-entries").json() == []
    assert client.get("/api/vendors/99999/bank-entries").status_code == 404


def test_the_vendor_details_dialog_reads_the_bank_entries():
    js = (ROOT / "app/static/js/vendors.js").read_text(encoding="utf-8")
    # the page lists them in its payment history (#569), typed "Bank entry"
    assert "/payment-history" in js and "bank_entry: 'Bank entry'" in js
    banking = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "_offerFeedVendors" in banking and "/banking/transactions/vendors" in banking


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + Path(db).as_posix()
    return cfg


def test_the_migration_adds_the_column_and_walks_back(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "vendor.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(REVISION).down_revision == "c4e5d6f7a8b9"
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    assert "vendor_id" in {c["name"] for c in insp.get_columns("transactions")}
    fks = insp.get_foreign_keys("transactions")
    assert any(f["referred_table"] == "vendors" for f in fks)
    assert "ix_transactions_vendor_id" in {
        i["name"] for i in insp.get_indexes("transactions")
    }
    engine.dispose()
    command.downgrade(cfg, "c4e5d6f7a8b9")
    engine = create_engine(f"sqlite:///{db}")
    assert "vendor_id" not in {
        c["name"] for c in inspect(engine).get_columns("transactions")
    }
    engine.dispose()
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
