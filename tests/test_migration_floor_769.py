"""The migration floor (#769): a company file from before 2.29.0 is not
migrated by this build.

The floor is 2.29.0's migration head. A file at or past it opens and
migrates as before; a file below it is refused, in one sentence that names
the 2.31.14 release, before anything opens it writable: at start-up (the
migrations themselves, the app's start-up guard, the desktop launcher and
its wait for the server), in the company picker's list, and on a backup
restore (SQLite copies and Postgres dumps). A new database, with no
migration history, is still built through the whole chain.
"""

import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

import desktop_launcher as dl
from app.services import backup_service, migration_floor, startup_progress
from app.services.migration_floor import (
    BELOW_FLOOR_MESSAGE,
    MIGRATION_FLOOR,
    BelowMigrationFloor,
)

ROOT = Path(__file__).resolve().parents[1]
SENTENCE = (
    "This company file is from before SlowBooks Pro 2.29.0. Open it once with "
    "2.31.14, then upgrade: "
    "https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/tag/v2.31.14"
)


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


def _url(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(_cfg("sqlite:///unused.db"))


def _one_before_the_floor() -> str:
    return _script().get_revision(MIGRATION_FLOOR).down_revision


def _stamped(path: Path, revision: str, company: str = "Harbor Light Bakery"):
    """A small file stamped at ``revision``: the floor is decided from
    alembic_version before any migration runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        conn.execute("INSERT INTO settings VALUES ('company_name', ?)", (company,))
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('original')")
        conn.commit()
    return path


def _current(path: Path) -> str:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


# ---------------------------------------------------------------------------
# the floor
# ---------------------------------------------------------------------------


def test_the_floor_is_the_head_2_29_0_shipped_the_59th_migration():
    script = _script()
    chain = [r.revision for r in script.walk_revisions()][::-1]
    assert MIGRATION_FLOOR == "c4e5d6f7a8b9"
    assert chain.index(MIGRATION_FLOOR) == 58
    assert len(script.get_heads()) == 1


def test_the_sentence_names_2_29_0_and_the_2_31_14_release_page():
    assert BELOW_FLOOR_MESSAGE == SENTENCE
    assert str(BelowMigrationFloor()) == SENTENCE


def test_where_a_revision_sits_is_walked_on_the_chain_not_compared_as_text():
    script = _script()
    head = script.get_current_head()
    first = [r.revision for r in script.walk_revisions()][-1]
    assert migration_floor.is_at_or_past_floor(MIGRATION_FLOOR)
    assert migration_floor.is_at_or_past_floor(head)
    assert not migration_floor.is_at_or_past_floor(_one_before_the_floor())
    assert not migration_floor.is_at_or_past_floor(first)
    # past the floor though it sorts before it, and below it though it
    # sorts after it
    assert "a3d8f1c6b9e4" < MIGRATION_FLOOR
    assert migration_floor.is_at_or_past_floor("a3d8f1c6b9e4")
    assert "d3e4f5a6b7c8" > MIGRATION_FLOOR
    assert not migration_floor.is_at_or_past_floor("d3e4f5a6b7c8")


def test_a_new_database_and_one_from_a_newer_build_are_not_this_refusals():
    assert migration_floor.is_at_or_past_floor(None)
    assert migration_floor.is_at_or_past_floor([])
    # a revision this build does not know is the newer-version refusal's
    assert migration_floor.is_at_or_past_floor("ffffffffffff")


# ---------------------------------------------------------------------------
# start-up: the migrations, the app's guard
# ---------------------------------------------------------------------------


def test_a_file_stamped_at_the_floor_opens_and_migrates_to_head(tmp_path):
    db = tmp_path / "at-the-floor.db"
    cfg = _cfg(_url(db))
    command.upgrade(cfg, MIGRATION_FLOOR)
    assert _current(db) == MIGRATION_FLOOR
    command.upgrade(cfg, "head")
    assert _current(db) == _script().get_current_head()


def test_a_file_one_step_before_the_floor_is_refused_and_left_byte_identical(
    tmp_path,
):
    db = tmp_path / "before-the-floor.db"
    cfg = _cfg(_url(db))
    command.upgrade(cfg, _one_before_the_floor())
    before = db.read_bytes()
    with pytest.raises(BelowMigrationFloor) as refused:
        command.upgrade(cfg, "head")
    assert str(refused.value) == SENTENCE
    assert db.read_bytes() == before
    assert _current(db) == _one_before_the_floor()


def test_a_new_database_is_still_built_through_the_migrations_below_the_floor(
    tmp_path,
):
    db = tmp_path / "new.db"
    command.upgrade(_cfg(_url(db)), _one_before_the_floor())
    assert _current(db) == _one_before_the_floor()


def test_the_start_up_guard_says_the_sentence_and_records_it(tmp_path, monkeypatch):
    import app.main as main

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    db = _stamped(tmp_path / "old.db", _one_before_the_floor())
    before = db.read_bytes()
    engine = sa.create_engine(_url(db))
    monkeypatch.setattr(main, "engine", engine)
    try:
        with pytest.raises(BelowMigrationFloor) as refused:
            main._refuse_a_database_behind_head()
    finally:
        engine.dispose()
    assert str(refused.value) == SENTENCE
    note = startup_progress.read(tmp_path / startup_progress.FILENAME)
    assert startup_progress.reason(note) == SENTENCE
    assert db.read_bytes() == before


# ---------------------------------------------------------------------------
# the desktop launcher and the company picker
# ---------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "companies").mkdir(parents=True)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(data))
    return data


def _manifest(data: Path, *files: str) -> None:
    (data / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": f.removesuffix(".db"), "file": f} for f in files
                ],
                "last_opened": None,
            }
        ),
        encoding="utf-8",
    )


def test_the_launcher_refuses_a_file_from_before_2_29_0_before_migrating_it(
    data_dir, monkeypatch
):
    old = _stamped(data_dir / "companies" / "old-books.db", _one_before_the_floor())
    before = old.read_bytes()

    def must_not(*a, **k):
        raise AssertionError("a file below the floor reached the migrations")

    monkeypatch.setattr(dl, "migrate", must_not)
    monkeypatch.setattr(dl, "start_server", must_not)
    monkeypatch.setattr(dl, "set_env_value", must_not)
    monkeypatch.setattr(dl, "_server_already_running", lambda port: False)
    with pytest.raises(BelowMigrationFloor) as refused:
        dl.launch_company("old-books.db", 3999)
    assert str(refused.value) == SENTENCE
    assert old.read_bytes() == before
    # recorded where the wait for the server reads its reasons
    note = startup_progress.read(dl.startup_progress_path())
    assert startup_progress.reason(note) == SENTENCE


def test_the_picker_opening_it_shows_the_sentence(data_dir, monkeypatch):
    _stamped(data_dir / "companies" / "old-books.db", _one_before_the_floor())
    monkeypatch.setattr(dl, "_server_already_running", lambda port: False)
    api = dl.PickerApi(3999)
    monkeypatch.setattr(api, "_prepare_server", lambda *a, **k: None)
    assert api.open_company("old-books.db") == {"success": False, "error": SENTENCE}


def test_the_launchers_wait_names_the_servers_reason_instead_of_a_timeout(
    data_dir, monkeypatch
):
    _stamped(data_dir / "companies" / "at-floor.db", MIGRATION_FLOOR)

    class _Refused:
        """A server that refused the file, said why, and exited."""

        def poll(self):
            return 1

    def start_server(*a, **k):
        startup_progress.refuse(SENTENCE, dl.startup_progress_path())
        return _Refused()

    monkeypatch.setattr(dl, "migrate", lambda *a, **k: None)
    monkeypatch.setattr(dl, "start_server", start_server)
    monkeypatch.setattr(dl, "_server_already_running", lambda port: False)
    with pytest.raises(RuntimeError) as refused:
        dl.launch_company("at-floor.db", 3999, persist=False)
    assert str(refused.value) == SENTENCE
    assert "did not become healthy" not in str(refused.value)


def test_the_wait_stops_at_once_on_a_recorded_reason(tmp_path, monkeypatch):
    note = tmp_path / startup_progress.FILENAME
    startup_progress.refuse(SENTENCE, note)
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(
        dl, "time", SimpleNamespace(monotonic=lambda: clock["now"], sleep=sleep)
    )

    class _Alive:
        def poll(self):
            return None

    assert dl.wait_for_health(_Alive(), 3999, timeout=120, progress_path=note) is False
    assert clock["now"] == 0.0


def test_the_pickers_status_carries_the_note_for_that_file_only(data_dir):
    from app.services import company_service

    _stamped(data_dir / "companies" / "old-books.db", _one_before_the_floor())
    _stamped(data_dir / "companies" / "new-books.db", MIGRATION_FLOOR)
    _manifest(data_dir, "old-books.db", "new-books.db")
    listed = {c["file"]: c for c in company_service.manifest_list_companies()}
    assert listed["old-books.db"]["note"] == SENTENCE
    assert "note" not in listed["new-books.db"]
    shown = {c["file"]: c for c in dl.PickerApi(3999).list_companies()["companies"]}
    assert shown["old-books.db"]["note"] == SENTENCE
    # the picker page shows it under the file's name
    assert "c.note" in dl.PICKER_HTML


def test_server_edition_leaves_a_file_from_before_2_29_0_unserved(
    data_dir, monkeypatch, capsys
):
    old = _stamped(data_dir / "companies" / "old-books.db", _one_before_the_floor())
    before = old.read_bytes()
    _stamped(data_dir / "companies" / "new-books.db", MIGRATION_FLOOR)
    _manifest(data_dir, "started.db", "old-books.db", "new-books.db")
    migrated = []
    monkeypatch.setattr(dl, "migrate", lambda url, **k: migrated.append(url))
    skipped = dl._migrate_other_companies("started.db")
    assert skipped == ["old-books.db"]
    assert migrated == [_url(data_dir / "companies" / "new-books.db")]
    assert SENTENCE in capsys.readouterr().out
    assert old.read_bytes() == before


# ---------------------------------------------------------------------------
# backup restore
# ---------------------------------------------------------------------------


class _KeepTheTestConnection:
    def dispose(self):
        pass


@pytest.fixture
def books(tmp_path, monkeypatch, client):
    import app.database as db_module

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "data"))
    live = _stamped(
        tmp_path / "companies" / "harbor-light-bakery.db",
        _script().get_current_head(),
    )
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(backup_service, "DATABASE_URL", _url(live))
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)
    monkeypatch.setattr(db_module, "engine", _KeepTheTestConnection())
    r = client.put("/api/settings", json={"company_name": "Harbor Light Bakery"})
    assert r.status_code == 200, r.text
    return SimpleNamespace(live=live, backups=backups)


def test_a_restore_of_a_backup_from_before_2_29_0_is_refused_and_writes_nothing(
    books, client
):
    old = "harbor-light-bakery_20260801_120000.db"
    _stamped(books.backups / old, _one_before_the_floor())
    live_before = books.live.read_bytes()
    r = client.post("/api/backups/restore", json={"filename": old})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "before_floor", "message": SENTENCE}
    assert books.live.read_bytes() == live_before
    # refused before the safety backup too
    assert sorted(p.name for p in books.backups.iterdir()) == [old]


def test_a_restore_of_a_backup_at_the_floor_is_not_this_refusal(books, client):
    at_floor = "harbor-light-bakery_20261001_120000.db"
    _stamped(books.backups / at_floor, MIGRATION_FLOOR)
    assert backup_service.backup_floor_problem(at_floor) is None


_DUMP_SCRIPT = """--
-- PostgreSQL database dump
--
SET statement_timeout = 0;

COPY public.alembic_version (version_num) FROM stdin;
{revision}
\\.

"""


def test_a_postgres_dump_from_before_2_29_0_is_read_without_a_database(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path)
    dump = tmp_path / "harbor-light-bakery_20260801_120000.sql"
    dump.write_bytes(b"PGDMP not really")
    seen = []

    def pg_restore(cmd, **kwargs):
        seen.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout=_DUMP_SCRIPT.format(revision=revision["now"]),
            stderr="",
        )

    revision = {"now": _one_before_the_floor()}
    monkeypatch.setattr(backup_service.subprocess, "run", pg_restore)
    assert backup_service.backup_floor_problem(dump.name) == SENTENCE
    assert seen[0][:5] == [
        "pg_restore",
        "--data-only",
        "--table=alembic_version",
        "-f",
        "-",
    ]
    assert "-d" not in seen[0]  # no database is named, so none is written
    revision["now"] = MIGRATION_FLOOR
    assert backup_service.backup_floor_problem(dump.name) is None


# ---------------------------------------------------------------------------
# PostgreSQL, when SLOWBOOKS_TEST_POSTGRES_URL names a server
# ---------------------------------------------------------------------------


@pytest.fixture
def postgres_url():
    admin_url = os.environ.get("SLOWBOOKS_TEST_POSTGRES_URL")
    if not admin_url:
        pytest.skip("SLOWBOOKS_TEST_POSTGRES_URL is not set")
    pytest.importorskip("psycopg2")
    name = "slowbooks_floor_" + uuid.uuid4().hex[:12]
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


def _stamp_postgres(url: str, revision: str) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            conn.execute(
                sa.text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision}
            )
            conn.execute(sa.text("CREATE TABLE t (v TEXT)"))
            conn.execute(sa.text("INSERT INTO t VALUES ('original')"))
    finally:
        engine.dispose()


def test_a_postgres_database_from_before_2_29_0_is_refused_and_left_as_it_was(
    postgres_url,
):
    _stamp_postgres(postgres_url, _one_before_the_floor())
    with pytest.raises(BelowMigrationFloor) as refused:
        command.upgrade(_cfg(postgres_url), "head")
    assert str(refused.value) == SENTENCE
    engine = sa.create_engine(postgres_url)
    try:
        assert set(sa.inspect(engine).get_table_names()) == {"alembic_version", "t"}
        with engine.connect() as conn:
            assert conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar() == (_one_before_the_floor())
    finally:
        engine.dispose()


def test_a_real_postgres_dump_from_before_2_29_0_is_refused(
    postgres_url, tmp_path, monkeypatch
):
    import shutil
    import subprocess

    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        pytest.skip("pg_dump and pg_restore are not installed")
    _stamp_postgres(postgres_url, _one_before_the_floor())
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path)
    dump = tmp_path / "harbor-light-bakery_20260801_120000.sql"
    url = sa.engine.make_url(postgres_url)
    where = []
    if url.host:
        where += ["-h", url.host]
    if url.port:
        where += ["-p", str(url.port)]
    if url.username:
        where += ["-U", url.username]
    subprocess.run(
        ["pg_dump", *where, "-F", "c", "-f", str(dump), url.database],
        env={**os.environ, "PGPASSWORD": url.password or ""},
        check=True,
        capture_output=True,
    )
    assert backup_service.backup_floor_problem(dump.name) == SENTENCE


# ---------------------------------------------------------------------------
# the suite
# ---------------------------------------------------------------------------


def test_the_chain_test_starts_at_the_floor():
    from tests import test_migration_chain_770 as chain

    assert chain.MIGRATION_FLOOR == MIGRATION_FLOOR
    source = (ROOT / "tests" / "test_migration_chain_770.py").read_text()
    assert "command.downgrade(cfg, MIGRATION_FLOOR)" in source
