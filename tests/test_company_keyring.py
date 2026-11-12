"""The desktop's own key for an encrypted company file (#290): a recovery
key the key is derived from, shown once; wrapped for each administrator's
password in a keyring beside the file; the picker unlocks with a sign-in
or the recovery key; other administrators are wrapped at their next
sign-in, removed ones dropped; a new recovery key re-keys the file and
retires the old; a backup made under another key restores with its
recovery key; first-run setup can encrypt from the first day."""

import os
import re
import types
from pathlib import Path

import pytest

import desktop_launcher as dl
from app.models.settings import Settings
from app.routes import company_file as routes
from app.services import backup_service as bs
from app.services import company_file, company_keyring, company_service

ROOT = Path(__file__).resolve().parents[1]
URL = "/api/company-file/encryption"
COMPANY = "Harbor Light Bakery"
PW = "lamplight-and-ledger"


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


@pytest.fixture(autouse=True)
def _no_runtime_key():
    company_file.set_runtime_key(None)
    yield
    company_file.set_runtime_key(None)


@pytest.fixture
def desktop(monkeypatch):
    """A desktop with no operator secret: the keyring is the key's home."""
    monkeypatch.delenv(company_file.KEY_ENV, raising=False)
    monkeypatch.delenv(company_file.SECRET_ENV, raising=False)
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")


# ---------------------------------------------------------------------------
# The recovery key and the wrappings
# ---------------------------------------------------------------------------


def test_a_recovery_key_is_eight_groups_of_four_and_derives_the_same_key_anywhere():
    key = company_keyring.new_recovery_key()
    assert re.fullmatch(r"([A-Z2-7]{4}-){7}[A-Z2-7]{4}", key), key
    assert company_keyring.new_recovery_key() != key
    derived = company_keyring.key_from_recovery(key)
    assert len(derived) == company_file.KEY_BYTES
    # typed any old way
    typed = key.lower().replace("-", " ")
    assert company_keyring.key_from_recovery(typed) == derived
    assert company_keyring.is_recovery_key(typed)
    assert not company_keyring.is_recovery_key(key[:-5])
    with pytest.raises(ValueError):
        company_keyring.key_from_recovery("ABCD")
    # its own salt: never the key the operator secret would derive
    assert derived != company_file.derive_key(
        company_keyring.normalize_recovery_key(key)
    )


def test_a_wrapping_opens_with_its_password_and_nothing_else():
    key = os.urandom(32)
    entry = company_keyring.wrap(key, PW)
    assert company_keyring.unwrap(entry, PW) == key
    assert company_keyring.unwrap(entry, PW + "x") is None
    assert company_keyring.unwrap({"salt": "zz", "wrapped": "zz"}, PW) is None
    # the wrapped key is not in the file in the clear
    assert key.hex() not in entry["wrapped"]


def test_the_keyring_file_holds_nothing_secret_and_knows_a_stale_wrapping(tmp_path):
    db = tmp_path / "books.db"
    key = os.urandom(32)
    ring = company_keyring.new_ring(key, company_name=COMPANY)
    company_keyring.set_admin(ring, "Alice", PW, key)
    company_keyring.save(db, ring)
    assert company_keyring.exists(db)
    text = company_keyring.keyring_path(db).read_text(encoding="utf-8")
    assert key.hex() not in text and PW not in text
    loaded = company_keyring.load(db)
    assert company_keyring.admins(loaded) == ["alice"]
    assert company_keyring.wrapping_current(loaded, "ALICE")
    # a new recovery key gives the ring a new identity: the old wrapping is stale
    loaded["key_id"] = "another"
    assert not company_keyring.wrapping_current(loaded, "alice")
    assert company_keyring.status(db)["admins"] == ["alice"]
    assert company_keyring.drop_admin(
        loaded, "alice"
    ) and not company_keyring.drop_admin(loaded, "alice")
    company_keyring.remove(db)
    assert not company_keyring.exists(db)
    assert company_keyring.status(db) is None


# ---------------------------------------------------------------------------
# Unlocking
# ---------------------------------------------------------------------------


def _encrypted_company(tmp_path, admins=("alice",)):
    recovery = company_keyring.new_recovery_key()
    key = company_keyring.key_from_recovery(recovery)
    db = tmp_path / "books.db"
    _new_company_file(db, key, rows=(1, 2))
    ring = company_keyring.new_ring(key, company_name=COMPANY)
    for name in admins:
        company_keyring.set_admin(ring, name, PW, key)
    company_keyring.save(db, ring)
    return db, key, recovery


def test_the_picker_unlocks_with_a_sign_in_or_the_recovery_key(tmp_path):
    db, key, recovery = _encrypted_company(tmp_path)
    assert company_keyring.unlock(db, username="Alice", password=PW) == key
    assert company_keyring.unlock(db, recovery_key=recovery.lower()) == key
    for who, pw in (("alice", "wrong"), ("bob", PW), ("", PW)):
        with pytest.raises(company_keyring.Locked) as exc:
            company_keyring.unlock(db, username=who, password=pw)
        assert str(exc.value) == company_keyring.WRONG_PASSWORD_MESSAGE
    with pytest.raises(company_keyring.Locked) as exc:
        company_keyring.unlock(db, recovery_key=company_keyring.new_recovery_key())
    assert str(exc.value) == company_keyring.WRONG_RECOVERY_MESSAGE
    with pytest.raises(company_keyring.Locked) as exc:
        company_keyring.unlock(db, recovery_key="not a key")
    assert str(exc.value) == company_keyring.WRONG_RECOVERY_MESSAGE
    # a stale wrapping (the file re-keyed since) says what to do
    ring = company_keyring.load(db)
    ring["key_id"] = "re-keyed"
    company_keyring.save(db, ring)
    with pytest.raises(company_keyring.Locked) as exc:
        company_keyring.unlock(db, username="alice", password=PW)
    assert str(exc.value) == company_keyring.STALE_WRAPPING_MESSAGE
    # no keyring at all: the plain sentence
    company_keyring.remove(db)
    with pytest.raises(company_keyring.Locked) as exc:
        company_keyring.unlock(db, username="alice", password=PW)
    assert str(exc.value) == company_keyring.LOCKED_MESSAGE


def test_the_running_process_wraps_an_administrator_at_sign_in_and_forgets_one_removed(
    tmp_path, desktop
):
    db, key, _ = _encrypted_company(tmp_path)
    # the process has the key (the launcher handed it over)
    company_file.set_runtime_key(key)
    assert company_file.resolve_key() == key
    assert company_keyring.ensure_wrapped(db, "bob", "bobs-password") is True
    assert (
        company_keyring.ensure_wrapped(db, "bob", "bobs-password") is False
    )  # current
    assert company_keyring.unlock(db, username="bob", password="bobs-password") == key
    # a password change re-wraps on request
    assert company_keyring.ensure_wrapped(db, "bob", "new-password", force=True) is True
    assert company_keyring.unlock(db, username="bob", password="new-password") == key
    with pytest.raises(company_keyring.Locked):
        company_keyring.unlock(db, username="bob", password="bobs-password")
    assert company_keyring.forget(db, "bob") is True
    assert company_keyring.admins(company_keyring.load(db)) == ["alice"]
    assert company_keyring.forget(db, "bob") is False
    # without the key in hand nothing is wrapped
    company_file.set_runtime_key(None)
    assert company_keyring.ensure_wrapped(db, "carol", PW) is False


# ---------------------------------------------------------------------------
# The launcher
# ---------------------------------------------------------------------------


class _Proc:
    def poll(self):
        return None


def _launch_stubs(monkeypatch, path):
    started = []
    monkeypatch.setattr(company_service, "company_db_path", lambda f: path)
    # the launcher keeps its .env beside itself in a checkout, so an unlock
    # here wrote DATABASE_URL into the repository's .env, and a test that
    # watches that file for stray writes (test_server_mode) failed when the
    # two ran at once; the .env goes beside the company file instead
    monkeypatch.setattr(dl, "_config_dir", lambda: Path(path).parent)
    monkeypatch.setattr(dl, "_server_already_running", lambda port: False)
    monkeypatch.setattr(
        dl,
        "migrate",
        lambda url, output=None, key=None: started.append(("migrate", key)),
    )
    monkeypatch.setattr(
        dl,
        "start_server",
        lambda url, port, output=None, bind_host="127.0.0.1", key=None: (
            started.append(("start", key)),
            _Proc(),
        )[1],
    )
    monkeypatch.setattr(
        dl, "wait_for_health", lambda proc, port, host="127.0.0.1", **_: True
    )
    return started


def test_the_launcher_says_locked_and_the_picker_unlocks(
    tmp_path, monkeypatch, desktop
):
    db, key, recovery = _encrypted_company(tmp_path)
    started = _launch_stubs(monkeypatch, db)
    with pytest.raises(
        dl.LockedCompany, match=re.escape(company_keyring.LOCKED_MESSAGE)
    ):
        dl.launch_company("books.db", 3999, persist=False)
    assert started == []
    api = dl.PickerApi(3999)
    out = api.open_company("books.db")
    assert out == {
        "success": False,
        "locked": True,
        "error": company_keyring.LOCKED_MESSAGE,
        "admins": ["alice"],
    }
    assert started == []
    # the wrong password launches nothing and says so
    out = api.unlock_company("books.db", "alice", "wrong", "")
    assert out["locked"] and out["error"] == company_keyring.WRONG_PASSWORD_MESSAGE
    assert started == []
    # the sign-in: the key goes to the migration and the server child
    out = api.unlock_company("books.db", "Alice", PW, "")
    assert out["success"] and out["url"].endswith(":3999")
    assert started == [("migrate", key), ("start", key)]
    # the recovery key, typed in lower case
    started.clear()
    out = api.unlock_company("books.db", "", "", recovery.lower())
    assert out["success"]
    assert started == [("migrate", key), ("start", key)]
    # a secret-keyed file (no keyring) keeps its own sentence
    company_keyring.remove(db)
    with pytest.raises(RuntimeError, match=re.escape(company_file.NO_KEY_MESSAGE)):
        dl.launch_company("books.db", 3999, persist=False)


def test_headless_says_how_a_desktop_keyed_file_unlocks(
    tmp_path, monkeypatch, desktop, capsys
):
    db, _, _ = _encrypted_company(tmp_path)
    _launch_stubs(monkeypatch, db)
    monkeypatch.setattr(company_service, "get_last_opened", lambda: "books.db")
    assert dl._start_default_company(3999) is None
    assert company_keyring.HEADLESS_MESSAGE in capsys.readouterr().out


def test_the_picker_page_has_the_unlock_panel():
    assert "unlock_company" in dl.PICKER_HTML
    assert 'id="unlock-key"' in dl.PICKER_HTML and 'id="unlock-pass"' in dl.PICKER_HTML
    assert "Use the recovery key instead" in dl.PICKER_HTML


# ---------------------------------------------------------------------------
# The switch, the recovery key, the status
# ---------------------------------------------------------------------------


@pytest.fixture
def company(tmp_path, monkeypatch, desktop):
    """A plain company file standing in as the running desktop app's."""
    import app.config as config
    import app.database as db_module

    path = tmp_path / "harbor-light-bakery.db"
    _new_company_file(path, rows=(1, 2, 3))
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///" + path.as_posix())
    monkeypatch.setattr(
        db_module, "engine", types.SimpleNamespace(dispose=lambda: None)
    )
    return path


def _name_the_company(db_session, name=COMPANY):
    row = db_session.query(Settings).filter(Settings.key == "company_name").first()
    if row is None:
        db_session.add(Settings(key="company_name", value=name))
    else:
        row.value = name
    db_session.commit()


def _admin_password(client):
    """The password the suite's client set up its administrator with
    (tests/conftest.py, the client fixture)."""
    return "test-password-123"


def test_turning_on_makes_a_recovery_key_and_wraps_the_administrator(
    client, company, db_session
):
    _name_the_company(db_session)
    st = client.get(URL).json()
    assert st["encrypted"] is False and st["key_source"] is None
    assert st["can_turn_on"] is True and st["desktop_key"] is True
    pw = _admin_password(client)
    # the password is asked for, and checked
    r = client.post(URL, json={"enabled": True, "company_name": COMPANY})
    assert r.status_code == 400 and r.json()["detail"] == routes.PASSWORD_NEEDED_MESSAGE
    r = client.post(
        URL, json={"enabled": True, "company_name": COMPANY, "password": "nope"}
    )
    assert r.status_code == 403 and r.json()["detail"] == routes.WRONG_PASSWORD_MESSAGE
    assert not company_file.is_encrypted_file(company)

    r = client.post(
        URL, json={"enabled": True, "company_name": COMPANY, "password": pw}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    recovery = out["recovery_key"]
    assert company_keyring.is_recovery_key(recovery)
    assert COMPANY in out["recovery_file"] and recovery in out["recovery_file"]
    assert out["encrypted"] is True and out["key_source"] == "keyring"
    assert out["keyring"]["admins"] == ["admin"] and out["keyring"]["stale"] == []
    key = company_keyring.key_from_recovery(recovery)
    assert company_file.is_encrypted_file(company)
    assert _rows(company, key) == [1, 2, 3]
    # the process keeps the key; the keyring beside the file wraps it
    assert company_file.resolve_key() == key
    assert company_keyring.unlock(company, username="admin", password=pw) == key
    # shown once: the status never carries it
    st = client.get(URL).json()
    assert "recovery_key" not in st and st["keyring"]["recovery_made_at"]

    # a new recovery key re-keys the file and retires the old one
    r = client.post(
        f"{URL}/recovery-key", json={"company_name": COMPANY, "password": "nope"}
    )
    assert r.status_code == 403
    r = client.post(
        f"{URL}/recovery-key", json={"company_name": COMPANY, "password": pw}
    )
    assert r.status_code == 200, r.text
    again = r.json()["recovery_key"]
    assert again != recovery
    new_key = company_keyring.key_from_recovery(again)
    assert _rows(company, new_key) == [1, 2, 3]
    with pytest.raises(company_file.CompanyFileLocked):
        company_file.verify_key(company, key)
    assert company_file.resolve_key() == new_key
    assert company_keyring.unlock(company, username="admin", password=pw) == new_key
    assert company_keyring.unlock(company, recovery_key=again) == new_key

    # off again: plain, no keyring, no key held
    r = client.post(
        URL, json={"enabled": False, "company_name": COMPANY, "password": pw}
    )
    assert r.status_code == 200, r.text
    assert r.json()["encrypted"] is False
    assert _rows(company) == [1, 2, 3]
    assert not company_keyring.exists(company)
    assert company_file.resolve_key() is None
    r = client.post(
        f"{URL}/recovery-key", json={"company_name": COMPANY, "password": pw}
    )
    assert r.status_code == 409 and "not encrypted" in r.json()["detail"]


def test_an_operator_secret_still_keys_a_server_the_old_way(
    client, company, db_session, monkeypatch
):
    _name_the_company(db_session)
    monkeypatch.setenv(company_file.SECRET_ENV, "correct horse battery staple")
    st = client.get(URL).json()
    assert st["key_source"] == "secret" and st["desktop_key"] is False
    r = client.post(URL, json={"enabled": True, "company_name": COMPANY})
    assert r.status_code == 200, r.text
    assert "recovery_key" not in r.json() and r.json()["keyring"] is None
    assert _rows(company, company_file.derive_key("correct horse battery staple")) == [
        1,
        2,
        3,
    ]
    r = client.post(
        f"{URL}/recovery-key", json={"company_name": COMPANY, "password": "x"}
    )
    assert (
        r.status_code == 409 and "COMPANY_FILE_ENCRYPTION_SECRET" in r.json()["detail"]
    )


def test_a_server_without_a_secret_is_told_what_to_set(
    client, company, db_session, monkeypatch
):
    _name_the_company(db_session)
    monkeypatch.delenv("SLOWBOOKS_DESKTOP", raising=False)
    st = client.get(URL).json()
    assert st["can_turn_on"] is False
    r = client.post(
        URL, json={"enabled": True, "company_name": COMPANY, "password": "x"}
    )
    assert r.status_code == 409 and r.json()["detail"] == routes.NO_KEY_SOURCE_MESSAGE


# ---------------------------------------------------------------------------
# Sign-ins keep the keyring current; a removed administrator is dropped
# ---------------------------------------------------------------------------


def test_another_administrators_sign_in_wraps_them_and_a_demotion_drops_them(
    client, company, db_session
):
    from app.models.users import ROLE_ADMIN, User
    from app.services import auth as auth_service

    _name_the_company(db_session)
    pw = _admin_password(client)
    r = client.post(
        URL, json={"enabled": True, "company_name": COMPANY, "password": pw}
    )
    assert r.status_code == 200, r.text
    bob = User(
        username="bob",
        display_name="Bob",
        password_hash=auth_service.hash_password("bobs-password"),
        role=ROLE_ADMIN,
        is_active=True,
    )
    db_session.add(bob)
    db_session.commit()
    assert company_keyring.admins(company_keyring.load(company)) == ["admin"]
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "bob", "password": "bobs-password"}
    )
    assert r.status_code == 200, r.text
    assert company_keyring.admins(company_keyring.load(company)) == ["admin", "bob"]
    key = company_file.resolve_key()
    assert (
        company_keyring.unlock(company, username="bob", password="bobs-password") == key
    )
    # bob changes his password: the wrapping follows
    r = client.post(
        "/api/auth/password",
        json={"current_password": "bobs-password", "new_password": "bobs-new-password"},
    )
    assert r.status_code == 200, r.text
    assert (
        company_keyring.unlock(company, username="bob", password="bobs-new-password")
        == key
    )
    # the first administrator demotes bob: his wrapping goes
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"username": "admin", "password": pw})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/users/{bob.id}", json={"role": "bookkeeper"})
    assert r.status_code == 200, r.text
    assert company_keyring.admins(company_keyring.load(company)) == ["admin"]
    # a bookkeeper's sign-in wraps nothing
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "bob", "password": "bobs-new-password"}
    )
    assert r.status_code == 200, r.text
    assert company_keyring.admins(company_keyring.load(company)) == ["admin"]


# ---------------------------------------------------------------------------
# Backups made under another key
# ---------------------------------------------------------------------------


def test_a_backup_made_under_another_key_restores_with_its_recovery_key(
    tmp_path, monkeypatch, desktop
):
    import app.database as db_module

    monkeypatch.setattr(
        db_module, "engine", types.SimpleNamespace(dispose=lambda: None)
    )
    live, live_key, _ = _encrypted_company(tmp_path)
    company_file.set_runtime_key(live_key)
    monkeypatch.setattr(bs, "_sqlite_db_path", lambda: live)
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(bs, "BACKUP_DIR", backups)
    # the other laptop's backup: encrypted under its own recovery key
    other_recovery = company_keyring.new_recovery_key()
    other_key = company_keyring.key_from_recovery(other_recovery)
    backup = backups / "other-laptop.db"
    _new_company_file(backup, other_key, rows=(7, 8, 9))

    assert bs.backup_key_problem("other-laptop.db") == {
        "code": "other_key",
        "message": bs.OTHER_KEY_MESSAGE,
    }
    assert (
        bs.backup_key_problem("other-laptop.db", company_keyring.new_recovery_key())[
            "code"
        ]
        == "wrong_recovery_key"
    )
    assert bs.backup_key_problem("other-laptop.db", other_recovery) is None
    out = bs._restore_sqlite_backup(backup, "other-laptop.db")
    assert out["success"] is False and out["code"] == "other_key"
    assert _rows(live, live_key) == [1, 2]
    out = bs._restore_sqlite_backup(backup, "other-laptop.db", other_recovery)
    assert out["success"] is True, out
    # the books are the backup's, under this company's own key still
    assert _rows(live, live_key) == [7, 8, 9]
    # the backup file itself is as it was, and nothing staged is left
    assert _rows(backup, other_key) == [7, 8, 9]
    assert not list(backups.glob("*.restoring"))
    # a plain company takes it too, plain
    plain = tmp_path / "plain.db"
    _new_company_file(plain, rows=(1,))
    company_file.set_runtime_key(None)
    monkeypatch.setattr(bs, "_sqlite_db_path", lambda: plain)
    out = bs._restore_sqlite_backup(backup, "other-laptop.db", other_recovery)
    assert out["success"] is True, out
    assert _rows(plain) == [7, 8, 9] and not company_file.is_encrypted_file(plain)


def test_the_restore_route_asks_for_the_recovery_key(client, db_session, monkeypatch):
    monkeypatch.setattr(
        bs,
        "backup_key_problem",
        lambda name, key=None: {"code": "other_key", "message": bs.OTHER_KEY_MESSAGE},
    )
    monkeypatch.setattr(bs, "backup_path", lambda name: Path(__file__))  # exists
    monkeypatch.setattr(bs, "other_company", lambda db, name: None)
    monkeypatch.setattr(bs, "backup_revision_problem", lambda name: None)
    r = client.post("/api/backups/restore", json={"filename": "x.db"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "other_key"


# ---------------------------------------------------------------------------
# First-run setup can encrypt from the first day
# ---------------------------------------------------------------------------


def test_setup_encrypts_from_the_first_day_on_a_desktop(unauthed_client, company):
    r = unauthed_client.post(
        "/api/auth/setup",
        json={"password": PW, "company_name": COMPANY, "encrypt": True},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["encrypted"] is True and company_keyring.is_recovery_key(
        out["recovery_key"]
    )
    key = company_keyring.key_from_recovery(out["recovery_key"])
    assert company_file.is_encrypted_file(company) and _rows(company, key) == [1, 2, 3]
    assert company_keyring.admins(company_keyring.load(company)) == ["admin"]
    assert company_keyring.unlock(company, username="admin", password=PW) == key


def test_setup_without_the_box_leaves_the_file_plain(unauthed_client, company):
    r = unauthed_client.post(
        "/api/auth/setup", json={"password": PW, "company_name": COMPANY}
    )
    assert r.status_code == 200, r.text
    assert "recovery_key" not in r.json()
    assert not company_file.is_encrypted_file(company)


# ---------------------------------------------------------------------------
# The pages and the docs
# ---------------------------------------------------------------------------


def test_the_pages_offer_the_key_and_the_docs_explain_it():
    settings = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert "New recovery key…" in settings and "showRecoveryKey(" in settings
    assert (
        "Save as a text file" in settings
        and "/company-file/encryption/recovery-key" in settings
    )
    assert "other_key" in settings and "recovery_key = typed.trim()" in settings
    auth = (ROOT / "app/static/js/auth.js").read_text(encoding="utf-8")
    assert (
        'id="auth-encrypt"' in auth
        and "showRecoveryKey(overlay, done, onSuccess)" in auth
    )
    doc = (ROOT / "docs/company-file-encryption.md").read_text(encoding="utf-8")
    for words in (
        "recovery key",
        "keyring",
        "New recovery key",
        "COMPANY_FILE_ENCRYPTION_SECRET",
    ):
        assert words in doc
    assert "company-file-encryption.md" in (ROOT / "README.md").read_text(
        encoding="utf-8"
    )
    hardening = (ROOT / "docs/security-hardening.md").read_text(encoding="utf-8")
    assert (
        "slowbooks-company-recovery-v1" in hardening
        and "Not yet"
        not in hardening.split("### Company file encryption")[1].split("---")[0]
    )
