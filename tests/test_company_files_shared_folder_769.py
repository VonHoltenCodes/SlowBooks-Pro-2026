"""The copy step brings each company's files in from the shared folder
(2.18.0), proved on books at the migration floor (#769).

Before 2.18.0 every company on a desktop install kept its uploads in one
shared folder, and the rows only held paths into it: company_logo_path
"/static/uploads/company_logo.png", attachments "uploads/attachments/...",
with backslashes when Windows wrote them (employee documents and scanned
receipts used str() on a WindowsPath). Migration c5e1f7a9b3d2 copies, once
per company, each file that company's rows point at into its own database:

- every copy is flagged from_shared_folder, because nothing ever recorded
  which company wrote a shared file (the file at a path is the LAST one
  written there, possibly by another company), and the app says so;
- a file that is not there is recorded as missing, not an error;
- a stored path never reads outside the uploads folder;
- the shared files are left where they are: another company may still
  need its copy;
- running the copy again copies nothing twice.

Books from before 2.18.0 are below the migration floor, so this build never
walks the migration up from them; 2.31.14 does. What the step does is proved
here without that walk: books at the floor are seeded with the rows a
release before 2.18.0 left (paths into the shared folder, no stored file),
and the migration's own copy step, ``copy_shared_folder_files``, and its own
``downgrade`` are run on them directly. Then the app is shown the books the
step brought in.

Runs on SQLite always, and on PostgreSQL when SLOWBOOKS_TEST_POSTGRES_URL
names a server the test may create a scratch database on (for example
postgresql:///postgres as a local superuser).
"""

import base64
import hashlib
import io
import os
import shutil
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.orm import sessionmaker
from starlette.requests import HTTPConnection

import app.database as db_module
from app.database import get_db
from app.main import app
from app.services.migration_floor import MIGRATION_FLOOR

# The seeded admin row is never signed in to with a password; a named
# constant keeps it apart from the username for the secret scanner.
_NO_PASSWORD = "x"

ROOT = Path(__file__).resolve().parents[1]
# the books are at the floor; the rows seeded on them are the release
# before 2.18.0's, which the copy step of REVISION brings in
BEFORE = MIGRATION_FLOOR
REVISION = "c5e1f7a9b3d2"

RECEIPT = b"Acme Signs receipt for invoice 1"
W4 = b"%PDF-1.4 W-4 Marisol Vance (the one left at this path)"
SCAN = b"\x89PNG\r\n\x1a\nscanned-receipt"
LOGO = b"\x89PNG\r\n\x1a\nthe-shared-logo"
OUTSIDE = b"a file outside the uploads folder"


def _sign_in_here(Session) -> None:
    """Opening another company file is signing in to it: a sign-in belongs
    to the company it was made in (app/services/auth.py). The file takes the
    id the client's session was signed in with, so the session stays good
    here, as it would after signing in. The session also names its user and
    the version it is good for (revocable sessions), re-checked against
    the users table every request, so the file carries the matching admin row
    (id 1, the client fixture's admin) the same way it carries the id above."""
    from app.models.users import User
    from app.services import auth as auth_service
    from app.services.settings_service import set_setting

    signed_in_to = auth_service._company_id()
    with Session() as s:
        set_setting(s, "company_session_id", signed_in_to)
        if s.get(User, 1) is None:
            s.add(
                User(
                    id=1,
                    username="admin",
                    display_name="Operator",
                    password_hash=_NO_PASSWORD,
                    role="admin",
                    is_active=True,
                    session_version=0,
                )
            )
        s.commit()


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


@pytest.fixture
def shared_folder(tmp_path, monkeypatch):
    """The data folder an older release left: every company's uploads."""
    data = tmp_path / "data"
    uploads = data / "uploads"
    (uploads / "attachments" / "invoice" / "1").mkdir(parents=True)
    (uploads / "attachments" / "employee" / "1").mkdir(parents=True)
    (uploads / "attachments" / "bill" / "3").mkdir(parents=True)
    (uploads / "attachments" / "invoice" / "1" / "receipt.txt").write_bytes(RECEIPT)
    (uploads / "attachments" / "employee" / "1" / "W-4.pdf").write_bytes(W4)
    (uploads / "attachments" / "bill" / "3" / "abcd1234-scan.png").write_bytes(SCAN)
    (uploads / "company_logo.png").write_bytes(LOGO)
    (tmp_path / "outside.txt").write_bytes(OUTSIDE)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(data))
    return data


# (filename, file_path as an older release stored it, record type, record id;
# an employee document's record is the employee)
LEGACY_ROWS = [
    ("receipt.txt", "uploads/attachments/invoice/1/receipt.txt", "invoice", 1),
    # an employee document, written on Windows
    ("W-4.pdf", "uploads\\attachments\\employee\\1\\W-4.pdf", "employee", None),
    # the SAME path again: an updated W-4.pdf overwrote the first on disk
    ("W-4.pdf", "uploads\\attachments\\employee\\1\\W-4.pdf", "employee", None),
    # a scanned receipt attached to a bill, written on Windows
    ("scan.png", "uploads\\attachments\\bill\\3\\abcd1234-scan.png", "bill", 3),
    # nothing there any more
    ("gone.pdf", "uploads/attachments/invoice/2/gone.pdf", "invoice", 2),
    # a path that would leave the uploads folder is never read
    ("outside.txt", "uploads/../../outside.txt", "invoice", 4),
]


_TEMPLATE = {}


@pytest.fixture(scope="module", autouse=True)
def _books_at_the_release_before(tmp_path_factory):
    """Empty books at the migration floor, migrated once for the module (from
    nothing, SQLite takes seconds); each SQLite test copies the file."""
    path = tmp_path_factory.mktemp("before") / "before.db"
    command.upgrade(_cfg("sqlite:///" + path.as_posix()), BEFORE)
    _TEMPLATE["sqlite"] = path
    yield
    _TEMPLATE.clear()


def _old_books(url: str, logo="/static/uploads/company_logo.png") -> None:
    """A company's books with the rows a release before 2.18.0 left: paths
    into the shared folder and no stored file, on books at the floor."""
    if url.startswith("sqlite:///") and "sqlite" in _TEMPLATE:
        shutil.copyfile(_TEMPLATE["sqlite"], url[len("sqlite:///") :])
    else:
        command.upgrade(_cfg(url), BEFORE)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO employees (first_name, last_name) "
                    "VALUES ('Marisol', 'Vance')"
                )
            )
            emp_id = conn.execute(sa.text("SELECT max(id) FROM employees")).scalar()
            for filename, path, entity, record in LEGACY_ROWS:
                is_document = entity == "employee"
                conn.execute(
                    sa.text(
                        "INSERT INTO attachments (entity_type, entity_id, "
                        "employee_id, doc_category, filename, file_path, "
                        "mime_type, file_size) VALUES (:t, :e, :emp, :cat, :f, "
                        ":p, :m, 10)"
                    ),
                    {
                        "t": entity,
                        "e": emp_id if is_document else record,
                        "emp": emp_id if is_document else None,
                        "cat": "W-4" if is_document else None,
                        "f": filename,
                        "p": path,
                        "m": "application/pdf",
                    },
                )
            conn.execute(
                sa.text(
                    "INSERT INTO settings (key, value) "
                    "VALUES ('company_logo_path', :v)"
                ),
                {"v": logo},
            )
    finally:
        engine.dispose()


def _upgraded_rows(url: str) -> list[dict]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT a.id, a.filename, a.file_path, a.stored_file_id, "
                    "s.kind, s.original_name, s.size, s.sha256, s.data, "
                    "s.legacy_path, s.from_shared_folder, s.missing "
                    "FROM attachments a LEFT JOIN stored_files s "
                    "ON s.id = a.stored_file_id ORDER BY a.id"
                )
            ).mappings()
            return [
                {
                    **row,
                    "data": bytes(row["data"]) if row["data"] is not None else None,
                    "from_shared_folder": bool(row["from_shared_folder"]),
                    "missing": bool(row["missing"]),
                }
                for row in rows
            ]
    finally:
        engine.dispose()


def _logo_row(url: str) -> dict:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            value = conn.execute(
                sa.text("SELECT value FROM settings WHERE key = 'company_logo_path'")
            ).scalar()
            row = None
            if value and value.startswith("/api/uploads/logo/"):
                row = (
                    conn.execute(
                        sa.text(
                            "SELECT kind, content_type, data, legacy_path, "
                            "from_shared_folder, missing FROM stored_files "
                            "WHERE id = :id"
                        ),
                        {"id": int(value.rsplit("/", 1)[1])},
                    )
                    .mappings()
                    .first()
                )
            return {"value": value, "row": dict(row) if row else None}
    finally:
        engine.dispose()


def _count(url: str, table: str) -> int:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()
    finally:
        engine.dispose()


def _assert_copied_in(url: str, shared: Path) -> None:
    rows = _upgraded_rows(url)
    assert len(rows) == len(LEGACY_ROWS)
    by_name = {}
    for row in rows:
        assert row["stored_file_id"] is not None, row
        assert row["file_path"] == f"stored_files/{row['stored_file_id']}"
        assert row["from_shared_folder"] is True, row  # nothing says whose it was
        assert "\\" not in row["legacy_path"]
        by_name.setdefault(row["filename"], []).append(row)

    (receipt,) = by_name["receipt.txt"]
    assert receipt["kind"] == "attachment"
    assert receipt["data"] == RECEIPT and receipt["missing"] is False
    assert receipt["size"] == len(RECEIPT)
    assert receipt["sha256"] == hashlib.sha256(RECEIPT).hexdigest()
    assert receipt["legacy_path"] == "uploads/attachments/invoice/1/receipt.txt"

    # Windows paths read; both W-4 rows get their OWN copy of what is there
    first_w4, second_w4 = by_name["W-4.pdf"]
    assert first_w4["kind"] == second_w4["kind"] == "employee_document"
    assert first_w4["data"] == second_w4["data"] == W4
    assert first_w4["stored_file_id"] != second_w4["stored_file_id"]
    assert first_w4["legacy_path"] == "uploads/attachments/employee/1/W-4.pdf"

    (scan,) = by_name["scan.png"]
    assert scan["data"] == SCAN and scan["kind"] == "attachment"

    for name in ("gone.pdf", "outside.txt"):
        (row,) = by_name[name]
        assert row["missing"] is True and row["data"] is None, name
        assert row["size"] == 0

    logo = _logo_row(url)
    assert logo["value"].startswith("/api/uploads/logo/")
    assert logo["row"]["kind"] == "logo"
    assert logo["row"]["content_type"] == "image/png"
    assert bytes(logo["row"]["data"]) == LOGO
    assert logo["row"]["legacy_path"] == "uploads/company_logo.png"
    assert bool(logo["row"]["from_shared_folder"]) is True

    # nothing in the shared folder was moved or deleted
    uploads = shared / "uploads"
    assert (uploads / "attachments" / "invoice" / "1" / "receipt.txt").read_bytes() == (
        RECEIPT
    )
    assert (uploads / "attachments" / "employee" / "1" / "W-4.pdf").read_bytes() == W4
    assert (uploads / "company_logo.png").read_bytes() == LOGO


def _migration():
    script = ScriptDirectory.from_config(_cfg("sqlite:///unused.db"))
    return script.get_revision(REVISION).module


def _copy_in(url: str) -> dict:
    """Run the migration's copy step on the books, as its upgrade does."""
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            stored_files = sa.Table("stored_files", sa.MetaData(), autoload_with=conn)
            return _migration().copy_shared_folder_files(conn, stored_files)
    finally:
        engine.dispose()


_copy_again = _copy_in  # a second run, as a second upgrade would be


def _run_the_downgrade(url: str) -> None:
    """The migration's own downgrade, run on its own (not the chain below
    the floor)."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                _migration().downgrade()
    finally:
        engine.dispose()


def _upgrade_scenario(url: str, shared: Path) -> None:
    _old_books(url)
    assert _copy_in(url) == {"copied": 5, "missing": 2}
    _assert_copied_in(url, shared)

    # idempotent: a second run of the step, or of the upgrade, copies nothing
    before = _count(url, "stored_files")
    assert _copy_again(url) == {"copied": 0, "missing": 0}
    command.upgrade(_cfg(url), "head")
    assert _count(url, "stored_files") == before
    _assert_copied_in(url, shared)

    # the downgrade points the rows at the shared folder again
    _run_the_downgrade(url)
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            paths = conn.execute(
                sa.text("SELECT file_path FROM attachments ORDER BY id")
            ).scalars()
            assert list(paths)[:2] == [
                "uploads/attachments/invoice/1/receipt.txt",
                "uploads/attachments/employee/1/W-4.pdf",
            ]
            logo = conn.execute(
                sa.text("SELECT value FROM settings WHERE key = 'company_logo_path'")
            ).scalar()
            assert logo == "/static/uploads/company_logo.png"
            assert "stored_files" not in sa.inspect(conn).get_table_names()
    finally:
        engine.dispose()


def test_the_copy_step_copies_each_file_in_flags_it_and_is_idempotent(
    tmp_path, shared_folder
):
    _upgrade_scenario("sqlite:///" + (tmp_path / "old.db").as_posix(), shared_folder)


@pytest.fixture
def postgres_url():
    admin_url = os.environ.get("SLOWBOOKS_TEST_POSTGRES_URL")
    if not admin_url:
        pytest.skip("SLOWBOOKS_TEST_POSTGRES_URL is not set")
    pytest.importorskip("psycopg2")
    name = "slowbooks_filestore_" + uuid.uuid4().hex[:12]
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


def test_the_copy_step_on_postgresql(postgres_url, shared_folder):
    _upgrade_scenario(postgres_url, shared_folder)


def test_every_company_copies_its_own_and_the_shared_file_stays(
    tmp_path, shared_folder
):
    """Nothing records which company wrote a shared file, and a copied
    company points at the same paths as its original: each company gets a
    copy of its own, and neither upgrade touches the other's."""
    acme = "sqlite:///" + (tmp_path / "acme.db").as_posix()
    copy = "sqlite:///" + (tmp_path / "acme-copy.db").as_posix()
    _old_books(acme)
    _old_books(copy)
    _copy_in(acme)
    _copy_in(copy)
    _assert_copied_in(acme, shared_folder)
    _assert_copied_in(copy, shared_folder)


def test_a_desktop_company_file_finds_its_data_folder_without_being_told(
    shared_folder, monkeypatch
):
    """The repair tool (--_repair-schema) runs the migrations with only a
    database URL: no SLOWBOOKS_DATA_DIR. A desktop company file lives in
    <data dir>/companies/, and that data dir is where its uploads were."""
    monkeypatch.delenv("SLOWBOOKS_DATA_DIR")
    (shared_folder / "companies").mkdir()
    url = "sqlite:///" + (shared_folder / "companies" / "acme.db").as_posix()
    _old_books(url)
    _copy_in(url)
    _assert_copied_in(url, shared_folder)


def test_a_logo_that_is_not_there_is_recorded_as_missing(tmp_path, shared_folder):
    (shared_folder / "uploads" / "company_logo.png").unlink()
    url = "sqlite:///" + (tmp_path / "old.db").as_posix()
    _old_books(url)
    _copy_in(url)
    logo = _logo_row(url)
    assert logo["row"]["missing"] in (True, 1)
    assert logo["row"]["data"] is None


@pytest.mark.parametrize("value", ["", "https://example.test/logo.png"])
def test_a_logo_setting_that_names_no_shared_file_is_left_alone(
    tmp_path, shared_folder, value
):
    url = "sqlite:///" + (tmp_path / "old.db").as_posix()
    _old_books(url, logo=value)
    _copy_in(url)
    assert _logo_row(url) == {"value": value, "row": None}


# ---------------------------------------------------------------------------
# What the app shows for books the upgrade brought in
# ---------------------------------------------------------------------------


@pytest.fixture
def upgraded(tmp_path, shared_folder, client, monkeypatch):
    """Older books, their files copied in, upgraded, opened in the app."""
    url = "sqlite:///" + (tmp_path / "old.db").as_posix()
    _old_books(url)
    _copy_in(url)
    command.upgrade(_cfg(url), "head")
    engine = sa.create_engine(url, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine, autoflush=False)

    def override(request: HTTPConnection = None):
        session = Session()
        try:
            yield session
        finally:
            session.close()

    _sign_in_here(Session)
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(db_module, "SessionLocal", Session)
    yield
    engine.dispose()


def test_the_app_says_where_an_upgraded_file_came_from(client, upgraded):
    listed = client.get("/api/attachments/invoice/1").json()
    assert [(a["filename"], a["from_shared_folder"], a["missing"]) for a in listed] == [
        ("receipt.txt", True, False)
    ]
    r = client.get(f"/api/attachments/download/{listed[0]['id']}")
    assert r.status_code == 200 and r.content == RECEIPT

    (gone,) = client.get("/api/attachments/invoice/2").json()
    assert gone["missing"] is True
    r = client.get(f"/api/attachments/download/{gone['id']}")
    assert r.status_code == 404
    assert "not in the shared folder" in r.json()["detail"]

    documents = client.get("/api/employees/1/documents").json()
    assert len(documents) == 2
    assert all(d["from_shared_folder"] and not d["missing"] for d in documents)
    for doc in documents:
        assert client.get(f"/api/employees/1/documents/{doc['id']}").content == W4

    logo = client.get("/api/uploads/logo").json()
    assert logo["from_shared_folder"] is True and logo["missing"] is False
    assert client.get(logo["path"]).content == LOGO


def test_a_file_attached_after_the_upgrade_carries_no_note(client, upgraded):
    customer = client.post("/api/customers", json={"name": "Harbor Light"})
    assert customer.status_code == 201, customer.text
    r = client.post(
        f"/api/attachments/customer/{customer.json()['id']}",
        files={"file": ("new.txt", io.BytesIO(b"attached on 2.18"), "text/plain")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["from_shared_folder"] is False
    assert r.json()["missing"] is False


def test_the_app_keeps_and_serves_files_on_postgresql(
    postgres_url, shared_folder, client, monkeypatch
):
    """Server Edition: the same routes on PostgreSQL (bytea, an enforced
    foreign key, real booleans), on books the upgrade brought in."""
    from app.models.stored_files import StoredFile
    from app.services import ocr_service, pdf_service

    _old_books(postgres_url)
    _copy_in(postgres_url)
    command.upgrade(_cfg(postgres_url), "head")
    engine = sa.create_engine(postgres_url)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override(request: HTTPConnection = None):
        session = Session()
        try:
            yield session
        finally:
            session.close()

    _sign_in_here(Session)
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(db_module, "SessionLocal", Session)
    try:
        # two uploads of one name on one record: two files, both kept
        customer = client.post("/api/customers", json={"name": "Harbor Light"})
        assert customer.status_code == 201, customer.text
        ids = []
        for text in (b"first receipt", b"second receipt"):
            r = client.post(
                f"/api/attachments/customer/{customer.json()['id']}",
                files={"file": ("receipt.txt", io.BytesIO(text), "text/plain")},
            )
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        assert [client.get(f"/api/attachments/download/{i}").content for i in ids] == [
            b"first receipt",
            b"second receipt",
        ]
        assert client.delete(f"/api/attachments/{ids[0]}").status_code == 200
        assert client.get(f"/api/attachments/download/{ids[0]}").status_code == 404

        # the upgraded W-4s plus a new one
        r = client.post(
            "/api/employees/1/documents",
            files={
                "file": ("W-4.pdf", io.BytesIO(b"%PDF-1.4 new W-4"), "application/pdf")
            },
        )
        assert r.status_code == 201, r.text
        docs = client.get("/api/employees/1/documents").json()
        assert len(docs) == 3
        assert client.get(f"/api/employees/1/documents/{r.json()['id']}").content == (
            b"%PDF-1.4 new W-4"
        )
        assert (
            client.delete(f"/api/employees/1/documents/{r.json()['id']}").status_code
            == 200
        )

        # the logo, replaced; the PDF embeds the company's own
        # a whole PNG: the upload verifies a raster logo (NEW-201)
        png = (
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
            )
            + b"postgres-logo"
        )
        r = client.post(
            "/api/uploads/logo",
            files={"file": ("logo.png", io.BytesIO(png), "image/png")},
        )
        assert r.status_code == 200, r.text
        assert client.get(r.json()["path"]).content == png
        uri = pdf_service._company_logo_data_uri(
            {"company_logo_path": r.json()["path"]}
        )
        assert uri.startswith("data:image/png;base64,")

        # a scanned receipt waits in the database, then is discarded
        monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
        monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")
        monkeypatch.setattr(
            ocr_service, "ocr_image_words", lambda data, lang=None: ("TOTAL $5.00", [])
        )
        r = client.post(
            "/api/ocr/receipt",
            files={"file": ("pg-scan.png", b"\x89PNG\r\n\x1a\nscan", "image/png")},
        )
        assert r.status_code == 200, r.text
        intake_id = r.json()["intake_id"]
        items = client.get("/api/dashboard/data?ids=receipts_review").json()
        assert [i["filename"] for i in items["receipts_review"]["items"]] == [
            "pg-scan.png"
        ]
        assert client.delete(f"/api/ocr/intake/{intake_id}").status_code == 200
        with Session() as db:
            assert db.query(StoredFile).filter_by(token=intake_id).count() == 0
            assert db.query(StoredFile).filter_by(kind="logo").count() == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
