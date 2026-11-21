"""Round 26: the mail server's own words, as plain text, at every stage.

NEW-275 / W-224: Recent sends showed the server's reply as Python wrote it
under each failed send's sentence ("(530, b'5.7.0 …', 'books@…')",
"{'who@…': (550, b'…')}", "ung\\xc3\\xbcltig"). The email log now stores the
server's words as plain text, and a row stored the old way reads clean too.

NEW-276 / W-225: a server that refused the message itself (a 554 after the
body, a refused DATA, a 554 greeting) lost its reason everywhere; batch
sends pointed at a log that did not have it. Every stage of the
conversation is run here against a plain loopback stub, and each answer
says what was refused. No real network; the password is made up per test.

The rule since: the server's own words are in no sentence. A refusal with
no sentence of its own reads "The mail server refused the message." or
"The mail server refused the connection.", ending with where the reason
can be read; the server's reply is the line under the sentence in Recent
sends and follows the sentence in Test Email's answer, both an
administrator's, as it was received.
"""

import secrets
import smtplib
import socket
import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.email_log import EmailLog
from app.models.invoices import Invoice, InvoiceStatus
from app.services import collections as svc
from app.services.email_errors import failure_sentence, server_words, stored_text
from app.services.settings_service import set_setting

FROM = "books@example.test"
TO = "mail-r26@example.test"


class StageStub:
    """A plain SMTP server on 127.0.0.1 that refuses at one stage. Serves
    every connection made to it until closed; `raw` is every byte read."""

    def __init__(self, stage: str):
        self.stage = stage
        self.raw = b""
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
            # no side of the conversation waits less than the client does
            conn.settimeout(20)
            try:
                self._talk(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _talk(self, conn):
        f = conn.makefile("rb")

        def say(text):
            conn.sendall(text.encode("utf-8") + b"\r\n")

        if self.stage == "greeting":
            say("554 5.3.2 no SMTP service here")
            return
        say("220 stub ESMTP")
        in_data = False
        while True:
            line = f.readline()
            if not line:
                return
            self.raw += line
            if in_data:
                if line in (b".\r\n", b".\n"):
                    in_data = False
                    if self.stage == "body":
                        say("554 5.7.1 Message rejected as spam")
                    elif self.stage == "drop":
                        return
                    elif self.stage == "utf8":
                        say("550 5.7.1 Absender ungültig")
                    else:
                        say("250 2.0.0 queued")
                continue
            verb = line.decode(errors="replace").split(" ", 1)[0].strip().upper()
            if verb in ("EHLO", "HELO"):
                if self.stage == "ehlo":
                    say("550 5.7.1 Client host rejected")
                else:
                    say("250 stub")
            elif verb == "MAIL":
                if self.stage == "mail":
                    say("553 5.1.8 Sender address rejected: domain not found")
                else:
                    say("250 2.1.0 OK")
            elif verb == "RCPT":
                if self.stage == "rcpt":
                    say("550 5.1.1 User unknown")
                else:
                    say("250 2.1.5 OK")
            elif verb == "DATA":
                if self.stage == "data":
                    say("503 5.5.1 No valid recipients")
                elif self.stage == "timeout":
                    # never answer: the client's timeout ends the send
                    while self.open and f.readline():
                        pass
                    return
                else:
                    say("354 go ahead")
                    in_data = True
            elif verb == "RSET":
                say("250 OK")
            elif verb == "QUIT":
                say("221 bye")
                return
            else:
                say("502 5.5.2 not implemented")


# stage → (the failure's sentence, the line under it in Recent sends: the
# server's reply, or the plain cause of a connection that failed)
STAGES = {
    "greeting": (
        "The mail server refused the connection.",
        "554 5.3.2 no SMTP service here",
    ),
    "ehlo": (
        "The mail server refused the connection.",
        "550 5.7.1 Client host rejected",
    ),
    "mail": (
        f"The mail server refused the sender address: {FROM}. Check the From "
        "address under Settings → Email (SMTP).",
        "553 5.1.8 Sender address rejected: domain not found",
    ),
    "rcpt": (
        f"The address was refused by the mail server: {TO}. Check it and send "
        "again.",
        f"{TO}: 550 5.1.1 User unknown",
    ),
    "data": (
        "The mail server refused the message.",
        "503 5.5.1 No valid recipients",
    ),
    "body": (
        "The mail server refused the message.",
        "554 5.7.1 Message rejected as spam",
    ),
    "utf8": (
        "The mail server refused the message.",
        "550 5.7.1 Absender ungültig",
    ),
    # the stub hangs up once it has the whole message, before answering:
    # the server was reached and may have taken it
    "drop": (
        "The connection to the mail server was lost after the message was "
        "handed to it, before it answered, so the email may or may not have "
        "gone out. Check with the recipient before sending it again.",
        "Connection unexpectedly closed",
    ),
    "timeout": (
        "The server could not be reached — check the host, port, and the Use "
        "TLS setting.",
        # a reply that never came: the plain cause, nothing closed (W-232)
        "timed out",
    ),
}

# the stages with no reply from the server: a connection that failed
NO_REPLY = ("drop", "timeout")
# where an administrator is told the reply can be read
SEE_WHY = "The reason is under Settings → Email (SMTP) → Recent sends."

# Python's wrapping, any piece of which in an answer is NEW-275
PYTHON = ("b'", 'b"', "', '", "{'", "('", "\\x", "\\n", "[sender", "[data")


def _plain(text):
    for piece in PYTHON:
        assert piece not in text, (piece, text)


# How long the client waits for a reply. Only the stage that never answers
# needs it short; every other stage answers, and a reply that is slow on a
# loaded machine (a long run on a small box, a garbage-collection pause)
# must not read as a timeout: a one-second wait for every reply failed the
# body stage once in a full Linux lap (it passed alone and on the next lap).
_ANSWERS_WITHIN = 20
_SILENT_WAIT = 1


@pytest.fixture
def quick_timeout(monkeypatch):
    """send_email() waits 30 s for a silent server; the "timeout" stage
    needs only a second, and a stage that answers keeps a long wait."""
    import app.services.email_service as email_service

    real = smtplib.SMTP
    wait = {"seconds": _ANSWERS_WITHIN}

    class Quick(real):
        def __init__(self, host="", port=0, local_hostname=None, timeout=None, **k):
            super().__init__(host, port, local_hostname, timeout=wait["seconds"], **k)

    monkeypatch.setattr(email_service.smtplib, "SMTP", Quick)
    return wait


def _configure(db, port):
    for key, value in (
        ("smtp_host", "127.0.0.1"),
        ("smtp_port", str(port)),
        ("smtp_user", ""),
        ("smtp_password", ""),
        ("smtp_from_email", FROM),
        ("smtp_use_tls", "false"),
    ):
        set_setting(db, key, value)
    db.commit()


@pytest.fixture(params=sorted(STAGES))
def stage(request, db_session, quick_timeout):
    if request.param == "timeout":
        quick_timeout["seconds"] = _SILENT_WAIT
    server = StageStub(request.param)
    _configure(db_session, server.port)
    yield request.param
    server.close()


def test_test_email_gives_the_sentence_then_the_servers_reply(client, stage):
    sentence, words = STAGES[stage]
    if stage == "rcpt":
        # Test Email sends to the From address
        sentence, words = sentence.replace(TO, FROM), words.replace(TO, FROM)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    _plain(detail)
    # the sentence, then (Test Email is an administrator's) the server's
    # reply as it was received; a connection that failed has no reply
    reply = "" if stage in NO_REPLY else f" The mail server's reply: {words}."
    # a message handed over before the connection was lost may have gone
    # out: the answer does not open by saying it was not sent
    opening = (
        "Test email may or may not have been sent."
        if "may or may not have gone out" in sentence
        else "Test email failed to send."
    )
    assert detail == (
        f"{opening} {sentence}{reply} The full list is under "
        "Settings → Email (SMTP) → Recent sends."
    )
    (row,) = client.get("/api/email-log?status=failed").json()["rows"]
    # `failure` is the sentence alone, for every reader; the reply (or the
    # plain cause) is the row's other field, an administrator's
    assert row["failure"] == sentence
    assert row["error_message"] == words
    if stage not in ("mail", "rcpt"):
        assert words not in row["failure"]


def _overdue(db):
    c = Customer(name="Mail Probe", email=TO, is_active=True, monthly_statement=True)
    db.add(c)
    db.commit()
    db.add(
        Invoice(
            invoice_number=f"R26-{c.id}",
            customer_id=c.id,
            status=InvoiceStatus.SENT,
            date=date.today() - timedelta(days=90),
            due_date=date.today() - timedelta(days=45),
            total=Decimal(100),
            balance_due=Decimal(100),
        )
    )
    db.commit()
    return c


@pytest.fixture
def no_pdfs(monkeypatch):
    import app.routes.reports.receivables as receivables
    from app.services import pdf_service

    for target, name in (
        (svc, "generate_statement_pdf"),
        (svc, "generate_invoice_pdf"),
        (receivables, "generate_statement_pdf"),
        (receivables, "generate_collection_letter_pdf"),
    ):
        monkeypatch.setattr(target, name, lambda *a, **k: b"%PDF-")
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: b"%PDF-")


@pytest.mark.parametrize("which", ["body", "data", "greeting"])
def test_every_batch_path_gives_the_sentence_a_single_send_gives(
    client, db_session, seed_accounts, seed_customer, no_pdfs, quick_timeout, which
):
    from tests.test_donor_documents import _giving_year

    server = StageStub(which)
    try:
        _configure(db_session, server.port)
        _overdue(db_session)
        svc.seed_default_rules(db_session)
        sentence = STAGES[which][0]
        answers = []
        r = client.post("/api/collections/statements/run", json={})
        answers += r.json()["errors"]
        r = client.post("/api/collections/reminders/run", json={})
        answers += r.json()["errors"]
        r = client.post("/api/reports/batch-email-statements")
        answers += r.json()["errors"]
        r = client.post(
            "/api/reports/collection-letters",
            json={"letter_type": "30", "send_email": True},
        )
        answers += r.json()["errors"]
        _giving_year(client, seed_accounts, seed_customer)
        r = client.post(
            "/api/donors/giving-statements/batch-email", json={"year": 2026}
        )
        giving = r.json()["errors"]
    finally:
        server.close()
    assert len(answers) == 4, answers
    # the stage, and nothing of the server's after it: the sentence ends
    # with where the reply can be read (the reader here is an administrator)
    assert giving == [
        f"Test Customer: the giving statement could not be sent. {sentence} {SEE_WHY}"
    ]
    for text in answers + giving:
        assert text.endswith(f"could not be sent. {sentence} {SEE_WHY}"), text
        assert "email log" not in text
        assert STAGES[which][1] not in text
        _plain(text)


def test_a_documents_email_gives_the_sentence_and_never_the_servers_words(
    client, db_session, seed_accounts, monkeypatch, quick_timeout
):
    import app.routes.invoices.documents as documents
    from tests.test_email_log import _invoice

    inv = _invoice(client)
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")
    server = StageStub("body")
    try:
        _configure(db_session, server.port)
        r = client.post(
            f"/api/invoices/{inv['id']}/email",
            json={"recipient": TO, "subject": "Invoice", "message": ""},
        )
    finally:
        server.close()
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == (
        f"Email could not be sent. The mail server refused the message. {SEE_WHY}"
    )


# -- rows stored before round 26 read clean ----------------------------------

OLD_ROWS = [
    (
        "(530, b'5.7.0 Authentication Required', 'books@example.com')",
        "530 5.7.0 Authentication Required",
        "The mail server wants a sign-in",
    ),
    (
        "{'mail199@example.com': (550, b'5.1.1 User unknown'), "
        "'b@example.com': (553, b'5.1.2 Bad domain')}",
        "mail199@example.com: 550 5.1.1 User unknown\n"
        "b@example.com: 553 5.1.2 Bad domain",
        "The address was refused by the mail server: mail199@example.com, "
        "b@example.com.",
    ),
    (
        "(553, b'5.1.8 Absender ung\\xc3\\xbcltig', 'books@example.com')",
        "553 5.1.8 Absender ungültig",
        "The mail server refused the sender address: books@example.com.",
    ),
    (
        "(554, b'5.7.1 Message rejected as spam')",
        "554 5.7.1 Message rejected as spam",
        # the old tuple lost the stage (W-231), and no sentence says the
        # server's words; the administrator's line keeps them
        "The mail server refused the email.",
    ),
    (
        "[Errno 111] Connection refused",
        "Connection refused",
        "The server could not be reached",
    ),
    # never run: a stored text that is not a literal stays as it was
    ("(__import__('os'))", "(__import__('os'))", "The email could not be sent."),
]


@pytest.mark.parametrize("stored,words,sentence", OLD_ROWS)
def test_an_old_row_reads_as_plain_words(client, db_session, stored, words, sentence):
    db_session.add(
        EmailLog(
            entity_type="invoice",
            entity_id=0,
            recipient=TO,
            subject="Old",
            status="failed",
            error_message=stored,
        )
    )
    db_session.commit()
    (row,) = client.get("/api/email-log?status=failed").json()["rows"]
    assert row["error_message"] == words
    assert row["failure"].startswith(sentence)
    assert server_words(stored) == words


def test_every_smtplib_exception_is_stored_as_plain_words():
    errors = [
        smtplib.SMTPSenderRefused(530, b"5.7.0 Authentication Required", FROM),
        smtplib.SMTPRecipientsRefused({TO: (550, "5.1.1 Ungültig".encode())}),
        smtplib.SMTPDataError(554, b"5.7.1 Message rejected"),
        smtplib.SMTPConnectError(554, b"no service"),
        smtplib.SMTPHeloError(501, b"bad helo"),
        smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials"),
        smtplib.SMTPResponseException(554, b"5.0.0 Refused"),
        smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
        smtplib.SMTPNotSupportedError("SMTP AUTH extension not supported by server."),
        socket.timeout("timed out"),
        ConnectionRefusedError(111, "Connection refused"),
        socket.gaierror(-2, "Name or service not known"),
    ]
    for error in errors:
        words = server_words(stored_text(error))
        assert words, error
        _plain(words)
        assert "[Errno" not in words
        _plain(failure_sentence(error))
        # the stored text and the exception give the same sentence
        assert failure_sentence(stored_text(error)) == failure_sentence(error)


def test_a_reply_that_repeats_the_sign_in_is_kept_as_it_was_received():
    """Nothing in a reply is changed or hidden: it is an administrator's,
    who set the sign-in. The sentence, which everyone reads, holds none of
    it."""
    import base64

    password = secrets.token_urlsafe(12)
    echoed = base64.b64encode(f"\0books\0{password}".encode()).decode()
    said = f"5.7.8 rejected {echoed} ({password})"
    error = smtplib.SMTPAuthenticationError(535, said.encode())
    text = stored_text(error)
    assert text == f"[auth] 535 {said}"
    assert server_words(text) == f"535 {said}"
    for sentence in (failure_sentence(text), failure_sentence(error)):
        assert sentence == "The server rejected the username or password — check both."


def test_the_retired_pointer_is_gone_from_the_app():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    for path in root.rglob("*.py"):
        assert "email log has the reason" not in path.read_text("utf-8"), path
