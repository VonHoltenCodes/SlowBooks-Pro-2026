"""Each company keeps its own files (2.18.0 gate, found after round 4).

On a desktop install every company wrote its uploads into ONE shared
folder: uploads/company_logo.<ext>, uploads/attachments/<type>/<record id>/
<name>, uploads/attachments/employee/<employee id>/<name> and uploads/intake/.
Nothing in a path said which company a file was, so (skytech on two copies of
NEONpulse; macbase1 NEW-14 for the logo):

- company B's logo replaced company A's, on A's invoices and PDFs;
- B's invoice 1 "receipt.txt" replaced A's, and deleting B's made A's 404;
- B's employee #1 W-4.pdf opened from A's employee #1;
- an updated W-4.pdf replaced the original, even within one company;
- deleting an employee document left the file (with its SSN) on disk;
- a backup was the database alone, so a restored company had no files.

Each company now keeps its files in its own database (stored_files). Most
tests here run two real company files, migrated by alembic the way the
desktop app makes them, and switch the app between them the way the
company picker does.
"""

import base64
import io
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.requests import HTTPConnection

import app.database as db_module
from app.database import enable_sqlite_tuning, get_db
from app.main import app
from app.models.users import ROLE_BOOKKEEPER, ROLE_READONLY, User
from app.services import auth as auth_service
from app.services import storage
from app.services.audit import register_audit_hooks

# The seeded admin row is never signed in to with a password; a named
# constant keeps it apart from the username for the secret scanner.
_NO_PASSWORD = "x"

# whole PNGs (the upload decodes a raster logo before keeping it, NEW-201),
# each told apart by the bytes after its end
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
PNG_ACME = _PNG_1PX + b"acme-red-logo"
PNG_BRAVO = _PNG_1PX + b"bravo-blue-logo"
VIEWER_PW = "viewer-password-1"
KEEPER_PW = "keeper-password-1"


# ---------------------------------------------------------------------------
# Two companies in one data folder
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def company_template(tmp_path_factory):
    """One company file made the way New Company makes it (alembic to head,
    chart of accounts seeded), copied for each company a test needs."""
    from app.services import company_service

    path = tmp_path_factory.mktemp("template") / "template.db"
    company_service._init_company_db("sqlite:///" + path.as_posix(), "Template")
    return path


def _sign_in_here(Session) -> None:
    """Opening another company file is signing in to it: a sign-in belongs
    to the company it was made in (app/services/auth.py). The file takes the
    id the client's session was signed in with, so the session stays good
    here, as it would after signing in. The session also names its user and
    the version it is good for (revocable sessions), re-checked against
    the users table every request; a real switch stashes the person from the
    company switched to, so this template file carries the matching admin row
    (id 1, the client fixture's admin) the same way it carries the id above."""
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


class _Company:
    def __init__(self, path: Path, name: str):
        self.path = path
        self.name = name
        self.engine = create_engine(
            "sqlite:///" + path.as_posix(), connect_args={"check_same_thread": False}
        )
        # Every connection starts from the SQLite default the Windows and
        # macOS builds have: deleted bytes left in free pages. (Debian's and
        # Ubuntu's SQLite is compiled with secure_delete on, which would hide
        # whether the app turns it on.)
        event.listen(
            self.engine,
            "connect",
            lambda conn, _record: conn.execute("PRAGMA secure_delete=OFF"),
        )
        enable_sqlite_tuning(self.engine)  # the app's own PRAGMAs, after
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        register_audit_hooks(self.Session)


def _company_file(template, folder: Path, name: str) -> _Company:
    path = folder / (name.lower().replace(" ", "-") + ".db")
    shutil.copyfile(template, path)
    with closing(sqlite3.connect(path)) as con:
        con.execute("UPDATE settings SET value = ? WHERE key = 'company_name'", (name,))
        con.commit()
    return _Company(path, name)


@pytest.fixture
def companies(company_template, tmp_path, client, monkeypatch):
    """Acme Signs and Bravo Bakery, two company files in one folder."""
    folder = tmp_path / "companies"
    folder.mkdir()
    made = [
        _company_file(company_template, folder, name)
        for name in ("Acme Signs", "Bravo Bakery")
    ]

    def open_company(company: _Company) -> _Company:
        """What opening a company does: the app serves that file, requests
        and the PDF code alike."""

        def override(request: HTTPConnection = None):
            session = company.Session()
            try:
                yield session
            finally:
                session.close()

        _sign_in_here(company.Session)
        app.dependency_overrides[get_db] = override
        monkeypatch.setattr(db_module, "SessionLocal", company.Session)
        return company

    yield SimpleNamespace(a=made[0], b=made[1], open=open_company, folder=folder)
    for company in made:
        company.engine.dispose()


def _attach(client, record, content, name="receipt.txt", entity="invoice"):
    r = client.post(
        f"/api/attachments/{entity}/{record}",
        files={"file": (name, io.BytesIO(content), "text/plain")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _customer(client, name="Acme Signs"):
    r = client.post("/api/customers", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _invoice_one(client):
    """Invoice 1 of a fresh company: a file is attached only to a record that
    is there, and each company's first invoice has the same id."""
    invoice = _invoice(client)
    assert invoice["id"] == 1
    return invoice["id"]


def _employee(client, first):
    r = client.post("/api/employees", json={"first_name": first, "last_name": "Vance"})
    assert r.status_code == 201, r.text
    return r.json()


def _w4(client, emp_id, content, name="W-4.pdf"):
    r = client.post(
        f"/api/employees/{emp_id}/documents",
        files={"file": (name, io.BytesIO(content), "application/pdf")},
        data={"doc_category": "W-4"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _logo(client, content, name="logo.png"):
    r = client.post(
        "/api/uploads/logo", files={"file": (name, io.BytesIO(content), "image/png")}
    )
    assert r.status_code == 200, r.text
    return r.json()["path"]


def _download(client, attachment):
    return client.get(f"/api/attachments/download/{attachment['id']}")


def _document(client, emp_id, doc):
    return client.get(f"/api/employees/{emp_id}/documents/{doc['id']}")


def _files_under(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []


# ---------------------------------------------------------------------------
# skytech's three
# ---------------------------------------------------------------------------


def test_two_companies_same_named_file_on_the_same_record_each_read_their_own(
    client, companies
):
    """skytech: A attaches receipt.txt to its invoice 1, B attaches a
    receipt.txt to ITS invoice 1, and A's attachment opened B's text; B
    deleting its copy made A's answer 404 "File not found on disk"."""
    acme_text = b"Acme Signs: vinyl for invoice 1"
    bravo_text = b"Bravo Bakery: flour for invoice 1"
    companies.open(companies.a)
    acme = _attach(client, _invoice_one(client), acme_text)
    companies.open(companies.b)
    bravo = _attach(client, _invoice_one(client), bravo_text)

    companies.open(companies.a)
    assert _download(client, acme).content == acme_text
    companies.open(companies.b)
    assert _download(client, bravo).content == bravo_text

    assert client.delete(f"/api/attachments/{bravo['id']}").status_code == 200
    companies.open(companies.a)
    r = _download(client, acme)
    assert r.status_code == 200, r.text
    assert r.content == acme_text


def test_two_companies_employee_1_w4s_are_kept_apart(client, companies):
    """skytech: company A's employee #1 got a W-4.pdf; B uploaded a W-4.pdf
    for its own employee #1, and A's W-4 then opened B's employee's."""
    companies.open(companies.a)
    marisol = _employee(client, "Marisol")
    acme_w4 = _w4(client, marisol["id"], b"%PDF-1.4 W-4 Marisol Vance 123-45-6789")
    companies.open(companies.b)
    jonah = _employee(client, "Jonah")
    bravo_w4 = _w4(client, jonah["id"], b"%PDF-1.4 W-4 Jonah Vance 987-65-4321")
    # the same employee number in each company: the collision's precondition
    assert marisol["id"] == jonah["id"] == 1

    companies.open(companies.a)
    r = _document(client, marisol["id"], acme_w4)
    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.4 W-4 Marisol Vance 123-45-6789"
    companies.open(companies.b)
    assert (
        _document(client, jonah["id"], bravo_w4).content
        == b"%PDF-1.4 W-4 Jonah Vance 987-65-4321"
    )


def test_an_updated_w4_is_a_second_document_and_the_first_is_kept(client):
    """skytech: within one company an updated W-4.pdf replaced the original,
    so both entries opened the new one and the first W-4 was gone."""
    emp = _employee(client, "Marisol")
    first = _w4(client, emp["id"], b"%PDF-1.4 W-4 filed 2025: single")
    second = _w4(client, emp["id"], b"%PDF-1.4 W-4 filed 2026: married")

    listing = client.get(f"/api/employees/{emp['id']}/documents").json()
    assert sorted(d["id"] for d in listing) == sorted([first["id"], second["id"]])
    assert {d["filename"] for d in listing} == {"W-4.pdf"}
    r = _document(client, emp["id"], first)
    assert r.content == b"%PDF-1.4 W-4 filed 2025: single"
    assert 'filename="W-4.pdf"' in r.headers["content-disposition"]
    assert _document(client, emp["id"], second).content == (
        b"%PDF-1.4 W-4 filed 2026: married"
    )


def test_a_deleted_documents_bytes_are_nowhere(client, companies):
    """skytech: deleting an employee document removed only the row, so a
    "deleted" W-4 (with its SSN) stayed on disk. Now its bytes go with it:
    not in any file under the data folder, and not left in the company
    file's free pages either (secure_delete), where a later copy or backup
    of the file would still carry them."""
    marker = b"SSN 123-45-6789 marker-for-a-deleted-w4"
    company = companies.open(companies.a)
    emp = _employee(client, "Marisol")
    doc = _w4(client, emp["id"], b"%PDF-1.4 " + marker + b" " + os.urandom(20000))

    r = client.delete(f"/api/employees/{emp['id']}/documents/{doc['id']}")
    assert r.status_code == 200, r.text
    assert _document(client, emp["id"], doc).status_code == 404

    leftovers = [
        p for p in _files_under(storage.files_root()) if marker in p.read_bytes()
    ]
    assert leftovers == [], f"the deleted W-4 is still on disk: {leftovers}"
    company.engine.dispose()  # the last connection closes: WAL checkpointed
    for path in company.path.parent.glob(company.path.name + "*"):
        assert marker not in path.read_bytes(), f"the deleted W-4 is still in {path}"


# ---------------------------------------------------------------------------
# The logo, and the PDFs that carry it
# ---------------------------------------------------------------------------


@pytest.fixture
def pdf_html(monkeypatch):
    """Capture the HTML a PDF would be rendered from (WeasyPrint not needed)."""
    from app.services import pdf_service

    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


def _invoice(client):
    customer = client.post("/api/customers", json={"name": "Harbor Light"})
    assert customer.status_code in (200, 201), customer.text
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer.json()["id"],
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [
                {"description": "Sign", "quantity": 1, "rate": 40, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _data_uri(png):
    import base64

    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def test_each_companys_logo_and_invoice_pdf_are_its_own(client, companies, pdf_html):
    """macbase1 NEW-14 / skytech: every company's logo was
    uploads/company_logo.png, so B's upload put B's logo on A's invoices,
    whatever either file was called."""
    companies.open(companies.a)
    acme_url = _logo(client, PNG_ACME, "acme-logo.png")
    acme_invoice = _invoice(client)
    companies.open(companies.b)
    bravo_url = _logo(client, PNG_BRAVO, "bravo_brand_2026.png")
    bravo_invoice = _invoice(client)

    companies.open(companies.a)
    assert client.get(acme_url).content == PNG_ACME
    settings = client.get("/api/settings").json()
    assert settings["company_logo_path"] == acme_url
    html = client.get(f"/api/invoices/{acme_invoice['id']}/pdf").text
    assert _data_uri(PNG_ACME) in html
    assert _data_uri(PNG_BRAVO) not in html

    companies.open(companies.b)
    assert client.get(bravo_url).content == PNG_BRAVO
    html = client.get(f"/api/invoices/{bravo_invoice['id']}/pdf").text
    assert _data_uri(PNG_BRAVO) in html
    assert _data_uri(PNG_ACME) not in html


def test_the_pdf_logo_is_read_from_the_companys_database(client, monkeypatch):
    """A file on disk at the old shared path is never read for a PDF."""
    from app.services import pdf_service

    url = _logo(client, PNG_ACME)
    shared = storage.uploads_root()
    shared.mkdir(parents=True, exist_ok=True)
    stray = shared / "company_logo.png"
    stray.write_bytes(PNG_BRAVO)
    try:
        assert pdf_service._company_logo_data_uri({"company_logo_path": url}) == (
            _data_uri(PNG_ACME)
        )
        # the old setting value names the shared file: nothing is embedded
        assert (
            pdf_service._company_logo_data_uri(
                {"company_logo_path": "/static/uploads/company_logo.png"}
            )
            == ""
        )
    finally:
        stray.unlink()


def test_a_new_logo_replaces_the_old_one_and_removing_it_keeps_nothing(
    client, db_session
):
    from app.models.stored_files import StoredFile

    first = _logo(client, PNG_ACME)
    second = _logo(client, PNG_BRAVO)
    assert first != second  # a new address, so no page shows the old image
    assert client.get(first).status_code == 404
    assert client.get(second).content == PNG_BRAVO
    logos = db_session.query(StoredFile).filter(StoredFile.kind == "logo").all()
    assert [row.id for row in logos] == [int(second.rsplit("/", 1)[1])]

    info = client.get("/api/uploads/logo").json()
    assert info["path"] == second
    assert info["from_shared_folder"] is False and info["missing"] is False

    assert client.delete("/api/uploads/logo").status_code == 200
    db_session.expunge_all()
    assert db_session.query(StoredFile).filter(StoredFile.kind == "logo").count() == 0
    assert client.get(second).status_code == 404
    assert client.get("/api/settings").json()["company_logo_path"] == ""
    assert client.get("/api/uploads/logo").json()["path"] is None


def test_an_svg_logo_is_served_as_an_image_that_runs_nothing(client):
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    r = client.post(
        "/api/uploads/logo",
        files={"file": ("logo.svg", io.BytesIO(svg), "image/svg+xml")},
    )
    assert r.status_code == 200, r.text
    served = client.get(r.json()["path"])
    assert served.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in served.headers["content-security-policy"]


def test_the_employee_portal_shows_the_companys_own_logo(client, db_session):
    from app.routes.portal import _branding

    url = _logo(client, PNG_ACME)
    file_id = url.rsplit("/", 1)[1]
    assert _branding(db_session)["company_logo_url"] == f"/portal/logo?v={file_id}"
    client.post("/api/auth/logout")  # the employee has no app session
    for path in ("/portal/logo", "/portal/favicon.ico"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        assert r.content == PNG_ACME
        assert r.headers["content-type"] == "image/png"


# ---------------------------------------------------------------------------
# Deleting a file deletes its bytes
# ---------------------------------------------------------------------------


def test_deleting_an_attachment_deletes_its_stored_file(client, db_session):
    from app.models.stored_files import StoredFile

    att = _attach(client, _customer(client), b"a receipt", entity="customer")
    stored_id = int(att["file_path"].rsplit("/", 1)[1])
    assert db_session.get(StoredFile, stored_id) is not None
    assert client.delete(f"/api/attachments/{att['id']}").status_code == 200
    db_session.expunge_all()
    assert db_session.query(StoredFile).filter_by(id=stored_id).first() is None
    assert _download(client, att).status_code == 404


def test_the_audit_log_never_holds_a_files_bytes(client, db_session):
    """Every insert and delete is snapshotted into audit_log; a file's bytes
    must not be (a deleted W-4 would live on there, as text)."""
    from app.models.audit import AuditLog

    marker = "audit-marker-6789"
    emp = _employee(client, "Marisol")
    doc = _w4(client, emp["id"], b"%PDF-1.4 " + marker.encode())
    r = client.delete(f"/api/employees/{emp['id']}/documents/{doc['id']}")
    assert r.status_code == 200, r.text
    rows = db_session.query(AuditLog).filter(AuditLog.table_name == "stored_files")
    snapshots = [str(row.new_values) + str(row.old_values) for row in rows.all()]
    assert snapshots, "the stored file's insert and delete are still audited"
    assert all(marker not in text for text in snapshots)


# ---------------------------------------------------------------------------
# Scanned receipts (OCR intake)
# ---------------------------------------------------------------------------


def _scan(client, monkeypatch, name="receipt.png"):
    from app.services import ocr_service

    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_service, "ocr_language", lambda: "eng")
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_words",
        lambda data, lang=None: ("ACME OFFICE SUPPLY\nTOTAL $76.93\n", []),
    )
    r = client.post(
        "/api/ocr/receipt",
        files={"file": (name, b"\x89PNG\r\n\x1a\nscan-" + name.encode(), "image/png")},
    )
    assert r.status_code == 200, r.text
    return r.json()["intake_id"]


def _receipts_to_review(client):
    r = client.get("/api/dashboard/data?ids=receipts_review")
    assert r.status_code == 200, r.text
    return [item["filename"] for item in r.json()["receipts_review"]["items"]]


def test_each_company_reviews_only_its_own_scanned_receipts(
    client, companies, monkeypatch
):
    """The intake folder was shared too: every company's dashboard listed
    every company's scans waiting to be attached."""
    companies.open(companies.a)
    _scan(client, monkeypatch, "acme-scan.png")
    companies.open(companies.b)
    assert "acme-scan.png" not in _receipts_to_review(client)
    _scan(client, monkeypatch, "bravo-scan.png")
    assert _receipts_to_review(client) == ["bravo-scan.png"]
    companies.open(companies.a)
    assert _receipts_to_review(client) == ["acme-scan.png"]


def test_a_scan_waits_in_the_company_database_and_discard_deletes_it(
    client, db_session, monkeypatch
):
    from app.models.stored_files import StoredFile

    intake_id = _scan(client, monkeypatch)
    row = db_session.query(StoredFile).filter(StoredFile.token == intake_id).one()
    assert row.kind == "receipt_scan"
    stored_id = row.id
    assert client.get(f"/api/ocr/intake/{intake_id}/image").content.startswith(
        b"\x89PNG"
    )
    assert client.delete(f"/api/ocr/intake/{intake_id}").status_code == 200
    db_session.expunge_all()
    assert db_session.query(StoredFile).filter_by(id=stored_id).first() is None


# ---------------------------------------------------------------------------
# Backups carry every file
# ---------------------------------------------------------------------------


class _NoDispose:
    """restore_backup() disposes the app's engine so it re-reads the file;
    the suite's own engine must keep this test's transaction."""

    def dispose(self):
        pass


def test_a_backup_restored_on_another_machine_brings_every_file_back(
    client, companies, company_template, tmp_path, monkeypatch, pdf_html
):
    """A backup was the database alone: a company restored on another
    machine had every receipt, W-4 and logo missing, and its rows 404."""
    from app.services import backup_service

    here = companies.open(companies.a)
    _logo(client, PNG_ACME)
    invoice = _invoice(client)
    receipt = _attach(client, invoice["id"], b"receipt for the sign", "receipt.txt")
    emp = _employee(client, "Marisol")
    w4 = _w4(client, emp["id"], b"%PDF-1.4 W-4 Marisol")
    intake_id = _scan(client, monkeypatch, "scanned.png")
    r = client.post(
        f"/api/ocr/intake/{intake_id}/attach",
        json={"entity_type": "invoice", "entity_id": invoice["id"]},
    )
    assert r.status_code == 201, r.text
    scanned = r.json()

    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + str(here.path))
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path / "machine-1-backups")
    backup_service.BACKUP_DIR.mkdir()
    with here.Session() as db:
        made = backup_service.create_backup(db)
    assert made["success"], made
    here.engine.dispose()

    # Machine 2: a fresh data folder and company file, and none of machine
    # 1's folders (its uploads folder is moved away for the rest of the
    # test). Only the backup file is carried over, and restored.
    machine_1_uploads = storage.uploads_root()
    moved = tmp_path / "machine-1-uploads"
    if machine_1_uploads.exists():
        shutil.move(str(machine_1_uploads), str(moved))
    other = tmp_path / "machine-2"
    (other / "backups").mkdir(parents=True)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(other))
    shutil.copy(backup_service.BACKUP_DIR / made["filename"], other / "backups")
    there = _company_file(company_template, other, "Acme Signs")
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + str(there.path))
    monkeypatch.setattr(backup_service, "BACKUP_DIR", other / "backups")
    monkeypatch.setattr(db_module, "engine", _NoDispose())
    try:
        with there.Session() as db:
            restored = backup_service.restore_backup(db, made["filename"])
        assert restored["success"], restored
        there.engine.dispose()

        companies.open(there)
        assert _download(client, receipt).content == b"receipt for the sign"
        assert _download(client, scanned).content.startswith(b"\x89PNG")
        assert _document(client, emp["id"], w4).content == b"%PDF-1.4 W-4 Marisol"
        logo = client.get("/api/settings").json()["company_logo_path"]
        assert client.get(logo).content == PNG_ACME
        html = client.get(f"/api/invoices/{invoice['id']}/pdf").text
        assert _data_uri(PNG_ACME) in html
    finally:
        there.engine.dispose()
        if moved.exists():
            shutil.rmtree(machine_1_uploads, ignore_errors=True)
            shutil.move(str(moved), str(machine_1_uploads))


# ---------------------------------------------------------------------------
# Who may read what
# ---------------------------------------------------------------------------


def _sign_in_as(client, db_session, username, password, role):
    db_session.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password(password),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text


def test_employee_documents_stay_behind_the_hr_rule(client, db_session):
    """Employee documents are attachments rows too, and the generic
    attachment routes had none of their admin-only rule: a read-only sign-in
    could download a W-4 by its id, and a bookkeeper could delete one."""
    emp = _employee(client, "Marisol")
    doc = _w4(client, emp["id"], b"%PDF-1.4 W-4 Marisol 123-45-6789")

    _sign_in_as(client, db_session, "viewer", VIEWER_PW, ROLE_READONLY)
    # the rule: HR's documents are admin-only
    r = client.get(f"/api/employees/{emp['id']}/documents/{doc['id']}")
    assert r.status_code == 403, r.status_code
    # ...and the attachment routes don't go around it
    r = client.get(f"/api/attachments/download/{doc['id']}")
    assert r.status_code == 404, r.status_code
    assert b"123-45-6789" not in r.content
    r = client.get(f"/api/attachments/employee/{emp['id']}")
    assert r.status_code == 400, r.text

    _sign_in_as(client, db_session, "keeper", KEEPER_PW, ROLE_BOOKKEEPER)
    assert client.delete(f"/api/attachments/{doc['id']}").status_code == 404
    db_session.expire_all()
    from app.models.attachments import Attachment

    assert db_session.get(Attachment, doc["id"]) is not None


def test_the_old_uploads_folder_is_not_served(client, unauthed_client):
    """/static/ needs no sign-in, and the shared uploads folder was served
    under it: a W-4 was one guessable URL away from anyone who could reach
    the server. Nothing is served from that folder any more."""
    emp = _employee(client, "Marisol")
    _w4(client, emp["id"], b"%PDF-1.4 W-4 Marisol 123-45-6789")
    legacy = storage.uploads_root() / "attachments" / "employee" / "1"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "old-W-4.pdf").write_bytes(b"%PDF-1.4 an older release's W-4")
    try:
        for path in (
            f"/static/uploads/attachments/employee/{emp['id']}/W-4.pdf",
            "/static/uploads/attachments/employee/1/old-W-4.pdf",
            "/static/uploads/company_logo.png",
        ):
            r = unauthed_client.get(path)
            assert r.status_code == 404, (path, r.status_code)
            assert b"W-4" not in r.content
        assert unauthed_client.get("/static/js/app.js").status_code == 200
    finally:
        (legacy / "old-W-4.pdf").unlink()


# ---------------------------------------------------------------------------
# What the pages say
# ---------------------------------------------------------------------------

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _node(script: str) -> str:
    import shutil as _shutil
    import subprocess

    node = _shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    ).stdout


def _js_function(source: str, pattern: str) -> str:
    import re

    match = re.search(pattern, source, re.S)
    assert match, pattern
    return match.group(0)


def _text(markup: str) -> str:
    """What a person reads: tags stripped (until none are left), entities
    decoded."""
    import html
    import re

    previous = None
    while previous != markup:
        previous, markup = markup, re.sub(r"<[^>]*>", "", markup)
    return html.unescape(markup).strip()


def test_an_attachment_from_the_shared_folder_says_so():
    import json

    utils = (JS / "utils.js").read_text(encoding="utf-8")
    escape = _js_function(utils, r"function escapeHtml\(str\) \{.*?\n\}")
    note = _js_function(utils, r"function storedFileNote\(.*?\n\}")
    cases = [
        {"from_shared_folder": True, "missing": False},
        {"from_shared_folder": True, "missing": True},
        {"from_shared_folder": False, "missing": False},
    ]
    out = _node(
        f"{escape}\n{note}\n"
        f"const cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify(["
        "...cases.map(c => storedFileNote(c)), storedFileNote(cases[0], 'upload')]));"
    )
    shared, missing, plain, document_note = json.loads(out)
    assert _text(shared) == (
        "Copied from the folder earlier versions shared between companies. "
        "If it isn't the right file, delete it and attach the right one."
    )
    assert _text(missing).startswith(
        "Missing: this file was not in the shared folder when these books were "
        "upgraded."
    )
    assert plain == ""
    assert "upload the right one" in _text(document_note)


@pytest.mark.parametrize("page", ["invoices.js", "bills.js", "expenses.js"])
def test_each_attachment_list_shows_the_note_and_links_only_real_files(page):
    source = (JS / page).read_text(encoding="utf-8")
    loader = _js_function(source, r"async loadAttachments\(.*?\n    \},")
    assert "storedFileNote(a)" in loader
    # a missing file is named, not linked to a download that would 404
    assert "a.missing ?" in loader


def test_the_employee_document_list_shows_the_note_and_its_size():
    source = (JS / "employees.js").read_text(encoding="utf-8")
    loader = _js_function(source, r"async _loadDocuments\(id\) \{.*?\n    \},")
    assert "storedFileNote(doc, 'upload')" in loader
    assert "doc.file_size" in loader
    assert "doc.missing ?" in loader


def test_settings_says_where_the_logo_came_from():
    import json

    settings = (JS / "settings.js").read_text(encoding="utf-8")
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    escape = _js_function(utils, r"function escapeHtml\(str\) \{.*?\n\}")
    method = _js_function(settings, r"_logoNote\(logo\) \{.*?\n    \},")
    out = _node(
        f"{escape}\nconst page = {{ {method} }};\n"
        "console.log(JSON.stringify([page._logoNote({from_shared_folder: true}), "
        "page._logoNote({from_shared_folder: true, missing: true}), "
        "page._logoNote({from_shared_folder: false}), page._logoNote(null)]));"
    )
    shared, missing, own, none = json.loads(out)
    assert _text(shared) == (
        "This logo was copied from the folder earlier versions shared between "
        "companies. If this isn't your logo, upload it again."
    )
    assert _text(missing) == (
        "Your logo file was not in the shared folder when these books were "
        "upgraded. Upload it again."
    )
    assert own == "" and none == ""
    render = _js_function(settings, r"async render\(\) \{.*?return `")
    assert "API.get('/uploads/logo')" in render
