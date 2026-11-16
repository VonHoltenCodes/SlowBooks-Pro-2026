"""The server under load (#959, 2.32.0 round 10: W-167 / NEW-242).

Twenty-four requests in flight at once froze the server for minutes: the
sign-in middleware re-read the user on the event loop's thread, waiting up
to 30 s for a pooled connection while the handlers holding the pool's
fifteen connections could not hand them back. The lookup runs in the thread
pool now, and a file-backed engine's pool is sized to that thread pool."""

import re
from pathlib import Path

from app.services.company_file import create_sqlite_engine

ROOT = Path(__file__).resolve().parents[1]


def test_the_sign_in_check_runs_off_the_event_loop():
    src = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "from starlette.concurrency import run_in_threadpool" in src
    body = src.split("async def _require_session(")[1]
    assert re.search(
        r"await run_in_threadpool\(\s*_check_session_user,\s*request\.session\s*\)",
        body,
    )
    # and never called on the loop's thread any more
    assert not re.search(
        r"(?<!await run_in_threadpool\(\n            )_check_session_user\(request\.session\)",
        body,
    )


def test_a_company_file_gets_a_pool_the_size_of_the_thread_pool(monkeypatch):
    # the pure helper, never a reload of app.database: the suite shares one
    # in-memory engine, and swapping it mid-run left later tests with no tables
    from app.database import engine_kwargs

    url = "sqlite:////books/company.db"
    monkeypatch.delenv("SLOWBOOKS_DB_POOL_SIZE", raising=False)
    kw = engine_kwargs(url)
    assert kw["pool_size"] == 40 and kw["max_overflow"] == 10 and kw["pool_pre_ping"]
    monkeypatch.setenv("SLOWBOOKS_DB_POOL_SIZE", "80")
    assert engine_kwargs(url)["pool_size"] == 80
    monkeypatch.setenv("SLOWBOOKS_DB_POOL_SIZE", "not a number")
    assert engine_kwargs(url)["pool_size"] == 40
    monkeypatch.setenv("SLOWBOOKS_DB_POOL_SIZE", "1")
    assert engine_kwargs(url)["pool_size"] == 5
    pg = engine_kwargs("postgresql://u:p@host/books")
    assert pg["pool_size"] == 5 and pg["max_overflow"] == 20


def test_the_in_memory_database_takes_no_pool_size(monkeypatch):
    from app.database import engine_kwargs

    monkeypatch.delenv("SLOWBOOKS_DB_POOL_SIZE", raising=False)
    kw = engine_kwargs("sqlite:///:memory:")
    assert "pool_size" not in kw and "max_overflow" not in kw


def test_a_company_files_engine_pool_is_that_size(tmp_path):
    from app.database import engine_kwargs

    engine = create_sqlite_engine(
        f"sqlite:///{tmp_path / 'sized.db'}", None, **engine_kwargs("sqlite:///x.db")
    )
    try:
        assert engine.pool.size() == 40
    finally:
        engine.dispose()


def test_forty_connections_open_on_one_file_at_once(tmp_path):
    engine = create_sqlite_engine(
        f"sqlite:///{tmp_path / 'many.db'}", None, pool_size=40, max_overflow=10
    )
    conns = [engine.connect() for _ in range(40)]
    try:
        assert all(c.exec_driver_sql("select 1").scalar() == 1 for c in conns)
        assert engine.pool.checkedout() == 40
    finally:
        for c in conns:
            c.close()
        engine.dispose()
