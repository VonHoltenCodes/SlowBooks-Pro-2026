"""Company-file encryption, the plumbing (issue #290).

The five things the owner asked to settle before the feature itself:

1. The driver (`sqlcipher3`) is a dependency, named in both installers'
   hidden imports, and the frozen smoke test reads its version.
2. The key is in hand before the migrations run and before the "already
   running" check — before anything opens the file.
3. Server Edition unlocks from an operator-set credential
   (`COMPANY_FILE_ENCRYPTION_SECRET`), the way the payroll secret is set.
4. PostgreSQL deployments are the host's job, and the page says so.
5. Turning it on and off is an administrator's, the company's name typed.

Plus what those drag in: an encrypted file's backups are ciphertext under
the same key, and a restore leaves the books in the state they are in."""

import base64
import inspect
import os
import re
import types
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi.testclient import TestClient
from sqlalchemy import text

import desktop_launcher as dl
from app.main import app
from app.models.settings import Settings
from app.routes import company_file as routes
from app.services import backup_service as bs
from app.services import company_file, company_service

ROOT = Path(__file__).resolve().parents[1]
KEY = bytes(range(32))
OTHER = bytes(range(32, 64))
URL = "/api/company-file/encryption"
COMPANY = "Harbor Light Bakery"


def _new_company_file(path, key=None, rows=(1,)):
    conn = company_file.connect_raw(path, key)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    conn.commit()
    conn.close()


def _rows(path, key=None):
    conn = company_file.connect_raw(path, key)
    try:
        return [r[0] for r in conn.execute("SELECT x FROM t ORDER BY x")]
    finally:
        conn.close()


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv(company_file.KEY_ENV, raising=False)
    monkeypatch.delenv(company_file.SECRET_ENV, raising=False)


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------


def test_the_key_derives_the_way_the_payroll_key_does_under_its_own_salt():
    key = company_file.derive_key("correct horse battery staple")
    assert len(key) == 32
    assert key == company_file.derive_key("correct horse battery staple")
    assert key != company_file.derive_key("correct horse battery stapler")
    payroll = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"slowbooks-payroll-v1",
        iterations=480000,
    ).derive(b"correct horse battery staple")
    assert key != payroll  # the same secret in both never shares a key
    assert base64.urlsafe_b64encode(key) != base64.urlsafe_b64encode(payroll)


def test_the_launchers_key_wins_over_the_secret(monkeypatch, no_key):
    assert company_file.resolve_key() is None
    assert company_file.key_source() is None
    monkeypatch.setenv(company_file.SECRET_ENV, "correct horse battery staple")
    assert company_file.key_source() == "secret"
    assert company_file.resolve_key() == company_file.derive_key(
        "correct horse battery staple"
    )
    monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
    # the launcher's key is the one used; the secret is still where it
    # came from, and what Settings names (W-2 / NEW-108)
    assert company_file.key_source() == "secret"
    assert company_file.resolve_key() == KEY


def test_a_malformed_launcher_key_is_ignored_not_used(monkeypatch, no_key):
    monkeypatch.setenv(company_file.KEY_ENV, "not hex")
    assert company_file.resolve_key() is None
    monkeypatch.setenv(company_file.KEY_ENV, "abcd")  # too short
    assert company_file.resolve_key() is None


# ---------------------------------------------------------------------------
# The file and raw connections
# ---------------------------------------------------------------------------


def test_an_encrypted_file_is_told_from_a_plain_one(tmp_path):
    plain = tmp_path / "plain.db"
    _new_company_file(plain)
    enc = tmp_path / "enc.db"
    _new_company_file(enc, KEY)
    assert not company_file.is_encrypted_file(plain)
    assert company_file.is_encrypted_file(enc)
    assert not company_file.is_encrypted_file(tmp_path / "missing.db")
    (tmp_path / "empty.db").write_bytes(b"")
    assert not company_file.is_encrypted_file(tmp_path / "empty.db")
    assert not company_file.is_encrypted_file(None)


def test_a_keyed_connection_writes_ciphertext_and_reads_it_back(tmp_path):
    import sqlite3

    path = tmp_path / "books.db"
    _new_company_file(path, KEY, rows=(1, 2))
    assert not path.read_bytes().startswith(company_file.SQLITE_HEADER)
    assert _rows(path, KEY) == [1, 2]
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("SELECT x FROM t")
    with pytest.raises(company_file.CompanyFileLocked) as exc:
        company_file.connect_raw(path)  # no key
    assert str(exc.value) == company_file.NO_KEY_MESSAGE


def test_the_wrong_key_is_one_sentence_and_none_of_the_drivers_noise(tmp_path, capfd):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    with pytest.raises(company_file.CompanyFileLocked) as exc:
        company_file.connect_raw(path, OTHER)
    assert str(exc.value) == company_file.WRONG_KEY_MESSAGE
    out, err = capfd.readouterr()
    assert "ERROR CORE" not in err and "hmac" not in err, err


def test_a_read_only_connection_leaves_no_sidecars(tmp_path):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY, rows=(3,))
    conn = company_file.connect_raw(path, KEY, readonly=True)
    assert conn.execute("SELECT x FROM t").fetchone() == (3,)
    conn.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["books.db"]


def test_without_the_module_an_encrypted_file_is_refused_in_one_sentence(
    tmp_path, monkeypatch
):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    monkeypatch.setattr(company_file, "_sqlcipher", lambda: None)
    assert not company_file.available()
    assert company_file.cipher_version() is None
    with pytest.raises(company_file.CompanyFileLocked) as exc:
        company_file.connect_raw(path, KEY)
    assert str(exc.value) == company_file.UNAVAILABLE_MESSAGE


def test_cipher_version_is_sqlcipher_4():
    assert (company_file.cipher_version() or "").startswith("4.")


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def test_convert_rewrites_the_file_in_place_either_way(tmp_path):
    path = tmp_path / "books.db"
    _new_company_file(path, rows=(1, 2, 3))
    company_file.convert(path, KEY, encrypt=True)
    assert company_file.is_encrypted_file(path)
    assert _rows(path, KEY) == [1, 2, 3]
    company_file.convert(path, KEY, encrypt=True)  # already: nothing to do
    assert _rows(path, KEY) == [1, 2, 3]
    company_file.convert(path, KEY, encrypt=False)
    assert not company_file.is_encrypted_file(path)
    assert _rows(path) == [1, 2, 3]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["books.db"]


def test_convert_needs_the_right_key(tmp_path):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    with pytest.raises(company_file.CompanyFileLocked) as exc:
        company_file.convert(path, None, encrypt=False)
    assert str(exc.value) == company_file.NO_KEY_MESSAGE
    with pytest.raises(company_file.CompanyFileLocked) as exc:
        company_file.convert(path, OTHER, encrypt=False)
    assert str(exc.value) == company_file.WRONG_KEY_MESSAGE
    assert company_file.is_encrypted_file(path)  # untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == ["books.db"]


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def test_the_engine_keys_the_connection_before_the_tuning_pragmas(tmp_path):
    from app.database import enable_sqlite_tuning

    path = tmp_path / "books.db"
    _new_company_file(path, KEY, rows=(4,))
    looked_up = []

    def provider():
        looked_up.append(1)
        return KEY

    engine = company_file.create_sqlite_engine("sqlite:///" + path.as_posix(), provider)
    enable_sqlite_tuning(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM t")).scalar() == 4
        assert str(conn.execute(text("PRAGMA journal_mode")).scalar()).lower() == "wal"
    engine.dispose()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM t")).scalar() == 4
    engine.dispose()
    assert looked_up == [1]  # found once, kept
    assert company_file.is_encrypted_file(path)


def test_a_plain_file_opens_without_looking_for_a_key(tmp_path):
    path = tmp_path / "plain.db"
    _new_company_file(path, rows=(9,))
    engine = company_file.create_sqlite_engine(
        "sqlite:///" + path.as_posix(),
        lambda: pytest.fail("the key was looked up for a plain file"),
    )
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x FROM t")).scalar() == 9
    engine.dispose()


def test_the_engine_refuses_an_encrypted_file_without_a_key(tmp_path):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    engine = company_file.create_sqlite_engine("sqlite:///" + path.as_posix(), None)
    with pytest.raises(Exception, match=re.escape(company_file.NO_KEY_MESSAGE)):
        engine.connect()
    engine.dispose()


def test_the_apps_engine_opens_the_suites_database_as_before():
    from app.database import engine

    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


# ---------------------------------------------------------------------------
# Point 2: the key before the migrations and before the probe
# ---------------------------------------------------------------------------


def test_a_migration_opens_an_encrypted_file_with_the_key(tmp_path):
    from alembic import command
    from alembic.config import Config

    path = tmp_path / "books.db"
    conn = company_file.connect_raw(path, KEY)
    conn.execute("CREATE TABLE placeholder (x)")
    conn.commit()
    conn.close()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + path.as_posix()
    cfg.attributes["company_file_key"] = KEY
    command.upgrade(cfg, "head")
    assert company_file.is_encrypted_file(path)
    conn = company_file.connect_raw(path, KEY)
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()
    conn.close()


def test_a_migration_without_the_key_stops_with_one_sentence(tmp_path, no_key):
    from alembic import command
    from alembic.config import Config

    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + path.as_posix()
    with pytest.raises(Exception, match=re.escape(company_file.NO_KEY_MESSAGE)):
        command.upgrade(cfg, "head")
    assert _rows(path, KEY) == [1]  # untouched


class _Proc:
    pid = 1

    def poll(self):
        return None


def test_the_launcher_has_the_key_before_it_touches_the_file(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(
        company_file, "resolve_key", lambda: (order.append("key"), KEY)[1]
    )
    monkeypatch.setattr(
        dl, "_server_already_running", lambda port: (order.append("probe"), False)[1]
    )
    monkeypatch.setattr(company_service, "company_db_path", lambda f: tmp_path / f)
    monkeypatch.setattr(
        dl,
        "migrate",
        lambda url, output=None, key=None: order.append(("migrate", key)),
    )
    monkeypatch.setattr(
        dl,
        "start_server",
        lambda url, port, output=None, bind_host="127.0.0.1", key=None: (
            order.append(("start", key)),
            _Proc(),
        )[1],
    )
    monkeypatch.setattr(
        dl, "wait_for_health", lambda proc, port, host="127.0.0.1", **_: True
    )
    dl.launch_company("books.db", 3999, persist=False)
    assert order == ["key", "probe", ("migrate", KEY), ("start", KEY)]


def test_an_encrypted_file_without_a_key_stops_before_the_probe(
    monkeypatch, tmp_path, no_key
):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    monkeypatch.setattr(company_service, "company_db_path", lambda f: path)
    probed = []
    monkeypatch.setattr(
        dl, "_server_already_running", lambda port: probed.append(port) or False
    )
    with pytest.raises(RuntimeError, match=re.escape(company_file.NO_KEY_MESSAGE)):
        dl.launch_company("books.db", 3999, persist=False)
    assert probed == []


def test_the_server_child_gets_the_key_from_its_environment(monkeypatch):
    monkeypatch.setenv(company_file.KEY_ENV, "stale value from the shell")
    env = dl._server_env("sqlite:///x.db", 3001, key=KEY)
    assert env[company_file.KEY_ENV] == KEY.hex()
    env = dl._server_env("sqlite:///x.db", 3001)
    assert company_file.KEY_ENV not in env


def test_the_in_process_migration_gets_the_key_too(monkeypatch, tmp_path):
    seen = {}

    class _Cfg:
        def __init__(self, path):
            self.attributes = {}

        def set_main_option(self, *a):
            pass

    monkeypatch.setattr(dl, "FROZEN", True)
    # The frozen path writes SLOWBOOKS_DATA_DIR and DATABASE_URL into the
    # process environment; registering them here is what puts them back.
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", os.environ.get("SLOWBOOKS_DATA_DIR", ""))
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    import alembic.config
    import alembic.command

    monkeypatch.setattr(alembic.config, "Config", _Cfg)
    monkeypatch.setattr(
        alembic.command, "upgrade", lambda cfg, rev: seen.update(cfg.attributes)
    )
    dl.migrate("sqlite:///x.db", key=KEY)
    assert seen["company_file_key"] == KEY
    assert seen["database_url"] == "sqlite:///x.db"


def test_startup_refuses_a_locked_file_with_one_sentence(tmp_path, monkeypatch, no_key):
    import app.config as config

    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///" + path.as_posix())
    with pytest.raises(RuntimeError) as exc:
        company_file.refuse_a_locked_file()
    assert str(exc.value) == company_file.NO_KEY_MESSAGE
    monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
    company_file.refuse_a_locked_file()
    plain = tmp_path / "plain.db"
    _new_company_file(plain)
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///" + plain.as_posix())
    monkeypatch.delenv(company_file.KEY_ENV)
    company_file.refuse_a_locked_file()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://x:y@localhost/z")
    company_file.refuse_a_locked_file()


def test_the_startup_checks_run_the_guard():
    import app.main as main

    src = inspect.getsource(main._run_startup_security_checks)
    assert src.index("refuse_a_locked_file()") < src.index(
        "_refuse_a_database_behind_head()"
    )


# ---------------------------------------------------------------------------
# Points 3, 4 and 5: the route
# ---------------------------------------------------------------------------


def _as(client, role):
    r = client.post("/api/tokens", json={"label": f"as-{role}", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def _name_the_company(db_session, name=COMPANY):
    row = db_session.query(Settings).filter(Settings.key == "company_name").first()
    if row is None:
        db_session.add(Settings(key="company_name", value=name))
    else:
        row.value = name
    db_session.flush()


@pytest.fixture
def company(tmp_path, monkeypatch):
    """A plain company file standing in as the running app's, and an
    operator-set secret for it."""
    import app.config as config
    import app.database as db_module

    path = tmp_path / "harbor-light-bakery.db"
    _new_company_file(path, rows=(1, 2, 3))
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///" + path.as_posix())
    # The suite's engine is the in-memory database every test shares;
    # the route disposes the pool of the file it rewrites, not that one.
    monkeypatch.setattr(
        db_module, "engine", types.SimpleNamespace(dispose=lambda: None)
    )
    monkeypatch.delenv(company_file.KEY_ENV, raising=False)
    monkeypatch.setenv(company_file.SECRET_ENV, "correct horse battery staple")
    return path


def test_status_and_the_switch_are_an_administrators(client, company):
    r = client.get(URL)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["engine"] == "sqlite" and st["supported"] is True
    assert st["encrypted"] is False and st["key_source"] == "secret"
    assert st["file"] == "harbor-light-bakery.db"
    for role in ("readonly", "bookkeeper"):
        c = _as(client, role)
        assert c.get(URL).status_code == 403, role
        r = c.post(URL, json={"enabled": True, "company_name": COMPANY})
        assert r.status_code == 403, (role, r.text)
    assert not company_file.is_encrypted_file(company)


def test_the_switch_asks_for_the_companys_name(client, company, db_session):
    _name_the_company(db_session)
    r = client.post(URL, json={"enabled": True, "company_name": "Harbor Lite Bakery"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == routes.NAME_MISMATCH_MESSAGE
    assert not company_file.is_encrypted_file(company)
    r = client.post(URL, json={"enabled": True})
    assert r.status_code == 422


def test_the_switch_rewrites_the_file_in_place(client, company, db_session):
    _name_the_company(db_session)
    r = client.post(
        URL, json={"enabled": True, "company_name": "  harbor light BAKERY "}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True and body["encrypted"] is True
    assert body["key_source"] == "secret"
    assert company_file.is_encrypted_file(company)
    assert _rows(company, company_file.resolve_key()) == [1, 2, 3]

    r = client.post(URL, json={"enabled": True, "company_name": COMPANY})
    assert r.status_code == 200 and r.json()["changed"] is False

    # Turn off proves the administrator too (2.24.0 round 2)
    r = client.post(
        URL,
        json={
            "enabled": False,
            "company_name": COMPANY,
            "password": "test-password-123",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["encrypted"] is False
    assert not company_file.is_encrypted_file(company)
    assert _rows(company) == [1, 2, 3]
    assert sorted(p.name for p in company.parent.iterdir()) == [company.name]


def test_without_a_key_source_the_switch_says_what_to_set(
    client, company, db_session, monkeypatch
):
    monkeypatch.delenv(company_file.SECRET_ENV)
    _name_the_company(db_session)
    assert client.get(URL).json()["key_source"] is None
    r = client.post(URL, json={"enabled": True, "company_name": COMPANY})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == routes.NO_KEY_SOURCE_MESSAGE
    assert not company_file.is_encrypted_file(company)


def test_a_postgresql_company_is_told_whose_job_it_is(client, db_session, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://b:b@localhost:5432/b")
    st = client.get(URL).json()
    assert st["engine"] == "postgresql" and st["supported"] is False
    _name_the_company(db_session)
    r = client.post(URL, json={"enabled": True, "company_name": COMPANY})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == company_file.POSTGRESQL_MESSAGE


# ---------------------------------------------------------------------------
# Backups of an encrypted company
# ---------------------------------------------------------------------------


@pytest.fixture
def quiet_engine(monkeypatch):
    import app.database as db_module

    monkeypatch.setattr(
        db_module, "engine", types.SimpleNamespace(dispose=lambda: None)
    )


def _clean(*paths):
    for p in paths:
        Path(p).unlink(missing_ok=True)


def test_a_backup_of_an_encrypted_company_is_encrypted(
    tmp_path, monkeypatch, db_session
):
    live = tmp_path / "books.db"
    _new_company_file(live, KEY, rows=(7,))
    monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
    monkeypatch.setattr(bs, "_sqlite_db_path", lambda: live)
    result = bs._create_sqlite_backup(db_session, "notes", "manual")
    assert result["success"], result
    backup = bs.BACKUP_DIR / result["filename"]
    try:
        assert company_file.is_encrypted_file(backup)
        assert _rows(backup, KEY) == [7]
    finally:
        _clean(backup)


def test_a_restore_leaves_the_books_in_the_state_they_are_in(
    tmp_path, monkeypatch, quiet_engine
):
    monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
    plain_backup = bs.BACKUP_DIR / "books_20260101_000000.db"
    enc_backup = bs.BACKUP_DIR / "books_20260102_000000.db"
    try:
        # A plain backup into an encrypted company arrives encrypted...
        live = tmp_path / "books.db"
        _new_company_file(live, KEY, rows=(1,))
        _new_company_file(plain_backup, rows=(5, 6))
        monkeypatch.setattr(bs, "_sqlite_db_path", lambda: live)
        r = bs._restore_sqlite_backup(plain_backup, plain_backup.name)
        assert r["success"], r
        assert company_file.is_encrypted_file(live)
        assert _rows(live, KEY) == [5, 6]
        assert not company_file.is_encrypted_file(plain_backup)  # untouched
        # ...and an encrypted backup into a plain company arrives plain.
        plain_live = tmp_path / "plain.db"
        _new_company_file(plain_live, rows=(1,))
        _new_company_file(enc_backup, KEY, rows=(8,))
        monkeypatch.setattr(bs, "_sqlite_db_path", lambda: plain_live)
        r = bs._restore_sqlite_backup(enc_backup, enc_backup.name)
        assert r["success"], r
        assert not company_file.is_encrypted_file(plain_live)
        assert _rows(plain_live) == [8]
        assert company_file.is_encrypted_file(enc_backup)  # untouched
        assert not list(bs.BACKUP_DIR.glob("*.restoring"))
    finally:
        _clean(plain_backup, enc_backup)


def test_a_restore_without_the_key_says_so(tmp_path, monkeypatch, quiet_engine, no_key):
    live = tmp_path / "books.db"
    _new_company_file(live, KEY, rows=(1,))
    backup = bs.BACKUP_DIR / "books_20260103_000000.db"
    _new_company_file(backup, rows=(2,))
    monkeypatch.setattr(bs, "_sqlite_db_path", lambda: live)
    try:
        r = bs._restore_sqlite_backup(backup, backup.name)
        assert r == {"success": False, "error": company_file.NO_KEY_MESSAGE}
        assert _rows(live, KEY) == [1]
    finally:
        _clean(backup)


def test_backup_facts_read_an_encrypted_backup_with_the_key(monkeypatch, no_key):
    backup = bs.BACKUP_DIR / "harbor_20260104_000000.db"
    conn = company_file.connect_raw(backup, KEY)
    conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('company_name', ?)", (COMPANY,))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
    conn.execute("INSERT INTO alembic_version VALUES ('abc123')")
    conn.commit()
    conn.close()
    try:
        assert bs.read_backup_facts(backup.name) == {}  # no key in hand
        monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
        assert bs.read_backup_facts(backup.name) == {
            "company_name": COMPANY,
            "revision": "abc123",
        }
    finally:
        _clean(backup)


# ---------------------------------------------------------------------------
# Point 1 and the page: what ships
# ---------------------------------------------------------------------------


def test_settings_offers_the_switch_to_an_administrator_only():
    js = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert 'id="settings-company-file" data-admin hidden' in js
    assert "API.get('/company-file/encryption')" in js
    assert "API.post('/company-file/encryption'" in js
    assert "database host\\'s job" in js
    assert "SettingsPage.loadCompanyFile();" in js
    assert "Type the company's name to confirm." in js


def test_the_installers_carry_the_driver_and_the_smoke_test_proves_it():
    for spec in (
        "packaging/windows/SlowBooksPro.spec",
        "packaging/macos/SlowBooksPro-mac.spec",
    ):
        assert '"sqlcipher3",' in (ROOT / spec).read_text(encoding="utf-8"), spec
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "sqlcipher3>=0.6.3,<0.7" in req
    src = inspect.getsource(dl.run_smoke_test)
    assert "cipher_version()" in src
    assert "is_encrypted_file(scratch)" in src


# ---------------------------------------------------------------------------
# 2.23.2 gate, round 1: W-1, W-2 / NEW-108, NEW-109
# ---------------------------------------------------------------------------


def test_the_key_source_is_the_secret_the_launcher_hands_on(monkeypatch):
    # the launcher derives its key from the secret and hands it to its
    # server; Settings named "the launcher" where the person set a secret
    monkeypatch.setenv(company_file.SECRET_ENV, "operator-secret")
    monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
    assert company_file.key_source() == "secret"
    monkeypatch.delenv(company_file.SECRET_ENV)
    assert company_file.key_source() == "launcher"
    monkeypatch.delenv(company_file.KEY_ENV)
    assert company_file.key_source() is None


def test_a_rewrite_that_cannot_replace_the_file_leaves_no_copy(monkeypatch, tmp_path):
    # W-1: another program holding the file open refused the replace on
    # Windows, and a plain .converting copy of the encrypted books stayed
    path = tmp_path / "books.db"
    _new_company_file(path, KEY, rows=(1, 2, 3))
    before = path.read_bytes()

    def refuse(src, dst):
        raise PermissionError(5, "Access is denied", str(dst))

    monkeypatch.setattr(company_file.os, "replace", refuse)
    monkeypatch.setattr(company_file.time, "sleep", lambda s: None)
    with pytest.raises(company_file.CompanyFileLocked) as exc:
        company_file.convert(path, KEY, encrypt=False)
    assert "another program has it open" in str(exc.value)
    assert isinstance(exc.value, company_file.CompanyFileBusy)
    assert not path.with_name("books.db.converting").exists()
    assert path.read_bytes() == before  # the books are as they were
    assert _rows(path, KEY) == [1, 2, 3]


def test_a_replace_that_fails_once_is_retried(monkeypatch, tmp_path):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY, rows=(7,))
    real = company_file.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(5, "Access is denied", str(dst))
        return real(src, dst)

    monkeypatch.setattr(company_file.os, "replace", flaky)
    monkeypatch.setattr(company_file.time, "sleep", lambda s: None)
    company_file.convert(path, KEY, encrypt=False)
    assert len(calls) == 2
    assert not company_file.is_encrypted_file(path)
    assert _rows(path) == [7]


def test_a_start_sweeps_what_an_interrupted_rewrite_left(monkeypatch, tmp_path):
    path = tmp_path / "books.db"
    _new_company_file(path, rows=(1,))
    stale = path.with_name("books.db.converting")
    stale.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "old.db.restoring").write_bytes(b"x")
    (backups / "keep.db").write_bytes(b"x")
    from app.services import storage

    monkeypatch.setattr(storage, "backups_root", lambda: backups)
    removed = company_file.sweep_stale_conversions(path)
    assert sorted(removed) == ["books.db.converting", "old.db.restoring"]
    assert not stale.exists() and (backups / "keep.db").exists()
    # and the startup guard runs it before anything else
    monkeypatch.setattr(company_file, "active_path", lambda: path)
    stale.write_bytes(b"again")
    company_file.refuse_a_locked_file()
    assert not stale.exists()


def test_a_wrong_key_is_refused_without_touching_the_file(monkeypatch, tmp_path):
    # NEW-109: the writable check folded the WAL and removed the sidecars;
    # the read-only, immutable check changes nothing
    path = tmp_path / "books.db"
    _new_company_file(path, KEY, rows=(1, 2))
    conn = company_file.connect_raw(path, KEY)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO t VALUES (3)")
    conn.commit()  # a WAL is left behind on purpose
    before = path.read_bytes()
    wal = path.with_name("books.db-wal")
    had_wal = wal.exists()
    monkeypatch.setattr(company_file, "active_path", lambda: path)
    monkeypatch.setenv(company_file.KEY_ENV, OTHER.hex())
    monkeypatch.delenv(company_file.SECRET_ENV, raising=False)
    with pytest.raises(RuntimeError) as exc:
        company_file.refuse_a_locked_file()
    assert "does not open it" in str(exc.value)
    assert path.read_bytes() == before
    assert wal.exists() == had_wal
    # (closing the test's own writer folds its WAL into the file: measure after)
    conn.close()
    settled = path.read_bytes()
    # the right key, read-only, is accepted and changes nothing either
    monkeypatch.setenv(company_file.KEY_ENV, KEY.hex())
    company_file.refuse_a_locked_file()
    assert path.read_bytes() == settled


def test_the_launcher_refuses_a_wrong_key_before_the_migration(monkeypatch, tmp_path):
    path = tmp_path / "books.db"
    _new_company_file(path, KEY)
    monkeypatch.setattr(company_service, "company_db_path", lambda f: path)
    monkeypatch.setattr(company_file, "resolve_key", lambda: OTHER)
    touched = []
    monkeypatch.setattr(dl, "_server_already_running", lambda port: False)
    monkeypatch.setattr(dl, "migrate", lambda *a, **k: touched.append("migrate"))
    monkeypatch.setattr(dl, "start_server", lambda *a, **k: touched.append("start"))
    with pytest.raises(RuntimeError) as exc:
        dl.launch_company("books.db", 3999, persist=False)
    assert "does not open it" in str(exc.value)
    assert touched == []
