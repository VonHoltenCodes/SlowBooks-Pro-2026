"""#666: on SQLite, with_for_update() is a no-op and pysqlite begins no
transaction until the first write, so two simultaneous payments on one
invoice both read the same balance and both pass. app.database now begins
the transaction IMMEDIATE when a statement asks FOR UPDATE on SQLite, so
the second request waits for the first to commit and reads what it left."""

import threading
from decimal import Decimal

import pytest
from sqlalchemy import Column, Integer, Numeric, create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.database import begin_write, enable_sqlite_tuning

Base = declarative_base()


class Thing(Base):
    __tablename__ = "things"
    id = Column(Integer, primary_key=True)
    balance = Column(Numeric(15, 2), nullable=False)


@pytest.fixture
def file_engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'locks.db'}"
    eng = create_engine(url, connect_args={"check_same_thread": False})
    enable_sqlite_tuning(eng)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Thing(id=1, balance=Decimal("100.00")))
        s.commit()
    return eng


def _apply(eng, amount, started, go, results, hold):
    """One request: lock the row, check the balance, hold a moment (the
    other request is in the same place), then apply and commit."""
    Local = sessionmaker(bind=eng)
    with Local() as s:
        started.set()
        go.wait()
        row = s.query(Thing).filter(Thing.id == 1).with_for_update().first()
        if amount > row.balance:
            results.append(("refused", row.balance))
            s.rollback()
            return
        hold.wait(0.3)
        row.balance = row.balance - amount
        s.commit()
        results.append(("applied", row.balance))


def test_two_simultaneous_applications_are_serialized(file_engine):
    results = []
    go = threading.Event()
    hold = threading.Event()
    starts = [threading.Event() for _ in range(4)]
    threads = [
        threading.Thread(
            target=_apply, args=(file_engine, Decimal("98.00"), st, go, results, hold)
        )
        for st in starts
    ]
    for t in threads:
        t.start()
    for st in starts:
        st.wait(5)
    go.set()
    for t in threads:
        t.join(20)
    assert sorted(r[0] for r in results) == ["applied", "refused", "refused", "refused"]
    with Session(file_engine) as s:
        assert s.get(Thing, 1).balance == Decimal("2.00")


def test_begin_write_takes_the_lock_once_and_only_on_sqlite(file_engine):
    with Session(file_engine) as s:
        begin_write(s)
        raw = s.connection().connection.dbapi_connection
        assert raw.in_transaction
        begin_write(s)  # already begun: no second BEGIN, no error
        s.rollback()

    class NotSqlite:
        class dialect:
            name = "postgresql"

    class FakeSession:
        def get_bind(self):
            return NotSqlite()

        def connection(self):  # pragma: no cover - must not be reached
            raise AssertionError("begin_write touched a non-SQLite connection")

    begin_write(FakeSession())


def test_a_plain_read_begins_no_write_transaction(file_engine):
    seen = []

    @event.listens_for(file_engine, "begin")
    def _b(conn):
        seen.append("begin")

    with Session(file_engine) as s:
        s.query(Thing).filter(Thing.id == 1).first()
        raw = s.connection().connection.dbapi_connection
        assert not raw.in_transaction
