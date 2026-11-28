"""One AI assistant connection, every company it was granted (2.32.0-beta.2).

A connection reaches each company an administrator of that company added,
inside that company, with a bookkeeper token of its own made in its own
books; the assistant names the books on every call by a handle only this
computer knows. Power over the whole connection (a sign-in code, Add,
Disconnect) belongs to someone who is an administrator of every company on
it. Every edit of the kept record is made under a lock the operating system
holds, and one process runs the line.

These run against a Server Edition host serving real company files (and,
where it is the desktop's rule, the suite's own desktop client), with the
fake relay of test_switchboard_connect_1087 on 127.0.0.1. Every password,
token and credential is made at run time with `secrets`.
"""

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.database as db_module
from app.database import CompanySession
from app.main import app
from app.services import auth as auth_service
from app.services import served_companies
from app.services import switchboard_link as link
from app.switchboard import line as line_client
from app.switchboard.client import Refused
from app.switchboard.server import build_registry
from tests.test_served_companies import _make_company
from tests.test_switchboard_connect_1087 import (
    _THIS_COMPUTER,
    FakeRelay,
    _in_process_transport,
    _link_itself,
    _wait,
)

ROOT = Path(__file__).resolve().parents[1]
HANDLE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}~[a-z2-7]{6}")

COMPANIES = (
    ("harbor.db", "Harbor Books"),
    ("smith.db", "Smith Plumbing"),
    ("lake.db", "Lake Dental"),
)


def _pw():
    import secrets

    return secrets.token_urlsafe(16)


def _secret(prefix):
    import secrets

    return prefix + secrets.token_urlsafe(32)


@pytest.fixture
def relay(monkeypatch, tmp_path):
    """The fake relay of test_switchboard_connect_1087, the kept record in
    this test's folder, and each company's requests made in-process."""
    fake = FakeRelay()
    monkeypatch.setenv(link.RELAY_ENV, fake.url)
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    monkeypatch.setattr(
        link,
        "TIMINGS",
        line_client.Timings(backoff_first=0.05, backoff_max=0.2, welcome_wait=5),
    )
    monkeypatch.setattr(link, "_books_transport", _in_process_transport)
    try:
        yield fake
    finally:
        link.stop()
        link.stop_retry()
        link.stop_owed_retry(timeout=15)
        fake.close()


# -- a Server Edition host with three companies ------------------------------------


@pytest.fixture
def three(tmp_path, monkeypatch):
    """Harbor Books (the company the server was started for), Smith Plumbing
    and Lake Dental. jdoe is an administrator of all three with one
    password; cli is Lake Dental's own administrator; bkpr keeps Harbor's
    books."""
    pw = {"jdoe": _pw(), "cli": _pw(), "bkpr": _pw()}
    folder = tmp_path / "companies"
    folder.mkdir()
    _make_company(
        folder / "harbor.db",
        "Harbor Books",
        [("jdoe", pw["jdoe"], "admin", True), ("bkpr", pw["bkpr"], "bookkeeper", True)],
    )
    _make_company(
        folder / "smith.db", "Smith Plumbing", [("jdoe", pw["jdoe"], "admin", True)]
    )
    _make_company(
        folder / "lake.db",
        "Lake Dental",
        [("jdoe", pw["jdoe"], "admin", True), ("cli", pw["cli"], "admin", True)],
    )
    host = SimpleNamespace(root=tmp_path, folder=folder, pw=pw)
    _serve(host, [f for f, _ in COMPANIES])
    url = "sqlite:///" + (folder / "harbor.db").as_posix()
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    for module in ("app.database", "app.services.served_companies"):
        monkeypatch.setattr(f"{module}.DATABASE_URL", url)
    from app.services import backup_service, company_service

    monkeypatch.setattr(company_service, "DATABASE_URL", url)
    monkeypatch.setattr(backup_service, "DATABASE_URL", url)
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    from app.services.audit import register_audit_hooks

    engine = db_module.create_engine(url)
    db_module.enable_sqlite_tuning(engine)
    factory = sessionmaker(
        class_=CompanySession, autocommit=False, autoflush=False, bind=engine
    )
    register_audit_hooks(factory)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    app.dependency_overrides.clear()
    auth_service.forget_company_ids()
    try:
        yield host
    finally:
        link.stop()
        db_module.forget_company_engines()
        engine.dispose()
        auth_service.forget_company_ids()
        served_companies._manifest_cache.update(stamp=None, rows=[])


def _serve(host, files):
    names = dict(COMPANIES)
    (host.root / "companies.json").write_text(
        json.dumps(
            {
                "companies": [{"name": names[f], "file": f} for f in files],
                "last_opened": "harbor.db",
            }
        ),
        encoding="utf-8",
    )


def _signed_in(host, username, company=None):
    person = TestClient(app)
    if company:
        assert (
            person.post("/api/auth/company", json={"file": company}).status_code == 200
        )
    r = person.post(
        "/api/auth/login",
        json={"username": username, "password": host.pw[username]},
    )
    assert r.status_code == 200, r.text
    return person


def _switch(person, company_file):
    r = person.post("/api/auth/company", json={"file": company_file})
    assert r.json()["authenticated"] is True, r.text


def _sql(company_file, statement, *params):
    """A statement on the company's own engine: the app's, so its file is
    read and written the way the app reads and writes it."""
    with db_module.company_engine(company_file).begin() as conn:
        result = conn.exec_driver_sql(statement, params)
        return list(result) if result.returns_rows else []


def _rows(host, company_file):
    """The company's API tokens, read from its own books."""
    return [
        SimpleNamespace(
            id=r[0], label=r[1], active=bool(r[2]), created_by=r[3], hash=r[4]
        )
        for r in _sql(
            company_file,
            "SELECT id, label, is_active, created_by, token_hash FROM api_tokens"
            " ORDER BY id",
        )
    ]


def _set_role(host, company_file, username, role):
    _sql(company_file, "UPDATE users SET role = ? WHERE username = ?", role, username)


def _record():
    return json.loads(link.link_path().read_text())


def _drain(fake):
    while True:
        try:
            fake.lines.get_nowait()
        except queue.Empty:
            return


def _connected_with(host, fake, *others):
    """jdoe connects in Harbor Books, then adds each other company from its
    own panel. Returns jdoe's client, back in Harbor Books."""
    jdoe = _signed_in(host, "jdoe")
    r = jdoe.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    fake.next_line()
    for company_file in others:
        _switch(jdoe, company_file)
        r = jdoe.post("/api/switchboard/add")
        assert r.status_code == 200, r.text
    _switch(jdoe, "harbor.db")
    return jdoe


# -- Add ------------------------------------------------------------------------------


def test_add_from_another_company_makes_its_token_in_its_own_books(three, relay):
    jdoe = _connected_with(three, relay)
    _switch(jdoe, "smith.db")
    st = jdoe.get("/api/switchboard/status").json()
    assert st["on_link"] is False and st["can_add"] is True
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["on_link"] is True and got["can_manage"] is True
    assert got["results"] == [
        {"file": "smith.db", "name": "Smith Plumbing", "result": "added"}
    ]
    record = _record()
    assert record["format"] == 2 and record["rev"] == 2
    assert [e["file"] for e in record["companies"]] == ["harbor.db", "smith.db"]
    harbor, smith = record["companies"]
    assert record["token"] == harbor["token"]  # the top mirrors the first
    assert re.fullmatch(r"smith~[a-z2-7]{6}", smith["books"])
    assert HANDLE.fullmatch(harbor["books"]) and smith["added_by"] == "jdoe"
    made = _rows(three, "smith.db")
    assert [(t.label, t.active, t.created_by) for t in made] == [
        ("AI assistant (relay)", True, "jdoe")
    ]
    assert made[0].hash == smith["token_sha256"] and made[0].id == smith["token_id"]
    assert len(_rows(three, "harbor.db")) == 1  # nothing was made in Harbor
    # the mint's session was Smith's own books, and so is every later one
    with link._in_company("smith.db") as db:
        assert db.get_bind().url.database.endswith("smith.db")
    # the manager sees both, by name and handle, and how many others
    st = jdoe.get("/api/switchboard/status").json()
    assert [(c["file"], c["books"], c["state"]) for c in st["companies"]] == [
        ("harbor.db", harbor["books"], "ready"),
        ("smith.db", smith["books"], "ready"),
    ]
    assert st["count_others"] == 1 and st["can_code"] and st["can_disconnect"]
    # adding it twice is refused
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 409 and r.json()["detail"] == link.ALREADY_ON


def test_only_an_administrator_adds_and_no_token_reaches_the_presses(three, relay):
    _connected_with(three, relay)
    bkpr = _signed_in(three, "bkpr")
    assert bkpr.post("/api/switchboard/add").status_code == 403
    r = bkpr.post("/api/switchboard/take-off", json={"file": "harbor.db"})
    assert r.status_code == 403
    record = _record()
    bot = TestClient(app, client=_THIS_COMPUTER)
    bot.headers["Authorization"] = f"Bearer {record['token']}"
    bot.headers.update(_link_itself())  # even as the connection itself
    assert bot.get("/api/switchboard/status").status_code == 403
    assert bot.post("/api/switchboard/add").status_code == 403
    r = bot.post("/api/switchboard/take-off", json={"file": "harbor.db"})
    assert r.status_code == 403
    assert len(_record()["companies"]) == 1


def test_a_client_administrator_can_neither_add_nor_learn_the_others(three, relay):
    _connected_with(three, relay, "smith.db")
    cli = _signed_in(three, "cli", company="lake.db")
    st = cli.get("/api/switchboard/status")
    got = st.json()
    assert got["on_link"] is False and got["can_add"] is False
    assert got["sentence"] == (
        "An AI assistant connection already exists on this server. Only someone "
        "who is an administrator of every company on it can add Lake Dental."
    )
    assert "companies" not in got and "count_others" not in got
    for name in ("Harbor", "Smith", "harbor.db", "smith.db", "~"):
        assert name not in st.text
    for what in ("add", "code", "disconnect"):
        r = cli.post(f"/api/switchboard/{what}")
        assert r.status_code == 403, (what, r.text)
        assert "Harbor" not in r.text and "Smith" not in r.text
    assert relay.codes == [] and len(_record()["companies"]) == 2


def test_a_code_needs_an_administrator_of_every_company(three, relay):
    jdoe = _connected_with(three, relay, "smith.db")
    assert jdoe.post("/api/switchboard/code").status_code == 200
    _set_role(three, "smith.db", "jdoe", "bookkeeper")
    r = jdoe.post("/api/switchboard/code")
    assert r.status_code == 403 and r.json()["detail"] == link.NOT_A_MANAGER
    st = jdoe.get("/api/switchboard/status").json()
    assert "companies" not in st and st["can_code"] is False
    # and the same for Add: Lake Dental cannot be added by him now
    _switch(jdoe, "lake.db")
    assert jdoe.post("/api/switchboard/add").status_code == 403
    _set_role(three, "smith.db", "jdoe", "admin")
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 200, r.text
    assert len(relay.codes) == 1


# -- Take off --------------------------------------------------------------------------


def test_take_off_revokes_by_hash_in_that_companys_books(three, relay):
    """Smith's token and Harbor's carry the same id and label, each in its
    own books: taking Smith off turns off Smith's and leaves Harbor's."""
    jdoe = _connected_with(three, relay, "smith.db")
    harbor, smith = _record()["companies"]
    assert (harbor["token_id"], harbor["token_label"]) == (
        smith["token_id"],
        smith["token_label"],
    )
    r = jdoe.post("/api/switchboard/take-off", json={"file": "smith.db"})
    assert r.status_code == 200, r.text
    assert r.json()["results"] == [
        {"file": "smith.db", "name": "Smith Plumbing", "result": "ended"}
    ]
    assert [t.active for t in _rows(three, "smith.db")] == [False]
    assert [t.active for t in _rows(three, "harbor.db")] == [True]
    assert [e["file"] for e in _record()["companies"]] == ["harbor.db"]
    r = jdoe.post("/api/switchboard/take-off", json={"file": "smith.db"})
    assert r.status_code == 404 and r.json()["detail"] == link.NOT_ON


def test_the_line_opens_with_any_company_open_and_not_with_none(three, relay):
    jdoe = _connected_with(three, relay, "smith.db")
    # Harbor, the company it was made in, comes off: Smith carries on
    r = jdoe.post("/api/switchboard/take-off", json={"file": "harbor.db"})
    assert r.status_code == 200, r.text
    assert [t.active for t in _rows(three, "harbor.db")] == [False]
    record = _record()
    assert [e["file"] for e in record["companies"]] == ["smith.db"]
    assert record["company_file"] == "smith.db"  # the mirror follows
    link.stop()
    _drain(relay)
    link.start_in_background()
    assert relay.next_line().hello["mcp"] is True
    # with no company on it served here, no line
    link.stop()
    _drain(relay)
    _serve(three, ["harbor.db", "lake.db"])
    link.start_in_background()
    time.sleep(0.3)
    with pytest.raises(queue.Empty):
        relay.next_line(timeout=0.5)


# -- Disconnect, and the leftovers -------------------------------------------------------


def test_disconnect_skips_a_company_not_served_and_ends_it_when_it_is_back(
    three, relay
):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    lake = _record()["companies"][2]
    _serve(three, ["harbor.db", "smith.db"])
    st = jdoe.get("/api/switchboard/status").json()
    assert [c["state"] for c in st["companies"]] == ["ready", "ready", "not_served"]
    assert st["can_disconnect"] is True and st["can_code"] is False
    r = jdoe.post("/api/switchboard/disconnect")
    assert r.status_code == 200, r.text
    got = r.json()
    assert [(x["file"], x["result"]) for x in got["results"]] == [
        ("harbor.db", "ended"),
        ("smith.db", "ended"),
        ("lake.db", "later"),
    ]
    assert got["sentence"].endswith(
        " Lake Dental is not open now; its token will be turned off the next time "
        "it is opened."
    )
    assert not link.link_path().exists()
    assert [t.active for t in _rows(three, "smith.db")] == [False]
    text = link.leftover_path().read_text()
    assert "sbp_" not in text and lake["token"] not in text
    kept = json.loads(text)
    assert [(i["token_id"], i["token_sha256"]) for i in kept["lake.db"]] == [
        (lake["token_id"], lake["token_sha256"])
    ]
    if os.name != "nt":
        assert os.stat(link.leftover_path()).st_mode & 0o777 == 0o600
    # Lake Dental is served again: its token is still on until the next
    # sweep turns it off
    _serve(three, [f for f, _ in COMPANIES])
    assert [t.active for t in _rows(three, "lake.db")] == [True]
    assert link.sweep_leftovers() == 0
    assert [t.active for t in _rows(three, "lake.db")] == [False]
    assert not link.leftover_path().exists()


def test_the_sweep_needs_the_id_and_the_hash_and_never_runs_at_an_engines_open(
    three,
):
    from app.routes.api_tokens import issue_token

    with link._in_company("smith.db") as db:
        first, _ = issue_token(
            db, label="AI assistant (relay)", role="bookkeeper", created_by="jdoe"
        )
        second, _ = issue_token(
            db, label="AI assistant (relay) 2", role="bookkeeper", created_by="jdoe"
        )
        first_id, first_hash, second_id = first.id, first.token_hash, second.id
    other_hash = "0" * 64
    link._save_leftovers(
        {
            "smith.db": [
                {"token_id": first_id, "token_sha256": first_hash, "why": "not_open"},
                # an id alone: by now it may be another token's
                {"token_id": second_id, "token_sha256": other_hash, "why": "not_open"},
            ]
        }
    )
    db_module.forget_company_engines()
    # the first request to Smith opens its engine, and does not wait on (or
    # run) the sweep
    jdoe = _signed_in(three, "jdoe")
    answered = {}

    def first_request():
        _switch(jdoe, "smith.db")
        answered["r"] = jdoe.get("/api/settings")

    worker = threading.Thread(target=first_request)
    worker.start()
    worker.join(15)
    assert not worker.is_alive() and answered["r"].status_code == 200
    assert [t.active for t in _rows(three, "smith.db")] == [True, True]
    assert link.leftover_path().exists()
    assert link.sweep_leftovers() == 0
    assert [(t.id, t.active) for t in _rows(three, "smith.db")] == [
        (first_id, False),
        (second_id, True),
    ]
    # audited in Smith's own log as the system ending the connection
    who = {r[0] for r in _sql("smith.db", "SELECT username FROM audit_log")}
    assert link.SWEEP_ACTOR in who


# -- rule 4 and rule 5: never half-made, one edit at a time --------------------------------


def test_an_add_that_cannot_be_kept_takes_its_token_back(three, relay, monkeypatch):
    jdoe = _connected_with(three, relay)
    before = link.link_path().read_bytes()
    _switch(jdoe, "smith.db")

    def cannot(*_a, **_k):
        raise PermissionError(13, "Permission denied", "/somewhere/claude-relay.json")

    monkeypatch.setattr(link, "_save", cannot)
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 503 and r.json()["detail"] == link.NOT_ADDED
    assert "/somewhere" not in r.text
    assert link.link_path().read_bytes() == before
    assert [t for t in _rows(three, "smith.db") if t.active] == []


_EDIT = r"""
import json, sys, time
from pathlib import Path
from app.services import switchboard_link as link
path = Path(sys.argv[1])
entry = json.loads(sys.argv[2])
link.link_path = lambda: path
with link._record_lock(wait=30):
    record = link._load()
    print("in", flush=True)
    time.sleep(0.5)
    link._save(link._format2(record, link._entries(record) + [entry]))
"""

# Another process holding one of the connection's locks, taken the way that
# platform's kernel takes it (flock on POSIX, a byte-range lock on Windows),
# not through the app's own helper, so the app's lock is checked against the
# operating system's.
_HOLD = r"""
import os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
if os.name == "nt":
    import msvcrt
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(120)
"""


def _child(script, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.Popen(
        [sys.executable, "-c", script, *map(str, args)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )


def _entry(company_file, name):
    token = _secret("sbp_")
    return {
        "books": link._new_handle(company_file),
        "file": company_file,
        "name": name,
        "token": token,
        "token_sha256": link._hash(token),
        "token_id": 1,
        "token_label": "AI assistant (relay)",
        "added_at": "2026-11-21T00:00:00+00:00",
        "added_by": "jdoe",
        "off": "",
    }


def _hand_made_record(*entries):
    base = {
        "on": True,
        "relay": "http://127.0.0.1:9",
        "device": "dev_" + _secret("")[:12],
        "account": "acct_" + _secret("")[:12],
        "slug": "abcdefghjk",
        "credential": _secret("sbd_"),
        "linked_at": "2026-11-21T00:00:00+00:00",
    }
    return link._format2(base, list(entries))


def test_two_processes_add_at_once_and_both_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    link._save(_hand_made_record(_entry("harbor.db", "Harbor Books")))
    adds = [_entry("smith.db", "Smith Plumbing"), _entry("lake.db", "Lake Dental")]
    children = [_child(_EDIT, link.link_path(), json.dumps(e)) for e in adds]
    for child in children:
        child.wait(60)
        assert child.returncode == 0
    record = _record()
    assert link._whole2(record)
    assert sorted(e["file"] for e in record["companies"]) == [
        "harbor.db",
        "lake.db",
        "smith.db",
    ]
    assert record["rev"] == 3


def test_a_killed_lock_holder_lets_the_lock_go(tmp_path, monkeypatch):
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    holder = _child(_HOLD, tmp_path / link.LOCK_FILE)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(link.SwitchboardError) as busy:
            with link._record_lock(wait=0.3):
                pass
        assert (
            busy.value.sentence
            == link.EDIT_BUSY
            == (
                "Another change to the AI assistant connection is under way, so "
                "nothing was done. Try again in a moment."
            )
        )
    finally:
        holder.kill()
        holder.wait(10)
    with link._record_lock(wait=5):
        pass  # the operating system let it go when the holder died


def test_a_second_process_waits_for_the_line_and_takes_it_after(
    relay, client, monkeypatch
):
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    link.stop()
    _drain(relay)
    monkeypatch.setattr(link, "LINE_RETRY", 0.1)
    other = _child(_HOLD, link.link_path().with_name(link.LINE_FILE))
    try:
        assert other.stdout.readline().strip() == "held"
        link.start_in_background()
        with pytest.raises(queue.Empty):
            relay.next_line(timeout=1.0)
        st = client.get("/api/switchboard/status").json()
        assert st["line_elsewhere"] is True and st["state"] == "not_connected"
    finally:
        other.kill()
        other.wait(10)
    assert relay.next_line(timeout=10).hello["mcp"] is True
    assert _wait(
        lambda: client.get("/api/switchboard/status").json()["state"] == "connected"
    )


# -- the requests: one company's token, that company's header ------------------------------


def test_each_token_travels_only_with_its_own_company(three, relay, monkeypatch):
    sent = []

    def logged(entry):
        send = _in_process_transport(entry)

        def wrapped(method, path, params, body, headers):
            got = send(method, path, params, body, headers)
            sent.append((entry["file"], entry["token"], dict(headers or {})))
            return got

        return wrapped

    monkeypatch.setattr(link, "_books_transport", logged)
    _connected_with(three, relay, "smith.db")
    harbor, smith = _record()["companies"]
    registry = build_registry(link.LinkBooks())
    for entry in (harbor, smith):
        result = registry.call("context", {"books": entry["books"]})
        assert result["isError"] is False, result
        assert result["content"][0]["text"] == (
            f"Books: {entry['name']} ({entry['books']})."
        )
        body = json.loads(result["content"][1]["text"])
        assert body["books"] == entry["books"] and body["company"] == entry["name"]
        assert body["company_file"] == entry["file"]
    tokens = {harbor["file"]: harbor["token"], smith["file"]: smith["token"]}
    assert sent
    for company_file, token, headers in sent:
        assert tokens[company_file] == token
        named = [v for k, v in headers.items() if k.lower() == "x-slowbooks-company"]
        assert named == [company_file]
    # with no books named, context lists them and nothing is sent
    count = len(sent)
    result = registry.call("context", {})
    listed = json.loads(result["content"][0]["text"])["books"]
    assert [b["books"] for b in listed] == [harbor["books"], smith["books"]]
    result = registry.call("list_records", {"kind": "customers"})
    assert result["isError"] is True and "Nothing was done." in str(result)
    # a company not served now: refused before anything is sent
    _serve(three, ["harbor.db", "lake.db"])
    result = registry.call("context", {"books": smith["books"]})
    assert result["isError"] is True
    assert result["content"][-1]["text"] == link.BOOKS_NOT_SERVED.format(
        name="Smith Plumbing"
    )
    assert len(sent) == count


def test_a_refused_token_loses_its_secret_and_a_restore_does_not_bring_it_back(
    three, relay
):
    """Smith fires the person who added it (he is demoted there): the next
    call is refused at once, the secret is deleted from the record, and
    making him an administrator again does not reopen the books through it:
    it is added again from Smith's panel."""
    _connected_with(three, relay, "smith.db")
    smith = _record()["companies"][1]
    registry = build_registry(link.LinkBooks())
    assert registry.call("context", {"books": smith["books"]})["isError"] is False
    _set_role(three, "smith.db", "jdoe", "bookkeeper")
    result = registry.call("context", {"books": smith["books"]})
    assert result["isError"] is True
    assert result["content"][-1]["text"] == (
        "Smith Plumbing no longer accepts this connection: its token was turned "
        "off there, or the person who added it is no longer an administrator "
        "there. To use it again, someone who is an administrator of Smith "
        "Plumbing, and of every other company on this connection, adds it again "
        "in Smith Plumbing's Settings, under Connections. Nothing was done."
    )
    kept = _record()["companies"][1]
    assert kept["off"] == "refused" and kept["token"] == ""
    assert kept["token_sha256"] == smith["token_sha256"]
    _set_role(three, "smith.db", "jdoe", "admin")
    result = registry.call("context", {"books": smith["books"]})
    assert result["isError"] is True and "no longer accepts" in str(result)
    # Harbor is untouched by Smith's refusal
    harbor = _record()["companies"][0]["books"]
    assert registry.call("context", {"books": harbor})["isError"] is False


# -- the desktop -------------------------------------------------------------------------


def test_the_desktop_never_opens_a_company_that_is_not_open(client):
    before = set(db_module._company_engines)
    with pytest.raises(link._NotOpen):
        with link._in_company("elsewhere.db"):
            pass
    assert set(db_module._company_engines) == before


def test_the_desktop_says_which_window_has_the_books(relay, client, monkeypatch):
    # a desktop started for a company file (the suite's own database is not one)
    monkeypatch.setattr(db_module, "started_company_file", lambda: "harbor.db")
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    record = _record()
    entries = link._entries(record) + [_entry("smith.db", "Smith Plumbing")]
    link._save(link._format2(record, entries))
    books = link.LinkBooks()
    handle = entries[1]["books"]
    with pytest.raises(link.Refused) as no:
        books.pick(handle, "context")
    assert no.value.detail == (
        "Smith Plumbing is not open in SlowBooks Pro on this computer. Ask the "
        "person to open it there, then try again. Nothing was done."
    )
    window = _child(_HOLD, link.link_path().with_name(link.OPEN_PREFIX + "smith.db"))
    try:
        assert window.stdout.readline().strip() == "held"
        with pytest.raises(link.Refused) as other:
            books.pick(handle, "context")
        here = entries[0]["name"]
        assert other.value.detail == (
            "Smith Plumbing is open in another SlowBooks Pro window; this "
            f"connection runs in the window that has {here} open. Ask the person "
            "to open Smith Plumbing in that window, then try again. Nothing was "
            "done."
        )
        st = client.get("/api/switchboard/status").json()
        assert [c["state"] for c in st["companies"]] == ["ready", "other_window"]
    finally:
        window.kill()
        window.wait(10)


# -- the kept record's format --------------------------------------------------------------


_BETA1_FIELDS = (
    "relay",
    "device",
    "account",
    "slug",
    "credential",
    "token",
    "token_id",
    "token_label",
    "company_file",
    "company_name",
    "linked_at",
)


def _beta1_whole(record) -> bool:
    """beta.1's own check of the kept file, as it shipped."""
    return (
        isinstance(record, dict)
        and all(
            isinstance(record.get(f), str) for f in _BETA1_FIELDS if f != "token_id"
        )
        and isinstance(record.get("token_id"), int)
        and not isinstance(record.get("token_id"), bool)
        and link._usable_relay(record["relay"])
    )


def _beta1_shape(record):
    return {k: v for k, v in record.items() if k in ("v", "on", *_BETA1_FIELDS)}


def test_a_connection_made_before_this_change_keeps_working(relay, client):
    """A beta.1 record (one company, no list) is read as one company with a
    handle that stays the same, the assistant's calls without books go
    there, the line opens from it, and the file is not rewritten until a
    write."""
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    old = _beta1_shape(_record())
    assert "companies" not in old and "format" not in old
    link._save(old)
    before = link.link_path().read_bytes()
    first = link._entries(link._load())
    again = link._entries(link._load())
    assert len(first) == 1 and first[0]["books"] == again[0]["books"]
    assert HANDLE.fullmatch(first[0]["books"]) and first[0]["token"] == old["token"]
    st = client.get("/api/switchboard/status").json()
    assert st["on_link"] is True and st["token_label"] == "AI assistant (relay)"
    registry = build_registry(link.LinkBooks())
    result = registry.call("context", {})
    assert result["isError"] is False, result
    assert len(result["content"]) == 1  # one company: no books stamp
    assert json.loads(result["content"][0]["text"])["books"] == first[0]["books"]
    # the line opens from it as it is
    link.stop()
    _drain(relay)
    link.start_in_background()
    relay.next_line()
    # the start's thread sweeps the leftovers after the line opens, and a
    # holder of the record lock makes the drops owed as it takes it: let it
    # finish, so the drop below is made by drop_secret itself
    for thread in threading.enumerate():
        if thread.name == "claude-relay-start":
            thread.join(10)
            assert not thread.is_alive()
    assert link.link_path().read_bytes() == before
    # the first write keeps the handle and makes it format 2
    assert link.drop_secret(first[0]["books"]) is True
    record = _record()
    assert record["format"] == 2
    assert record["companies"][0]["books"] == first[0]["books"]
    assert link._whole2(record)


def _hold_the_record_lock(let_go: threading.Event):
    """Another holder of the record lock (the start's sweep, a change, or
    another window) in a thread; returns it once it holds the lock."""
    held = threading.Event()

    def hold():
        with link._record_lock():
            held.set()
            let_go.wait(30)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(10)
    return holder


def test_a_refused_tokens_secret_is_dropped_while_the_record_is_held_a_moment(
    relay, client, monkeypatch
):
    """The line's start sweeps the leftovers under the record lock just after
    the line opens: a 401 heard then still deletes the token's secret."""
    monkeypatch.setattr(link, "_owed_drops", set(), raising=False)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    books = link._entries(link._load())[0]["books"]
    let_go = threading.Event()
    holder = _hold_the_record_lock(let_go)
    threading.Timer(0.5, let_go.set).start()
    assert link.drop_secret(books) is True
    holder.join(10)
    entry = _record()["companies"][0]
    assert entry["books"] == books and entry["token"] == ""
    assert entry["off"] == link.OFF_REFUSED
    assert not link._owed_drops


def test_a_drop_the_lock_is_not_free_for_in_time_is_made_by_its_next_holder(
    relay, client, monkeypatch
):
    """Held past the press deadline: the books answer as lost at once and
    the next change to the connection in this window deletes the secret."""
    monkeypatch.setattr(link, "_owed_drops", set(), raising=False)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    books = link._entries(link._load())[0]["books"]
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 0.2)
    let_go = threading.Event()
    holder = _hold_the_record_lock(let_go)
    try:
        assert link.drop_secret(books) is False
        assert _record()["companies"][0]["token"] != ""
        with pytest.raises(Refused) as refused:
            link.LinkBooks().pick(books, "context")
        assert refused.value.status == 403
        assert "no longer accepts this connection" in refused.value.detail
    finally:
        let_go.set()
        holder.join(10)
    with link._record_lock():
        pass
    entry = _record()["companies"][0]
    assert entry["books"] == books and entry["token"] == ""
    assert entry["off"] == link.OFF_REFUSED
    assert not link._owed_drops


def _wait_until(check, seconds: float = 15.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return check()


def test_an_owed_drop_is_made_soon_after_the_lock_frees_with_nothing_else_happening(
    relay, client, monkeypatch
):
    """A drop owed in a quiet window: no change, no further call and no
    further refusal; its own retry deletes the secret once the lock frees."""
    monkeypatch.setattr(link, "_owed_drops", set(), raising=False)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    books = link._entries(link._load())[0]["books"]
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 0.2)
    monkeypatch.setattr(link, "OWED_RETRY", 0.05)
    # the retry waits for the lock longer than the holder below keeps it
    monkeypatch.setattr(link, "OWED_RETRY_WAIT", 30.0)
    tried = threading.Event()
    real_lock = link._record_lock

    def watched(wait=None, pay=True):
        if wait == 30.0:
            tried.set()
        return real_lock(wait=wait, pay=pay)

    monkeypatch.setattr(link, "_record_lock", watched)
    let_go = threading.Event()
    holder = _hold_the_record_lock(let_go)
    try:
        assert link.drop_secret(books) is False
        assert link._drop_owed(books)
        # the retry is waiting for the lock while it is still held
        assert tried.wait(10)
        assert _record()["companies"][0]["token"] != ""
    finally:
        let_go.set()
        holder.join(10)
    assert _wait_until(lambda: _record()["companies"][0]["token"] == "")
    entry = _record()["companies"][0]
    assert entry["books"] == books and entry["off"] == link.OFF_REFUSED
    assert not link._owed_drops
    assert _wait_until(lambda: link._owed_retry["thread"] is None)


def test_a_call_refused_as_owed_makes_the_drop_itself(relay, client, monkeypatch):
    """The call that is refused because the books are owed tries for the
    lock a moment and deletes the secret, without waiting for the retry."""
    monkeypatch.setattr(link, "_owed_drops", set(), raising=False)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    books = link._entries(link._load())[0]["books"]
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 0.2)
    monkeypatch.setattr(link, "OWED_RETRY", 600.0)  # the retry never runs here
    let_go = threading.Event()
    holder = _hold_the_record_lock(let_go)
    try:
        assert link.drop_secret(books) is False
    finally:
        let_go.set()
        holder.join(10)
    assert _record()["companies"][0]["token"] != ""
    with pytest.raises(Refused) as refused:
        link.LinkBooks().pick(books, "context")
    assert refused.value.status == 403
    assert "no longer accepts this connection" in refused.value.detail
    entry = _record()["companies"][0]
    assert entry["token"] == "" and entry["off"] == link.OFF_REFUSED
    assert not link._owed_drops


def test_a_call_made_while_the_drop_waits_for_the_lock_is_answered_as_lost(
    relay, client, monkeypatch
):
    """From the 401 on, the books answer as lost, also while drop_secret is
    still waiting for the record lock; the mark is cleared only once the
    secret is deleted."""
    monkeypatch.setattr(link, "_owed_drops", set(), raising=False)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    books = link._entries(link._load())[0]["books"]
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 30.0)
    monkeypatch.setattr(link, "OWED_RETRY", 600.0)
    monkeypatch.setattr(link, "OWED_PAY_WAIT", 0.05)
    let_go = threading.Event()
    holder = _hold_the_record_lock(let_go)
    result = {}
    dropping = threading.Thread(
        target=lambda: result.setdefault("done", link.drop_secret(books)),
        daemon=True,
    )
    try:
        dropping.start()
        assert _wait_until(lambda: link._drop_owed(books))
        assert dropping.is_alive()  # still waiting for the lock
        with pytest.raises(Refused) as refused:
            link.LinkBooks().pick(books, "context")
        assert refused.value.status == 403
        assert "no longer accepts this connection" in refused.value.detail
        assert dropping.is_alive() and link._drop_owed(books)
        assert _record()["companies"][0]["token"] != ""
    finally:
        let_go.set()
        holder.join(10)
    dropping.join(10)
    assert result["done"] is True
    entry = _record()["companies"][0]
    assert entry["token"] == "" and entry["off"] == link.OFF_REFUSED
    assert not link._owed_drops


def test_a_drop_still_owed_when_the_app_ends_is_made_by_the_next_refusal(
    relay, client, monkeypatch
):
    """The marks live in this process only: after a restart the books' first
    call reaches the company again, and its 401 deletes the secret."""
    monkeypatch.setattr(link, "_owed_drops", set(), raising=False)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    books = link._entries(link._load())[0]["books"]
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 0.2)
    monkeypatch.setattr(link, "OWED_RETRY", 600.0)
    let_go = threading.Event()
    holder = _hold_the_record_lock(let_go)
    try:
        assert link.drop_secret(books) is False
        # the app ends here: its retry stops and its marks are gone
        link.stop_owed_retry(timeout=5)
        link._owed_drops.clear()
    finally:
        let_go.set()
        holder.join(10)
    entry = _record()["companies"][0]
    assert entry["token"] != "" and entry["off"] == ""
    _client, public = link.LinkBooks().pick(books, "context")
    assert public["books"] == books
    assert link.drop_secret(books) is True
    entry = _record()["companies"][0]
    assert entry["token"] == "" and entry["off"] == link.OFF_REFUSED


def test_a_record_of_several_companies_still_reads_as_one_to_beta1(three, relay):
    _connected_with(three, relay, "smith.db", "lake.db")
    record = _record()
    assert len(record["companies"]) == 3
    assert _beta1_whole(record)
    assert record["company_file"] == "harbor.db" and record["token"] == (
        record["companies"][0]["token"]
    )


def _twice(field):
    def change(record):
        extra = _entry("smith.db", "Smith Plumbing")
        extra[field] = record["companies"][0][field]
        if field == "token":
            extra["token_sha256"] = record["companies"][0]["token_sha256"]
        record["companies"].append(extra)

    return change


def _set(*path_value):
    *path, value = path_value

    def change(record):
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


@pytest.mark.parametrize(
    "change",
    [
        _twice("file"),
        _twice("books"),
        _twice("token"),
        _set("company_name", "Somebody Else"),
        _set("companies", 0, "books", "Harbor Books"),
        _set("companies", 0, "token_sha256", "0" * 64),
        _set("companies", 0, "off", "maybe"),
        _set("companies", 0, "token_id", True),
        _set("companies", []),
        _set("format", 3),
    ],
    ids=[
        "file-twice",
        "handle-twice",
        "token-twice",
        "mirror",
        "handle-shape",
        "hash",
        "off",
        "token-id-bool",
        "no-companies",
        "format",
    ],
)
def test_a_record_that_does_not_hold_together_is_damaged(relay, client, change):
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    record = _record()
    change(record)
    link._save(record)
    st = client.get("/api/switchboard/status").json()
    assert st["state"] == "damaged" and st["sentence"] == link.DAMAGED


def test_postgresql_serves_one_company_so_add_is_refused(relay, client, monkeypatch):
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    record = _record()
    moved = dict(record["companies"][0], file="another.db", name="Another")
    link._save(
        dict(
            record, company_file="another.db", company_name="Another", companies=[moved]
        )
    )
    monkeypatch.setattr(db_module, "DATABASE_URL", "postgresql://localhost/books")
    with pytest.raises(link.SwitchboardError) as refused:
        link.add_here(None, "operator", {"role": "admin"})
    assert refused.value.status == 409
    assert refused.value.sentence == (
        "On PostgreSQL this server serves one company, so the connection reaches "
        "only this one. Nothing was done."
    )


# -- never a secret --------------------------------------------------------------------------


def test_no_secret_in_an_answer_a_log_or_the_leftovers(three, relay, caplog):
    caplog.set_level(logging.DEBUG)
    jdoe = _signed_in(three, "jdoe")
    answers = [jdoe.post("/api/switchboard/connect")]
    relay.next_line()
    for company_file in ("smith.db", "lake.db"):
        _switch(jdoe, company_file)
        answers.append(jdoe.post("/api/switchboard/add"))
    _switch(jdoe, "harbor.db")
    record = _record()
    answers.append(jdoe.get("/api/switchboard/status"))
    answers.append(jdoe.post("/api/switchboard/take-off", json={"file": "smith.db"}))
    _serve(three, ["harbor.db", "smith.db"])
    answers.append(jdoe.post("/api/switchboard/disconnect"))
    assert all(r.status_code == 200 for r in answers), [r.text for r in answers]
    secrets_ = [record["credential"]] + [e["token"] for e in record["companies"]]
    leftovers = link.leftover_path().read_text()
    for secret in secrets_:
        assert secret not in caplog.text
        assert secret not in leftovers
        for r in answers:
            assert secret not in r.text
    for r in answers:
        assert "sbp_" not in r.text and "sbd_" not in r.text


def test_stopping_the_retry_never_waits_on_a_thread_that_has_not_begun():
    """The stop at shutdown runs beside whatever arms the retry. A thread
    that was made and not yet started is signaled, never joined."""
    stop_event = threading.Event()
    unstarted = threading.Thread(target=lambda: None, daemon=True)
    with link._owed_lock:
        link._owed_retry["thread"], link._owed_retry["stop"] = unstarted, stop_event
    link.stop_owed_retry(timeout=0.1)
    assert stop_event.is_set()
    assert link._owed_retry["thread"] is None and link._owed_retry["stop"] is None
