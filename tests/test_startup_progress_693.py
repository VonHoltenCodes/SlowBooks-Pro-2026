"""#693: a big company file that never started after an upgrade.

The model declares transaction_lines(transaction_id) and (account_id)
indexed, but no migration created them, so a file built by migrations had
neither and the cash-basis backfill read the whole table once per payment.
The launcher then stopped the server after two minutes, every time. These
tests build the file the way a customer's is built (alembic, not
create_all), check the launcher waits while the start-up reports progress,
and check the backfill reports it."""

import json
from pathlib import Path
from types import SimpleNamespace

import desktop_launcher as dl
from app.services import startup_progress

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d7f3a9c1b5e2"
BEFORE = "c4e8a1f7d2b6"
OURS = {"ix_transaction_lines_transaction_id", "ix_transaction_lines_account_id"}


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


def _indexes(db):
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{db}")
    try:
        return {
            i["name"]: i["column_names"]
            for i in inspect(engine).get_indexes("transaction_lines")
        }
    finally:
        engine.dispose()


def test_a_file_built_by_migrations_has_both_indexes(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db = tmp_path / "fresh.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == BEFORE
    command.upgrade(cfg, "head")
    indexes = _indexes(db)
    assert indexes["ix_transaction_lines_transaction_id"] == ["transaction_id"]
    assert indexes["ix_transaction_lines_account_id"] == ["account_id"]


def test_hand_made_indexes_do_not_stop_ours_and_downgrade_drops_only_ours(
    tmp_path,
):
    from alembic import command
    from sqlalchemy import create_engine, text

    db = tmp_path / "customer.db"
    cfg = _cfg(db)
    command.upgrade(cfg, BEFORE)
    assert not OURS & set(_indexes(db))  # the file the customer had
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX ix_tmp_tl_txn ON transaction_lines (transaction_id)")
        )
        conn.execute(
            text("CREATE INDEX ix_tmp_tl_acct ON transaction_lines (account_id)")
        )
    engine.dispose()

    command.upgrade(cfg, "head")
    names = set(_indexes(db))
    assert OURS <= names and {"ix_tmp_tl_txn", "ix_tmp_tl_acct"} <= names
    command.upgrade(cfg, "head")  # a second run has nothing to do

    command.downgrade(cfg, BEFORE)
    names = set(_indexes(db))
    assert not OURS & names
    assert {"ix_tmp_tl_txn", "ix_tmp_tl_acct"} <= names


# ---------------------------------------------------------------------------
# the launcher's wait
# ---------------------------------------------------------------------------


class _Proc:
    def poll(self):
        return None  # alive the whole time


def _fake_world(monkeypatch, on_tick):
    """A clock that moves 0.5 s per sleep, a /health that never answers,
    and on_tick(now) at every sleep (the server writing its note)."""
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds
        on_tick(clock["now"])

    monkeypatch.setattr(
        dl, "time", SimpleNamespace(monotonic=lambda: clock["now"], sleep=sleep)
    )

    def refuse(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(dl.urllib.request, "urlopen", refuse)
    return clock


def _write(path, done, total):
    path.write_text(
        json.dumps(
            {
                "label": "Catching up cash-basis lines (payments)",
                "done": done,
                "total": total,
                "updated": done,
            }
        )
    )


def test_the_wait_goes_on_while_progress_moves_and_stops_when_it_stalls(
    tmp_path, monkeypatch
):
    note = tmp_path / startup_progress.FILENAME
    seen = []

    def server(now):
        # one payment every 2 s until 400 s in, then stuck
        if now <= 400 and now % 2 == 0:
            _write(note, int(now // 2), 8580)

    clock = _fake_world(monkeypatch, server)
    ok = dl.wait_for_health(
        _Proc(),
        3001,
        timeout=120,
        progress_path=note,
        on_progress=seen.append,
        stall=60,
    )
    assert ok is False
    # well past the two minutes while it moved; gave up a minute after it stopped
    assert 460 <= clock["now"] <= 462
    assert seen[0] == "Catching up cash-basis lines (payments), 1 of 8,580"
    assert seen[-1] == "Catching up cash-basis lines (payments), 200 of 8,580"


def test_with_no_progress_the_timeout_holds(tmp_path, monkeypatch):
    clock = _fake_world(monkeypatch, lambda now: None)
    ok = dl.wait_for_health(
        _Proc(), 3001, timeout=120, progress_path=tmp_path / "none.json", stall=60
    )
    assert ok is False and 120 <= clock["now"] <= 121


def test_the_wait_ends_when_health_answers(tmp_path, monkeypatch):
    note = tmp_path / startup_progress.FILENAME
    healthy = {"yes": False}

    def server(now):
        _write(note, int(now), 1000)
        healthy["yes"] = now >= 300

    _fake_world(monkeypatch, server)

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(*a, **k):
        if healthy["yes"]:
            return _Resp()
        raise OSError("not yet")

    monkeypatch.setattr(dl.urllib.request, "urlopen", urlopen)
    assert dl.wait_for_health(_Proc(), 3001, timeout=120, progress_path=note)


def test_the_note_reads_as_a_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    startup_progress.clear()
    startup_progress.report("Catching up cash-basis lines", 1200, 8580)
    state = startup_progress.read(tmp_path / startup_progress.FILENAME)
    assert startup_progress.describe(state) == (
        "Catching up cash-basis lines, 1,200 of 8,580"
    )
    startup_progress.report("Checking account balances against the ledger")
    state = startup_progress.read(tmp_path / startup_progress.FILENAME)
    assert startup_progress.describe(state) == (
        "Checking account balances against the ledger"
    )
    startup_progress.clear()
    assert not (tmp_path / startup_progress.FILENAME).exists()


# ---------------------------------------------------------------------------
# the backfill reports its counts
# ---------------------------------------------------------------------------


def test_the_backfill_reports_its_counts(
    client, db_session, seed_accounts, seed_customer
):
    from app.services import cash_basis
    from tests.test_cash_basis import _invoice, _payment, _strip_cash_lines

    cust = seed_customer.id
    for day in range(10, 13):
        inv = _invoice(client, cust, "2026-03-01", [{"rate": 10}])
        _payment(client, cust, f"2026-03-{day}", 10, [(inv["id"], 10)])
    _strip_cash_lines(db_session)

    notes = []
    done = cash_basis.backfill(db_session, on_progress=lambda *note: notes.append(note))
    assert done["payments"] == 3
    counted = [n for n in notes if n[0] == "Catching up cash-basis lines (payments)"]
    assert [(d, t) for _, d, t in counted] == [(0, 3), (1, 3), (2, 3), (3, 3)]
    # nothing left, nothing counted
    notes.clear()
    cash_basis.backfill(db_session, on_progress=lambda *note: notes.append(note))
    assert all(n[1] is None for n in notes)
