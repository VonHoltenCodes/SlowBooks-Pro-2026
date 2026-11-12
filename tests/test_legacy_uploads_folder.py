"""An administrator can clear the folder earlier versions shared (2.18.0).

Before 2.18.0 every company on an install wrote its uploads into one folder
(storage.uploads_root()). Each company now keeps its files in its own
database, and the upgrade copies a company's files in from the folder the
first time that company is opened on 2.18. The folder is no longer read,
but it stayed on disk with every old logo, attachment and employee document,
the old copy of a document deleted since among them, and nothing removed it.

GET /api/uploads/legacy says what it holds and which companies still have to
copy their files from it; DELETE removes the regular files in it once none
does, and never the folder itself, anything reached through a link, or
anything outside it. Both are the administrator's.
"""

import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app
from app.services import legacy_uploads
from app.services.migration_floor import MIGRATION_FLOOR

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "d3d40d716684"  # the release before the upgrade that copies files in

LOGO = b"\x89PNG\r\n\x1a\nthe-shared-logo"
RECEIPT = b"%PDF-1.4 Acme Signs receipt for invoice 1"
W4 = b"%PDF-1.4 W-4 Marisol Vance"
OUTSIDE = b"a file outside the uploads folder"
SHARED = {
    "company_logo.png": LOGO,
    "attachments/invoice/1/receipt.pdf": RECEIPT,
    "attachments/employee/1/W-4.pdf": W4,
}
SHARED_BYTES = sum(len(data) for data in SHARED.values())

# Both ways the folder can be walked: by descriptor where the platform has
# it (Linux, macOS), by path everywhere (Windows).
WALKS = [False] + ([True] if legacy_uploads._FD_WALK else [])


def _url(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


@pytest.fixture(scope="module")
def books_before(tmp_path_factory):
    """Empty books at the release before 2.18.0, migrated once for the
    module; each company copies the file."""
    path = tmp_path_factory.mktemp("before") / "before.db"
    command.upgrade(_cfg(_url(path)), BEFORE)
    return path


def _old_books(template: Path, path: Path) -> None:
    """A company as the release before left it: its rows point into the
    shared folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, path)
    engine = sa.create_engine(_url(path))
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO employees (first_name, last_name) "
                    "VALUES ('Marisol', 'Vance')"
                )
            )
            emp = conn.execute(sa.text("SELECT max(id) FROM employees")).scalar()
            for entity, record, employee, filename, stored in (
                (
                    "invoice",
                    1,
                    None,
                    "receipt.pdf",
                    "attachments/invoice/1/receipt.pdf",
                ),
                ("employee", emp, emp, "W-4.pdf", "attachments/employee/1/W-4.pdf"),
            ):
                conn.execute(
                    sa.text(
                        "INSERT INTO attachments (entity_type, entity_id, "
                        "employee_id, filename, file_path, mime_type, file_size) "
                        "VALUES (:t, :e, :emp, :f, :p, 'application/pdf', 10)"
                    ),
                    {
                        "t": entity,
                        "e": record,
                        "emp": employee,
                        "f": filename,
                        "p": "uploads/" + stored,
                    },
                )
            conn.execute(
                sa.text(
                    "INSERT INTO settings (key, value) VALUES "
                    "('company_logo_path', '/static/uploads/company_logo.png')"
                )
            )
    finally:
        engine.dispose()


def _opened_with_2_31_14(path: Path) -> None:
    """Books from before 2.18.0 are below the migration floor (#769): this
    build does not migrate them, and 2.31.14 is the release that does (the
    copy step its upgrade runs takes the company's files in). The folder
    reads only where a company's migrations stand, so a company opened that
    way is books stamped past the copy step."""
    engine = sa.create_engine(_url(path))
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE alembic_version SET version_num = :r"),
                {"r": MIGRATION_FLOOR},
            )
    finally:
        engine.dispose()


def _manifest(data: Path, *companies: tuple[str, str]) -> None:
    """The desktop company list (the company picker's)."""
    (data / "companies.json").write_text(
        json.dumps(
            {
                "companies": [{"name": n, "file": f} for n, f in companies],
                "last_opened": None,
            }
        ),
        encoding="utf-8",
    )


def _link_folder(link: Path, target: Path) -> bool:
    """A link to a folder somewhere else: a symbolic link, or on Windows
    without the right to make one, a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return True
    return False


def _link_file(link: Path, target: Path) -> bool:
    """A link to a file somewhere else, where the platform lets us make one."""
    try:
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError):
        return False


@pytest.fixture
def data_folder(tmp_path, monkeypatch):
    """A data folder an older release left: a logo, an attachment and an
    employee document in the shared uploads folder, and a file outside it."""
    data = tmp_path / "data"
    for relative, content in SHARED.items():
        path = data / "uploads" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.pdf").write_bytes(OUTSIDE)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(data))
    return data


def _shared_files_left(uploads: Path) -> dict:
    return {
        relative: (uploads / relative).read_bytes()
        for relative in SHARED
        if (uploads / relative).is_file()
    }


def test_the_folder_waits_for_every_company_then_an_administrator_clears_it(
    client, db_session, data_folder, books_before, tmp_path
):
    uploads = data_folder / "uploads"
    outside = tmp_path / "outside"
    acme = data_folder / "companies" / "acme.db"
    beta = data_folder / "companies" / "beta.db"
    _old_books(books_before, acme)
    _opened_with_2_31_14(acme)
    _old_books(books_before, beta)  # not opened since the update
    _manifest(data_folder, ("Acme Consulting", "acme.db"), ("Beta Bakery", "beta.db"))
    assert _link_folder(uploads / "elsewhere", outside)
    linked_file = _link_file(uploads / "escape.pdf", outside / "keep.pdf")

    r = client.get("/api/uploads/legacy")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "files": 3,
        "bytes": SHARED_BYTES,
        "pending_companies": ["Beta Bakery"],
        "can_remove": False,
    }

    # Beta still has to copy its files from the folder: nothing is removed.
    r = client.delete("/api/uploads/legacy")
    assert r.status_code == 409, r.text
    assert "Beta Bakery" in r.json()["detail"]
    assert "Acme" not in r.json()["detail"]
    assert _shared_files_left(uploads) == SHARED

    _opened_with_2_31_14(beta)

    r = client.get("/api/uploads/legacy")
    assert r.json() == {
        "files": 3,
        "bytes": SHARED_BYTES,
        "pending_companies": [],
        "can_remove": True,
    }

    r = client.delete("/api/uploads/legacy")
    assert r.status_code == 200, r.text
    assert r.json() == {"removed": 3, "bytes": SHARED_BYTES}
    assert _shared_files_left(uploads) == {}
    # The folder itself stays (on Docker it is where a volume is mounted);
    # the folders it emptied go; the links are left where they were,
    # unfollowed, and what they point at is untouched.
    assert uploads.is_dir()
    assert not (uploads / "attachments").exists()
    assert sorted(os.listdir(uploads)) == sorted(
        ["elsewhere"] + (["escape.pdf"] if linked_file else [])
    )
    assert (outside / "keep.pdf").read_bytes() == OUTSIDE
    assert os.listdir(outside) == ["keep.pdf"]

    r = client.get("/api/uploads/legacy")
    assert r.json() == {
        "files": 0,
        "bytes": 0,
        "pending_companies": [],
        "can_remove": False,
    }

    from app.models.audit import AuditLog

    logged = db_session.query(AuditLog).filter_by(table_name="shared_uploads").all()
    assert [(a.action, a.new_values) for a in logged] == [
        ("DELETE", {"removed": 3, "bytes": SHARED_BYTES})
    ]


@pytest.mark.parametrize("role", ["bookkeeper", "readonly"])
def test_only_an_administrator_sees_or_clears_the_folder(client, data_folder, role):
    r = client.post("/api/tokens", json={"label": f"{role}-agent", "role": role})
    assert r.status_code == 201, r.text
    other = TestClient(app)
    other.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert other.get("/api/uploads/legacy").status_code == 403
    assert other.delete("/api/uploads/legacy").status_code == 403
    assert _shared_files_left(data_folder / "uploads") == SHARED
    assert client.get("/api/uploads/legacy").json()["files"] == 3


# (the books' tables, their migration revision, whether they still need the
# folder)
BOOKS = {
    # A server started against books behind the upgrade creates stored_files
    # (create_all) without copying anything; the repair then runs the copy
    # from the folder. The revision says so, not the table.
    "half upgraded by a server": (
        {"attachments", "settings", "stored_files"},
        BEFORE,
        True,
    ),
    "at the upgrade": (
        {"attachments", "settings", "stored_files"},
        "c5e1f7a9b3d2",
        False,
    ),
    # a revision this build doesn't know: a newer version's, and the table
    # shows the copy has run
    "from a newer version": (
        {"attachments", "settings", "stored_files"},
        "0f1e2d3c4b5a",
        False,
    ),
    "built without migrations before 2.18": ({"attachments", "settings"}, None, True),
    "built without migrations since 2.18": (
        {"attachments", "settings", "stored_files"},
        None,
        False,
    ),
    "with no tables at all": (set(), None, False),
}


@pytest.mark.parametrize("books", list(BOOKS))
def test_books_are_done_with_the_folder_once_past_the_upgrade(
    client, data_folder, books
):
    tables, revision, needs = BOOKS[books]
    path = data_folder / "companies" / "books.db"
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as conn:
        for table in sorted(tables):
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        if revision:
            conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
            conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        conn.commit()
    _manifest(data_folder, ("Harbor Light", "books.db"))
    state = client.get("/api/uploads/legacy").json()
    assert state["pending_companies"] == (["Harbor Light"] if needs else [])
    assert state["can_remove"] is not needs


def test_a_company_file_that_is_gone_or_not_books_is_not_waited_for(
    client, data_folder
):
    """A company file that is missing, or is not a database, can't copy
    anything, so it doesn't hold the folder."""
    companies = data_folder / "companies"
    companies.mkdir()
    (companies / "junk.db").write_bytes(b"this is not a database " * 64)
    _manifest(
        data_folder,
        ("Gone Company", "gone.db"),
        ("Junk Company", "junk.db"),
        ("Odd Name", "../outside.db"),
    )
    r = client.get("/api/uploads/legacy")
    assert r.json()["pending_companies"] == []
    assert r.json()["can_remove"] is True


def test_a_company_file_another_program_is_writing_keeps_the_folder(
    client, data_folder, books_before, monkeypatch
):
    """Books that can't be read right now can't be shown to be done with
    the folder, even ones upgraded long ago."""
    monkeypatch.setattr(legacy_uploads, "BUSY_SECONDS", 0.1)
    busy = data_folder / "companies" / "busy.db"
    _old_books(books_before, busy)
    _opened_with_2_31_14(busy)
    _manifest(data_folder, ("Busy Books", "busy.db"))
    # the other program's lock, held through the library the sweep reads with:
    # in one process only the same SQLite library sees the lock (NEW-156)
    from app.services.company_file import sqlite_module

    with closing(sqlite_module().connect(busy, isolation_level=None)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        r = client.get("/api/uploads/legacy")
        writer.execute("ROLLBACK")
    assert r.json()["pending_companies"] == ["Busy Books"]
    assert client.get("/api/uploads/legacy").json()["pending_companies"] == []


def test_a_server_checks_every_database_its_companies_table_lists(
    client, db_session, data_folder, books_before, tmp_path, monkeypatch
):
    """PostgreSQL: each company is a database of its own on one server,
    listed in the companies table, and every one of them wrote to the same
    uploads folder. The served database is read through its own session,
    the others by connecting to them; SQLite files stand in for them here.
    A database the server no longer has can't copy anything; one it has
    but that can't be read may still need the folder."""
    from app.models.companies import Company
    from app.services import company_service

    server = tmp_path / "server"
    _old_books(books_before, server / "harbor.db")
    _opened_with_2_31_14(server / "harbor.db")
    _old_books(books_before, server / "beta.db")
    (server / "broken.db").write_bytes(b"not a database " * 64)
    for name, database in (
        ("Harbor Light", "harbor"),
        ("Beta Bakery", "beta"),
        ("Broken Books", "broken"),
        ("Dropped Company", "dropped"),
        ("Served Company", "served"),
    ):
        db_session.add(Company(name=name, database_name=database))
    db_session.commit()
    monkeypatch.setattr(
        company_service, "DATABASE_URL", "postgresql://slowbooks@db.invalid/served"
    )
    monkeypatch.setattr(
        legacy_uploads,
        "_server_databases",
        lambda db: {"postgres", "served", "harbor", "beta", "broken"},
    )
    monkeypatch.setattr(
        legacy_uploads,
        "_company_database_url",
        lambda database: sa.engine.make_url(_url(server / f"{database}.db")),
    )

    r = client.get("/api/uploads/legacy")
    assert r.status_code == 200, r.text
    assert r.json()["pending_companies"] == ["Beta Bakery", "Broken Books"]
    assert client.delete("/api/uploads/legacy").status_code == 409

    # Beta is opened, and the broken database is dropped from the server.
    _opened_with_2_31_14(server / "beta.db")
    monkeypatch.setattr(
        legacy_uploads, "_server_databases", lambda db: {"served", "harbor", "beta"}
    )
    assert client.get("/api/uploads/legacy").json()["can_remove"] is True


@pytest.mark.parametrize("fd_walk", WALKS)
def test_the_removal_takes_only_regular_files_inside_the_folder(
    client, data_folder, tmp_path, monkeypatch, fd_walk
):
    monkeypatch.setattr(legacy_uploads, "_FD_WALK", fd_walk)
    uploads = data_folder / "uploads"
    outside = tmp_path / "outside"
    (outside / "nested").mkdir()
    (outside / "nested" / "also-keep.pdf").write_bytes(OUTSIDE)
    assert _link_folder(uploads / "attachments" / "elsewhere", outside)
    linked_file = _link_file(
        uploads / "attachments" / "invoice" / "escape.pdf", outside / "keep.pdf"
    )

    # links aren't counted: they are not files in the folder
    assert client.get("/api/uploads/legacy").json()["files"] == 3
    r = client.delete("/api/uploads/legacy")
    assert r.json() == {"removed": 3, "bytes": SHARED_BYTES}

    assert _shared_files_left(uploads) == {}
    assert uploads.is_dir()
    assert not (uploads / "attachments" / "employee").exists()
    assert (outside / "keep.pdf").read_bytes() == OUTSIDE
    assert (outside / "nested" / "also-keep.pdf").read_bytes() == OUTSIDE
    # a folder holding a link keeps it, and so is not empty
    assert os.path.lexists(uploads / "attachments" / "elsewhere")
    if linked_file:
        assert os.path.lexists(uploads / "attachments" / "invoice" / "escape.pdf")
    else:
        assert not (uploads / "attachments" / "invoice").exists()


@pytest.mark.parametrize("fd_walk", WALKS)
def test_an_uploads_folder_that_is_a_link_is_not_followed(
    client, tmp_path, monkeypatch, fd_walk
):
    monkeypatch.setattr(legacy_uploads, "_FD_WALK", fd_walk)
    data = tmp_path / "data"
    data.mkdir()
    real = tmp_path / "somewhere-else"
    (real / "attachments").mkdir(parents=True)
    (real / "attachments" / "W-4.pdf").write_bytes(W4)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(data))
    assert _link_folder(data / "uploads", real)

    assert client.get("/api/uploads/legacy").json()["files"] == 0
    assert client.delete("/api/uploads/legacy").json() == {"removed": 0, "bytes": 0}
    assert (real / "attachments" / "W-4.pdf").read_bytes() == W4


def test_no_folder_holds_nothing(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "fresh"))
    assert client.get("/api/uploads/legacy").json() == {
        "files": 0,
        "bytes": 0,
        "pending_companies": [],
        "can_remove": False,
    }
    assert client.delete("/api/uploads/legacy").json() == {"removed": 0, "bytes": 0}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_settings_offers_the_folder_to_an_administrator_while_it_holds_files():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "settings_legacy_files_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    rows = [json.loads(line) for line in out.stdout.splitlines()]
    shown = {(r["state"], r["role"]): r for r in rows if "state" in r}

    # nothing for anyone but an administrator: not even the question
    for state in ("ready", "waiting", "empty"):
        for role in ("bookkeeper", "readonly"):
            row = shown[(state, role)]
            assert not row["placeholder"] and not row["asked"], row
            assert not row["shown"], row

    ready = shown[("ready", "admin")]
    assert ready["shown"] and ready["button"] and ready["dataWrite"], ready
    assert not ready["disabled"], ready
    assert "3 files" in ready["text"]
    assert "inside its company file" in ready["text"]

    # a company still to open: the button is locked, and the page says why
    waiting = shown[("waiting", "admin")]
    assert waiting["shown"] and waiting["button"] and waiting["disabled"], waiting
    assert "1 file " in waiting["text"]
    assert "Beta &amp; Sons &lt;b&gt; and Harbor Light" in waiting["html"]
    assert "Open each of them once" in waiting["text"]

    # an empty folder: nothing shown
    empty = shown[("empty", "admin")]
    assert empty["asked"] and not empty["shown"], empty

    (remove,) = [r for r in rows if r.get("remove")]
    assert "backup made before version 2.18" in remove["confirm"]
    assert "without its logo, attachments and employee documents" in remove["confirm"]
    assert "3 files" in remove["confirm"]
    assert remove["calls"] == ["DELETE /uploads/legacy", "GET /uploads/legacy"]
    assert remove["hiddenAfter"] is True


def _wal_books(path: Path, revision: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE settings (key TEXT, value TEXT)")
    con.execute("CREATE TABLE alembic_version (version_num TEXT)")
    con.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    con.commit()
    return con


def test_looking_at_a_company_nobody_has_open_leaves_nothing_beside_it(tmp_path):
    # macbase1 NEW-17: a read-only open made SQLite create empty -wal and
    # -shm files beside companies nobody had opened
    db = tmp_path / "closed.db"
    _wal_books(db, "d3d40d716684").close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["closed.db"]
    assert legacy_uploads._company_file_needs_folder(db) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["closed.db"]


def test_a_company_in_use_is_read_through_its_wal(tmp_path):
    # its upgrade is still only in the -wal: the check must see it there
    db = tmp_path / "open.db"
    con = _wal_books(db, "d3d40d716684")
    try:
        con.execute("UPDATE alembic_version SET version_num = 'c5e1f7a9b3d2'")
        con.commit()
        assert (tmp_path / "open.db-wal").exists()
        assert legacy_uploads._company_file_needs_folder(db) is False
    finally:
        con.close()


def test_an_encrypted_company_file_is_left_alone(tmp_path):
    """The shared-folder decision never opens an encrypted company file
    (2.23.2, #290): it cannot read one, and the plain read would have left
    -wal and -shm files beside it. Such a file is new enough to keep nothing
    in the shared folder, so the answer is no, with nothing written."""
    from app.services import company_file

    if not company_file.available():
        pytest.skip("sqlcipher3 is not installed")
    path = tmp_path / "Encrypted Co.db"
    key = b"\x11" * company_file.KEY_BYTES
    with closing(company_file.connect_raw(path, key)) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('e8f9a0b1c2d3')")
        conn.commit()
    assert company_file.is_encrypted_file(path)
    assert legacy_uploads._company_file_needs_folder(path) is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Encrypted Co.db"]
