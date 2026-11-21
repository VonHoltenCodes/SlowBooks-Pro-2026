"""Connect to AI assistant: the relay link's one rule, against a relay that
misbehaves in every way a relay can.

The rule is written at the top of app/services/switchboard_link.py:

1. one deadline for a whole press, however the relay behaves;
2. every relay answer classified once, each class with one fixed sentence,
   never an HTTP 500, an address with a bare status code, or raw exception
   text;
3. "the relay deleted its record" only when the relay's answer says so;
4. local state never half-made: a Connect that fails leaves no token and no
   file; Disconnect always revokes and clears, a damaged file included;
5. one Connect at a time;
6. (the panel: tests/test_browser_r23_relay_select.py).

The fake relay here is a bare socket on 127.0.0.1, so it can answer things
no HTTP library would: a banner that is not HTTP, a body a byte at a time,
headers that never end. Every credential, code and token is made at run
time with `secrets`; none is written in this file.
"""

import json
import logging
import os
import re
import secrets
import socket
import threading
import time

import pytest

from app.models.api_tokens import ApiToken
from app.services import switchboard_link as link

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PRESS = 1.0  # the press's deadline in these tests (the product's is 10 s)
ADDRESS = re.compile(r"127\.0\.0\.1|https?://|localhost")
# a bare HTTP status code in a sentence, as "answered 403."
STATUS_CODE = re.compile(r"\b[1-5]\d\d\b")


def http(status, body=b"", ctype="application/json", extra=""):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    head = (
        f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\n{extra}Connection: close\r\n\r\n"
    )
    return head.encode() + body


def _code():
    return "-".join(
        "".join(secrets.choice(CROCKFORD) for _ in range(4)) for _ in range(3)
    )


class RawRelay:
    """A relay on a bare socket: `behave[path]` is the bytes to answer, or
    a function given the connection; anything else is answered well."""

    def __init__(self):
        self.calls = []  # (path, bearer)
        self.credentials = []
        self.slugs = []
        self.behave = {}
        self.heard = {}  # path -> an Event set when a request for it arrives
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.alive = True
        self.stopping = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.alive:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def good_link(self):
        credential = "sbd_" + secrets.token_urlsafe(32)
        slug = "s" + secrets.token_hex(4)
        self.credentials.append(credential)
        self.slugs.append(slug)
        return http(
            201,
            {
                "account": "acct_" + secrets.token_hex(4),
                "device": "dev_" + secrets.token_hex(4),
                "credential": credential,
                "slug": slug,
            },
        )

    def _one(self, conn):
        try:
            conn.settimeout(5)
            buf = b""
            while b"\r\n\r\n" not in buf:
                got = conn.recv(65536)
                if not got:
                    return
                buf += got
            head = buf.split(b"\r\n\r\n")[0].decode("latin1").split("\r\n")
            path = head[0].split(" ")[1]
            auth = [h for h in head if h.lower().startswith("authorization:")]
            self.calls.append((path, auth[0].split(" ")[-1] if auth else None))
            if path in self.heard:
                self.heard[path].set()
            how = self.behave.get(path)
            conn.settimeout(60)
            if callable(how):
                how(conn)
            elif how is not None:
                conn.sendall(how)
            elif path == "/v1/link":
                conn.sendall(self.good_link())
            elif path == "/v1/code":
                conn.sendall(http(201, {"code": _code(), "for": "sign-in"}))
            elif path == "/v1/forget":
                conn.sendall(http(200, {"deleted": True, "devices_left": 0}))
            elif path == "/v1/unlink":
                conn.sendall(http(200, {}))
            else:
                conn.sendall(http(404, {"error": "No such route."}))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def paths(self):
        return [p for p, _ in self.calls]

    def close(self):
        self.alive = False
        self.stopping.set()
        self.sock.close()


@pytest.fixture
def raw(monkeypatch, tmp_path):
    fake = RawRelay()
    monkeypatch.setenv(link.RELAY_ENV, fake.url)
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    monkeypatch.setattr(link, "PRESS_TIMEOUT", PRESS)
    monkeypatch.setattr(link, "RETRY_FIRST", 3600.0)  # the tests try by hand
    # these tests are about the presses: the line is not opened
    fake.started = []
    monkeypatch.setattr(link, "_start", lambda record: fake.started.append(record))
    try:
        yield fake
    finally:
        link.stop()
        link.stop_retry()
        fake.close()


def _tokens(db):
    db.expire_all()
    return [
        (t.label, t.is_active)
        for t in db.query(ApiToken).filter(ApiToken.label.like("AI assistant%")).all()
    ]


def _pending():
    path = link.pending_path()
    return json.loads(path.read_text())["pending"] if path.exists() else []


def _connect(client):
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    return json.loads(link.link_path().read_text())


def _plain(r, *, secrets_=()):
    """An answer a person can read: a sentence, no address, no bare status
    code, no exception text, no secret."""
    text = r.text
    assert r.status_code != 500, text
    assert not ADDRESS.search(text), text
    for word in ("Traceback", "Errno", "Exception", "Error:", "WinError"):
        assert word not in text, text
    for secret in secrets_:
        assert secret not in text
    return r


# -- the ways a relay can answer ---------------------------------------------------


def _trickle_body(conn):
    conn.sendall(
        b"HTTP/1.1 201 X\r\nContent-Type: application/json\r\n"
        b"Content-Length: 4000\r\nConnection: close\r\n\r\n"
    )
    for _ in range(80):
        conn.sendall(b" ")
        time.sleep(0.1)


def _trickle_headers(conn):
    conn.sendall(b"HTTP/1.1 201 X\r\n")
    for i in range(80):
        conn.sendall(f"X-Slow-{i}: 1\r\n".encode())
        time.sleep(0.1)


def _silent_after_headers(conn):
    conn.sendall(
        b"HTTP/1.1 200 X\r\nContent-Type: application/json\r\n"
        b"Content-Length: 4000\r\n\r\n"
    )
    time.sleep(8)


def _endless_body(conn):
    conn.sendall(b"HTTP/1.1 200 X\r\nContent-Type: application/json\r\n\r\n")
    chunk = b" " * 65536
    try:
        for _ in range(4000):  # up to 260 MB, as fast as it is taken
            conn.sendall(chunk)
    except OSError:
        pass


def _bad_chunk(conn):
    conn.sendall(
        b"HTTP/1.1 200 X\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n\r\nZZZ\r\n{}\r\n0\r\n\r\n"
    )


def _cut_chunk(conn):
    conn.sendall(
        b"HTTP/1.1 200 X\r\nContent-Type: application/json\r\n"
        b'Transfer-Encoding: chunked\r\n\r\n40\r\n{"dele'
    )


SLOW = {
    "trickled body": _trickle_body,
    "trickled headers": _trickle_headers,
    "silent after the headers": _silent_after_headers,
    "an endless body": _endless_body,
}

GARBLED = {
    "not HTTP": b"SSH-2.0-OpenSSH_9.6\r\n\r\n",
    "a bad chunk": _bad_chunk,
    "a cut-off chunk": _cut_chunk,
    "150 headers": (
        b"HTTP/1.1 200 X\r\n"
        + b"".join(b"X-H-%d: 1\r\n" % i for i in range(150))
        + b"Content-Length: 2\r\n\r\n{}"
    ),
    "a header line too long": (
        b"HTTP/1.1 200 X\r\nX-Long: " + b"a" * 70000 + b"\r\n\r\n{}"
    ),
    "a 5 MB body": http(200, b"[" + b"1," * 2_500_000 + b"1]"),
    "an HTML page": http(200, b"<html><body>Sign in</body></html>", "text/html"),
    "a 2xx without the fields": http(200, {"ok": True}),
    "a 204": b"HTTP/1.1 204 X\r\nConnection: close\r\n\r\n",
    "a redirect": http(302, b"", extra="Location: https://elsewhere.example/\r\n"),
}

REFUSALS = {
    "a 400 without a body": http(400),
    "a 403 HTML page": http(403, b"<html>Forbidden</html>", "text/html"),
    "a 409 without a body": http(409),
}

BUSY = {
    "a 429": http(429, {"error": "Too many tries."}),
    "a 503": http(503, {"error": "Internal error 7f3a."}),
    "a 500 page": http(500, b"<html>oops</html>", "text/html"),
}


# -- rule 2: one sentence for each class, on Connect and a sign-in code ----------


@pytest.mark.parametrize("how", sorted(GARBLED))
def test_connect_with_an_answer_that_makes_no_sense(raw, client, db_session, how):
    raw.behave["/v1/link"] = GARBLED[how]
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == link.RELAY_ODD
    assert not STATUS_CODE.search(r.json()["detail"])
    assert _tokens(db_session) == [] and not link.link_path().exists()
    assert raw.started == []
    st = client.get("/api/switchboard/status").json()
    assert st["linked"] is False and st["state"] == "not_connected"


@pytest.mark.parametrize("how", sorted(REFUSALS))
def test_connect_refused_says_the_relay_did_not_accept_it(raw, client, db_session, how):
    raw.behave["/v1/link"] = REFUSALS[how]
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == link.RELAY_REFUSED
    assert not STATUS_CODE.search(r.json()["detail"])
    assert _tokens(db_session) == [] and not link.link_path().exists()
    # a refusal is not asked again by itself
    assert raw.paths() == ["/v1/link"]


def test_a_refusal_carries_the_relays_own_sentence_as_plain_text(raw, client):
    said = 'Free sign-up is closed.\n<img src=x onerror="alert(1)">' + "z" * 900
    raw.behave["/v1/link"] = http(403, {"error": said})
    r = _plain(client.post("/api/switchboard/connect"))
    detail = r.json()["detail"]
    assert detail.startswith(link.RELAY_REFUSED + " The relay said: ")
    assert "Free sign-up is closed." in detail
    # one line, bounded; the panel escapes it (the browser test)
    assert "\n" not in detail and len(detail) < len(link.RELAY_REFUSED) + 300


@pytest.mark.parametrize("how", sorted(BUSY))
def test_connect_with_the_relay_busy(raw, client, db_session, how):
    raw.behave["/v1/link"] = BUSY[how]
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == link.RELAY_BUSY
    assert "7f3a" not in r.text and "Too many" not in r.text
    assert _tokens(db_session) == [] and not link.link_path().exists()


@pytest.mark.parametrize(
    "how", sorted({**GARBLED, **REFUSALS, **BUSY}), ids=lambda h: h.replace(" ", "-")
)
def test_a_sign_in_code_under_every_answer(raw, client, how):
    record = _connect(client)
    answers = {**GARBLED, **REFUSALS, **BUSY}
    raw.behave["/v1/code"] = answers[how]
    r = _plain(
        client.post("/api/switchboard/code"),
        secrets_=(record["credential"], record["token"]),
    )
    want = (
        (502, link.RELAY_ODD)
        if how in GARBLED
        else (502, link.RELAY_REFUSED) if how in REFUSALS else (503, link.RELAY_BUSY)
    )
    assert (r.status_code, r.json()["detail"]) == want
    # the connection is as it was
    assert link.link_path().exists()


@pytest.mark.parametrize("status", [200, 201])
def test_a_sign_in_code_is_a_code_whichever_2xx_carries_it(raw, client, status):
    _connect(client)
    code = _code()
    raw.behave["/v1/code"] = http(status, {"code": code, "for": "sign-in"})
    r = client.post("/api/switchboard/code")
    assert r.status_code == 200, r.text
    assert r.json()["code"] == code


def test_a_link_is_a_link_on_200_or_201(raw, client, db_session):
    credential = "sbd_" + secrets.token_urlsafe(32)
    raw.behave["/v1/link"] = http(
        200,
        {
            "account": "acct_" + secrets.token_hex(4),
            "device": "dev_" + secrets.token_hex(4),
            "credential": credential,
            "slug": "s" + secrets.token_hex(4),
        },
    )
    assert client.post("/api/switchboard/connect").status_code == 200
    assert _tokens(db_session) == [("AI assistant (relay)", True)]


# -- rule 1: one deadline for a whole press ---------------------------------------


@pytest.mark.parametrize("how", sorted(SLOW))
@pytest.mark.parametrize("press", ["connect", "code", "disconnect"])
def test_a_press_ends_at_its_deadline_however_the_relay_answers(
    raw, client, db_session, press, how
):
    path = {"connect": "/v1/link", "code": "/v1/code", "disconnect": "/v1/forget"}
    if press != "connect":
        _connect(client)
    raw.behave[path[press]] = SLOW[how]
    started = time.monotonic()
    r = _plain(client.post(f"/api/switchboard/{press}"))
    took = time.monotonic() - started
    # the deadline, and what it takes to notice it: never the 8 s the relay
    # would have kept the press
    assert took < PRESS + 1.5, (took, r.text)
    if press == "disconnect":
        # undone here; the relay did not confirm, so the note and the retry
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["unlinked"] is False and got["revoked"] is True
        assert "deleted its record of these books" not in got["sentence"]
        assert [p["slug"] for p in _pending()] == [raw.slugs[0]]
        assert not link.link_path().exists()
    else:
        assert r.status_code in (502, 503), r.text
        assert r.json()["detail"] in (link.RELAY_AWAY, link.RELAY_ODD)
        if press == "connect":
            assert _tokens(db_session) == [] and not link.link_path().exists()


def test_the_deadline_is_the_whole_presss_not_each_calls(raw, client, monkeypatch):
    """Disconnect on a relay from before /v1/forget makes two calls (forget,
    then unlink): one deadline covers both."""
    _connect(client)
    raw.behave["/v1/forget"] = lambda conn: (
        time.sleep(PRESS * 0.7),
        conn.sendall(http(404, {"error": "No such route."})),
    )
    raw.behave["/v1/unlink"] = _silent_after_headers
    started = time.monotonic()
    r = _plain(client.post("/api/switchboard/disconnect"))
    assert time.monotonic() - started < PRESS + 1.5
    assert r.status_code == 200 and r.json()["unlinked"] is False


def test_a_press_waits_about_ten_seconds():
    # the product's own figure (the `raw` fixture shortens it for the tests)
    assert 9 <= link.PRESS_TIMEOUT <= 10


# -- rule 3: "deleted" only when the relay says so ---------------------------------

NOT_A_DELETION = {
    "an HTML page": http(200, b"<html>Welcome</html>", "text/html"),
    "a 204": b"HTTP/1.1 204 X\r\nConnection: close\r\n\r\n",
    "another object": http(200, {"ok": True}),
    "a list": http(200, [1, 2]),
    "deleted false, none left": http(200, {"deleted": False, "devices_left": 0}),
    "deleted as a word": http(200, {"deleted": "true"}),
    "a 5 MB body": http(200, b"[" + b"1," * 2_500_000 + b"1]"),
    "not HTTP": b"SSH-2.0-OpenSSH_9.6\r\n\r\n",
    "a redirect": http(302, b"", extra="Location: https://elsewhere.example/\r\n"),
}


@pytest.mark.parametrize("how", sorted(NOT_A_DELETION))
def test_disconnect_does_not_say_deleted_unless_the_relay_did(
    raw, client, db_session, how
):
    record = _connect(client)
    raw.behave["/v1/forget"] = NOT_A_DELETION[how]
    r = _plain(
        client.post("/api/switchboard/disconnect"),
        secrets_=(record["credential"], record["token"]),
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["deleted"] is False and got["unlinked"] is False
    assert "deleted its record of these books" not in got["sentence"]
    assert "could not be reached" not in got["sentence"]  # it answered
    assert got["sentence"] == link.pending_sentence(record["slug"], "unconfirmed")
    # undone here all the same
    assert got["revoked"] is True and _tokens(db_session) == [
        ("AI assistant (relay)", False)
    ]
    assert not link.link_path().exists()
    # the note is kept and the retry goes on
    assert [p["slug"] for p in _pending()] == [record["slug"]]
    st = client.get("/api/switchboard/status").json()
    assert st["pending"][0]["sentence"] == got["sentence"]
    # the same answer on a retry is still not a deletion
    assert link.retry_pending_once() == 1
    assert [p["slug"] for p in _pending()] == [record["slug"]]
    # and when the relay does delete it, the note goes
    del raw.behave["/v1/forget"]
    assert link.retry_pending_once() == 0
    assert _pending() == []


def test_disconnect_says_deleted_when_the_relay_says_deleted(raw, client):
    record = _connect(client)
    r = client.post("/api/switchboard/disconnect")
    got = r.json()
    assert got["deleted"] is True and got["unlinked"] is True
    assert got["sentence"] == (
        "This computer is unlinked, and the relay deleted its record of these "
        f"books (relay name {record['slug']})."
    )
    assert _pending() == []


def test_a_relay_that_no_longer_knows_the_computer_is_not_a_deletion_claim(raw, client):
    record = _connect(client)
    raw.behave["/v1/forget"] = http(401, {"error": "Not linked."})
    got = client.post("/api/switchboard/disconnect").json()
    assert got["unlinked"] is True and got["deleted"] is False
    assert "deleted its record" not in got["sentence"]
    assert record["slug"] in got["sentence"]
    assert _pending() == []


def test_other_computers_left_is_said_as_the_relay_said_it(raw, client):
    record = _connect(client)
    raw.behave["/v1/forget"] = http(200, {"deleted": False, "devices_left": 2})
    got = client.post("/api/switchboard/disconnect").json()
    assert got["unlinked"] is True and got["devices_left"] == 2
    assert "2 other computers" in got["sentence"] and record["slug"] in got["sentence"]
    assert _pending() == []


def test_a_relay_from_before_forget_is_unlinked_and_says_no_more(raw, client):
    record = _connect(client)
    raw.behave["/v1/forget"] = http(404, {"error": "No such route."})
    got = client.post("/api/switchboard/disconnect").json()
    assert raw.paths()[-2:] == ["/v1/forget", "/v1/unlink"]
    assert got["unlinked"] is True and got["deleted"] is False
    assert "deleted its record" not in got["sentence"]
    assert record["slug"] in got["sentence"] and "support@" in got["sentence"]
    assert _pending() == []


# -- rule 2 on Disconnect: a refusal is said as a refusal, and not retried ----------


@pytest.mark.parametrize("how", sorted(REFUSALS))
def test_disconnect_refused_says_so_and_is_not_quietly_retried(
    raw, client, db_session, caplog, how
):
    record = _connect(client)
    raw.behave["/v1/forget"] = REFUSALS[how]
    r = _plain(client.post("/api/switchboard/disconnect"))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["unlinked"] is False and got["revoked"] is True
    assert "could not be reached" not in got["sentence"]
    assert "did not accept" in got["sentence"]
    assert got["sentence"] == link.pending_sentence(record["slug"], "refused")
    assert "keep trying" not in got["sentence"]
    assert f"Send that name to {link.account.SUPPORT}" in got["sentence"]
    assert not link.link_path().exists()
    # the note stays, with the name for support
    assert [p["slug"] for p in _pending()] == [record["slug"]]
    st = client.get("/api/switchboard/status").json()
    assert st["pending"][0]["sentence"] == got["sentence"]
    # the same refusal is not asked for again and again
    asked = raw.paths().count("/v1/forget")
    caplog.set_level(logging.WARNING)
    assert link.retry_pending_once() == 0  # nothing left to try
    assert link.retry_pending_once() == 0
    assert raw.paths().count("/v1/forget") == asked
    assert [p["slug"] for p in _pending()] == [record["slug"]]
    assert record["credential"] not in caplog.text
    # the next start tries it once: support may have removed it by then
    raw.behave["/v1/forget"] = http(401, {"error": "Not linked."})
    link.new_start()
    assert link.retry_pending_once() == 0
    assert _pending() == []


@pytest.mark.parametrize("how", sorted(BUSY))
def test_disconnect_with_the_relay_busy_undoes_nothing(raw, client, db_session, how):
    record = _connect(client)
    raw.started.clear()
    raw.behave["/v1/forget"] = BUSY[how]
    r = _plain(client.post("/api/switchboard/disconnect"))
    assert r.status_code == 503 and r.json()["detail"] == link.RELAY_BUSY
    assert link.link_path().exists() and _pending() == []
    assert _tokens(db_session) == [("AI assistant (relay)", True)]
    assert [s["device"] for s in raw.started] == [record["device"]]  # line reopened
    del raw.behave["/v1/forget"]
    assert client.post("/api/switchboard/disconnect").json()["deleted"] is True


def test_the_retry_never_raises_whatever_the_relay_answers(raw, client, caplog):
    record = _connect(client)
    raw.behave["/v1/forget"] = GARBLED["not HTTP"]
    client.post("/api/switchboard/disconnect")
    caplog.set_level(logging.DEBUG)
    for how in sorted(GARBLED):
        raw.behave["/v1/forget"] = GARBLED[how]
        assert link.retry_pending_once() == 1, how
    assert "Traceback" not in caplog.text
    assert record["credential"] not in caplog.text


def test_the_retry_logs_a_deletion_only_for_a_deletion(raw, client, caplog):
    record = _connect(client)
    raw.behave["/v1/forget"] = GARBLED["an HTML page"]
    client.post("/api/switchboard/disconnect")
    caplog.set_level(logging.WARNING)
    assert link.retry_pending_once() == 1
    assert "the relay deleted" not in caplog.text
    del raw.behave["/v1/forget"]
    assert link.retry_pending_once() == 0
    assert (
        f"the relay deleted 1 record(s) left by a disconnect (relay name "
        f"{record['slug']})" in caplog.text
    )
    assert record["credential"] not in caplog.text


# -- rule 4: nothing half-made ------------------------------------------------------


def _cannot_save(*_a, **_k):
    raise PermissionError(13, "Permission denied", "/somewhere/claude-relay.json")


def test_connect_that_cannot_keep_the_file_leaves_nothing(
    raw, client, db_session, monkeypatch
):
    monkeypatch.setattr(link, "_save_new", _cannot_save)
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == link.NOT_KEPT
    assert "/somewhere" not in r.text and "Permission" not in r.text
    # no token (not even a revoked one), no file, no line
    assert _tokens(db_session) == []
    assert not link.link_path().exists() and raw.started == []
    # the relay was told to delete what it had just made, with its credential
    assert raw.calls[-1] == ("/v1/forget", raw.credentials[0])
    assert _pending() == []
    # so the next Connect takes the plain label again
    monkeypatch.undo()
    monkeypatch.setenv(link.RELAY_ENV, raw.url)


def test_a_data_folder_that_cannot_be_written_on_connect(
    raw, client, db_session, tmp_path, monkeypatch
):
    """The real write, failing as it fails on every platform: the folder
    the file belongs in is not a folder."""
    (tmp_path / "blocked").write_text("not a folder")
    monkeypatch.setattr(
        link, "link_path", lambda: tmp_path / "blocked" / link.LINK_FILE
    )
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 503 and r.json()["detail"] == link.NOT_KEPT
    assert "blocked" not in r.text
    assert _tokens(db_session) == []
    assert raw.calls[-1] == ("/v1/forget", raw.credentials[0])
    # nothing is in the way of the next press, which takes the plain label
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    assert _tokens(db_session) == [("AI assistant (relay)", True)]


def test_a_note_that_cannot_be_kept_is_said_once_with_the_relay_name(
    raw, client, db_session, tmp_path, monkeypatch
):
    """Neither the file nor the note can be written, and the relay cannot be
    told: the press itself says the relay name to send to support."""
    (tmp_path / "blocked").write_text("not a folder")
    monkeypatch.setattr(
        link, "link_path", lambda: tmp_path / "blocked" / link.LINK_FILE
    )
    raw.behave["/v1/forget"] = lambda conn: conn.close()
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail.startswith(link.NOT_KEPT)
    assert f"relay name {raw.slugs[0]}" in detail and link.account.SUPPORT in detail
    assert raw.credentials[0] not in r.text
    assert _tokens(db_session) == []


@pytest.mark.parametrize("how", ["away", "refused", "garbled"])
def test_connect_that_fails_after_the_link_leaves_a_note_for_the_relay_record(
    raw, client, db_session, monkeypatch, how
):
    """The relay linked, the file could not be kept, and the relay then
    could not be told: the record it holds is noted and retried."""
    monkeypatch.setattr(link, "_save_new", _cannot_save)
    raw.behave["/v1/forget"] = {
        "away": lambda conn: conn.close(),
        "refused": REFUSALS["a 403 HTML page"],
        "garbled": GARBLED["not HTTP"],
    }[how]
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 503 and r.json()["detail"] == link.NOT_KEPT
    assert _tokens(db_session) == [] and not link.link_path().exists()
    kept = _pending()
    assert [p["slug"] for p in kept] == [raw.slugs[0]]
    assert kept[0]["credential"] == raw.credentials[0]
    if os.name != "nt":
        assert os.stat(link.pending_path()).st_mode & 0o777 == 0o600
    notice = client.get("/api/switchboard/status").json()["pending"][0]
    assert raw.slugs[0] in notice["sentence"]
    assert "could not be finished" in notice["sentence"]
    assert raw.credentials[0] not in json.dumps(notice)
    # the relay is back: the retry removes the record
    del raw.behave["/v1/forget"]
    link.new_start()
    assert link.retry_pending_once() == 0 and _pending() == []


def test_connect_whose_token_cannot_be_made_leaves_nothing(
    raw, client, db_session, monkeypatch
):
    from app.routes import api_tokens

    quoted = secrets.token_urlsafe(16)

    def no_token(*_a, **_k):
        raise RuntimeError(f"database is locked {quoted}")

    monkeypatch.setattr(api_tokens, "issue_token", no_token)
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 503 and r.json()["detail"] == link.NOT_KEPT
    assert "locked" not in r.text and quoted not in r.text
    assert _tokens(db_session) == [] and not link.link_path().exists()
    assert raw.calls[-1] == ("/v1/forget", raw.credentials[0])


# -- rule 4: Disconnect always revokes and clears ----------------------------------


@pytest.mark.parametrize("damage", ["empty", "not JSON", "a list", "cut short"])
def test_disconnect_on_a_file_that_cannot_be_read(raw, client, db_session, damage):
    record = _connect(client)
    text = json.dumps(record)
    link.link_path().write_text(
        {"empty": "", "not JSON": "}{", "a list": "[1]", "cut short": text[:40]}[damage]
    )
    st = client.get("/api/switchboard/status").json()
    assert st["state"] == "damaged" and st["sentence"] == link.DAMAGED
    r = _plain(
        client.post("/api/switchboard/disconnect"),
        secrets_=(record["credential"], record["token"]),
    )
    assert r.status_code == 200, r.text
    got = r.json()
    # the token is revoked and the file gone
    assert got["revoked"] is True
    assert _tokens(db_session) == [("AI assistant (relay)", False)]
    assert not link.link_path().exists()
    # and it says plainly what could not be done, and what to give support
    assert got["unlinked"] is False and got["deleted"] is False
    assert "could not be removed" in got["sentence"]
    assert link.account.SUPPORT in got["sentence"]
    assert got["say"] is True
    assert "/v1/forget" not in raw.paths()
    assert client.get("/api/switchboard/status").json()["state"] == "not_connected"
    # connecting again does not leave the old token beside the new one
    _connect(client)
    assert _tokens(db_session) == [
        ("AI assistant (relay)", False),
        ("AI assistant (relay) 2", True),
    ]


def test_the_revoked_token_no_longer_reads_the_books(raw, client, db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    record = _connect(client)
    bot = TestClient(app)
    bot.headers["Authorization"] = f"Bearer {record['token']}"
    assert bot.get("/api/customers").status_code == 200
    link.link_path().write_text("")
    assert client.post("/api/switchboard/disconnect").status_code == 200
    assert bot.get("/api/customers").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"token_id": None},
        {"token_id": "seven"},
        {"relay": "garbage"},
        {"relay": "ftp://relay.example"},
        {"device": 7},
    ],
    ids=["no-token-id", "token-id-a-word", "relay-not-an-address", "ftp", "device"],
)
def test_a_hand_edited_file_is_damaged_not_a_crash(raw, client, db_session, change):
    record = _connect(client)
    edited = {**record, **change}
    if change.get("token_id", 1) is None:
        del edited["token_id"]
    link.link_path().write_text(json.dumps(edited))
    st = client.get("/api/switchboard/status").json()
    assert st["state"] == "damaged"
    r = _plain(client.post("/api/switchboard/code"))
    assert r.status_code == 409 and r.json()["detail"] == link.DAMAGED
    r = _plain(
        client.post("/api/switchboard/disconnect"),
        secrets_=(record["credential"], record["token"]),
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["revoked"] is True
    assert _tokens(db_session) == [("AI assistant (relay)", False)]
    assert not link.link_path().exists()
    if "relay" in change:
        # where the relay is was lost: its record stays, and the name is
        # what support needs
        assert got["unlinked"] is False and "/v1/forget" not in raw.paths()
        assert record["slug"] in got["sentence"]
        assert "could not be removed" in got["sentence"]
        assert link.account.SUPPORT in got["sentence"]
    else:
        # the relay and the credential were still there: the relay was told
        assert raw.calls[-1] == ("/v1/forget", record["credential"])
        assert got["unlinked"] is True and got["deleted"] is True


def test_a_damaged_file_with_the_relay_away_keeps_the_note(raw, client, db_session):
    record = _connect(client)
    edited = dict(record)
    del edited["token_id"]
    link.link_path().write_text(json.dumps(edited))
    raw.behave["/v1/forget"] = lambda conn: conn.close()
    got = _plain(client.post("/api/switchboard/disconnect")).json()
    assert got["unlinked"] is False and got["revoked"] is True
    assert [p["slug"] for p in _pending()] == [record["slug"]]
    assert record["slug"] in got["sentence"] and "keep trying" in got["sentence"]


def test_disconnect_leaves_a_token_someone_else_named_alike_alone(
    raw, client, db_session
):
    """Only the connection's own labels are revoked with an unreadable file:
    "AI assistant (relay)" and "AI assistant (relay) <n>"."""
    client.post(
        "/api/tokens", json={"label": "AI assistant (relay) spare", "role": "readonly"}
    )
    _connect(client)
    link.link_path().write_text("")
    client.post("/api/switchboard/disconnect")
    assert sorted(_tokens(db_session)) == [
        ("AI assistant (relay)", False),
        ("AI assistant (relay) spare", True),
    ]


# -- rule 5: one Connect at a time --------------------------------------------------


def test_two_connects_at_once_make_one_record_and_one_token(
    raw, client, db_session, monkeypatch
):
    raw.heard["/v1/link"] = threading.Event()
    go = threading.Event()

    def slow_link(conn):
        go.wait(5)
        conn.sendall(raw.good_link())

    raw.behave["/v1/link"] = slow_link
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 8.0)
    first = {}

    def press():
        first["r"] = client.post("/api/switchboard/connect")

    one = threading.Thread(target=press)
    one.start()
    try:
        assert raw.heard["/v1/link"].wait(5)
        # the second press, while the first is still with the relay
        with pytest.raises(link.SwitchboardError) as second:
            link.connect(None, "operator")
        assert second.value.status == 409
        assert second.value.sentence == link.PRESS_BUSY
    finally:
        go.set()
        one.join(10)
    assert first["r"].status_code == 200, first["r"].text
    assert raw.paths().count("/v1/link") == 1 and len(raw.credentials) == 1
    assert _tokens(db_session) == [("AI assistant (relay)", True)]
    # and once it is done, the press is free again
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 409 and r.json()["detail"] == link.ALREADY


def test_a_connect_made_by_another_process_meanwhile_wins(raw, client, db_session):
    """Two server processes share the data folder and not the lock: the
    file is made only if it is not there, and the one that finds it there
    undoes what it made (its token, its relay record)."""
    other = {}

    def link_while_another_finishes(conn):
        # the other process's Connect lands while this one is with the relay
        other.update(
            {
                "v": 1,
                "on": True,
                "relay": raw.url,
                "device": "dev_" + secrets.token_hex(4),
                "account": "acct_" + secrets.token_hex(4),
                "slug": "s" + secrets.token_hex(4),
                "credential": "sbd_" + secrets.token_urlsafe(32),
                "token": "sbp_" + secrets.token_urlsafe(32),
                "token_id": 999,
                "token_label": "AI assistant (relay)",
                "company_file": link._company_here(),
                "company_name": "Test",
                "linked_at": "2026-01-01T00:00:00+00:00",
            }
        )
        link._save(other)
        conn.sendall(raw.good_link())

    raw.behave["/v1/link"] = link_while_another_finishes
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 409 and r.json()["detail"] == link.ALREADY
    # the other's file is untouched; this press's token and record are gone
    assert json.loads(link.link_path().read_text()) == other
    assert _tokens(db_session) == []
    assert raw.calls[-1] == ("/v1/forget", raw.credentials[0])
    if os.name != "nt":
        assert os.stat(link.link_path()).st_mode & 0o777 == 0o600


def test_a_new_connection_file_is_this_users_only_from_the_first_byte(raw, client):
    _connect(client)
    path = link.link_path()
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o777 == 0o600
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".")] == []


def test_disconnect_while_a_press_is_being_worked_on_is_refused(raw, client):
    _connect(client)
    assert link._press_lock.acquire(blocking=False)
    try:
        r = client.post("/api/switchboard/disconnect")
        assert r.status_code == 409 and r.json()["detail"] == link.PRESS_BUSY
        assert link.link_path().exists()
    finally:
        link._press_lock.release()
    assert client.post("/api/switchboard/disconnect").status_code == 200


# -- never an HTTP 500 from these routes --------------------------------------------


@pytest.mark.parametrize("what", ["status", "connect", "code", "disconnect", "dismiss"])
def test_a_fault_of_our_own_is_a_sentence_not_a_500(raw, client, monkeypatch, what):
    secret = "sbd_" + secrets.token_urlsafe(32)

    def broken(*_a, **_k):
        raise RuntimeError(f"boom {secret} at {raw.url}")

    name = {"code": "new_code", "dismiss": "dismiss_pending"}.get(what, what)
    monkeypatch.setattr(link, name, broken)
    if what == "status":
        r = client.get("/api/switchboard/status")
    elif what == "dismiss":
        r = client.post("/api/switchboard/dismiss", json={"slug": "abc"})
    else:
        r = client.post(f"/api/switchboard/{what}")
    _plain(r, secrets_=(secret,))
    assert r.status_code == 503 and r.json()["detail"] == link.UNFINISHED
    assert "boom" not in r.text


def test_dismiss_that_cannot_be_written_says_so(raw, client, monkeypatch):
    record = _connect(client)
    raw.behave["/v1/forget"] = lambda conn: conn.close()
    client.post("/api/switchboard/disconnect")
    monkeypatch.setattr(link, "_write_private", _cannot_save)
    r = _plain(client.post("/api/switchboard/dismiss", json={"slug": record["slug"]}))
    assert r.status_code == 503 and r.json()["detail"] == link.NOT_WRITTEN


def test_a_relay_address_that_is_not_one_is_a_sentence(raw, client, monkeypatch):
    monkeypatch.setenv(link.RELAY_ENV, "relay.example.test/v1?x=1")
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 502, r.text
    assert "must look like" in r.json()["detail"]


# -- the classification itself -------------------------------------------------------


def test_every_status_has_one_class():
    want = lambda data: "x" in data  # noqa: E731
    kinds = {}
    for status in range(100, 600):
        for data in (None, {"x": 1}, {"y": 1}, [1]):
            kinds[(status, json.dumps(data))] = link._classify(status, data, want).kind
    assert kinds[(200, '{"x": 1}')] == link.OK
    assert kinds[(201, '{"x": 1}')] == link.OK
    assert kinds[(200, '{"y": 1}')] == link.ODD
    assert kinds[(204, "null")] == link.ODD
    assert kinds[(302, '{"x": 1}')] == link.ODD
    assert kinds[(101, "null")] == link.ODD
    assert kinds[(403, "null")] == link.REFUSED
    assert kinds[(401, '{"x": 1}')] == link.REFUSED
    assert kinds[(429, '{"x": 1}')] == link.BUSY
    assert kinds[(500, "null")] == link.BUSY
    assert kinds[(599, '{"x": 1}')] == link.BUSY
    assert set(kinds.values()) == {link.OK, link.ODD, link.REFUSED, link.BUSY}
    # and each class that is not success has its one sentence
    said = {
        link.Answer(kind).error().sentence
        for kind in (link.AWAY, link.REFUSED, link.BUSY, link.ODD)
    }
    assert said == {
        link.RELAY_AWAY,
        link.RELAY_REFUSED,
        link.RELAY_BUSY,
        link.RELAY_ODD,
    }
    for sentence in said:
        assert not ADDRESS.search(sentence) and not STATUS_CODE.search(sentence)


def test_no_relay_address_or_status_code_reaches_a_person(raw, client, caplog):
    """The detail goes to the log (with no credential), not to the panel."""
    caplog.set_level(logging.WARNING)
    raw.behave["/v1/link"] = REFUSALS["a 403 HTML page"]
    r = client.post("/api/switchboard/connect")
    assert "403" not in r.text and "127.0.0.1" not in r.text
    assert "403" in caplog.text and "/v1/link" in caplog.text


# -- the review of the rule: what got past it ----------------------------------------

# JSON nested too deep to decode: json.loads raises RecursionError, which is
# neither a ValueError nor an OSError
DEEP = {"a list": b"[" * 30000, "an object": b'{"a":' * 30000}


@pytest.mark.parametrize("how", sorted(DEEP))
def test_an_answer_nested_too_deep_is_one_that_cannot_be_read(
    raw, client, db_session, how
):
    raw.behave["/v1/link"] = http(200, DEEP[how])
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 502 and r.json()["detail"] == link.RELAY_ODD
    assert _tokens(db_session) == [] and not link.link_path().exists()
    del raw.behave["/v1/link"]
    _connect(client)
    raw.behave["/v1/code"] = http(200, DEEP[how])
    r = _plain(client.post("/api/switchboard/code"))
    assert r.status_code == 502 and r.json()["detail"] == link.RELAY_ODD


@pytest.mark.parametrize("how", sorted(DEEP))
def test_disconnect_finishes_when_the_answer_is_nested_too_deep(
    raw, client, db_session, how
):
    record = _connect(client)
    raw.behave["/v1/forget"] = http(200, DEEP[how])
    r = _plain(client.post("/api/switchboard/disconnect"))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["deleted"] is False and got["revoked"] is True
    assert got["why"] == link.WHY_UNCONFIRMED and got["pending"] is True
    assert _tokens(db_session) == [("AI assistant (relay)", False)]
    assert not link.link_path().exists()
    assert [p["slug"] for p in _pending()] == [record["slug"]]
    # and the retry does not raise on it either
    assert link.retry_pending_once() == 1
    del raw.behave["/v1/forget"]
    assert link.retry_pending_once() == 0 and _pending() == []


def test_a_connect_undone_against_an_answer_nested_too_deep_keeps_the_note(
    raw, client, db_session, monkeypatch
):
    monkeypatch.setattr(link, "_save_new", _cannot_save)
    raw.behave["/v1/forget"] = http(200, DEEP["a list"])
    r = _plain(client.post("/api/switchboard/connect"))
    assert r.status_code == 503 and r.json()["detail"] == link.NOT_KEPT
    assert _tokens(db_session) == [] and not link.link_path().exists()
    assert [p["slug"] for p in _pending()] == [raw.slugs[0]]


def test_nothing_gets_past_the_classifier(raw, monkeypatch):
    """Whatever a call trips on, the answer is a class, never an exception."""
    quoted = secrets.token_urlsafe(16)

    def trips(*_a, **_k):
        raise RuntimeError(f"something of our own {quoted}")

    monkeypatch.setattr(link, "_relay_call", trips)
    answer = link._relay_post(raw.url, "/v1/code", None, want=lambda data: True)
    assert answer.kind == link.ODD
    monkeypatch.undo()

    def want_trips(data):
        raise RecursionError(quoted)

    answer = link._relay_post(raw.url, "/v1/code", None, want=want_trips)
    assert answer.kind == link.ODD


def test_a_kept_file_nested_too_deep_is_damaged_not_a_fault(raw, client, db_session):
    _connect(client)
    link.link_path().write_text("[" * 30000)
    st = client.get("/api/switchboard/status")
    assert st.status_code == 200 and st.json()["state"] == "damaged"
    link.pending_path().write_text("[" * 30000)
    assert client.get("/api/switchboard/status").json()["pending"] == []
    link.pending_path().unlink()
    r = _plain(client.post("/api/switchboard/disconnect"))
    assert r.status_code == 200, r.text
    assert _tokens(db_session) == [("AI assistant (relay)", False)]
    assert not link.link_path().exists()


def test_disconnect_revokes_the_token_whatever_it_was_renamed_to(
    raw, client, db_session
):
    """The connection's token is the one whose secret the file holds: an
    administrator renaming it in Settings, API Tokens does not hide it."""
    from fastapi.testclient import TestClient

    from app.main import app

    record = _connect(client)
    bot = TestClient(app)
    bot.headers["Authorization"] = f"Bearer {record['token']}"
    assert bot.get("/api/customers").status_code == 200
    renamed = "Assistant on the office phone"
    r = client.put(f"/api/tokens/{record['token_id']}", json={"label": renamed})
    assert r.status_code == 200, r.text
    got = client.post("/api/switchboard/disconnect").json()
    assert got["revoked"] is True and got["deleted"] is True
    db_session.expire_all()
    row = db_session.query(ApiToken).filter(ApiToken.id == record["token_id"]).one()
    assert (row.label, row.is_active) == (renamed, False)
    assert bot.get("/api/customers").status_code == 401


def test_disconnect_revokes_by_the_secret_not_by_a_number_someone_edited(
    raw, client, db_session
):
    """The file's token id pointing at another token does not revoke that
    one: the secret says which token is the connection's."""
    made = client.post(
        "/api/tokens", json={"label": "the receipt service", "role": "readonly"}
    ).json()
    record = _connect(client)
    link._save(dict(record, token_id=made["id"], token_label="the receipt service"))
    assert client.post("/api/switchboard/disconnect").json()["revoked"] is True
    db_session.expire_all()
    rows = {t.label: t.is_active for t in db_session.query(ApiToken).all()}
    assert rows["the receipt service"] is True
    assert rows["AI assistant (relay)"] is False


_FOLDER_MODES = pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a folder's mode does not stop this user here",
)


@_FOLDER_MODES
def test_disconnect_in_a_folder_that_cannot_be_written_still_disconnects(
    raw, client, db_session, tmp_path
):
    """The file cannot be removed from a read-only folder, but it is this
    user's own: it is written over as ended, so nothing of the connection
    stays, and the answer says what the relay did."""
    record = _connect(client)
    os.chmod(tmp_path, 0o555)
    try:
        r = _plain(client.post("/api/switchboard/disconnect"))
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["deleted"] is True and got["revoked"] is True
        assert "deleted its record" in got["sentence"]
        assert "nothing was changed" not in r.text
        assert _tokens(db_session) == [("AI assistant (relay)", False)]
        left = link.link_path().read_text()
        assert record["credential"] not in left and record["token"] not in left
        assert os.stat(link.link_path()).st_mode & 0o777 == 0o600
        # it is no connection any more: the panel, a code, a second press
        st = client.get("/api/switchboard/status").json()
        assert st["linked"] is False and st["state"] == "not_connected"
        assert st["slug"] == "" and st["address"] == ""
        r = client.post("/api/switchboard/code")
        assert r.status_code == 409 and r.json()["detail"] == link.NOT_LINKED
        r = client.post("/api/switchboard/disconnect")
        assert r.status_code == 409 and r.json()["detail"] == link.NOT_LINKED
        assert raw.paths() == ["/v1/link", "/v1/forget"]
        # Connect while the folder still cannot be written: said before the
        # relay is asked for anything
        r = _plain(client.post("/api/switchboard/connect"))
        assert r.status_code == 503 and r.json()["detail"] == link.NOT_KEPT
        assert raw.paths() == ["/v1/link", "/v1/forget"]
        assert _tokens(db_session) == [("AI assistant (relay)", False)]
    finally:
        os.chmod(tmp_path, 0o755)
    # the folder can be written again: Connect takes the ended file away
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    assert json.loads(link.link_path().read_text())["slug"] == raw.slugs[1]
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


@_FOLDER_MODES
def test_disconnect_in_a_folder_that_cannot_be_written_with_the_relay_away(
    raw, client, db_session, tmp_path
):
    """Neither the file nor the note can be written and the relay is away:
    the answer gives the relay name to send to support."""
    record = _connect(client)
    raw.behave["/v1/forget"] = lambda conn: conn.close()
    os.chmod(tmp_path, 0o555)
    try:
        r = _plain(client.post("/api/switchboard/disconnect"))
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["deleted"] is False and got["unlinked"] is False
        assert got["revoked"] is True and got["pending"] is False
        assert got["say"] is True
        assert f"relay name {record['slug']}" in got["sentence"]
        assert "Send that name" in got["sentence"]
        assert "nothing was changed" not in r.text
        assert _tokens(db_session) == [("AI assistant (relay)", False)]
        assert client.get("/api/switchboard/status").json()["linked"] is False
        assert record["credential"] not in link.link_path().read_text()
    finally:
        os.chmod(tmp_path, 0o755)


@pytest.mark.parametrize("relay_is", ["there", "away"])
def test_a_file_that_can_be_neither_removed_nor_ended_is_said_as_it_is(
    raw, client, db_session, monkeypatch, relay_is
):
    """What was done is said (the relay's record, the token), then what was
    not: never that nothing was changed."""
    record = _connect(client)
    if relay_is == "away":
        raw.behave["/v1/forget"] = lambda conn: conn.close()
    monkeypatch.setattr(link, "_forget", _cannot_save)
    monkeypatch.setattr(link, "_end_in_place", lambda: False)
    r = _plain(client.post("/api/switchboard/disconnect"))
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "nothing was changed" not in detail
    assert detail.endswith(link.FILE_KEPT)
    assert "Its token is revoked." in detail
    assert f"relay name {record['slug']}" in detail
    if relay_is == "there":
        assert "the relay deleted its record of these books" in detail
    else:
        assert "could not be reached" in detail and "deleted" not in detail
        assert [p["slug"] for p in _pending()] == [record["slug"]]
    assert "/somewhere" not in detail and record["credential"] not in detail
    assert _tokens(db_session) == [("AI assistant (relay)", False)]
    # the line is not opened again on a revoked token (once, by Connect)
    assert len(raw.started) == 1


def test_a_token_that_cannot_be_revoked_is_said_and_the_rest_is_done(
    raw, client, db_session, monkeypatch
):
    quoted = secrets.token_urlsafe(16)

    def locked(*_a, **_k):
        raise RuntimeError(f"database is locked {quoted}")

    record = _connect(client)
    monkeypatch.setattr(link, "_revoke", locked)
    r = _plain(client.post("/api/switchboard/disconnect"), secrets_=(quoted,))
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "the relay deleted its record of these books" in detail
    assert link.TOKEN_KEPT.format(label=record["token_label"]) in detail
    assert "locked" not in detail and "could not finish that" not in detail
    assert not link.link_path().exists()
    assert raw.paths() == ["/v1/link", "/v1/forget"]


def test_an_ended_file_is_never_taken_for_another_processs_link(raw, client):
    """Clearing an ended file moves it aside and looks at it there: a link
    another server process kept in that moment is put back, not removed."""
    record = _connect(client)
    with pytest.raises(link._AlreadyKept):
        link._clear_ended()
    assert json.loads(link.link_path().read_text()) == record
    assert [p.name for p in link.link_path().parent.iterdir()] == [link.LINK_FILE]


def _part_link(**fields):
    """A link answer that is not a whole link."""
    return http(201, fields)


def test_a_link_answer_with_a_credential_but_not_whole_is_undone_at_the_relay(
    raw, client, db_session
):
    """The relay made a record and said what deleting it needs, but the
    answer cannot be kept as a link: the record is deleted, not left."""
    credential = "sbd_" + secrets.token_urlsafe(32)
    slug = "s" + secrets.token_hex(4)
    raw.behave["/v1/link"] = _part_link(
        credential=credential, slug=slug, device="dev_" + secrets.token_hex(4)
    )
    r = _plain(client.post("/api/switchboard/connect"), secrets_=(credential,))
    assert r.status_code == 502 and r.json()["detail"] == link.RELAY_ODD
    assert raw.calls == [("/v1/link", None), ("/v1/forget", credential)]
    assert _pending() == [] and _tokens(db_session) == []
    assert not link.link_path().exists()


def test_a_part_link_the_relay_cannot_be_told_to_delete_is_noted(
    raw, client, db_session
):
    credential = "sbd_" + secrets.token_urlsafe(32)
    slug = "s" + secrets.token_hex(4)
    raw.behave["/v1/link"] = _part_link(credential=credential, slug=slug)
    raw.behave["/v1/forget"] = lambda conn: conn.close()
    r = _plain(client.post("/api/switchboard/connect"), secrets_=(credential,))
    assert r.status_code == 502 and r.json()["detail"] == link.RELAY_ODD
    kept = _pending()
    assert [(p["slug"], p["credential"], p["connect"]) for p in kept] == [
        (slug, credential, True)
    ]
    notice = client.get("/api/switchboard/status").json()["pending"][0]
    assert slug in notice["sentence"] and "could not be finished" in notice["sentence"]
    assert _tokens(db_session) == []
    del raw.behave["/v1/forget"]
    assert link.retry_pending_once() == 0 and _pending() == []


def test_a_part_link_with_no_relay_name_says_what_to_tell_support(raw, client):
    credential = "sbd_" + secrets.token_urlsafe(32)
    raw.behave["/v1/link"] = _part_link(credential=credential, slug="<b>no</b>")
    raw.behave["/v1/forget"] = lambda conn: conn.close()
    r = _plain(client.post("/api/switchboard/connect"), secrets_=(credential,))
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail.startswith(link.RELAY_ODD)
    assert "say when you pressed Connect" in detail and "<b>" not in detail
    assert raw.calls[-1] == ("/v1/forget", credential) and _pending() == []


def test_an_answer_with_no_credential_made_nothing_to_undo(raw, client):
    raw.behave["/v1/link"] = _part_link(slug="s" + secrets.token_hex(4))
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 502 and r.json()["detail"] == link.RELAY_ODD
    assert raw.paths() == ["/v1/link"] and _pending() == []
