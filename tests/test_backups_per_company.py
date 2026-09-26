"""Backups belong to a company (explore 2.17.3, macbase1 F25).

Every company's backups shared data/backups/ under anonymous names
("slowbooks_<date>_<time>.db": Riverbend x3, NEONpulse x2, Harbor Light x1),
"Backup / Restore" offered only Download, and POST /api/backups/restore —
reachable, with no button — copied ANY file in that shared folder over the
open company. Now backups are named for their company, the list and the
download serve only the open company's, Restore has a button with a strong
confirmation, another company's backup needs a second confirmation, a copy
made by a newer version is refused, and a safety backup is taken first.
"""

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import backup_service

ROOT = Path(__file__).resolve().parents[1]


def _make_books(path, company, value, revision=None):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        conn.execute("INSERT INTO settings VALUES ('company_name', ?)", (company,))
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (value,))
        if revision:
            conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
            conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        conn.commit()


def _value(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT v FROM t").fetchone()[0]


def _change(path, value):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE t SET v = ?", (value,))
        conn.commit()


class _KeepTheTestConnection:
    """A restore disposes the app's engine so it re-reads the file. Here the
    'live' company is a temp file, and disposing the suite's engine would
    drop the test's own transaction (see conftest's _suite_engine)."""

    def dispose(self):
        pass


@pytest.fixture
def books(tmp_path, monkeypatch, client):
    import app.database as db_module

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "data"))
    live = tmp_path / "companies" / "harbor-light-bakery.db"
    live.parent.mkdir()
    _make_books(live, "Harbor Light Bakery", "original")
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + live.as_posix())
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)
    monkeypatch.setattr(db_module, "engine", _KeepTheTestConnection())
    r = client.put("/api/settings", json={"company_name": "Harbor Light Bakery"})
    assert r.status_code == 200, r.text
    return SimpleNamespace(live=live, backups=backups)


def _backup(client):
    r = client.post("/api/backups")
    assert r.status_code == 200, r.text
    return r.json()["filename"]


def _listed(client):
    r = client.get("/api/backups")
    assert r.status_code == 200, r.text
    return {b["filename"]: b for b in r.json()}


def test_a_backup_is_named_for_its_company(books, client):
    name = _backup(client)
    assert re.fullmatch(r"harbor-light-bakery_\d{8}_\d{6}\.db", name), name
    assert _value(books.backups / name) == "original"


def test_two_backups_in_the_same_second_do_not_overwrite(books, client, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 26, 9, 0, 59)

    monkeypatch.setattr(backup_service, "datetime", Frozen)
    first, second = _backup(client), _backup(client)
    assert first == "harbor-light-bakery_20260926_090059.db"
    assert second == "harbor-light-bakery_20260926_090059-2.db"
    assert (books.backups / first).exists() and (books.backups / second).exists()


def _others(books):
    """The shared folder, as the Mac tester found it: other companies'
    backups beside this one's, old and new names."""
    _make_books(
        books.backups / "riverbend-books_20260901_120000.db", "Riverbend Books", "rb"
    )
    _make_books(
        books.backups / "slowbooks_20260902_120000.db", "Riverbend Books", "rb-old"
    )
    _make_books(
        books.backups / "slowbooks_20260903_120000.db", "Harbor Light Bakery", "hl-old"
    )
    _make_books(
        books.backups / "harbor-light-bakery_20260904_120000-before-restore.db",
        "Harbor Light Bakery",
        "hl-safety",
    )


def test_the_list_holds_only_this_companys_backups(books, client):
    mine = _backup(client)
    _others(books)
    listed = _listed(client)
    assert set(listed) == {
        mine,
        "slowbooks_20260903_120000.db",  # an old anonymous name, but its books are ours
        "harbor-light-bakery_20260904_120000-before-restore.db",
    }
    safety = listed["harbor-light-bakery_20260904_120000-before-restore.db"]
    assert safety["backup_type"] == "pre-restore"
    assert listed[mine]["backup_type"] == "manual"
    assert list(listed)[0] == mine  # newest first
    assert listed[mine]["created_at"].endswith("+00:00")


def test_download_serves_only_this_companys_backups(books, client):
    _others(books)
    other = client.get("/api/backups/download/riverbend-books_20260901_120000.db")
    assert other.status_code == 404
    other_old = client.get("/api/backups/download/slowbooks_20260902_120000.db")
    assert other_old.status_code == 404
    ours = client.get("/api/backups/download/slowbooks_20260903_120000.db")
    assert ours.status_code == 200
    assert ours.content == (books.backups / "slowbooks_20260903_120000.db").read_bytes()


def test_restore_takes_a_safety_backup_first(books, client):
    name = _backup(client)
    _change(books.live, "changed since")
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 200, r.text
    safety = r.json()["safety_backup"]
    assert re.fullmatch(
        r"harbor-light-bakery_\d{8}_\d{6}-before-restore(-\d+)?\.db", safety
    )
    assert _value(books.live) == "original"
    # the books as they were are one restore away, and listed as such
    assert _value(books.backups / safety) == "changed since"
    assert _listed(client)[safety]["backup_type"] == "pre-restore"


@pytest.mark.parametrize(
    "filename", ["riverbend-books_20260901_120000.db", "slowbooks_20260902_120000.db"]
)
def test_another_companys_backup_is_refused_until_confirmed(books, client, filename):
    _others(books)
    before = sorted(p.name for p in books.backups.iterdir())
    r = client.post("/api/backups/restore", json={"filename": filename})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "other_company"
    assert detail["backup_company"] == "Riverbend Books"
    assert detail["current_company"] == "Harbor Light Bakery"
    assert "books of Riverbend Books, not Harbor Light Bakery" in detail["message"]
    # nothing touched: no restore, no safety copy
    assert _value(books.live) == "original"
    assert sorted(p.name for p in books.backups.iterdir()) == before

    r = client.post(
        "/api/backups/restore", json={"filename": filename, "allow_other_company": True}
    )
    assert r.status_code == 200, r.text
    assert _value(books.live) in ("rb", "rb-old")


def test_a_backup_from_a_newer_version_is_refused(books, client):
    newer = "harbor-light-bakery_20260905_120000.db"
    _make_books(books.backups / newer, "Harbor Light Bakery", "future", "ffffffffffff")
    r = client.post("/api/backups/restore", json={"filename": newer})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "newer_version"
    assert "newer version of SlowBooks Pro" in r.json()["detail"]["message"]
    assert _value(books.live) == "original"


def _older_revision():
    _cfg, script = backup_service._migration_script()
    head = script.get_current_head()
    return script.get_revision(head).down_revision


def test_an_older_backup_is_brought_up_to_date(books, client, monkeypatch):
    from alembic import command

    calls = []
    monkeypatch.setattr(
        command,
        "upgrade",
        lambda cfg, rev: calls.append(cfg.attributes["database_url"]),
    )
    older = "harbor-light-bakery_20260906_120000.db"
    _make_books(
        books.backups / older, "Harbor Light Bakery", "older", _older_revision()
    )
    r = client.post("/api/backups/restore", json={"filename": older})
    assert r.status_code == 200, r.text
    assert calls == ["sqlite:///" + books.live.as_posix()]
    assert _value(books.live) == "older"


def test_books_that_cannot_be_brought_up_to_date_are_put_back(
    books, client, monkeypatch
):
    from alembic import command

    def fail(cfg, rev):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(command, "upgrade", fail)
    older = "harbor-light-bakery_20260906_120000.db"
    _make_books(
        books.backups / older, "Harbor Light Bakery", "older", _older_revision()
    )
    _change(books.live, "today's books")
    r = client.post("/api/backups/restore", json={"filename": older})
    assert r.status_code == 500, r.text
    assert "as they were before the restore" in r.json()["detail"]
    assert _value(books.live) == "today's books"


def test_settings_offers_restore_with_a_strong_confirmation():
    js = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    listing = js[js.index("async loadBackups() {") : js.index("_when(iso) {")]
    assert "SettingsPage.confirmRestore(this.dataset.filename)" in listing
    assert "Restore…" in listing
    dialog = js[
        js.index("confirmRestore(filename) {") : js.index("async restoreBackup(e) {")
    ]
    # a tick is required before the (danger) button can be pressed
    assert 'type="checkbox" name="understood" required' in dialog
    assert '<button type="submit" class="btn btn-danger" disabled>' in dialog
    assert "A safety backup of the books as they are now is taken first" in dialog
    restore = js[js.index("async restoreBackup(e) {") :]
    # another company's backup asks a second time before it is sent again
    assert "err.detail.code === 'other_company'" in restore
    assert restore.index("confirm(") < restore.index("result = await send(true)")


def test_a_restore_that_fails_part_way_puts_the_books_back(books, client, monkeypatch):
    import app.routes.backups as routes

    real = routes.restore_backup
    calls = []

    def failing_once(db, filename):
        calls.append(filename)
        if len(calls) == 1:  # the restore itself: a half-written copy, then an error
            _change(books.live, "half-copied")
            return {"success": False, "error": "SQLite restore failed. Check logs."}
        return real(db, filename)  # the safety copy going back

    monkeypatch.setattr(routes, "restore_backup", failing_once)
    name = _backup(client)
    _change(books.live, "today's books")
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert "Your books as they were are in the safety backup" in detail
    assert calls[0] == name and "before-restore" in calls[1]
    assert _value(books.live) == "today's books"
