"""Three races between two sign-ins on PostgreSQL (#501), each proved with
two real sessions on a scratch database:

1. One receipt deposited twice: Make Deposits locked the payment lines it
   took but read back the copies waiting_items() had already put in the
   session, so a deposit that committed while it waited for the lock went
   unseen. The locked query now refreshes them (populate_existing).
2. A write-off without a lock: it read the invoice plainly, so a payment
   landing at the same moment was a lost update and A/R was off by it. The
   write-off now row-locks the invoice, as the payment path does.
3. Duplicate bill numbers: Enter Bill's check was SELECT-then-INSERT. A
   unique index on (vendor_id, bill_number) refuses the second, with the
   409 the check gives; the migration renames any duplicates first.

They need SLOWBOOKS_TEST_POSTGRES_URL (an admin URL on a server the test may
CREATE/DROP DATABASE on) and skip without it. The SQLite tests at the end
pin that nothing changes there."""

import contextlib
import os
import threading
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
REVISION = "f2e3d4c5b6a7"
WAIT = 3.0  # how long a session holds its place for the other one


@contextlib.contextmanager
def _scratch_pg():
    """An empty PostgreSQL database, created here and dropped after."""
    import sqlalchemy as sa

    admin_url = os.environ.get("SLOWBOOKS_TEST_POSTGRES_URL")
    if not admin_url:
        pytest.skip("SLOWBOOKS_TEST_POSTGRES_URL is not set")
    pytest.importorskip("psycopg2")
    name = "slowbooks_races501_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - depends on the machine
        admin.dispose()
        pytest.skip(f"cannot create a scratch PostgreSQL database: {exc}")
    url = sa.engine.make_url(admin_url).set(database=name)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def pg(monkeypatch):
    """A scratch PostgreSQL database with the app's schema and chart, an
    authenticated client on it and a session factory for the racers."""
    import sqlalchemy as sa
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    import app.main as main
    from app.database import Base, get_db
    from app.models.accounts import Account, AccountType
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
    from tests.conftest import _wire_app

    with _scratch_pg() as url:
        engine = sa.create_engine(url)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, autoflush=False)
        with Session() as s:
            accts = {}
            for data in CHART_OF_ACCOUNTS:
                a = Account(
                    account_number=data["account_number"],
                    name=data["name"],
                    account_type=AccountType(data["account_type"]),
                    bank_kind=data.get("bank_kind"),
                    detail_type=data.get("detail_type"),
                    is_system=True,
                    balance=Decimal("0"),
                )
                s.add(a)
                accts[a.account_number] = a
            s.commit()
            ids = {n: a.id for n, a in accts.items()}
        # what reads SessionLocal itself (sign-in, settings) uses it too
        import app.database as db_module

        monkeypatch.setattr(db_module, "engine", engine)
        monkeypatch.setattr(db_module, "SessionLocal", Session)
        _wire_app(Session)
        client = TestClient(main.app)
        r = client.post("/api/auth/setup", json={"password": "test-password-123"})
        assert r.status_code == 200, r.text
        try:
            yield client, Session, ids
        finally:
            main.app.dependency_overrides.pop(get_db, None)
            engine.dispose()


def _race(*workers):
    """Run each worker in its own thread with its own outcome slot."""
    out = [None] * len(workers)

    def run(i, fn):
        try:
            out[i] = fn()
        except HTTPException as exc:
            out[i] = exc
        except Exception as exc:  # noqa: BLE001 - a 500 is a finding too
            out[i] = exc

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "a session hung"
    return out


def _customer_invoice(client, amount):
    cid = client.post("/api/customers", json={"name": "Acme Diner"}).json()["id"]
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return cid, r.json()


# -- 1. one receipt deposited twice -----------------------------------------


def test_two_deposits_taking_the_same_payment_on_postgres(pg, monkeypatch):
    from app.routes import deposits
    from app.schemas.deposits import DepositCreate

    client, Session, ids = pg
    cid, inv = _customer_invoice(client, "250.00")
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": "250.00",
            "method": "Check",
            "allocations": [{"invoice_id": inv["id"], "amount": "250.00"}],
        },
    )
    assert r.status_code == 201, r.text
    [row] = client.get("/api/deposits/pending").json()
    line_id = row["transaction_line_id"]

    # both sign-ins have the payment in their session, as Make Deposits
    # reads it, before either deposits
    both_read = threading.Barrier(2)
    real = deposits.waiting_items

    def waiting_then_meet(db, uf_id=None):
        rows = real(db, uf_id)
        both_read.wait(WAIT)
        return rows

    monkeypatch.setattr(deposits, "waiting_items", waiting_then_meet)

    def deposit():
        with Session() as db:
            data = DepositCreate(
                deposit_to_account_id=ids["1000"],
                date=date(2026, 9, 11),
                line_ids=[line_id],
            )
            return deposits.create_deposit(data, db)

    a, b = _race(deposit, deposit)
    outcomes = sorted(
        "409" if isinstance(x, HTTPException) and x.status_code == 409 else "ok"
        for x in (a, b)
    )
    assert outcomes == ["409", "ok"], (a, b)
    reg = client.get(f"/api/banking/check-register?account_id={ids['1000']}").json()
    assert reg["balance"] == 250.0  # deposited once, not 500


# -- 2. a write-off and a payment at the same moment ------------------------


def test_a_write_off_and_a_payment_at_once_lose_neither_on_postgres(pg, monkeypatch):
    from app.routes import payments
    from app.routes.invoices import lifecycle
    from app.schemas.payments import PaymentCreate

    client, Session, ids = pg
    cid, inv = _customer_invoice(client, "100.00")

    # the write-off has read the invoice; the payment goes now, and the
    # write-off carries on once the payment is in (or after WAIT, when the
    # payment is waiting for the write-off's lock)
    read, paid = threading.Event(), threading.Event()
    real = lifecycle._post_write_off

    def post_after_payment(db, inv_, *args, **kw):
        read.set()
        paid.wait(WAIT)
        return real(db, inv_, *args, **kw)

    monkeypatch.setattr(lifecycle, "_post_write_off", post_after_payment)

    def write_off():
        with Session() as db:
            data = lifecycle.WriteOffRequest(date=date(2026, 9, 20))
            return lifecycle.write_off_invoice(inv["id"], data, db)

    def pay():
        read.wait(WAIT)
        try:
            with Session() as db:
                data = PaymentCreate(
                    customer_id=cid,
                    date=date(2026, 9, 20),
                    amount=Decimal("60.00"),
                    method="Check",
                    allocations=[{"invoice_id": inv["id"], "amount": "60.00"}],
                )
                return payments.create_payment(data, db)
        finally:
            paid.set()

    w, p = _race(write_off, pay)
    assert not isinstance(w, Exception) or isinstance(w, HTTPException), w
    got = client.get(f"/api/invoices/{inv['id']}").json()
    paid_amount = Decimal("0") if isinstance(p, Exception) else Decimal("60.00")
    written = Decimal(str(w.total)) if not isinstance(w, Exception) else Decimal("0")
    # every dollar of the 100.00 is accounted for exactly once
    assert Decimal(str(got["amount_paid"])) == paid_amount + written, (got, w, p)
    assert Decimal(str(got["balance_due"])) == Decimal("100.00") - paid_amount - written
    assert Decimal(str(got["balance_due"])) >= 0
    # and the ledger's A/R says the same as the invoice
    from sqlalchemy import func

    from app.models.transactions import TransactionLine

    with Session() as db:
        dr, cr = (
            db.query(
                func.coalesce(func.sum(TransactionLine.debit), 0),
                func.coalesce(func.sum(TransactionLine.credit), 0),
            )
            .filter(TransactionLine.account_id == ids["1100"])
            .one()
        )
    assert Decimal(dr) - Decimal(cr) == Decimal(str(got["balance_due"])), (dr, cr, got)


# -- 3. two bills with one vendor number ------------------------------------


def test_two_bills_with_the_same_number_on_postgres(pg, monkeypatch):
    from app.routes import bills
    from app.schemas.bills import BillCreate

    client, Session, ids = pg
    vid = client.post("/api/vendors", json={"name": "Corner Hardware"}).json()["id"]

    # both have passed the duplicate check before either saves
    both_checked = threading.Barrier(2)
    real = bills._due_date_from_terms

    def due_then_meet(*args, **kw):
        both_checked.wait(WAIT)
        return real(*args, **kw)

    monkeypatch.setattr(bills, "_due_date_from_terms", due_then_meet)

    def enter_bill():
        with Session() as db:
            data = BillCreate(
                vendor_id=vid,
                bill_number="INV-1050",
                date=date(2026, 9, 5),
                lines=[
                    {
                        "account_id": ids["6000"],
                        "description": "Lumber",
                        "quantity": 1,
                        "rate": "75.00",
                    }
                ],
            )
            return bills.create_bill(data, db)

    a, b = _race(enter_bill, enter_bill)
    outcomes = sorted(
        "409" if isinstance(x, HTTPException) and x.status_code == 409 else "ok"
        for x in (a, b)
    )
    assert outcomes == ["409", "ok"], (a, b)
    listed = client.get(f"/api/bills?vendor_id={vid}").json()
    assert [b["bill_number"] for b in listed] == ["INV-1050"]
    ap = client.get("/api/reports/ap-aging").json()
    assert ap  # the books still read


def test_the_migration_renames_duplicates_then_indexes_on_postgres():
    """Upgrade a 2.29.0 database holding duplicate pairs on PostgreSQL."""
    with _scratch_pg() as url:
        _walk_migration(url)


# -- the migration, and SQLite unchanged ------------------------------------


def _cfg(url):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


def _walk_migration(url):
    import sqlalchemy as sa
    from alembic import command
    from alembic.script import ScriptDirectory

    cfg = _cfg(url)
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(REVISION).down_revision == "d7e8f9a0b1c2"
    command.upgrade(cfg, "d7e8f9a0b1c2")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO vendors (id, name) VALUES (1, 'A'), (2, 'B')")
        )
        for bill_id, vendor, number in (
            (1, 1, "111"),
            (2, 1, "111"),
            (3, 1, "111 (2)"),
            (4, 1, "111"),
            (5, 2, "111"),
            (6, 1, "112"),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO bills (id, bill_number, vendor_id, date) "
                    "VALUES (:i, :n, :v, '2026-09-01')"
                ),
                {"i": bill_id, "n": number, "v": vendor},
            )
    engine.dispose()
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        got = dict(conn.execute(sa.text("SELECT id, bill_number FROM bills")).all())
    assert got == {
        1: "111",
        2: "111 (3)",
        3: "111 (2)",
        4: "111 (4)",
        5: "111",
        6: "112",
    }
    idx = {i["name"]: i for i in sa.inspect(engine).get_indexes("bills")}
    assert idx["uq_bills_vendor_bill_number"]["unique"]
    assert idx["uq_bills_vendor_bill_number"]["column_names"] == [
        "vendor_id",
        "bill_number",
    ]
    with engine.begin() as conn, pytest.raises(sa.exc.IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO bills (id, bill_number, vendor_id, date) "
                "VALUES (7, '112', 1, '2026-09-02')"
            )
        )
    engine.dispose()
    command.downgrade(cfg, "d7e8f9a0b1c2")
    engine = sa.create_engine(url)
    assert "uq_bills_vendor_bill_number" not in {
        i["name"] for i in sa.inspect(engine).get_indexes("bills")
    }
    engine.dispose()
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)


def test_the_migration_renames_duplicates_then_indexes_on_sqlite(tmp_path):
    _walk_migration("sqlite:///" + (tmp_path / "bills.db").as_posix())


def test_a_second_bill_with_the_number_is_still_a_409_on_sqlite(client, seed_accounts):
    vid = client.post("/api/vendors", json={"name": "Corner Hardware"}).json()["id"]
    body = {
        "vendor_id": vid,
        "bill_number": "INV-1050",
        "date": "2026-09-05",
        "lines": [
            {"account_id": seed_accounts["6000"].id, "quantity": 1, "rate": "75.00"}
        ],
    }
    assert client.post("/api/bills", json=body).status_code == 201
    r = client.post("/api/bills", json=body)
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    # another vendor may use the number
    other = client.post("/api/vendors", json={"name": "Other"}).json()["id"]
    assert (
        client.post("/api/bills", json={**body, "vendor_id": other}).status_code == 201
    )
    # renumbering onto a taken number is a 409 too, and leaves the bill alone
    body2 = {**body, "bill_number": "INV-1051"}
    second = client.post("/api/bills", json=body2).json()
    r = client.put(f"/api/bills/{second['id']}", json={"bill_number": "INV-1050"})
    assert r.status_code == 409
    assert client.get(f"/api/bills/{second['id']}").json()["bill_number"] == "INV-1051"


def test_the_unique_index_answers_409_when_the_check_is_passed(
    db_session, seed_accounts
):
    """The index's own refusal reads as the check's 409 — what the second
    of two simultaneous saves meets on PostgreSQL — on SQLite as well."""
    from app.models.bills import Bill
    from app.models.contacts import Vendor
    from app.routes import bills

    v = Vendor(name="Corner Hardware")
    db_session.add(v)
    db_session.flush()
    db_session.add(Bill(vendor_id=v.id, bill_number="INV-9", date=date(2026, 9, 5)))
    db_session.flush()
    db_session.add(Bill(vendor_id=v.id, bill_number="INV-9", date=date(2026, 9, 6)))
    with pytest.raises(HTTPException) as err:
        bills._flush_number(db_session, "INV-9")
    assert err.value.status_code == 409


def test_a_po_turned_into_a_bill_steps_past_a_number_already_used(
    client, seed_accounts
):
    vid = client.post(
        "/api/vendors",
        json={
            "name": "Corner Hardware",
            "default_expense_account_id": seed_accounts["6000"].id,
        },
    ).json()["id"]
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vid,
            "date": "2026-09-05",
            "lines": [{"description": "Lumber", "quantity": 1, "rate": "40.00"}],
        },
    )
    assert po.status_code == 201, po.text
    po = po.json()
    taken = f"BILL-{po['po_number']}"
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vid,
            "bill_number": taken,
            "date": "2026-09-05",
            "lines": [
                {"account_id": seed_accounts["6000"].id, "quantity": 1, "rate": "1.00"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill")
    assert r.status_code in (200, 201), r.text
    numbers = sorted(
        b["bill_number"] for b in client.get(f"/api/bills?vendor_id={vid}").json()
    )
    assert numbers == [taken, f"{taken}-2"]


def test_sqlite_deposit_and_write_off_paths_are_as_they_were(client, seed_accounts):
    cid, inv = _customer_invoice(client, "100.00")
    r = client.post(
        f"/api/invoices/{inv['id']}/write-off",
        json={"date": "2026-09-20", "amount": "40.00"},
    )
    assert r.status_code == 201, r.text
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert Decimal(str(got["balance_due"])) == Decimal("60.00")
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-21",
            "amount": "60.00",
            "method": "Check",
            "allocations": [{"invoice_id": inv["id"], "amount": "60.00"}],
        },
    )
    assert r.status_code == 201
    [row] = client.get("/api/deposits/pending").json()
    body = {
        "deposit_to_account_id": seed_accounts["1000"].id,
        "date": "2026-09-22",
        "line_ids": [row["transaction_line_id"]],
    }
    assert client.post("/api/deposits", json=body).status_code in (200, 201)
    r = client.post("/api/deposits", json=body)
    assert r.status_code == 400  # already deposited, as before
