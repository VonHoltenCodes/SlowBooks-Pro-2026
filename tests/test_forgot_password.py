"""Forgot your password? (#719)

There was no way back into a company whose only administrator had forgotten
the password. Now:

- an encrypted desktop file opened with its recovery key sets a new
  administrator password: the company key is wrapped for it, the recovery
  key still opens the file, the other administrator is untouched;
- a desktop file that is not encrypted resets at the computer, with no
  other proof;
- the sign-in page's "Forgot your password?" says what to do for the
  company in front of it.

Either reset is made only by the desktop app's own window: the launcher
sends it with this run's window secret (app/services/desktop_window.py). The
same request from a browser on the network, a browser on this computer, an
API token, Server Edition, Docker or a windowless run is refused. Every
reset is in the audit log by how it was made, and ends that administrator's
other sessions.
"""

import json
import types

import pytest
from fastapi.testclient import TestClient

import desktop_launcher as dl
from app.models.audit import AuditLog
from app.models.users import ROLE_ADMIN, ROLE_BOOKKEEPER, User
from app.routes import auth as routes
from app.services import auth as auth_service
from app.services import company_file, company_keyring, desktop_window
from tests.test_company_keyring import _launch_stubs, _new_company_file

# sign-in names kept apart from the passwords below: a literal name beside a
# password reads as a credential to the repository's secret scanner
ADMIN_NAME = "admin"
BOB_NAME = "bob"
ADMIN_PW = "test-password-123"  # the suite's client fixture (conftest.py)
BOB_PW = "bobs-own-password"
NEW_PW = "a-brand-new-password"
SECRET = "w" * 43
RESET = "/api/auth/reset-at-computer"


@pytest.fixture(autouse=True)
def _no_runtime_key():
    company_file.set_runtime_key(None)
    yield
    company_file.set_runtime_key(None)


@pytest.fixture
def window(monkeypatch):
    """The desktop app's own window, started by the launcher for it."""
    monkeypatch.delenv(company_file.KEY_ENV, raising=False)
    monkeypatch.delenv(company_file.SECRET_ENV, raising=False)
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    monkeypatch.setenv(desktop_window.SECRET_ENV, SECRET)
    monkeypatch.delenv(desktop_window.RECOVERY_OPENED_ENV, raising=False)


def _point_at(monkeypatch, path):
    import app.config as config
    import app.database as db_module

    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///" + path.as_posix())
    monkeypatch.setattr(
        db_module, "engine", types.SimpleNamespace(dispose=lambda: None)
    )


@pytest.fixture
def plain(tmp_path, monkeypatch, window):
    """A plain company file standing in as the running desktop app's."""
    path = tmp_path / "harbor-light-bakery.db"
    _new_company_file(path, rows=(1, 2, 3))
    _point_at(monkeypatch, path)
    return path


def _add_bob(db_session, role=ROLE_ADMIN):
    bob = User(
        username="bob",
        display_name="Bob",
        password_hash=auth_service.hash_password(BOB_PW),
        role=role,
        is_active=True,
    )
    db_session.add(bob)
    db_session.commit()
    return bob


@pytest.fixture
def encrypted(tmp_path, monkeypatch, window, client, db_session):
    """An encrypted desktop company with two administrators, opened with
    its recovery key: the process holds the key, as the launcher hands it."""
    _add_bob(db_session)
    recovery = company_keyring.new_recovery_key()
    key = company_keyring.key_from_recovery(recovery)
    path = tmp_path / "harbor-light-bakery.db"
    _new_company_file(path, key, rows=(1, 2, 3))
    ring = company_keyring.new_ring(key, company_name="Harbor Light Bakery")
    company_keyring.set_admin(ring, "admin", ADMIN_PW, key)
    company_keyring.set_admin(ring, "bob", BOB_PW, key)
    company_keyring.save(path, ring)
    _point_at(monkeypatch, path)
    company_file.set_runtime_key(key)
    monkeypatch.setenv(desktop_window.RECOVERY_OPENED_ENV, "1")
    return types.SimpleNamespace(path=path, key=key, recovery=recovery)


def _at(host="127.0.0.1"):
    from app.main import app

    return TestClient(app, client=(host, 40000))


def _from_window(body, host="127.0.0.1", headers=None):
    """The request the launcher makes for its window."""
    sent = {desktop_window.SECRET_HEADER: SECRET}
    sent.update(headers or {})
    return _at(host).post(RESET, json=body, headers=sent)


def _window_admins(host="127.0.0.1", headers=None):
    """The launcher's question for its window: whose password may be set."""
    sent = {desktop_window.SECRET_HEADER: SECRET} if headers is None else headers
    return _at(host).get(RESET, headers=sent)


def _forgot(host="127.0.0.1"):
    r = _at(host).get("/api/auth/status")
    assert r.status_code == 200, r.text
    return r.json().get("forgot")


def _signs_in(password, username=None):
    body = {"password": password}
    if username:
        body["username"] = username
    return _at().post("/api/auth/login", json=body).status_code == 200


def _reset_rows(db_session):
    db_session.expire_all()
    return (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "users", AuditLog.action == "RESET")
        .all()
    )


# ---------------------------------------------------------------------------
# An encrypted file, opened with its recovery key
# ---------------------------------------------------------------------------


def test_the_recovery_key_sets_a_new_administrator_password(
    encrypted, client, db_session
):
    # the sign-in page offers it, with the administrators to choose from
    forgot = _forgot()
    assert forgot["kind"] == "recovery_reset"
    assert forgot["message"] == routes.FORGOT_RECOVERY_OPENED
    # the status names no administrator (W-125); the window asks for them
    assert "admins" not in forgot
    assert forgot["elsewhere"] == routes.FORGOT_ELSEWHERE
    assert _window_admins().json() == {"admins": ["admin", "bob"], "multi_user": True}
    # the administrator's session from before, elsewhere
    assert client.get("/api/settings").status_code == 200

    r = _from_window(
        {
            "username": "admin",
            "new_password": NEW_PW,
            "recovery_key": encrypted.recovery.lower(),
        }
    )
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "admin"
    assert r.json()["how"] == routes.RESET_BY_RECOVERY_KEY

    # their other sessions ended
    assert client.get("/api/settings").status_code == 401
    # the new password signs in; the old one is refused
    assert _signs_in(NEW_PW, "admin")
    assert not _signs_in(ADMIN_PW, "admin")
    # the other administrator is untouched
    assert _signs_in(BOB_PW, "bob")
    assert (
        company_keyring.unlock(encrypted.path, username=BOB_NAME, password=BOB_PW)
        == encrypted.key
    )
    # the company key is wrapped for the new password, not the old
    assert (
        company_keyring.unlock(encrypted.path, username=ADMIN_NAME, password=NEW_PW)
        == encrypted.key
    )
    with pytest.raises(company_keyring.Locked):
        company_keyring.unlock(encrypted.path, username=ADMIN_NAME, password=ADMIN_PW)
    # the recovery key is not used up: it still opens the file
    assert (
        company_keyring.unlock(encrypted.path, recovery_key=encrypted.recovery)
        == encrypted.key
    )
    # the audit log says how
    rows = _reset_rows(db_session)
    assert len(rows) == 1
    assert rows[0].source == "password reset by recovery key"
    assert rows[0].new_values["username"] == "admin"
    assert rows[0].new_values["password_reset"] == "by the recovery key"
    assert NEW_PW not in str(rows[0].new_values)


def test_an_encrypted_file_asks_for_its_own_recovery_key(encrypted, db_session):
    # no recovery key: open the company with it first
    r = _from_window({"username": "admin", "new_password": NEW_PW})
    assert r.status_code == 403
    assert r.json()["detail"] == routes.ENCRYPTED_NEEDS_KEY_MESSAGE
    # another file's recovery key
    r = _from_window(
        {
            "username": "admin",
            "new_password": NEW_PW,
            "recovery_key": company_keyring.new_recovery_key(),
        }
    )
    assert r.status_code == 403
    assert r.json()["detail"] == company_keyring.WRONG_RECOVERY_MESSAGE
    r = _from_window(
        {"username": "admin", "new_password": NEW_PW, "recovery_key": "not a key"}
    )
    assert r.status_code == 403
    # several administrators: which one
    r = _from_window({"new_password": NEW_PW, "recovery_key": encrypted.recovery})
    assert r.status_code == 400 and r.json()["detail"] == routes.CHOOSE_ADMIN_MESSAGE
    assert _signs_in(ADMIN_PW, "admin") and _reset_rows(db_session) == []


def test_an_encrypted_file_not_opened_with_its_key_says_how(encrypted, monkeypatch):
    monkeypatch.delenv(desktop_window.RECOVERY_OPENED_ENV)
    forgot = _forgot()
    assert forgot["kind"] == "recovery"
    # another administrator can reset it, and so can the recovery key
    assert forgot["message"] == routes.FORGOT_ASK_ANOTHER + " " + routes.FORGOT_RECOVERY
    assert "admins" not in forgot


# ---------------------------------------------------------------------------
# A desktop file that is not encrypted: being at the computer is the proof
# ---------------------------------------------------------------------------


def test_a_plain_desktop_file_resets_from_the_window(plain, client, db_session):
    forgot = _forgot()
    assert forgot == {
        "kind": "reset_here",
        "message": routes.FORGOT_HERE,
        "elsewhere": routes.FORGOT_ELSEWHERE,
    }
    assert _window_admins().json() == {"admins": ["admin"], "multi_user": False}
    assert client.get("/api/settings").status_code == 200
    # one administrator: the page need not name them
    r = _from_window({"new_password": NEW_PW})
    assert r.status_code == 200, r.text
    assert r.json()["how"] == routes.RESET_AT_COMPUTER
    assert r.json()["multi_user"] is False
    assert client.get("/api/settings").status_code == 401
    assert _signs_in(NEW_PW) and not _signs_in(ADMIN_PW)
    rows = _reset_rows(db_session)
    assert len(rows) == 1
    assert rows[0].source == "password reset at the computer"
    assert rows[0].username == "admin"
    assert "at the computer" in rows[0].new_values["password_reset"]
    # the settings-table hash, the single operator's, follows the row
    db_session.expire_all()
    assert auth_service.check_password(db_session, NEW_PW)
    assert not company_keyring.exists(plain)


def test_with_several_administrators_one_is_chosen_and_the_other_untouched(
    plain, client, db_session
):
    _add_bob(db_session)
    forgot = _forgot()
    assert forgot["kind"] == "reset_here"
    assert forgot["message"] == (
        routes.FORGOT_ASK_ANOTHER + " " + routes.FORGOT_HERE_OR_ASK
    )
    assert forgot["elsewhere"] == (
        routes.FORGOT_ASK_ANOTHER + " " + routes.FORGOT_ELSEWHERE
    )
    assert "admins" not in forgot
    assert _window_admins().json()["admins"] == ["admin", "bob"]
    r = _from_window({"new_password": NEW_PW})
    assert r.status_code == 400 and r.json()["detail"] == routes.CHOOSE_ADMIN_MESSAGE
    r = _from_window({"username": "BOB", "new_password": NEW_PW})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "status": "ok",
        "username": "bob",
        "multi_user": True,
        "how": routes.RESET_AT_COMPUTER,
    }
    assert _signs_in(NEW_PW, "bob") and not _signs_in(BOB_PW, "bob")
    assert _signs_in(ADMIN_PW, "admin")
    # the administrator who did not forget keeps their session
    assert client.get("/api/settings").status_code == 200


def test_only_an_administrator_and_a_real_password(plain, client, db_session):
    _add_bob(db_session, role=ROLE_BOOKKEEPER)
    r = _from_window({"username": "bob", "new_password": NEW_PW})
    assert r.status_code == 400 and r.json()["detail"] == routes.NOT_AN_ADMIN_MESSAGE
    r = _from_window({"username": "nobody", "new_password": NEW_PW})
    assert r.status_code == 400
    r = _from_window({"username": "admin", "new_password": "short"})
    assert r.status_code == 400 and "at least" in r.json()["detail"]
    assert _signs_in(ADMIN_PW, "admin") and _signs_in(BOB_PW, "bob")
    assert _reset_rows(db_session) == []


# ---------------------------------------------------------------------------
# Anything that is not the desktop app's own window is refused
# ---------------------------------------------------------------------------


def _refused(r):
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == routes.WINDOW_ONLY_MESSAGE


def test_the_same_request_from_anywhere_else_is_refused(
    plain, client, db_session, monkeypatch
):
    body = {"new_password": NEW_PW}
    # a browser on the network, even with the secret somehow in hand
    _refused(_from_window(body, host="192.168.68.60"))
    # a browser on this computer: no secret, or a wrong one
    _refused(_at().post(RESET, json=body))
    _refused(_from_window(body, headers={desktop_window.SECRET_HEADER: "x" * 43}))
    _refused(_from_window(body, headers={desktop_window.SECRET_HEADER: ""}))
    # the signed-in session's own cookie proves nothing either
    _refused(client.post(RESET, json=body))
    # an API token, whatever else it carries
    _refused(_from_window(body, headers={"Authorization": "Bearer sbp_anything"}))
    # Server Edition (--serve-lan)
    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    _refused(_from_window(body))
    assert _forgot()["kind"] == "ask_admin"
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE")
    # Docker, or a source run: no launcher
    monkeypatch.delenv("SLOWBOOKS_DESKTOP")
    _refused(_from_window(body))
    assert _forgot()["kind"] == "ask_admin"
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    # a windowless run (--no-window): the launcher gave it no secret
    monkeypatch.delenv(desktop_window.SECRET_ENV)
    _refused(_from_window(body))
    _refused(_from_window(body, headers={desktop_window.SECRET_HEADER: ""}))
    assert _forgot()["kind"] == "ask_admin"
    # nothing changed
    assert client.get("/api/settings").status_code == 200
    assert _signs_in(ADMIN_PW) and _reset_rows(db_session) == []


def test_forgot_says_ask_an_administrator_off_the_window(plain, client):
    client.post("/api/auth/logout")
    forgot = _forgot(host="192.168.68.60")
    assert forgot == {"kind": "ask_admin", "message": routes.FORGOT_ASK_ADMIN}


def test_forgot_is_not_in_the_status_once_signed_in(plain, client):
    assert "forgot" not in client.get("/api/auth/status").json()


def test_a_file_keyed_by_an_operator_secret_asks_an_administrator(
    plain, client, db_session, monkeypatch
):
    key = company_file.derive_key("correct horse battery staple")
    path = plain.with_name("secret-keyed.db")
    _new_company_file(path, key)
    _point_at(monkeypatch, path)
    assert _forgot()["kind"] == "ask_admin"
    r = _from_window({"new_password": NEW_PW})
    assert r.status_code == 403 and r.json()["detail"] == routes.FORGOT_ASK_ADMIN
    assert _signs_in(ADMIN_PW)


# ---------------------------------------------------------------------------
# The launcher: the secret goes to the window's server alone
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_window_env():
    saved = dict(dl._WINDOW_ENV)
    dl._WINDOW_ENV.clear()
    yield dl._WINDOW_ENV
    dl._WINDOW_ENV.clear()
    dl._WINDOW_ENV.update(saved)


def test_only_the_windows_loopback_server_gets_the_secret(
    clean_window_env, monkeypatch
):
    # a value from the shell never leaks through
    monkeypatch.setenv(desktop_window.SECRET_ENV, "from-the-shell")
    monkeypatch.setenv(desktop_window.RECOVERY_OPENED_ENV, "1")
    env = dl._server_env("sqlite:///x.db", 3001)
    assert desktop_window.SECRET_ENV not in env
    assert desktop_window.RECOVERY_OPENED_ENV not in env
    # the picker's own
    clean_window_env.update({"secret": SECRET, "recovery": True})
    env = dl._server_env("sqlite:///x.db", 3001)
    assert env[desktop_window.SECRET_ENV] == SECRET
    assert env[desktop_window.RECOVERY_OPENED_ENV] == "1"
    # Server Edition binds the network: never
    for host in ("0.0.0.0", "192.168.68.72"):
        env = dl._server_env("sqlite:///x.db", 3001, bind_host=host)
        assert desktop_window.SECRET_ENV not in env
        assert env["SLOWBOOKS_SERVER_MODE"] == "1"


def test_the_picker_says_how_the_company_was_opened(
    tmp_path, monkeypatch, window, clean_window_env
):
    recovery = company_keyring.new_recovery_key()
    key = company_keyring.key_from_recovery(recovery)
    db = tmp_path / "books.db"
    _new_company_file(db, key)
    ring = company_keyring.new_ring(key)
    company_keyring.set_admin(ring, "alice", BOB_PW, key)
    company_keyring.save(db, ring)
    _launch_stubs(monkeypatch, db)
    api = dl.PickerApi(3999)
    # nothing of it is a public attribute pywebview would hand the page
    assert [k for k in vars(api) if not k.startswith("_")] == []
    assert len(api._window_secret) >= 32
    assert dl.PickerApi(3999)._window_secret != api._window_secret
    assert api.unlock_company("books.db", "alice", BOB_PW, "")["success"]
    assert clean_window_env == {"secret": api._window_secret, "recovery": False}
    assert api._recovery_key == ""
    assert api.unlock_company("books.db", "", "", recovery)["success"]
    assert clean_window_env == {"secret": api._window_secret, "recovery": True}
    assert api._recovery_key == recovery
    # back to the list: forgotten
    monkeypatch.setattr(dl, "stop_server", lambda proc: None)
    api.show_picker()
    assert api._recovery_key == ""


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_launcher_sends_the_reset_with_its_secret(monkeypatch):
    import io
    import json
    import urllib.error

    sent = []

    class _Opener:
        def __init__(self, answer):
            self._answer = answer

        def open(self, request, timeout=None):
            sent.append(request)
            return self._answer(request)

    def ok(request):
        return _Resp(b'{"status": "ok", "username": "bob", "multi_user": true}')

    handlers = []

    def build_opener(*hs):
        handlers.extend(hs)
        return _Opener(answer)

    answer = ok
    monkeypatch.setattr(dl.urllib.request, "build_opener", build_opener)
    api = dl.PickerApi(3999)
    api._recovery_key = "ABCD-EFGH"
    out = api.reset_admin_password("bob", NEW_PW)
    assert out == {"success": True, "username": "bob", "multi_user": True}
    request = sent[0]
    assert request.full_url == "http://127.0.0.1:3999" + RESET
    assert request.get_method() == "POST"
    assert request.get_header(desktop_window.SECRET_HEADER.capitalize()) == (
        api._window_secret
    )
    assert json.loads(request.data) == {
        "username": "bob",
        "new_password": NEW_PW,
        "recovery_key": "ABCD-EFGH",
    }
    # never through a proxy
    proxies = [h for h in handlers if isinstance(h, dl.urllib.request.ProxyHandler)]
    assert proxies and proxies[0].proxies == {}

    def refused(request):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(json.dumps({"detail": routes.WINDOW_ONLY_MESSAGE}).encode()),
        )

    answer = refused
    assert api.reset_admin_password("bob", NEW_PW) == {
        "success": False,
        "error": routes.WINDOW_ONLY_MESSAGE,
    }

    def down(request):
        raise ConnectionRefusedError()

    answer = down
    out = api.reset_admin_password("bob", NEW_PW)
    assert out["success"] is False and "not open" in out["error"]


def test_the_docs_and_the_pages_say_what_the_key_can_do():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    doc = " ".join(
        (root / "docs/company-file-encryption.md").read_text(encoding="utf-8").split()
    )
    assert "## A forgotten password" in doc
    assert "Set a new administrator password" in doc
    assert "not used up" in doc
    words = "lets you set a new administrator password"
    assert words in doc
    assert words in (root / "app/static/js/auth.js").read_text(encoding="utf-8")
    settings = (root / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert words in " ".join(settings.split())
    text = company_keyring.recovery_file_text("Harbor Light Bakery", "ABCD")
    assert "set a new\nadministrator password" in text


# ---------------------------------------------------------------------------
# W-125: the status is read by anyone at the app's address on this computer
# ---------------------------------------------------------------------------


def test_the_status_names_no_administrator_and_the_names_need_the_secret(
    plain, client, db_session
):
    _add_bob(db_session)
    client.post("/api/auth/logout")
    # a browser at 127.0.0.1 reads the same status as the window: no names
    forgot = _forgot()
    assert forgot["kind"] == "reset_here"
    assert "admins" not in forgot and "bob" not in json.dumps(forgot)
    # and it cannot ask for them: no secret, a wrong one, a token, the LAN
    assert _window_admins(headers={}).status_code == 403
    wrong = {desktop_window.SECRET_HEADER: "x" * 43}
    assert _window_admins(headers=wrong).status_code == 403
    both = {desktop_window.SECRET_HEADER: SECRET, "Authorization": "Bearer tok-abc"}
    assert _window_admins(headers=both).status_code == 403
    assert _window_admins(host="192.168.68.60").status_code == 403
    r = _window_admins(headers={})
    assert r.json()["detail"] == routes.WINDOW_ONLY_MESSAGE
    # the window's launcher, with this run's secret, is told
    assert _window_admins().json()["admins"] == ["admin", "bob"]


def test_the_launcher_asks_for_the_names_with_its_secret(monkeypatch):
    api = dl.PickerApi(3999)
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"admins": ["admin", "bob"], "multi_user": True}).encode()

    class _Opener:
        def open(self, request, timeout=None):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["secret"] = request.get_header(
                desktop_window.SECRET_HEADER.capitalize()
            )
            return _Resp()

    monkeypatch.setattr(dl.urllib.request, "build_opener", lambda *a: _Opener())
    assert api.reset_admins() == {
        "success": True,
        "admins": ["admin", "bob"],
        "multi_user": True,
    }
    assert seen["url"] == "http://127.0.0.1:3999/api/auth/reset-at-computer"
    assert seen["method"] == "GET"
    assert seen["secret"] == api._window_secret
