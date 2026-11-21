"""What a failed send is said to be: true sentences, chosen by where the
send failed and the reply code, never copied from what the server wrote.

Each case here was a sentence a person would act on and be wrong: a
temporary refusal at sign-in read as a wrong password, a message over the
size limit read as a bad From address, a refused customer logged as sent
because a copy went through, a delivered message logged as failed because
the server hung up before answering QUIT. A loopback stub plays the server,
and each send goes through send_email() as the app makes it.

The sentence is the same for every reader and holds nothing of the
server's; the reply is an administrator's line (tests/test_email_failure_rule.py).

Every sign-in is generated when the test runs.
"""

import base64
import secrets
import socket
import threading
import time

import pytest

from app.models.email_log import EmailLog
from app.models.users import ROLE_BOOKKEEPER
from app.services import email_service
from app.services.email_errors import (
    FailureReader,
    failure_sentence,
    stored_text,
    where_to_see_why,
)
from app.services.email_service import send_email
from tests.test_email_failure_rule import FROM, _configure, _session
from tests.test_email_failure_rule import no_pdfs as no_documents_drawn  # noqa: F401

TO = "customer@example.test"
COPY = "copy@example.test"
POINTER = where_to_see_why(False)
SEE_WHY = where_to_see_why(True)
REFUSED_MESSAGE = "The mail server refused the message."
REFUSED_CONNECTION = "The mail server refused the connection."

LATER = "The mail server asked to try again later."
WRONG_SIGN_IN = "The server rejected the username or password — check both."
USE_TLS = (
    "The mail server takes mail only over a secure connection. Turn on Use "
    "TLS under Settings → Email (SMTP)."
)
SIGN_IN_FAILED = "The mail server did not accept the sign-in."
WANTS_SIGN_IN = (
    "The mail server wants a sign-in, and none is set. Enter the username and "
    "password under Settings → Email (SMTP)."
)
TOO_BIG = (
    "The mail server refused the message because it is too large. Send a "
    "smaller attachment, or ask your mail provider about its size limit."
)
UNREACHABLE = (
    "The server could not be reached — check the host, port, and the Use TLS setting."
)
NO_SIGN_IN_OFFERED = (
    "The mail server does not offer a sign-in by username and password. "
    "Clear the username under Settings → Email (SMTP) if the server needs "
    "none, or ask your mail provider."
)
LETTERS = (
    "The SMTP username or password has a character that cannot be sent to "
    "the mail server (a letter with an accent, for example), so nothing was "
    "sent. Change it under Settings → Email (SMTP), or use an app password "
    "from your mail provider."
)
GOOGLE = (
    "Google will not accept a normal password here. Turn on 2-Step "
    "Verification on the account and use a 16-character app password as the "
    "SMTP password."
)


class Stub:
    """A plain SMTP server on 127.0.0.1 that follows a script. `say` maps a
    step (GREETING, EHLO, AUTH, MAIL, RCPT, DATA, BODY, QUIT) to the lines
    it answers with, or to a function of the client's line that returns
    them; the word "hang-up" closes the connection instead. Steps not in
    the script get the usual yes."""

    USUAL = {
        "GREETING": ["220 stub ESMTP"],
        "EHLO": ["250-stub", "250 AUTH PLAIN LOGIN"],
        "AUTH": ["235 2.7.0 accepted"],
        "MAIL": ["250 2.1.0 OK"],
        "RCPT": ["250 2.1.5 OK"],
        "DATA": ["354 go ahead"],
        "BODY": ["250 2.0.0 queued"],
        "QUIT": ["221 bye"],
    }

    def __init__(self, say=None):
        self.say = {**self.USUAL, **(say or {})}
        self.lines: list[str] = []
        self.messages = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.open = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        self.open = False
        self.thread.join(5)
        self.sock.close()

    def _serve(self):
        while self.open:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            conn.settimeout(30)  # a loaded machine is slow, not gone
            try:
                self._talk(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _answer(self, conn, step, line="") -> bool:
        """False when the script hangs up here."""
        lines = self.say[step]
        if callable(lines):
            lines = lines(line)
        if lines == "hang-up":
            return False
        for text in lines:
            conn.sendall(text.encode("utf-8") + b"\r\n")
        return True

    def _talk(self, conn):
        f = conn.makefile("rb")
        if not self._answer(conn, "GREETING"):
            return
        in_data = False
        while True:
            raw = f.readline()
            if not raw:
                return
            if in_data:
                if raw in (b".\r\n", b".\n"):
                    in_data = False
                    self.messages += 1
                    if not self._answer(conn, "BODY"):
                        return
                continue
            text = raw.decode(errors="replace").strip()
            self.lines.append(text)
            verb = text.split(" ", 1)[0].upper()
            if verb == "HELO":
                verb = "EHLO"
            if verb == "RSET":
                conn.sendall(b"250 OK\r\n")
                continue
            if verb not in self.say:
                conn.sendall(b"502 5.5.2 not implemented\r\n")
                continue
            if not self._answer(conn, verb, text):
                return
            if verb == "DATA" and self.say["DATA"] == self.USUAL["DATA"]:
                in_data = True
            if verb == "QUIT":
                return


@pytest.fixture
def stub():
    made = []

    def make(say=None):
        made.append(Stub(say))
        return made[-1]

    yield make
    for one in made:
        one.close()


@pytest.fixture
def no_pdfs(monkeypatch):
    """The send is under test, not the render."""
    import app.routes.invoices.documents as documents

    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")


def _send(db, server, user="", password="", cc=None, to=TO):
    _configure(db, server.port, user, password)
    ok = send_email(db, to, "A subject", "<p>Hello</p>", cc=cc)
    row = db.query(EmailLog).order_by(EmailLog.id.desc()).first()
    return ok, row


def _read(db, row, admin=False):
    return FailureReader(db).read(row, admin)


def _sign_in():
    return "u" + secrets.token_hex(4) + "@example.test", secrets.token_urlsafe(12)


# -- only a 535 means the username or password is wrong ---------------------------

AUTH_REPLIES = [
    ("535 5.7.8 Authentication credentials invalid", WRONG_SIGN_IN),
    ("454 4.7.0 Temporary authentication failure", LATER),
    ("421 4.3.0 Service shutting down", LATER),
    ("451 4.3.0 Try again", LATER),
    ("530 5.7.0 Must issue a STARTTLS command first", USE_TLS),
    ("538 5.7.11 Encryption required for requested mechanism", USE_TLS),
    ("534 5.7.9 Authentication mechanism is too weak", SIGN_IN_FAILED),
    ("432 4.7.12 A password transition is needed", SIGN_IN_FAILED),
    ("504 5.5.4 Unrecognized authentication type", SIGN_IN_FAILED),
]


@pytest.mark.parametrize("reply,sentence", AUTH_REPLIES)
def test_a_reply_to_sign_in_reads_as_what_it_is(db_session, stub, reply, sentence):
    user, password = _sign_in()
    server = stub({"AUTH": [reply]})
    ok, row = _send(db_session, server, user, password)
    assert not ok and row.status == "failed"
    code = reply[:3]
    assert row.error_message == f"[auth] {reply}"
    seen = _read(db_session, row)
    mine = _read(db_session, row, admin=True)
    assert seen.sentence == mine.sentence == sentence
    assert server.messages == 0
    # the one with no sentence of its own for the reason ends with where
    # the reason can be read; the server's words are in neither reading
    if sentence == SIGN_IN_FAILED:
        assert seen.reason() == f"{SIGN_IN_FAILED} {POINTER}"
        assert mine.reason() == f"{SIGN_IN_FAILED} {SEE_WHY}"
    else:
        assert seen.reason() == mine.reason() == sentence
    # the reply is the administrator's line, as it was received
    assert mine.reply == reply and seen.reply is None
    # the same reply read from a row of an earlier release, either format
    assert failure_sentence(f"[auth] {reply}") == sentence
    if sentence != SIGN_IN_FAILED:
        python_wrote = repr((int(code), reply[4:].encode()))
        assert failure_sentence(python_wrote) == sentence


def test_a_googles_534_still_asks_for_an_app_password():
    reply = "[auth] 534 5.7.9 Application-specific password required"
    assert failure_sentence(reply, "smtp.gmail.com") == GOOGLE
    assert failure_sentence(reply, "mail.example.test") == SIGN_IN_FAILED
    # and a temporary refusal there is not a wrong password either
    assert (
        failure_sentence("[auth] 454 4.7.0 Too many login attempts", "smtp.gmail.com")
        == LATER
    )


# -- a message over the size limit is not a bad From address ----------------------


def test_a_size_refusal_at_mail_from_is_about_the_message(db_session, stub):
    words = "552 5.3.4 Message size exceeds fixed maximum message size"
    server = stub({"MAIL": [words]})
    ok, row = _send(db_session, server)
    assert not ok
    assert row.error_message == f"[sender {FROM}] {words}"
    seen = _read(db_session, row)
    assert seen.sentence == seen.reason() == TOO_BIG
    assert FROM not in seen.sentence
    # an earlier release's row of the same reply, both formats
    assert failure_sentence(f"[sender {FROM}] {words}") == TOO_BIG
    assert failure_sentence(repr((552, words[4:].encode(), FROM))) == TOO_BIG


@pytest.mark.parametrize(
    "words,sentence",
    [
        ("552 5.2.3 Message exceeds maximum fixed size", TOO_BIG),
        ("554 5.3.4 Message too big for system", TOO_BIG),
        # a 552 that does not say which: it may be the mailbox that is full
        ("552 Requested mail action aborted", REFUSED_MESSAGE),
        ("552 5.2.2 Mailbox full", REFUSED_MESSAGE),
        ("554 5.7.1 Message rejected as spam", REFUSED_MESSAGE),
    ],
)
def test_a_size_refusal_of_the_message_says_so(db_session, stub, words, sentence):
    ok, row = _send(db_session, stub({"BODY": [words]}))
    assert not ok
    assert _read(db_session, row).sentence == sentence


@pytest.mark.parametrize(
    "words,sentence",
    [
        ("552 Message size exceeds fixed maximum message size", TOO_BIG),
        ("552 5.2.3 Message too long", TOO_BIG),
        # about something else, and still not the From address
        ("552 5.2.2 Over quota", REFUSED_MESSAGE),
    ],
)
def test_a_552_at_mail_from_never_blames_the_from_address(
    db_session, stub, words, sentence
):
    ok, row = _send(db_session, stub({"MAIL": [words]}))
    assert not ok
    seen = _read(db_session, row)
    assert seen.sentence == sentence
    assert FROM not in seen.reason() and "From address" not in seen.reason()


def test_a_refused_from_address_is_still_one(db_session, stub):
    ok, row = _send(db_session, stub({"MAIL": ["553 5.1.8 Sender address rejected"]}))
    assert not ok
    assert _read(db_session, row).sentence == (
        f"The mail server refused the sender address: {FROM}. Check the From "
        "address under Settings → Email (SMTP)."
    )


# -- a refused address never reads as one that received the message ---------------


def _refuse(address, reply="550 5.1.1 User unknown"):
    return lambda line: [reply] if address in line else ["250 2.1.5 OK"]


def test_the_to_address_refused_is_a_failed_send_though_a_copy_went(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_document_activity_894 import _activity, _customer, _invoice

    canary = "canary" + secrets.token_hex(8)
    server = stub({"RCPT": _refuse(TO, f"550 5.1.1 User unknown {canary}")})
    _configure(db_session, server.port)
    inv = _invoice(client, _customer(client)["id"])
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": TO, "subject": "Invoice", "message": "", "cc": COPY},
    )
    sentence = (
        f"The address was refused by the mail server: {TO}. Check it and send "
        f"again. A copy still went to: {COPY}."
    )
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == f"Email could not be sent. {sentence} {SEE_WHY}"
    assert server.messages == 1  # the copy did go
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["status"] == "failed"
    assert row["failure"] == sentence
    assert row["error_message"] == f"{TO}: 550 5.1.1 User unknown {canary}"
    events = _activity(client, "invoice", inv["id"])["events"]
    emailed = next(e for e in events if e["kind"] == "emailed")
    assert "could not be sent" in emailed["text"]
    assert emailed["details"] == [sentence]
    # the invoice is not marked as emailed to the address that refused it
    assert client.get(f"/api/invoices/{inv['id']}").json()["last_emailed_to"] is None
    # nobody else is given the server's words
    keeper = _session(db_session, ROLE_BOOKKEEPER)
    (theirs,) = keeper.get("/api/email-log").json()["rows"]
    assert theirs["failure"] == theirs["error_message"] == sentence
    assert canary not in keeper.get("/api/email-log").text


def test_a_refused_copy_is_noted_on_the_sent_row(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_document_activity_894 import _activity, _customer, _invoice

    canary = "canary" + secrets.token_hex(8)
    server = stub({"RCPT": _refuse(COPY, f"550 5.1.1 User unknown {canary}")})
    _configure(db_session, server.port)
    inv = _invoice(client, _customer(client)["id"])
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": TO, "subject": "Invoice", "message": "", "cc": COPY},
    )
    assert r.status_code == 200, r.text
    note = f"The mail server refused a copy to: {COPY}."
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["status"] == "sent" and row["failure"] is None
    assert row["note"] == note
    # the server's reply to the refused copy is an administrator's
    assert row["error_message"] == f"{COPY}: 550 5.1.1 User unknown {canary}"
    events = _activity(client, "invoice", inv["id"])["events"]
    emailed = next(e for e in events if e["kind"] == "emailed")
    assert emailed["text"].startswith(f"Emailed to {TO} ")
    assert emailed["details"][-1] == note
    keeper = _session(db_session, ROLE_BOOKKEEPER)
    (theirs,) = keeper.get("/api/email-log").json()["rows"]
    assert theirs["note"] == note and theirs["error_message"] is None
    assert canary not in keeper.get("/api/email-log").text
    assert canary not in repr(_activity(keeper, "invoice", inv["id"]))
    # a send with nothing refused carries no note
    ok, plain = _send(db_session, stub())
    assert ok and plain.error_message is None
    assert _read(db_session, plain, admin=True).note is None


def test_a_to_address_asked_to_wait_while_a_copy_went(db_session, stub):
    server = stub({"RCPT": _refuse(TO, "451 4.7.1 Greylisted, try again")})
    ok, row = _send(db_session, server, cc=COPY)
    assert not ok
    assert _read(db_session, row).sentence == (
        f"The mail server asked to try again later. A copy still went to: {COPY}."
    )


@pytest.mark.parametrize(
    "reply,sentence",
    [
        ("530 5.7.1 Authentication required", WANTS_SIGN_IN),
        (
            "554 5.7.1 Relay access denied",
            f"The mail server would not take mail for: {TO}. If the address is "
            "right, the server may want a sign-in (Settings → Email (SMTP)) or "
            "may not allow mail to it.",
        ),
        (
            "550 5.1.1 User unknown",
            f"The address was refused by the mail server: {TO}. Check it and "
            "send again.",
        ),
    ],
)
def test_a_refusal_about_permission_is_not_a_bad_address(
    db_session, stub, reply, sentence
):
    ok, row = _send(db_session, stub({"RCPT": [reply]}))
    assert not ok
    assert _read(db_session, row).sentence == sentence
    # and from a row an earlier build stored, either way
    assert failure_sentence(f"[recipients] {TO}: {reply}") == sentence
    python_wrote = repr({TO: (int(reply[:3]), reply[4:].encode())})
    assert failure_sentence(python_wrote) == sentence


# -- a refusal with no sentence of its own; a server that is not there -------------


@pytest.mark.parametrize(
    "step,reply,sentence",
    [
        ("GREETING", "554 5.3.2 no SMTP service here", REFUSED_CONNECTION),
        ("EHLO", "550 5.7.1 Client host rejected", REFUSED_CONNECTION),
        ("DATA", "503 5.5.1 No valid recipients", REFUSED_MESSAGE),
        ("BODY", "554 5.7.1 Message rejected as spam", REFUSED_MESSAGE),
    ],
)
def test_a_refusal_with_no_sentence_of_its_own_is_the_stage_and_nothing_after(
    db_session, stub, step, reply, sentence
):
    ok, row = _send(db_session, stub({step: [reply]}))
    assert not ok
    theirs, mine = _read(db_session, row), _read(db_session, row, admin=True)
    assert theirs.sentence == mine.sentence == sentence
    # nothing after it but where the reason can be read, for either reader
    assert theirs.reason() == f"{sentence} {POINTER}"
    assert mine.reason() == f"{sentence} {SEE_WHY}"
    assert mine.reply == reply and theirs.reply is None
    # the same reply on a row that cannot say its stage (a release before
    # 2.32 stored one tuple for a refused greeting and a refused message)
    python_wrote = repr((int(reply[:3]), reply[4:].encode()))
    assert failure_sentence(python_wrote) == "The mail server refused the email."


def test_a_server_that_never_answers_reads_as_timed_out(db_session, monkeypatch):
    monkeypatch.setattr(email_service, "_SOCKET_TIMEOUT", 1)
    quiet = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    quiet.bind(("127.0.0.1", 0))
    quiet.listen(1)
    try:
        server = type("S", (), {"port": quiet.getsockname()[1]})
        ok, row = _send(db_session, server)
    finally:
        quiet.close()
    assert not ok
    assert row.error_message == "[disconnected] timed out"
    seen = _read(db_session, row, admin=True)
    assert seen.sentence == seen.reason() == UNREACHABLE
    assert seen.reply == "timed out"


def test_a_port_nothing_listens_on_reads_as_a_server_not_reached(db_session):
    closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    ok, row = _send(db_session, type("S", (), {"port": port}))
    assert not ok
    assert row.error_message.startswith("[socket] ")
    seen = _read(db_session, row, admin=True)
    assert seen.sentence == UNREACHABLE
    # the system's own words for it, without Python's "[Errno 111] "
    assert seen.reply and "[Errno" not in seen.reply and "[WinError" not in seen.reply


def test_a_connection_dropped_mid_conversation_reads_as_a_server_not_reached(
    db_session, stub
):
    ok, row = _send(db_session, stub({"MAIL": "hang-up"}))
    assert not ok
    assert row.error_message == "[disconnected] Connection unexpectedly closed"
    assert _read(db_session, row).sentence == UNREACHABLE


# -- a message the server took is a sent message ----------------------------------


@pytest.mark.parametrize(
    "at_quit",
    ["hang-up", ["421 4.3.0 closing"], ["500 5.5.1 what"], ["not an answer at all"]],
)
def test_a_message_the_server_accepted_is_sent_whatever_quit_does(
    db_session, stub, at_quit
):
    server = stub({"QUIT": at_quit})
    ok, row = _send(db_session, server)
    assert server.messages == 1
    assert ok and row.status == "sent" and row.error_message is None


def test_a_hang_up_after_the_message_body_is_also_sent_once(db_session, stub):
    """The server answers 250 to the body and drops the line: the client
    never gets to say QUIT."""

    class HangsUpAfterTheBody(Stub):
        def _answer(self, conn, step, line=""):
            answered = super()._answer(conn, step, line)
            return answered and step != "BODY"

    server = HangsUpAfterTheBody()
    try:
        ok, row = _send(db_session, server)
    finally:
        server.close()
    assert server.messages == 1
    assert ok and row.status == "sent"


# -- failures on our own side: never Python's words --------------------------------


@pytest.mark.parametrize("where", ["password", "username"])
def test_a_sign_in_with_an_accented_letter_says_so(db_session, stub, where):
    user, password = _sign_in()
    letter = "é"
    if where == "password":
        password = password[:5] + letter + password[5:]
    else:
        user = "b" + letter + user
    server = stub()
    ok, row = _send(db_session, server, user, password)
    assert not ok
    assert row.error_message == (
        "[auth letters] The username or password has a character the "
        "sign-in cannot carry"
    )
    for text in (row.error_message, _read(db_session, row, admin=True).reply):
        assert "codec" not in text and "\\x" not in text and "position" not in text
        assert letter not in text
    assert _read(db_session, row).sentence == LETTERS
    assert not any(line.upper().startswith("AUTH") for line in server.lines)
    assert server.messages == 0


@pytest.mark.parametrize("host", ["smtp..example.test", "a" * 70 + ".example.test"])
def test_a_host_name_no_connection_can_be_made_to(db_session, host):
    from app.services.settings_service import set_setting

    _configure(db_session, 2525)
    set_setting(db_session, "smtp_host", host)
    db_session.commit()
    assert not send_email(db_session, TO, "A subject", "<p>Hello</p>")
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.error_message == "[socket] Host name is not valid"
    seen = _read(db_session, row, admin=True)
    assert seen.sentence == UNREACHABLE
    assert seen.reply == "Host name is not valid"


@pytest.mark.parametrize(
    "offered", [["250 stub"], ["250-stub", "250 AUTH XOAUTH2 OAUTHBEARER"]]
)
def test_a_server_that_offers_no_sign_in_we_can_use(db_session, stub, offered):
    user, password = _sign_in()
    ok, row = _send(db_session, stub({"EHLO": offered}), user, password)
    assert not ok
    assert row.error_message.startswith("[auth none-offered] ")
    assert _read(db_session, row).sentence == NO_SIGN_IN_OFFERED
    # an earlier release kept smtplib's sentence bare
    assert failure_sentence("SMTP AUTH extension not supported by server.") == (
        NO_SIGN_IN_OFFERED
    )


def test_a_port_that_does_not_speak_smtp(db_session, stub):
    server = stub({"GREETING": ["HTTP/1.1 400 Bad Request"]})
    ok, row = _send(db_session, server)
    assert not ok
    # smtplib keeps the line without its first four characters: what is
    # stored says so in words of ours, and is not called a server's reply
    words = "The answer was not an SMTP reply: …/1.1 400 Bad Request"
    assert row.error_message == f"[connect not-smtp] {words}"
    seen = _read(db_session, row, admin=True)
    assert seen.sentence == UNREACHABLE
    assert seen.reply == words
    assert seen.with_reply() == UNREACHABLE
    # the same answer on a row of an earlier release reads the same
    old = "(-1, b'/1.1 400 Bad Request')"
    assert failure_sentence(old) == UNREACHABLE
    row.error_message = old  # test-only: a row as 2.31 stored it
    seen = _read(db_session, row, admin=True)
    assert seen.reply == words
    assert seen.with_reply() == UNREACHABLE


def test_test_email_does_not_call_a_cut_off_line_the_servers_reply(
    client, db_session, stub
):
    server = stub({"GREETING": ["HTTP/1.1 400 Bad Request"]})
    _configure(db_session, server.port)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502
    assert r.json()["detail"] == (
        f"Test email failed to send. {UNREACHABLE} The full list is under "
        "Settings → Email (SMTP) → Recent sends."
    )
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["error_message"] == (
        "The answer was not an SMTP reply: …/1.1 400 Bad Request"
    )


def test_a_sign_in_the_server_never_finishes(db_session, stub):
    """smtplib gives up on a server that answers every step with another
    334: the row keeps that reply as readable text (never Python's tuple),
    as it was received, and the sentence says only that the sign-in was
    not accepted."""
    user, password = _sign_in()
    echo = base64.b64encode(password.encode()).decode()
    server = stub({"EHLO": ["250-stub", "250 AUTH LOGIN"], "AUTH": [f"334 {echo}"]})
    # every later line of the client's (its base64 answers) gets a 334 too
    server.say = _Always(server.say, [f"334 {echo}"])
    ok, row = _send(db_session, server, user, password)
    assert not ok
    assert row.error_message == f"[auth loop] 334 {echo}"
    mine = _read(db_session, row, admin=True)
    theirs = _read(db_session, row)
    assert mine.sentence == theirs.sentence == SIGN_IN_FAILED
    assert mine.reply == f"334 {echo}"
    assert "(" not in mine.reply and "b'" not in mine.reply
    # nobody else, and no sentence, holds any of it
    assert theirs.reply is None
    for text in (theirs.reason(), mine.reason()):
        assert echo not in text and password not in text
    assert theirs.reason() == f"{SIGN_IN_FAILED} {POINTER}"


class _Always(dict):
    """A script that answers any line it has no step for the same way."""

    def __init__(self, known, lines):
        super().__init__(known)
        self._lines = lines

    def __contains__(self, key):
        return True

    def __missing__(self, key):
        return self._lines


def test_any_other_fault_stores_none_of_pythons_text(
    db_session, stub, monkeypatch, caplog
):
    """A fault of ours in the middle of a send: the row says "Unexpected
    error" (no class name, none of the exception's text), and the server
    log has what kind it was and where, without its text."""
    import logging
    import smtplib

    secret = secrets.token_urlsafe(12)

    def broken(self, *a, **k):
        raise RuntimeError(f"something about {secret}")

    monkeypatch.setattr(smtplib.SMTP, "sendmail", broken)
    caplog.set_level(logging.DEBUG)
    ok, row = _send(db_session, stub())
    assert not ok
    assert row.error_message == "[local] Unexpected error"
    assert stored_text(RuntimeError(f"something about {secret}")) == row.error_message
    seen = _read(db_session, row, admin=True)
    assert seen.sentence == "The email could not be sent."
    assert seen.reply == "Unexpected error"
    assert "Email send failed with RuntimeError at:" in caplog.text
    assert "in broken" in caplog.text
    # (the frame's source line is ours; the text the exception carried is not there)
    assert caplog.text.count(secret) == 0


# -- a reply cannot run on for ever -------------------------------------------------


def test_a_reply_that_never_ends_is_given_up_on(db_session, monkeypatch):
    """A server may answer with any number of "220-…" lines; one sent more
    often than the socket's wait kept a send going for as long as the
    server liked."""
    monkeypatch.setattr(email_service, "_REPLY_TIMEOUT", 1.0)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2)
    sock.settimeout(0.2)
    running = True

    def drip():
        while running:
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            try:
                while running:
                    conn.sendall(b"220-still getting ready\r\n")
                    time.sleep(0.05)
            except OSError:
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()
    try:
        server = type("S", (), {"port": sock.getsockname()[1]})
        started = time.monotonic()
        ok, row = _send(db_session, server)
        took = time.monotonic() - started
    finally:
        running = False
        thread.join(5)
        sock.close()
    assert not ok
    assert took < 15, took
    assert row.error_message == "[disconnected] timed out"
    assert _read(db_session, row).sentence == UNREACHABLE


def test_an_ordinary_send_is_not_cut_short(db_session, stub, monkeypatch):
    """The wait is each reply's own, however many replies a send has."""
    monkeypatch.setattr(email_service, "_REPLY_TIMEOUT", 2.0)

    def slow(line):
        time.sleep(0.6)
        return ["250 2.1.5 OK"]

    server = stub({"MAIL": slow, "RCPT": slow, "BODY": lambda line: slow(line)})
    ok, row = _send(db_session, server, cc=COPY)
    assert ok and row.status == "sent"


# -- the same failure reads the same on a row of any age ----------------------------


@pytest.mark.parametrize(
    "stored",
    [
        "[Errno 104] Connection reset by peer",
        "[Errno 32] Broken pipe",
        "[WinError 10054] An existing connection was forcibly closed by the "
        "remote host",
        "Connection unexpectedly closed: [Errno 104] Connection reset by peer",
        "[socket] Connection reset by peer",
        "[disconnected] Connection reset by peer",
        "[Errno -3] Temporary failure in name resolution",
        "[Errno 8] nodename nor servname provided, or not known",
        "[WinError 10060] A connection attempt failed because the connected "
        "party did not properly respond after a period of time",
        "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)",
    ],
)
def test_a_reset_connection_reads_as_unreachable_however_stored(stored):
    assert failure_sentence(stored) == UNREACHABLE


# -- Recent sends names every kind of send ------------------------------------------


def test_every_kind_of_send_has_a_name_in_recent_sends():
    """The What column showed the raw codes "estimate" and "reminder"."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    kinds = set()
    for path in (root / "app").rglob("*.py"):
        source = path.read_text("utf-8")
        if "send_email(" not in source:
            continue
        kinds.update(re.findall(r'entity_type=f?"([a-z_]+)', source))
    assert {"invoice", "estimate", "reminder", "statement", "collection"} <= kinds
    script = (root / "app/static/js/settings.js").read_text("utf-8")
    start = script.index("_emailSourceLabel(entityType) {")
    labels = script[
        start : script.index("},", script.index("return entityType", start))
    ]
    for kind in sorted(kinds):
        if kind.startswith("acknowledgment"):
            assert "startsWith('acknowledgment')" in labels
        else:
            assert re.search(rf"\b{kind}: ", labels), f"no name for {kind!r}"


# -- a size refusal at RCPT is about the message, not the address -------------------


@pytest.mark.parametrize(
    "reply",
    ["552 5.3.4 Message size exceeds fixed limit", "552 5.2.3 Message too long"],
)
def test_a_size_refusal_at_rcpt_is_about_the_message(db_session, stub, reply):
    ok, row = _send(db_session, stub({"RCPT": [reply]}))
    assert not ok
    seen = _read(db_session, row)
    assert seen.sentence == seen.reason() == TOO_BIG
    assert TO not in seen.sentence
    # the administrator's line keeps who the server answered, and how
    assert _read(db_session, row, admin=True).reply == f"{TO}: {reply}"
    # the same refusal on a row of an earlier release
    code, words = reply[:3], reply[4:]
    assert failure_sentence(repr({TO: (int(code), words.encode())})) == TOO_BIG
    # a full mailbox is still about the address
    ok, row = _send(db_session, stub({"RCPT": ["552 5.2.2 Mailbox full"]}))
    assert _read(db_session, row).sentence == (
        f"The address was refused by the mail server: {TO}. Check it and send again."
    )


# -- a refused STARTTLS: the reply is kept, and "not now" is not a setting ----------

TLS_REFUSED = (
    "SlowBooks Pro could not set up a secure connection to the mail server, "
    "so nothing was sent and your password was not given to it. Check the "
    "port and the Use TLS setting under Settings → Email (SMTP), or ask your "
    "mail provider. (127.0.0.1)"
)
TLS_NOT_NOW = (
    "The mail server could not set up a secure connection just now, so "
    "nothing was sent and your password was not given to it. Try again "
    "later. (127.0.0.1)"
)


@pytest.mark.parametrize(
    "code,words,sentence",
    [
        (454, "4.7.0 TLS not available due to temporary reason", TLS_NOT_NOW),
        (501, "5.5.4 Syntax error (no parameters allowed)", TLS_REFUSED),
    ],
)
def test_a_refused_starttls_keeps_the_reply_and_says_what_it_was(
    client, db_session, code, words, sentence
):
    from tests.test_email_failure_rule import RefusingStub

    canary = "canary" + secrets.token_hex(8)
    user, password = _sign_in()
    server = RefusingStub("starttls", lambda u, p: [f"{words} {canary}"], code)
    try:
        _configure(db_session, server.port, user, password, use_tls="true")
        r = client.post("/api/settings/test-email")
        keeper = _session(db_session, ROLE_BOOKKEEPER)
        theirs = keeper.get("/api/email-log")
    finally:
        server.close()
    reply = f"{code} {words} {canary}"
    assert r.status_code == 502
    # Test Email is an administrator's: the sentence, then the reply
    assert r.json()["detail"] == (
        f"Test email failed to send. {sentence} The mail server's reply: "
        f"{reply}. The full list is under Settings → Email (SMTP) → Recent sends."
    )
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["failure"] == sentence
    assert row["error_message"] == reply
    # no sign-in and no mail went to a server with no secure connection
    assert not {"AUTH", "MAIL", "RCPT", "DATA"} & set(server.verbs)
    # nobody else reads the reply
    assert canary not in theirs.text
    (shown,) = theirs.json()["rows"]
    assert shown["failure"] == shown["error_message"] == sentence


# -- a refused copy is said in the answer to the send, on every path ----------------


@pytest.mark.usefixtures("no_documents_drawn")
def test_a_refused_copy_is_said_in_every_answer_to_a_send(
    client, db_session, seed_accounts, seed_customer
):
    """The To address took the message and the server refused the Always
    CC address: each answer that says "sent" says the copy was refused, in
    our words, to the administrator and the bookkeeper alike."""
    from app.services.settings_service import set_setting
    from tests import test_email_failure_rule as rule

    canary = "canary" + secrets.token_hex(8)
    server = Stub({"RCPT": _refuse(COPY, f"550 5.1.1 User unknown {canary}")})
    note = f"The mail server refused a copy to: {COPY}."
    try:
        _configure(db_session, server.port)
        set_setting(db_session, "smtp_always_cc", COPY)
        db_session.commit()
        ids = rule._books(client, db_session, seed_accounts, seed_customer)
        keeper = _session(db_session, ROLE_BOOKKEEPER)
        theirs = dict((p, (s, t)) for p, s, t in rule._every_send(keeper, ids))
        mine = dict((p, (s, t)) for p, s, t in rule._every_send(client, ids))
        # the reminder by hand went first each time, so the run had nothing
        # due: a new overdue invoice gives it one
        rule._overdue(db_session)
        run = client.post("/api/collections/reminders/run", json={})
    finally:
        server.close()
    assert server.messages >= 10
    said = 0
    for who, answers in (("administrator", mine), ("bookkeeper", theirs)):
        for path, (status, text) in answers.items():
            assert canary not in text, (who, path)
            if who == "bookkeeper" and path == rule.TEST_EMAIL:
                assert status == 403
                continue
            assert status in (200, 400), (who, path, text)
            if status == 400 or '"sent":0' in text or '"emailed":0' in text:
                # nothing was due on this path the second time round
                continue
            assert note in text, f"{who}: {path} said sent and no more: {text}"
            said += 1
    # Test Email, the three documents, the reminder and the five batches
    assert len(mine) == 10
    told = {p for p, (s, t) in mine.items() if note in t} | {
        p for p, (s, t) in theirs.items() if note in t
    }
    assert sorted(set(mine) - told) == ["/api/collections/reminders/run"]
    assert run.status_code == 200 and run.json()["sent"] == 1, run.text
    (line,) = run.json()["notes"]
    assert line.startswith("Mail Probe, ") and line.endswith(f": {note}")
    assert canary not in run.text
    assert said >= 12
    # the single answers carry it as `note`
    import json

    for path in (rule.TEST_EMAIL, f"/api/invoices/{ids['invoice']}/email"):
        assert json.loads(mine[path][1])["note"] == note
    # a send with nothing refused carries none
    plain = Stub()
    try:
        _configure(db_session, plain.port)
        r = client.post(rule.TEST_EMAIL)
    finally:
        plain.close()
    assert r.json() == {"status": "sent"}


# -- a long reply costs no more than its length --------------------------------------


def test_a_very_long_reply_is_stored_and_read_at_once():
    """A server chooses how long its reply is. Storing one, and reading a
    long row of an earlier release, once took time that grew with the
    square of its length."""
    import smtplib

    from app.services.email_errors import _holds_reply, server_words

    started = time.monotonic()
    for filler in (b"250 ", b"A", b"AUTH PLAIN ", b"a=b "):
        long = filler * (50000 // len(filler))
        for error in (
            smtplib.SMTPDataError(554, long),
            smtplib.SMTPRecipientsRefused({TO: (550, long)}),
            smtplib.SMTPAuthenticationError(535, long),
        ):
            stored = stored_text(error)
            assert len(stored) <= 2000
            # what 2.31 stored: Python's str() of the exception, any length
            for text in (stored, str(error), long.decode()):
                failure_sentence(text, "smtp.example.test")
                server_words(text)
                _holds_reply(text)
    assert time.monotonic() - started < 5


# -- a connection lost once the message was handed over ------------------------------

LOST_AFTER_BODY = (
    "The connection to the mail server was lost after the message was handed "
    "to it, before it answered, so the email may or may not have gone out. "
    "Check with the recipient before sending it again."
)


def test_a_hang_up_before_the_answer_to_the_message_says_it_may_have_gone(
    client, db_session, stub
):
    """The server was reached, signed in to and given the whole message,
    then hung up without answering. "Could not be reached, check the host
    and port" is untrue of it, and a second send could be a duplicate."""
    server = stub({"BODY": "hang-up"})
    ok, row = _send(db_session, server)
    assert not ok and row.status == "failed"
    assert server.messages == 1
    assert (
        row.error_message == "[disconnected after-body] Connection unexpectedly closed"
    )
    seen = _read(db_session, row, admin=True)
    assert seen.sentence == seen.reason() == seen.with_reply() == LOST_AFTER_BODY
    assert seen.reply == "Connection unexpectedly closed"
    (shown,) = client.get("/api/email-log").json()["rows"]
    assert shown["failure"] == LOST_AFTER_BODY
    # lost any earlier, nothing had been handed over: as before
    for step in ("MAIL", "RCPT", "DATA"):
        ok, row = _send(db_session, stub({step: "hang-up"}))
        assert not ok
        assert row.error_message == "[disconnected] Connection unexpectedly closed"
        assert _read(db_session, row).sentence == UNREACHABLE


def test_a_server_silent_after_the_message_says_it_may_have_gone(
    db_session, stub, monkeypatch
):
    monkeypatch.setattr(email_service, "_REPLY_TIMEOUT", 1.0)

    def silent(line):
        time.sleep(2.5)
        return "hang-up"

    ok, row = _send(db_session, stub({"BODY": silent}))
    assert not ok
    assert row.error_message == "[disconnected after-body] timed out"
    assert _read(db_session, row).sentence == LOST_AFTER_BODY
