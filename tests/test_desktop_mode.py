# ============================================================================
# Native desktop (SQLite) mode — file-per-company manifest + file backups
#
# Covers the pieces added for the no-Docker Windows desktop install:
#   - company filename sanitization (same trust-boundary pattern as
#     backup_service._safe_backup_filename)
#   - manifest create/list/last-opened (companies.json), including a REAL
#     `alembic upgrade head` run against a fresh on-disk SQLite file
#   - the SQLite branch of backup_service (snapshot / restore / list)
# ============================================================================

import json
import sqlite3

import pytest

from app.services import backup_service, company_service


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "slowbooks-data"
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(d))
    return d


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------


def test_safe_company_filename_accepts_normal_names():
    assert company_service.safe_company_filename("acme-consulting.db") == (
        "acme-consulting.db"
    )
    assert company_service.safe_company_filename("co2.db") == "co2.db"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../evil.db",
        "..\\evil.db",
        "sub/dir.db",
        ".hidden.db",
        "no-extension",
        "wrong.sql",
        "UPPER.db",  # slugs are lowercase-only
        "a" * 300 + ".db",
        "-leading-dash.db",
    ],
)
def test_safe_company_filename_rejects_unsafe(bad):
    assert company_service.safe_company_filename(bad) is None


def test_company_filename_for_tricky_names():
    assert company_service.company_filename_for("Acme Consulting") == (
        "acme-consulting.db"
    )
    assert company_service.company_filename_for("Acme / Rentals, LLC") == (
        "acme-rentals-llc.db"
    )
    assert company_service.company_filename_for("../../etc/passwd") == ("etc-passwd.db")
    assert company_service.company_filename_for("!!!") is None
    assert company_service.company_filename_for("") is None


# ---------------------------------------------------------------------------
# Manifest + real on-disk company creation (real alembic upgrade head)
# ---------------------------------------------------------------------------


def test_create_company_migrates_and_registers(data_dir):
    result = company_service.manifest_create_company("Acme Consulting")
    assert result["success"], result
    assert result["file"] == "acme-consulting.db"

    db_file = data_dir / "companies" / "acme-consulting.db"
    assert db_file.exists()

    # The file went through real migrations: version-stamped and populated.
    with sqlite3.connect(db_file) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "alembic_version" in tables
        assert "invoices" in tables
        assert "settings" in tables
        # Chart of Accounts seeded, like the Docker first-run does.
        (count,) = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()
        assert count > 0

    manifest = json.loads((data_dir / "companies.json").read_text())
    assert manifest["companies"] == [
        {"name": "Acme Consulting", "file": "acme-consulting.db"}
    ]
    assert manifest["last_opened"] == "acme-consulting.db"


def test_create_company_duplicate_and_invalid(data_dir):
    assert company_service.manifest_create_company("Acme")["success"]
    dup = company_service.manifest_create_company("Acme")
    assert not dup["success"]
    assert "exists" in dup["error"]

    bad = company_service.manifest_create_company("###")
    assert not bad["success"]

    empty = company_service.manifest_create_company("   ")
    assert not empty["success"]


def test_manifest_list_and_last_opened(data_dir):
    company_service.manifest_create_company("First Co")
    company_service.manifest_create_company("Second Co")

    listed = company_service.manifest_list_companies()
    assert [c["name"] for c in listed] == ["First Co", "Second Co"]

    assert company_service.get_last_opened() == "first-co.db"
    company_service.set_last_opened("second-co.db")
    assert company_service.get_last_opened() == "second-co.db"
    # Unsafe values are ignored on write and dropped on read.
    company_service.set_last_opened("../evil.db")
    assert company_service.get_last_opened() == "second-co.db"


def test_companies_api_uses_manifest_in_sqlite_mode(client, data_dir):
    """Under a SQLite DATABASE_URL (the desktop mode), /api/companies lists
    and creates against the JSON manifest, not a Postgres companies table."""
    r = client.get("/api/companies")
    assert r.status_code == 200
    assert r.json() == []

    r = client.post("/api/companies", json={"name": "Acme Desktop"})
    assert r.status_code == 201, r.text
    assert r.json()["file"] == "acme-desktop.db"
    assert (data_dir / "companies" / "acme-desktop.db").exists()

    r = client.get("/api/companies")
    assert [c["name"] for c in r.json()] == ["Acme Desktop"]


# ---------------------------------------------------------------------------
# SQLite branch of backup_service
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_live_db(tmp_path, monkeypatch):
    """A file-backed 'live' database plus an isolated BACKUP_DIR."""
    live = tmp_path / "company.db"
    with sqlite3.connect(live) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('original')")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + live.as_posix())
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backup_dir)
    return live, backup_dir


def test_sqlite_create_backup_snapshots_file(sqlite_live_db, db_session):
    live, backup_dir = sqlite_live_db
    result = backup_service.create_backup(db_session, notes="test")
    assert result["success"], result
    assert result["filename"].endswith(".db")

    snapshot = backup_dir / result["filename"]
    assert snapshot.exists()
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("SELECT v FROM t").fetchone() == ("original",)

    listed = backup_service.list_backup_files()
    assert [f["filename"] for f in listed] == [result["filename"]]


def test_sqlite_restore_backup_overwrites_live_db(sqlite_live_db, db_session):
    live, backup_dir = sqlite_live_db
    created = backup_service.create_backup(db_session)
    assert created["success"]

    with sqlite3.connect(live) as conn:
        conn.execute("UPDATE t SET v = 'changed'")

    result = backup_service.restore_backup(db_session, created["filename"])
    assert result["success"], result

    with sqlite3.connect(live) as conn:
        assert conn.execute("SELECT v FROM t").fetchone() == ("original",)


def test_sqlite_backup_rejects_memory_database(monkeypatch, db_session):
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///:memory:")
    result = backup_service.create_backup(db_session)
    assert not result["success"]


def test_sqlite_restore_still_validates_filenames(sqlite_live_db, db_session):
    result = backup_service.restore_backup(db_session, "../../etc/passwd")
    assert not result["success"]
    assert result["error"] == "Invalid filename"
