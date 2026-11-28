"""Connect to AI assistant from Settings (#1087; "Connect Claude" before 2.32.0).

SlowBooks Pro links itself to the SlowBooks relay, makes a bookkeeper API
token for the connection and holds the line to the relay itself, with
SlowBooks Switchboard's line client (app/switchboard). Here against a fake
relay on 127.0.0.1: the HTTP routes the client calls (/v1/link, /v1/code,
/v1/forget, /v1/unlink) and the WebSocket line (/v1/line), written on the standard
library apart from the client's own ws.py, so a mistake in one is not
hidden by the same mistake in the other.

Every credential, token, code and password here is made at run time with
`secrets`; none is written in the file.
"""

import base64
import hashlib
import json
import logging
import os
import queue
import re
import secrets
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import _role_allows, app
from app.models.api_tokens import ApiToken
from app.services import switchboard_link as link
from app.switchboard import line as line_client
from tests.switchboard_clock import BACKSTOP, hold_the_clock, settle_the_start

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_RE = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){2}")


# -- the fake relay -------------------------------------------------------------


class _Line:
    """The relay's end of one WebSocket line."""

    def __init__(self, sock):
        self.sock = sock
        self.frames = queue.Queue()
        self.closed = threading.Event()
        self.lock = threading.Lock()

    def _exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def read_loop(self):
        try:
            while True:
                b1, b2 = self._exact(2)
                opcode, length = b1 & 0x0F, b2 & 0x7F
                if length == 126:
                    (length,) = struct.unpack("!H", self._exact(2))
                elif length == 127:
                    (length,) = struct.unpack("!Q", self._exact(8))
                mask = self._exact(4) if b2 & 0x80 else b"\0\0\0\0"
                raw = self._exact(length)
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(raw))
                if opcode == 8:
                    break
                if opcode == 9:
                    self._send(10, payload)
                    continue
                if opcode == 1:
                    frame = json.loads(payload.decode("utf-8"))
                    if frame.get("t") == "ping":
                        self.send({"t": "pong"})
                        continue
                    self.frames.put(frame)
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            self.closed.set()

    def _send(self, opcode, payload: bytes):
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        with self.lock:
            self.sock.sendall(head + payload)

    def send(self, obj):
        self._send(1, json.dumps(obj).encode("utf-8"))

    def expect(self, kind, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                frame = self.frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if frame.get("t") == kind:
                return frame
        raise AssertionError(f"no {kind} frame")

    def mcp(self, body, headers=None):
        rid = "r" + secrets.token_hex(4)
        self.send(
            {
                "t": "mcp",
                "id": rid,
                "era": "2025-11-25",
                "headers": {"content-type": "application/json", **(headers or {})},
                "body": body,
            }
        )
        while True:
            frame = self.expect("mcp.result", timeout=30)
            if frame.get("id") == rid:
                return frame


class FakeRelay:
    def __init__(self):
        self.credential = "sbd_" + secrets.token_urlsafe(32)
        self.device = "dev_" + secrets.token_hex(6)
        self.account = "acct_" + secrets.token_hex(6)
        self.slug = "".join(secrets.choice("abcdefghjkmnpqrstvwxyz") for _ in range(10))
        self.calls = []  # (method, path, bearer, body)
        self.codes = []
        self.lines = queue.Queue()
        self.link_status = 201
        self.link_error = "The relay is closed today."
        self.fail = {}  # path -> a status to answer instead (a 5xx, say)
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _bearer(self):
                auth = self.headers.get("Authorization") or ""
                return auth[7:] if auth.startswith("Bearer ") else None

            def _answer(self, status, obj):
                data = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                relay.calls.append(("POST", self.path, self._bearer(), body))
                if self.path in relay.fail:
                    return self._answer(
                        relay.fail[self.path], {"error": "Internal error 7f3a."}
                    )
                if self.path == "/v1/link":
                    if relay.link_status != 201:
                        return self._answer(
                            relay.link_status, {"error": relay.link_error}
                        )
                    return self._answer(
                        201,
                        {
                            "account": relay.account,
                            "device": relay.device,
                            "credential": relay.credential,
                            "slug": relay.slug,
                        },
                    )
                if self._bearer() != relay.credential:
                    return self._answer(401, {"error": "Not linked."})
                if self.path == "/v1/code":
                    code = "-".join(
                        "".join(secrets.choice(CROCKFORD) for _ in range(4))
                        for _ in range(3)
                    )
                    relay.codes.append(code)
                    until = datetime.now(timezone.utc) + timedelta(minutes=10)
                    return self._answer(
                        201,
                        {"code": code, "for": "sign-in", "expires": until.isoformat()},
                    )
                if self.path == "/v1/unlink":
                    return self._answer(200, {"unlinked": True})
                if self.path == "/v1/forget":
                    return self._answer(
                        200, {"deleted": True, "devices_left": 0, "slug": relay.slug}
                    )
                return self._answer(404, {"error": "No such route."})

            def do_GET(self):
                relay.calls.append(("GET", self.path, self._bearer(), None))
                if self.path != "/v1/line":
                    return self._answer(404, {"error": "No such route."})
                if self._bearer() != relay.credential:
                    return self._answer(401, {"error": "Not linked."})
                key = self.headers.get("Sec-WebSocket-Key", "").encode("ascii")
                accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
                self.wfile.write(
                    (
                        "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
                    ).encode("ascii")
                )
                self.wfile.flush()
                line = _Line(self.connection)
                hello = None
                reader = threading.Thread(target=line.read_loop, daemon=True)
                reader.start()
                hello = line.expect("hello")
                line.hello = hello
                line.send(
                    {
                        "t": "welcome",
                        "account": relay.account,
                        "slug": relay.slug,
                        "subscription": {"status": "active", "until": None},
                    }
                )
                relay.lines.put(line)
                line.closed.wait()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def next_line(self, timeout=10) -> _Line:
        return self.lines.get(timeout=timeout)

    def paths(self):
        return [(m, p) for m, p, _, _ in self.calls]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(cond, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


# -- fixtures -------------------------------------------------------------------


@pytest.fixture
def relay(monkeypatch, tmp_path):
    fake = FakeRelay()
    monkeypatch.setenv(link.RELAY_ENV, fake.url)
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    monkeypatch.setattr(
        link,
        "TIMINGS",
        line_client.Timings(backoff_first=0.05, backoff_max=0.2, welcome_wait=5),
    )
    # Each company's token's requests go to the app in-process, not to a port.
    monkeypatch.setattr(link, "_books_transport", _in_process_transport)
    # a press's deadline runs out only when a test runs it out: against
    # this relay, which answers at once, a press's outcome is its answer
    hold_the_clock(monkeypatch)
    try:
        yield fake
    finally:
        link.stop()
        link.stop_retry()
        fake.close()


# The connection's token is used from this computer's own address only, so
# the in-process stand-in for its requests calls from loopback too.
_THIS_COMPUTER = ("127.0.0.1", 50000)


def _link_itself() -> dict:
    """The header the connection's own requests carry (_books_transport):
    without it the gate turns a connection token away, whatever address
    the request seems to come from."""
    from app.services.api_token_service import LINK_ORIGIN_HEADER, link_origin_secret

    return {LINK_ORIGIN_HEADER: link_origin_secret()}


def _in_process_transport(entry):
    """One company's requests, as _books_transport makes them: its own
    token, its own company's header, and the answer's headers handed back
    so the client can check which company answered."""
    api = TestClient(app, client=_THIS_COMPUTER)
    company = entry.get("file") or ""

    def send(method, path, params, body, headers):
        hdrs = {
            k: v
            for k, v in (headers or {}).items()
            if k.lower() != "x-slowbooks-company"
        }
        hdrs["Authorization"] = f"Bearer {entry['token']}"
        hdrs.update(_link_itself())
        if company:
            hdrs["X-SlowBooks-Company"] = company
        r = api.request(method, path, params=params or None, json=body, headers=hdrs)
        try:
            payload = r.json()
        except ValueError:
            payload = r.text
        return r.status_code, payload, dict(r.headers)

    return send


def _hold_the_start(monkeypatch):
    """Hold the work link.start_in_background() hands its thread (from its
    first step) until ``go`` is set: a start that returns, or an app that
    starts, while ``entered`` is set and ``go`` is not, did not wait for it.
    ``held_out``: the hold reached its backstop, so something waited."""
    held = SimpleNamespace(
        entered=threading.Event(), go=threading.Event(), held_out=threading.Event()
    )
    first_step = link.new_start

    def held_start():
        held.entered.set()
        if not held.go.wait(BACKSTOP):
            held.held_out.set()
        first_step()

    monkeypatch.setattr(link, "new_start", held_start)
    return held


def _connected(fake, client):
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    line = fake.next_line()
    assert _wait(
        lambda: client.get("/api/switchboard/status").json()["state"] == "connected"
    )
    return r, line


def _password():
    return secrets.token_urlsafe(16)


def _sign_in(role, client):
    """A second client signed in with the role (the first becomes Server
    Edition when a user is added)."""
    name = f"{role}-{secrets.token_hex(3)}"
    pw = _password()
    r = client.post("/api/users", json={"username": name, "password": pw, "role": role})
    assert r.status_code in (200, 201), r.text
    person = TestClient(app)
    r = person.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return person


# -- connect, status, code, disconnect --------------------------------------------


def test_not_connected_until_an_administrator_connects(relay, client):
    st = client.get("/api/switchboard/status").json()
    assert st["state"] == "not_connected" and st["linked"] is False
    assert st["can_manage"] is True
    assert st["terms"] == "https://www.slowbookspro.com/terms/"
    assert st["privacy"] == "https://www.slowbookspro.com/privacy/"
    assert relay.calls == []  # nothing is sent anywhere until the button


def test_connect_links_makes_the_token_and_opens_the_line(relay, client, db_session):
    r, line = _connected(relay, client)
    # linked with no key, email or password: the account-less free sign-up
    method, path, bearer, body = relay.calls[0]
    assert (method, path, bearer) == ("POST", "/v1/link", None)
    assert set(body) == {"device_name", "switchboard"}
    # the line: the device credential as a bearer, Claude's door on only
    assert ("GET", "/v1/line", relay.credential, None) in relay.calls
    assert line.hello["mcp"] is True and line.hello["http"] is False
    st = client.get("/api/switchboard/status").json()
    assert st["address"] == relay.url + "/mcp"
    assert st["this_company"] is True and st["token_label"] == "AI assistant (relay)"
    assert st["company"]
    row = (
        db_session.query(ApiToken)
        .filter(ApiToken.label == "AI assistant (relay)")
        .one()
    )
    assert row.role == "bookkeeper" and row.is_active
    # neither the credential nor the token is in an answer
    kept = json.loads(link.link_path().read_text())
    for text in (r.text, json.dumps(st)):
        assert relay.credential not in text and kept["token"] not in text
        assert "sbp_" not in text and "sbd_" not in text


def test_claude_works_the_books_through_the_line_as_the_token(relay, client):
    _, line = _connected(relay, client)
    init = line.mcp(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
    )
    assert init["status"] == 200, init
    sid = init["headers"]["mcp-session-id"]
    called = line.mcp(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "context", "arguments": {}},
        },
        {"mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"},
    )
    assert called["status"] == 200, called
    result = called["body"]["result"]
    assert result.get("isError") is not True, result
    # the request reached the app under the token: "last used" is stamped
    tokens = client.get("/api/tokens").json()
    mine = next(t for t in tokens if t["label"] == "AI assistant (relay)")
    assert mine["last_used_at"] is not None


def test_a_sign_in_code_is_the_relays_once(relay, client):
    _connected(relay, client)
    r = client.post("/api/switchboard/code")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    got = r.json()
    assert CODE_RE.fullmatch(got["code"]) and got["code"] == relay.codes[-1]
    assert got["expires"]
    method, path, bearer, body = relay.calls[-1]
    assert (path, bearer, body) == ("/v1/code", relay.credential, {"for": "sign-in"})


def test_disconnect_closes_unlinks_revokes_and_forgets(relay, client, db_session):
    _, line = _connected(relay, client)
    r = client.post("/api/switchboard/disconnect")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["unlinked"] is True and got["revoked"] is True
    assert got["deleted"] is True and got["slug"] == relay.slug
    assert got["sentence"] == (
        "This computer is unlinked, and the relay deleted its record of these "
        f"books (relay name {relay.slug})."
    )
    assert line.closed.wait(5)
    assert ("POST", "/v1/forget") in relay.paths()
    assert relay.calls[-1][2] == relay.credential
    db_session.expire_all()
    row = (
        db_session.query(ApiToken)
        .filter(ApiToken.label == "AI assistant (relay)")
        .one()
    )
    assert row.is_active is False
    assert not link.link_path().exists()
    assert client.get("/api/switchboard/status").json()["state"] == "not_connected"
    # connecting again makes a new token: a revoked one stays revoked
    _connected(relay, client)
    assert client.get("/api/switchboard/status").json()["token_label"] == (
        "AI assistant (relay) 2"
    )


def test_connecting_twice_is_refused(relay, client):
    _connected(relay, client)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 409
    assert r.json()["detail"] == link.ALREADY


# -- off the critical path ----------------------------------------------------------


def test_a_restart_opens_the_line_again(relay, client):
    _connected(relay, client)
    link.stop()  # the app closed
    assert _wait(
        lambda: client.get("/api/switchboard/status").json()["state"] != "connected"
    )
    link.start_in_background()  # the app's next start
    again = relay.next_line()
    assert again.hello["mcp"] is True
    assert _wait(
        lambda: client.get("/api/switchboard/status").json()["state"] == "connected"
    )


def test_the_relay_down_at_start_holds_nothing_and_the_panel_says_so(
    relay, client, monkeypatch
):
    _connected(relay, client)
    link.stop()
    # the relay moves to an address where nothing answers
    record = json.loads(link.link_path().read_text())
    record["relay"] = f"http://127.0.0.1:{_free_port()}"
    link._save(record)
    start = _hold_the_start(monkeypatch)
    link.start_in_background()
    # it returned with its own work still held: nothing of it is waited for
    # (the hold reaches its backstop only if the start waited for it)
    assert start.entered.wait(BACKSTOP)
    # the books answer as before, also while the start's work is held
    assert client.get("/api/accounts").status_code == 200
    start.go.set()
    settle_the_start()
    assert not start.held_out.is_set()
    assert _wait(
        lambda: client.get("/api/switchboard/status").json()["state"] == "unreachable"
    )
    assert client.get("/api/accounts").status_code == 200


@pytest.mark.parametrize("shape", ["one company (beta.1)", "format 2"])
def test_the_app_starts_with_the_relay_down(
    monkeypatch, tmp_path, lifespan_client, shape
):
    """The lifespan's own start, with a kept link whose relay is gone: a
    link kept as beta.1 kept it, and one kept as this build keeps it."""
    # (lifespan_client has already started; start it again as a restart would)
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    monkeypatch.setattr(
        link, "TIMINGS", line_client.Timings(backoff_first=0.05, backoff_max=0.2)
    )
    from app.services import served_companies

    kept = {
        "v": 1,
        "on": True,
        "relay": f"http://127.0.0.1:{_free_port()}",
        "device": "dev_" + secrets.token_hex(4),
        "account": "acct_" + secrets.token_hex(4),
        "slug": "abcdefghjk",
        "credential": "sbd_" + secrets.token_urlsafe(32),
        "token": "sbp_" + secrets.token_urlsafe(32),
        "token_id": 1,
        "token_label": "Claude (relay)",
        "company_file": served_companies.started_file() or "",
        "company_name": "Test",
        "linked_at": datetime.now(timezone.utc).isoformat(),
    }
    if shape == "format 2":
        kept = link._format2(kept, link._entries(kept))
        assert kept["companies"] and link._whole2(kept)
    link._save(kept)
    start = _hold_the_start(monkeypatch)
    try:
        with TestClient(app) as again:
            # started while the line's start is still held: the app's start
            # waits for nothing of the relay's
            assert start.entered.wait(BACKSTOP)
            assert again.get("/health").status_code == 200
            start.go.set()
            settle_the_start()
            assert not start.held_out.is_set()
            # the line is trying in its own thread, holding nothing up
            assert _wait(lambda: link.status()["state"] == "unreachable")
            assert again.get("/health").status_code == 200
        # the app's shutdown closed it
        assert link.status()["state"] == "not_connected"
    finally:
        link.stop()


def test_connect_with_the_relay_down_leaves_nothing(
    relay, client, db_session, monkeypatch
):
    monkeypatch.setenv(link.RELAY_ENV, f"http://127.0.0.1:{_free_port()}")
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 503
    assert r.json()["detail"] == link.RELAY_AWAY
    assert not link.link_path().exists()
    assert db_session.query(ApiToken).count() == 0
    # a refusal (a 4xx) is said as one, with the relay's own sentence after
    # the product's; no address and no status code
    relay.link_status = 403
    monkeypatch.setenv(link.RELAY_ENV, relay.url)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 502
    assert r.json()["detail"] == (
        "The relay did not accept the request. SlowBooks Pro is working "
        "normally; if this keeps happening, write to support@slowbookspro.com. "
        "The relay said: The relay is closed today."
    )
    assert "127.0.0.1" not in r.text and "403" not in r.text
    assert db_session.query(ApiToken).count() == 0
    # a 429 is the relay busy, like a server error: its one sentence
    relay.link_status = 429
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 503
    assert r.json()["detail"] == link.RELAY_BUSY
    assert db_session.query(ApiToken).count() == 0


# -- who may ------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["bookkeeper", "accountant", "readonly"])
def test_only_an_administrator_connects_codes_or_disconnects(relay, client, role):
    _connected(relay, client)
    person = _sign_in(role, client)
    st = person.get("/api/switchboard/status")
    assert st.status_code == 200, st.text
    assert st.json()["state"] == "connected" and st.json()["can_manage"] is False
    for what in ("connect", "code", "disconnect"):
        r = person.post(f"/api/switchboard/{what}")
        assert r.status_code == 403, (what, r.text)
    assert relay.codes == []
    assert link.link_path().exists()
    assert not _role_allows(role, "POST", "/api/switchboard/connect")
    assert _role_allows(role, "GET", "/api/switchboard/status")


def test_no_api_token_reaches_the_connection(relay, client):
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    for token in (
        record["token"],
        client.post(
            "/api/tokens", json={"label": "other", "role": "bookkeeper"}
        ).json()["token"],
    ):
        bot = TestClient(app, client=_THIS_COMPUTER)
        bot.headers["Authorization"] = f"Bearer {token}"
        # even as the connection itself
        bot.headers.update(_link_itself())
        assert bot.get("/api/switchboard/status").status_code == 403
        for what in ("connect", "code", "disconnect"):
            assert bot.post(f"/api/switchboard/{what}").status_code == 403
    assert relay.codes == [] and link.link_path().exists()


def _elsewhere(record, company_file, name):
    """The kept record as if its one company were another one."""
    entry = dict(record["companies"][0], file=company_file, name=name)
    return dict(
        record,
        company_file=company_file,
        company_name=name,
        companies=[entry],
    )


def test_another_company_is_offered_add_and_hears_no_name(relay, client):
    """A connection whose companies do not include this one: an
    administrator here may Add it (on the desktop the open company's
    administrator holds the connection), a person who is not one gets
    nothing, and nobody here learns the other company's name or how many
    there are until this one is on it."""
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    link._save(_elsewhere(record, "another-company.db", "Larkspur Supply"))
    st = client.get("/api/switchboard/status")
    got = st.json()
    assert got["on_link"] is False and got["this_company"] is False
    assert got["state"] == "not_connected" and got["address"] == ""
    assert got["can_add"] is True and got["can_code"] is False
    assert "companies" not in got and "count_others" not in got
    assert "Larkspur Supply" not in st.text and "another-company" not in st.text
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 409 and r.json()["detail"] == (
        "An AI assistant connection already exists on this computer. Nothing was "
        "done. Use Add instead."
    )
    for what in ("code", "disconnect"):
        r = client.post(f"/api/switchboard/{what}")
        assert r.status_code == 403 and "Larkspur" not in r.text, what
    person = _sign_in("bookkeeper", client)
    st = person.get("/api/switchboard/status")
    assert st.json()["sentence"] == "Only an administrator can connect an AI assistant."
    assert st.json()["can_add"] is False and "Larkspur" not in st.text
    # and a start where no company on it is open opens no line
    link.stop()
    lines = relay.paths().count(("GET", "/v1/line"))
    link.start_in_background()
    settle_the_start()
    assert link._line is None and link._waiter["stop"] is None
    assert relay.paths().count(("GET", "/v1/line")) == lines
    with pytest.raises(queue.Empty):
        relay.lines.get_nowait()


# -- what is kept, and what is never said -------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_the_kept_file_is_this_users_only(relay, client):
    _connected(relay, client)
    path = link.link_path()
    assert os.stat(path).st_mode & 0o777 == 0o600
    # one another user can read is refused, not used
    os.chmod(path, 0o644)
    st = client.get("/api/switchboard/status").json()
    assert st["state"] == "damaged" and "other users" in st["sentence"]
    # and Disconnect does the whole job: the file could still be read, so
    # the relay is told with its credential and deletes its record, and the
    # token (which other users could have read) is revoked
    record = json.loads(path.read_text())
    r = client.post("/api/switchboard/disconnect")
    assert r.status_code == 200, r.text
    got = r.json()
    assert not path.exists()
    assert relay.calls[-1][1:3] == ("/v1/forget", record["credential"])
    assert got["unlinked"] is True and got["deleted"] is True
    assert got["revoked"] is True
    bot = TestClient(app, client=_THIS_COMPUTER)
    bot.headers["Authorization"] = f"Bearer {record['token']}"
    assert bot.get("/api/customers").status_code == 401
    assert not link.pending_path().exists()


def test_no_secret_in_a_log_or_an_answer(relay, client, caplog):
    caplog.set_level(logging.DEBUG)
    r1, line = _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    r2 = client.post("/api/switchboard/code")
    code = r2.json()["code"]
    r3 = client.get("/api/switchboard/status")
    r4 = client.post("/api/switchboard/disconnect")
    line.closed.wait(5)
    logged = caplog.text
    for secret in (record["credential"], record["token"], code):
        assert secret not in logged
    for r in (r1, r3, r4):
        assert record["credential"] not in r.text and record["token"] not in r.text
        assert code not in r.text
    # the vendored client's logger carries its own mask
    masked = logging.getLogger("sbplive")
    assert any(type(f).__name__ == "_MaskSecrets" for f in masked.filters)


# -- Disconnect deletes the relay record; the relay name and the fallback ----------


def test_status_shows_the_relay_name_while_connected(relay, client):
    _connected(relay, client)
    assert client.get("/api/switchboard/status").json()["slug"] == relay.slug


def test_disconnect_with_the_relay_out_of_reach_gives_the_fallback(
    relay, client, db_session, monkeypatch
):
    _, line = _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    record["relay"] = "http://127.0.0.1:9"  # nothing listens there
    link.link_path().write_text(json.dumps(record))
    r = client.post("/api/switchboard/disconnect")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["unlinked"] is False and got["revoked"] is True
    assert got["slug"] == relay.slug and got["deleted"] is False
    assert got["sentence"] == (
        "This computer is unlinked here, but the relay could not be reached, so it "
        f"still holds a record under relay name {relay.slug}. SlowBooks Pro will "
        "keep trying to remove that record itself; the name is here in case you "
        "ever need to ask support@slowbookspro.com about it."
    )
    assert not link.link_path().exists()
    assert record["credential"] not in r.text
