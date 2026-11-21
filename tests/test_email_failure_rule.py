"""Who is told what about a failed send.

Everyone reads a sentence of ours, chosen by where the send failed and the
reply code, with nothing of the mail server's in it. The server's reply is
an administrator's, in two places: the line under the sentence in Recent
sends (GET /api/email-log's `error_message`) and the answer to Test Email.
It is shown there as it was received; nothing in it is changed or hidden.
Nobody else is given it: no other role, no API token, and no other answer
or page, an administrator's included.

Proved with a canary: a loopback stub puts a random string in its refusal
at every stage of the conversation, and the string must appear in no answer
to anyone but an administrator, and for an administrator in those two
places alone.

Two more things are held here: the email log's stored text is made in one
place and read in one place, and a row of any age reads without an error.

Every sign-in here is generated when the test runs; none is written down.
"""

import ast
import base64
import logging
import re
import secrets
import smtplib
import socket
import threading
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.contacts import Customer
from app.models.email_log import EmailLog
from app.models.invoices import Invoice, InvoiceStatus
from app.models.users import (
    ROLE_ACCOUNTANT,
    ROLE_BOOKKEEPER,
    ROLE_READONLY,
    User,
)
from app.services import auth as auth_service
from app.services import collections as svc
from app.services.email_errors import (
    FailureReader,
    MailTLSError,
    failure_sentence,
    server_words,
    stored_text,
    where_to_see_why,
)
from app.services.settings_service import set_setting

FROM = "books@example.test"
TO = "rule-probe@example.test"
_RANDOM = secrets.SystemRandom()

REFUSED_CONNECTION = "The mail server refused the connection."
REFUSED_MESSAGE = "The mail server refused the message."
NO_SECURE_CONNECTION = (
    "SlowBooks Pro could not set up a secure connection to the mail server, "
    "so nothing was sent and your password was not given to it. Check the "
    "port and the Use TLS setting under Settings → Email (SMTP), or ask your "
    "mail provider. (127.0.0.1)"
)
# every sentence of ours the stubs here can cause (the certificate
# sentences are tests/test_smtp_tls_verify.py's)
SENTENCES = {
    "An address was refused by the mail server. Check the addresses and send again.",
    f"The mail server refused the sender address: {FROM}. Check the From address "
    "under Settings → Email (SMTP).",
    "The mail server asked to try again later.",
    "The server rejected the username or password — check both.",
    "The mail server wants a sign-in, and none is set. Enter the username and "
    "password under Settings → Email (SMTP).",
    "The server could not be reached — check the host, port, and the Use TLS "
    "setting.",
    "The mail server refused the email.",
    REFUSED_CONNECTION,
    REFUSED_MESSAGE,
    NO_SECURE_CONNECTION,
    "The mail server did not accept the sign-in.",
    "The email could not be sent.",
}
POINTER = where_to_see_why(False)
SEE_WHY = where_to_see_why(True)
# the refused-address sentences, naming addresses of our own
_REFUSED_ONE = re.compile(
    r"The address was refused by the mail server: [\w.+-]+@example\.(?:test|org)\. "
    r"Check it and send again\."
    r"|The mail server would not take mail for: [\w.+-]+@example\.(?:test|org)\. "
    r"If the address is right, the server may want a sign-in \(Settings → Email "
    r"\(SMTP\)\) or may not allow mail to it\."
)


# -- a mail server that refuses at one stage, in words we choose ---------------


class RefusingStub:
    """A plain SMTP server on 127.0.0.1. It offers STARTTLS, AUTH PLAIN and
    LOGIN and refuses at `stage` with `code` and the lines `say_no(user,
    password)` returns (the sign-in it was given, so it can repeat it).
    `verbs` is every command it was sent."""

    def __init__(self, stage: str, say_no, code: int = 554):
        self.stage, self.say_no, self.code = stage, say_no, code
        self.verbs: list[str] = []
        self.calls = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
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
            self.calls += 1
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
        given = {"user": "", "password": ""}

        def say(text):
            conn.sendall(text.encode("utf-8") + b"\r\n")

        def refuse():
            lines = self.say_no(given["user"], given["password"])
            for line in lines[:-1]:
                say(f"{self.code}-{line}")
            say(f"{self.code} {lines[-1]}")

        if self.stage == "greeting":
            refuse()
            return
        say("220 stub ESMTP")
        in_data = False
        while True:
            line = f.readline()
            if not line:
                return
            if in_data:
                if line in (b".\r\n", b".\n"):
                    in_data = False
                    refuse() if self.stage == "body" else say("250 2.0.0 queued")
                continue
            text = line.decode(errors="replace").strip()
            verb = text.split(" ", 1)[0].upper()
            self.verbs.append(verb)
            if verb in ("EHLO", "HELO"):
                if self.stage == "ehlo":
                    refuse()
                else:
                    say("250-stub")
                    say("250-STARTTLS")
                    say("250 AUTH PLAIN LOGIN")
            elif verb == "STARTTLS":
                # only the stage that refuses it is ever asked
                refuse()
            elif verb == "AUTH":
                parts = text.split(" ")
                if len(parts) > 2 and parts[1].upper() == "PLAIN":
                    arg = base64.b64decode(parts[2]).split(b"\0")
                    given["user"] = arg[-2].decode()
                    given["password"] = arg[-1].decode()
                refuse() if self.stage == "auth" else say("235 2.7.0 accepted")
            elif verb == "MAIL":
                refuse() if self.stage == "mail" else say("250 2.1.0 OK")
            elif verb == "RCPT":
                refuse() if self.stage == "rcpt" else say("250 2.1.5 OK")
            elif verb == "DATA":
                if self.stage == "data":
                    refuse()
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


# the stages of the conversation, and the code each is refused with
STAGES = {
    "greeting": 554,
    "ehlo": 550,
    "starttls": 454,
    "auth": 535,
    "mail": 553,
    "rcpt": 550,
    "data": 554,
    "body": 554,
}


def _configure(db, port, user="", password="", use_tls="false"):
    for key, value in (
        ("smtp_host", "127.0.0.1"),
        ("smtp_port", str(port)),
        ("smtp_user", user),
        ("smtp_password", password),
        ("smtp_from_email", FROM),
        ("smtp_use_tls", use_tls),
    ):
        set_setting(db, key, value)
    db.commit()


@pytest.fixture
def no_pdfs(monkeypatch):
    """The send is under test, not the render: no document is drawn."""
    import app.routes.donors as donors
    import app.routes.estimates as estimates
    import app.routes.invoices.documents as documents
    import app.routes.reports.receivables as receivables
    from app.services import pdf_service

    for target, name in (
        (svc, "generate_statement_pdf"),
        (svc, "generate_invoice_pdf"),
        (documents, "generate_invoice_pdf"),
        (estimates, "generate_estimate_pdf"),
        (donors, "generate_acknowledgment_letter_pdf"),
        (donors, "generate_giving_statement_pdf"),
        (receivables, "generate_statement_pdf"),
        (receivables, "generate_collection_letter_pdf"),
    ):
        monkeypatch.setattr(target, name, lambda *a, **k: b"%PDF-")
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: b"%PDF-")


def _session(db, role):
    name = f"{role}-{secrets.token_hex(4)}"
    login = secrets.token_urlsafe(16)
    db.add(
        User(
            username=name,
            display_name=name,
            password_hash=auth_service.hash_password(login),
            role=role,
            is_active=True,
        )
    )
    db.commit()
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": login})
    assert r.status_code == 200, r.text
    return c


def _token(client, role):
    r = client.post("/api/tokens", json={"label": f"{role}-bot", "role": role})
    assert r.status_code in (200, 201), r.text
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return api


def _overdue(db):
    c = Customer(name="Mail Probe", email=TO, is_active=True, monthly_statement=True)
    db.add(c)
    db.commit()
    inv = Invoice(
        invoice_number=f"RULE-{c.id}",
        customer_id=c.id,
        status=InvoiceStatus.SENT,
        date=date.today() - timedelta(days=90),
        due_date=date.today() - timedelta(days=45),
        total=Decimal(100),
        balance_due=Decimal(100),
    )
    db.add(inv)
    db.commit()
    return c, inv


TEST_EMAIL = "/api/settings/test-email"


def _detail(text: str) -> str:
    import json

    return json.loads(text)["detail"]


def _every_send(who, ids):
    """Each path that sends mail and answers with what went wrong: Test
    Email, the three documents' Email, a reminder by hand, and the five
    batch sends. Returns every answer's whole text, whatever its status."""
    email = {"recipient": TO, "subject": "Probe", "message": ""}
    answers = []
    for path, body in (
        (TEST_EMAIL, None),
        (f"/api/invoices/{ids['invoice']}/email", email),
        (f"/api/estimates/{ids['estimate']}/email", email),
        (f"/api/donors/gifts/invoice/{ids['gift']}/acknowledgment/email", {}),
        (f"/api/collections/invoices/{ids['overdue']}/remind", None),
        ("/api/collections/statements/run", {"force": True}),
        ("/api/collections/reminders/run", {}),
        ("/api/reports/batch-email-statements", None),
        ("/api/reports/collection-letters", {"letter_type": "30", "send_email": True}),
        ("/api/donors/giving-statements/batch-email", {"year": 2026}),
    ):
        r = who.post(path, json=body) if body is not None else who.post(path)
        answers.append((path, r.status_code, r.text))
    return answers


def _every_read(who, ids):
    """The email log, and the Activity of each document and customer a
    send was made for."""
    answers = []
    for path in (
        "/api/email-log?limit=200",
        "/api/email-log?limit=200&status=failed",
        f"/api/activity?type=invoice&id={ids['invoice']}&tz_offset=0",
        f"/api/activity?type=invoice&id={ids['overdue']}&tz_offset=0",
        f"/api/activity?type=invoice&id={ids['gift']}&tz_offset=0",
        f"/api/activity?type=estimate&id={ids['estimate']}&tz_offset=0",
        *(f"/api/activity?type=customer&id={c}&tz_offset=0" for c in ids["customers"]),
        "/api/collections/invoices",
        "/api/collections/reminders/due",
        "/api/collections/statements/due",
    ):
        r = who.get(path)
        answers.append((path, r.status_code, r.text))
    return answers


def _books(client, db_session, seed_accounts, seed_customer):
    """A document of each kind that can be emailed, and the customers the
    batch sends reach."""
    from tests.test_document_activity_894 import _customer, _invoice
    from tests.test_donor_documents import _giving_year, _receipt
    from tests.test_estimate_void_1128 import _estimate

    svc.seed_default_rules(db_session)
    mail_probe, overdue = _overdue(db_session)
    cust = _customer(client)
    client.put(f"/api/customers/{cust['id']}", json={"email": TO})
    inv = _invoice(client, cust["id"])
    est = _estimate(client, cust["id"])
    _giving_year(client, seed_accounts, seed_customer)
    gift = _receipt(client, seed_customer.id, date="2026-07-01").json()["invoice"]
    return {
        "invoice": inv["id"],
        "estimate": est["id"],
        "gift": gift["id"],
        "overdue": overdue.id,
        "customers": [mail_probe.id, cust["id"], seed_customer.id],
    }


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_the_servers_words_reach_nobody_but_an_administrator(
    client, db_session, seed_accounts, seed_customer, no_pdfs, caplog, stage
):
    canary = "canary" + secrets.token_hex(12)
    user = "u" + secrets.token_hex(5) + "@example.test"
    password = secrets.token_urlsafe(15)
    code = STAGES[stage]
    said = f"5.7.1 refused {canary} here"
    server = RefusingStub(stage, lambda u, p: [said], code)
    caplog.set_level(logging.DEBUG)
    try:
        _configure(
            db_session,
            server.port,
            user,
            password,
            use_tls="true" if stage == "starttls" else "false",
        )
        ids = _books(client, db_session, seed_accounts, seed_customer)
        others = {
            "bookkeeper": _session(db_session, ROLE_BOOKKEEPER),
            "accountant": _session(db_session, ROLE_ACCOUNTANT),
            "read-only": _session(db_session, ROLE_READONLY),
            "token bookkeeper": _token(client, ROLE_BOOKKEEPER),
            "token accountant": _token(client, ROLE_ACCOUNTANT),
            "token read-only": _token(client, ROLE_READONLY),
        }
        seen = {}
        # the others send first, so their answers are of real failures
        for name, who in others.items():
            seen[name] = _every_send(who, ids)
        mine = _every_send(client, ids)
        for name, who in others.items():
            seen[name] += _every_read(who, ids)
        admin_reads = _every_read(client, ids)
    finally:
        server.close()

    # two bookkeepers (a sign-in and a token) and the administrator each
    # made every send; nobody else may send at all
    assert server.calls >= 25, "the stub was hardly spoken to"
    if stage == "starttls":
        # nothing more is said to a server that gave no secure connection
        assert not {"AUTH", "MAIL", "RCPT", "DATA"} & set(server.verbs)
    reply = f"{code} {said}"

    # -- everyone who is not an administrator: no word of the server's ----
    for name, answers in seen.items():
        for path, status, text in answers:
            assert canary not in text, f"{name} was given the server's words: {path}"
            assert password not in text
            assert status != 500, (name, path, text)
    # the bookkeeper really was told of failures, in our words: both
    # documents, the reminder by hand and the five batch sends
    told = [t for _, status, t in seen["bookkeeper"] if "could not be sent" in t]
    assert len(told) >= 8, seen["bookkeeper"]
    # Test Email is an administrator's: nobody else may ask
    for name, answers in seen.items():
        assert answers[0][:2] == (TEST_EMAIL, 403), (name, answers[0])

    # -- an administrator: the reply, in two places and no third ----------
    by_path = {path: text for path, _, text in mine}
    # 1. Test Email's answer: the sentence, then the reply as received
    # (a refused recipient's reply stands after the address we sent to)
    lead = f"{FROM}: " if stage == "rcpt" else ""
    assert f"The mail server's reply: {lead}{reply}." in by_path[TEST_EMAIL]
    for path, _, text in mine:
        if path != TEST_EMAIL:
            assert canary not in text, f"an answer to a send gave the reply: {path}"
    # 2. Recent sends: `error_message`, and no other field of any row
    log = client.get("/api/email-log?limit=200").json()["rows"]
    assert len(log) >= 25
    for row in log:
        assert row["status"] == "failed"
        assert row["error_message"].endswith(reply), row["error_message"]
        rest = {k: v for k, v in row.items() if k != "error_message"}
        assert canary not in repr(rest), "a field other than error_message"
    # Activity carries no server words for any reader, administrators too
    for path, status, text in admin_reads[2:]:
        assert status == 200, (path, text)
        assert canary not in text, f"an administrator's page gave the reply: {path}"

    # -- the server log: no reply text, no password ------------------------
    assert canary not in caplog.text, "a log line carries the server's words"
    assert password not in caplog.text

    # -- what a non-administrator reads in the log is a sentence of ours,
    #    and one that does not say why ends with who can read the reason --
    rows = others["bookkeeper"].get("/api/email-log?limit=200&status=failed").json()
    assert len(rows["rows"]) == len(log)
    for row in rows["rows"]:
        assert row["failure"] in SENTENCES or _REFUSED_ONE.fullmatch(
            row["failure"]
        ), row["failure"]
        shown = row["error_message"]
        assert shown in (row["failure"], f"{row['failure']} {POINTER}"), shown
        assert row["note"] is None
    fixed = {
        "greeting": REFUSED_CONNECTION,
        "ehlo": REFUSED_CONNECTION,
        "starttls": NO_SECURE_CONNECTION,
        "auth": "The server rejected the username or password — check both.",
        "mail": f"The mail server refused the sender address: {FROM}. Check the "
        "From address under Settings → Email (SMTP).",
        "data": REFUSED_MESSAGE,
        "body": REFUSED_MESSAGE,
    }.get(stage)
    if fixed:
        assert {r["failure"] for r in rows["rows"]} == {fixed}
        assert _detail(by_path[TEST_EMAIL]) == (
            f"Test email failed to send. {fixed} The mail server's reply: "
            f"{reply}. The full list is under Settings → Email (SMTP) → Recent "
            "sends."
        )
    if stage in ("greeting", "ehlo", "data", "body"):
        # no sentence of its own for the reason: the stage, and nothing
        # after it but where the reason can be read, for either reader
        assert all(r["error_message"] == f"{fixed} {POINTER}" for r in rows["rows"])
        invoice_email = f"/api/invoices/{ids['invoice']}/email"
        assert _detail(by_path[invoice_email]) == (
            f"Email could not be sent. {fixed} {SEE_WHY}"
        )
        theirs = {p: t for p, _, t in seen["bookkeeper"]}
        assert _detail(theirs[invoice_email]) == (
            f"Email could not be sent. {fixed} {POINTER}"
        )


def test_an_api_token_is_never_given_the_servers_words():
    """Whatever role a token carries; an administrator's token is refused
    at the door, and nothing here depends on that."""
    from app.routes._roles import reads_mail_server_words

    def request(role=None, token_role=None):
        state = SimpleNamespace(
            token_principal={"role": token_role} if token_role else None
        )
        return SimpleNamespace(state=state, session={"role": role} if role else {})

    assert reads_mail_server_words(request(role="admin"))
    assert reads_mail_server_words(request())  # the single-user install
    assert not reads_mail_server_words(request(role="bookkeeper"))
    assert not reads_mail_server_words(request(role="accountant"))
    assert not reads_mail_server_words(request(role="readonly"))
    assert not reads_mail_server_words(request(role="admin", token_role="admin"))
    assert not reads_mail_server_words(request(token_role="bookkeeper"))


# -- the reply is shown as it was received ---------------------------------------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_a_reply_that_repeats_the_sign_in_is_an_administrators_as_received(
    client, db_session, seed_accounts, no_pdfs
):
    """A server may repeat the sign-in it was given. The administrator,
    who set it, reads the reply as it came, in Recent sends and in Test
    Email's answer; nothing in it is changed. Nobody else, and no other
    answer or page, holds any of it."""
    from tests.test_document_activity_894 import _activity, _customer, _invoice

    user = "u" + secrets.token_hex(5) + "@example.test"
    password = secrets.token_urlsafe(15)

    def say_no(u, p):
        return [
            f"5.7.1 Rejected sign-in {u} / {p} / AUTH PLAIN {_b64(f'{u}{chr(0)}{u}{chr(0)}{p}')}"
        ]

    server = RefusingStub("body", say_no)
    try:
        _configure(db_session, server.port, user, password)
        cust = _customer(client)
        inv = _invoice(client, cust["id"])
        keeper = _session(db_session, ROLE_BOOKKEEPER)
        email = {"recipient": TO, "subject": "Probe", "message": ""}
        theirs = keeper.post(f"/api/invoices/{inv['id']}/email", json=email)
        mine = client.post(f"/api/invoices/{inv['id']}/email", json=email)
        tested = client.post(TEST_EMAIL)
    finally:
        server.close()
    token = _b64(f"{user}\0{user}\0{password}")
    reply = f"554 5.7.1 Rejected sign-in {user} / {password} / AUTH PLAIN {token}"

    # the two places, word for word
    assert tested.json()["detail"] == (
        f"Test email failed to send. {REFUSED_MESSAGE} The mail server's reply: "
        f"{reply}. The full list is under Settings → Email (SMTP) → Recent sends."
    )
    rows = client.get("/api/email-log").json()["rows"]
    assert len(rows) == 3
    assert [r["error_message"] for r in rows] == [reply] * 3
    # what is stored is the reply itself, behind our tag
    assert {r.error_message for r in db_session.query(EmailLog)} == {f"[data] {reply}"}

    # everything else, for the administrator too, is the sentence alone
    assert (
        mine.json()["detail"] == f"Email could not be sent. {REFUSED_MESSAGE} {SEE_WHY}"
    )
    assert theirs.json()["detail"] == (
        f"Email could not be sent. {REFUSED_MESSAGE} {POINTER}"
    )
    pages = [
        theirs.text,
        mine.text,
        keeper.get("/api/email-log").text,
        *(repr(_activity(who, "invoice", inv["id"])) for who in (client, keeper)),
        *(repr({k: v for k, v in r.items() if k != "error_message"}) for r in rows),
    ]
    for text in pages:
        for piece in (password, token, _b64(password), "Rejected sign-in", "••••"):
            assert piece not in text, piece
    for who in (client, keeper):
        events = _activity(who, "invoice", inv["id"])["events"]
        details = [d for e in events if e["kind"] == "emailed" for d in e["details"]]
        assert details == [f"{REFUSED_MESSAGE} {POINTER}"] * 2


# -- what a row stores ---------------------------------------------------------------


def _data(words, code=554):
    return smtplib.SMTPDataError(
        code, words if isinstance(words, bytes) else words.encode()
    )


STORED = [
    # the code and the decoded words, on one line
    (
        _data("5.7.1 Message rejected\nas spam"),
        "[data] 554 5.7.1 Message rejected as spam",
    ),
    (
        smtplib.SMTPConnectError(554, b"5.3.2 no SMTP service here"),
        "[connect] 554 5.3.2 no SMTP service here",
    ),
    (
        smtplib.SMTPHeloError(550, b"5.7.1 Client host rejected"),
        "[helo] 550 5.7.1 Client host rejected",
    ),
    (
        smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials"),
        "[auth] 535 5.7.8 Bad credentials",
    ),
    (
        smtplib.SMTPSenderRefused(553, b"5.1.8 Sender rejected", FROM),
        f"[sender {FROM}] 553 5.1.8 Sender rejected",
    ),
    # refused recipients, one per line; our tag keeps who and the codes
    (
        smtplib.SMTPRecipientsRefused(
            {
                "a@example.test": (550, b"5.1.1 User unknown"),
                "b@example.test": (451, b"4.7.1 Try again"),
            }
        ),
        "[recipients a@example.test 550 5.1.1, b@example.test 451 4.7.1] "
        "a@example.test: 550 5.1.1 User unknown\nb@example.test: 451 4.7.1 Try again",
    ),
    # bytes read as UTF-8, and what is not UTF-8 replaced
    (
        _data("5.1.8 Absender ungültig — nicht erlaubt"),
        "[data] 554 5.1.8 Absender ungültig — nicht erlaubt",
    ),
    (_data(b"5.7.1 caf\xe9 \xff refused"), "[data] 554 5.7.1 caf� � refused"),
    # a failure that was not a reply: its plain cause, never "[Errno …]"
    (TimeoutError("timed out"), "[socket] timed out"),
    (socket.timeout(), "[socket] timed out"),
    (ConnectionRefusedError(111, "Connection refused"), "[socket] Connection refused"),
    (
        ConnectionResetError(104, "Connection reset by peer"),
        "[socket] Connection reset by peer",
    ),
    (
        socket.gaierror(-2, "Name or service not known"),
        "[socket] Name or service not known",
    ),
    (
        smtplib.SMTPServerDisconnected("Connection unexpectedly closed: timed out"),
        "[disconnected] timed out",
    ),
    (
        smtplib.SMTPServerDisconnected(
            "Connection unexpectedly closed: [Errno 104] Connection reset by peer"
        ),
        "[disconnected] Connection reset by peer",
    ),
    (
        smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
        "[disconnected] Connection unexpectedly closed",
    ),
    # ... nor a Python class name, nor Python's own text for a fault of ours
    (OSError(), "[socket] Connection failed"),
    (RuntimeError("boom at 0x7f"), "[local] Unexpected error"),
    (
        UnicodeEncodeError("ascii", "naïve", 2, 3, "ordinal not in range(128)"),
        "[local] Unexpected error",
    ),
    # words of our own
    (
        "Too many Cc addresses: at most 50 on one email.",
        "[local] Too many Cc addresses: at most 50 on one email.",
    ),
    (
        MailTLSError(
            "Certificate not verified (mail.example.test): certificate has expired"
        ),
        "Certificate not verified (mail.example.test): certificate has expired",
    ),
]


@pytest.mark.parametrize(
    "error,stored", STORED, ids=[s.split("\n")[0][:48] for _, s in STORED]
)
def test_a_new_row_stores_readable_text(error, stored):
    made = stored_text(error)
    assert made == stored
    # what an administrator is shown is that text without our tag
    shown = server_words(made)
    assert shown == re.sub(r"^\[[^\]\n]*\] ", "", stored)
    for piece in ("[Errno", "[WinError", "b'", "Error(", "Error:", "0x7f", "ordinal"):
        assert piece not in shown, piece
    # and the exception and its row read as the same sentence
    assert failure_sentence(made) == failure_sentence(error)


def test_mail_that_is_not_set_up_stores_what_it_always_has():
    assert stored_text("SMTP not configured", stage="setup") == "SMTP not configured"


def test_what_is_stored_is_cut_at_its_limit():
    stored = stored_text(_data("5.7.1 " + "x " * 1500))
    assert len(stored) == 2000 and stored.endswith("…")
    assert failure_sentence(stored) == REFUSED_MESSAGE


def test_a_long_list_of_addresses_never_cuts_our_tag():
    """The row's limit falls in the server's words: the tag stays whole,
    so the sentence still names who was refused and no tag is ever shown."""
    refused = {
        f"{'a' * 60}{i}@example.test": (550, b"5.1.1 User unknown " + b"x" * 80)
        for i in range(120)
    }
    error = smtplib.SMTPRecipientsRefused(refused)
    stored = stored_text(error)
    assert len(stored) == 2000 and stored.endswith("…")
    first = next(iter(refused))
    assert stored.startswith(f"[recipients {first} 550 5.1.1, ")
    sentence = failure_sentence(stored)
    assert sentence.startswith(f"The address was refused by the mail server: {first}, ")
    assert sentence.endswith(". Check it and send again.")
    shown = server_words(stored)
    assert shown.startswith(f"{first}: 550 5.1.1 User unknown ")
    assert "[" not in shown and "]" not in shown
    # an address no mail system would take is cut, not let through whole
    absurd = "b" * 5000 + "@example.test"
    stored = stored_text(smtplib.SMTPRecipientsRefused({absurd: (550, b"5.1.1 no")}))
    assert len(stored) <= 2000
    assert failure_sentence(stored) == (
        f"The address was refused by the mail server: {'b' * 320}. Check it and "
        "send again."
    )
    sender = stored_text(smtplib.SMTPSenderRefused(553, b"5.1.8 no", absurd))
    assert sender == f"[sender {'b' * 320}] 553 5.1.8 no"


# -- rows of every age read ----------------------------------------------------------

_UNLIKELY = [
    None,
    "",
    " ",
    "[",
    "]",
    "[]",
    "[data]",
    "[data] ",
    "[data] not a code",
    "[recipients]",
    "[recipients] ",
    "[recipients ] : 550",
    "[recipients ; sent ] x",
    "[recipients a@example.test] a@example.test",
    "[recipients a@example.test 999 9.9.9, , ; sent , ] zzz",
    "[sender] 553",
    "[sender ] ",
    "[starttls] 454",
    "[starttls ] ",
    "[auth letters]",
    "[auth none-offered]",
    "[auth loop] ",
    "[connect not-smtp]",
    "[socket]",
    "[local]",
    "[other]",
    "[2 data] 554 5.7.1 x",
    "[nonsense] 554",
    "(",
    "{",
    "(1,",
    "()",
    "{}",
    "{'a': 1}",
    "(550,)",
    "(550, 'words that are not bytes')",
    "('x', b'y')",
    "(550, b'x', 5)",
    "(550, b'x', 'a@example.test', 'more')",
    "{'a@example.test': 'not a pair'}",
    "{1: (550, b'x')}",
    "{'a@example.test': (None, b'x')}",
    "(-1, b'')",
    "(99999, b'x')",
    "(__import__('os').system('true'))",
    "Certificate not verified",
    "Certificate not verified (): ",
    "Secure connection failed (a)b): x",
    "[Errno",
    "[Errno 111]",
    "\x00\x01\x02",
    "5" * 30000,
    "(" * 5000,
    "[data] " + "9" * 5000,
]


def _row(stored, status="failed"):
    return SimpleNamespace(
        status=status, error_message=stored, recipient=TO, cc=None, bcc=None
    )


# rows as releases before 2.32 stored them (str() of the exception), and as
# the test builds of 2.32.0 did (a stage tag with no addresses in it; some
# with a run of "•" where a build had hidden part of a reply)
def _earlier_rows(canary):
    said = f"5.7.1 refused {canary} here".encode()
    errors = [
        smtplib.SMTPConnectError(554, said),
        smtplib.SMTPHeloError(550, said),
        smtplib.SMTPAuthenticationError(535, said),
        smtplib.SMTPSenderRefused(553, said, FROM),
        smtplib.SMTPRecipientsRefused(
            {TO: (550, said), "cc@example.test": (451, said)}
        ),
        smtplib.SMTPDataError(554, said),
        smtplib.SMTPResponseException(421, said),
    ]
    rows = [str(e) for e in errors]
    words = said.decode()
    rows += [
        f"[connect] 554 {words}",
        f"[helo] 550 {words}",
        f"[auth] 535 {words}",
        f"[sender {FROM}] 553 {words}",
        f"[recipients] {TO}: 550 {words}\ncc@example.test: 451 {words}",
        f"[recipients] : 550 {words}: 421 {canary}",
        f"[data] 554 {words}",
        f"[response] 421 {words}",
        f"[data] 554 5.7.1 Rejected: AUTH PLAIN •••• {canary} seen ••••",
        f"Server AUTH mechanism infinite loop. Last response: (334, b'{canary}')",
        f"(554, b'5.7.1 {canary} with a quote \\' and \\xc3\\xbc')",
    ]
    return rows


def test_a_row_of_any_age_reads_without_an_error(db_session):
    set_setting(db_session, "smtp_host", "127.0.0.1")
    set_setting(db_session, "smtp_from_email", FROM)
    db_session.commit()
    reader = FailureReader(db_session)
    canary = "canary" + secrets.token_hex(8)
    pieces = ["[", "]", "(", ")", "{", "}", "'", '"', "b'", ",", ":", ";", " ", "\n"]
    pieces += ["550", "5.7.1", "a@example.test", "sent", "recipients", "data", "\\x"]
    random_rows = [
        "".join(_RANDOM.choice(pieces) for _ in range(_RANDOM.randrange(1, 40)))
        for _ in range(3000)
    ]
    earlier = _earlier_rows(canary)
    for stored in [*_UNLIKELY, *earlier, *random_rows]:
        for status in ("failed", "sent"):
            for admin in (False, True):
                seen = reader.read(_row(stored, status), admin)
                assert (seen.sentence is None) == (status == "sent")
                for text in (seen.reason(), seen.with_reply(), seen.note or ""):
                    assert isinstance(text, str)
                if not admin:
                    assert seen.reply is None
                    assert seen.with_reply() == (seen.sentence or "")
                elif stored:
                    assert isinstance(seen.reply, str)
    # the earlier builds' rows: a sentence of ours for everyone, with none
    # of the server's words; the words are the administrator's line
    for stored in earlier:
        other = reader.read(_row(stored), admin=False)
        mine = reader.read(_row(stored), admin=True)
        assert other.sentence == mine.sentence
        assert canary not in other.reason(), stored
        assert canary not in mine.reason(), stored
        assert other.sentence in SENTENCES or _REFUSED_ONE.fullmatch(
            other.sentence
        ), other.sentence
        assert canary in mine.reply, stored
        for piece in ("b'", "(5", "{'", "\\x"):
            if stored[0] in "({":
                assert piece not in mine.reply, (piece, mine.reply)


def test_a_row_of_a_release_before_2_32_is_read_as_well_as_it_allows():
    """Python's tuple and dict are read back as the reply they held; the
    stage a tuple cannot say is not guessed."""
    read = [
        (
            "(535, b'5.7.8 Username and Password not accepted')",
            "535 5.7.8 Username and Password not accepted",
            "The server rejected the username or password — check both.",
        ),
        (
            "(554, b'5.7.1 Message rejected')",
            "554 5.7.1 Message rejected",
            "The mail server refused the email.",
        ),
        (
            f"{{'{TO}': (550, b'5.1.1 User unknown')}}",
            f"{TO}: 550 5.1.1 User unknown",
            f"The address was refused by the mail server: {TO}. Check it and send again.",
        ),
        (
            f"(553, b'5.1.8 Absender ung\\xc3\\xbcltig', '{FROM}')",
            "553 5.1.8 Absender ungültig",
            f"The mail server refused the sender address: {FROM}. Check the From "
            "address under Settings → Email (SMTP).",
        ),
        (
            "[Errno 111] Connection refused",
            "Connection refused",
            "The server could not be reached — check the host, port, and the Use TLS setting.",
        ),
        (
            "SMTP not configured",
            "SMTP not configured",
            "SMTP is not configured — fill in the fields under Settings → Email (SMTP).",
        ),
        # what is not simple to tidy is given as it is stored
        (
            "Server AUTH mechanism infinite loop. Last response: (334, b'UGFzc3dvcmQ6')",
            "Server AUTH mechanism infinite loop. Last response: (334, b'UGFzc3dvcmQ6')",
            "The email could not be sent.",
        ),
    ]
    for stored, shown, sentence in read:
        assert server_words(stored) == shown
        assert failure_sentence(stored) == sentence


# -- one way in, one way out ---------------------------------------------------------

_APP = Path(__file__).resolve().parents[1] / "app"
_ERRORS = "app/services/email_errors.py"
_LOG_ROUTE = "app/routes/email_log.py"
_API_FIELD = '"error_message": failure.reply if admin else failure.reason() or None,'


def _calls_stored_text(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "stored_text"
    )


def _sources():
    root = _APP.parent
    for path in sorted(_APP.rglob("*.py")):
        yield path.relative_to(root).as_posix(), path.read_text("utf-8")


def test_every_writer_of_the_stored_text_goes_through_stored_text():
    """Whatever sets EmailLog.error_message sets it to stored_text(...);
    nothing writes the column another way."""
    offenders, writers = [], 0
    for rel, source in _sources():
        if "error_message" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.keyword) and node.arg == "error_message":
                writers += 1
                if not _calls_stored_text(node.value):
                    offenders.append(where)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "error_message"
                    ):
                        writers += 1
                        if not _calls_stored_text(node.value):
                            offenders.append(where)
    assert not offenders, "set EmailLog.error_message only to stored_text(...)"
    # send_email's rows (a refused copy among them), and the invoice route's
    assert writers == 5


def test_nothing_reads_the_stored_text_but_the_one_reader():
    """Outside app/services/email_errors.py no code names the column but
    to write it (above), to define it, and to fill the API's field from
    the reader: not as an attribute, not by name in a string (getattr, a
    dict of columns), and not in SQL written by hand."""
    offenders = []
    for rel, source in _sources():
        if rel == _ERRORS:
            continue
        tree = ast.parse(source)
        writes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                writes.update(id(t) for t in node.targets)
        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Attribute) and node.attr == "error_message":
                if id(node) not in writes:
                    offenders.append(f"{where} reads .error_message")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if "error_message" in text and not (
                    rel == _LOG_ROUTE and text == "error_message"
                ):
                    offenders.append(f"{where} names the column in a string")
                # the table in SQL written by hand, which would read every
                # column around the model
                if re.search(r"(?i)\b(from|join|update|into)\s+email_log\b", text):
                    offenders.append(f"{where} reaches the table in SQL")
    assert not offenders, offenders
    # the model defines the column and nothing else there touches it
    model = (_APP / "models/email_log.py").read_text("utf-8")
    assert model.count("error_message") == 1
    assert "error_message = Column(Text, nullable=True)" in model
    # the API's field is filled from the reader, on one line
    route = (_APP / "routes/email_log.py").read_text("utf-8")
    lines = [ln.strip() for ln in route.splitlines() if '"error_message"' in ln]
    assert lines == [_API_FIELD]
    assert "failure = reader.read(r, admin)" in route


def test_only_two_callers_ask_the_reader_for_the_reply():
    """`.reply` and `.with_reply()` are the server's words: the log's API
    field and Test Email's answer use them, and nothing else in the app
    that reads the log."""
    users = {}
    for rel, source in _sources():
        if rel == _ERRORS:
            continue
        # only code that asks the reader can hold what it returns
        if "FailureReader" not in source and "last_send_failure" not in source:
            continue
        users[rel] = len(re.findall(r"\.(?:reply\b|with_reply\()", source))
    assert users == {_LOG_ROUTE: 1, "app/services/activity.py": 0}
    # Test Email's answer is made in email_errors, by the one function that
    # calls with_reply, and the settings route is its one caller
    errors = (_APP / "services/email_errors.py").read_text("utf-8")
    assert errors.count(".with_reply()") == 1
    callers = [
        rel for rel, source in _sources() if "failed_test_email_detail(" in source
    ]
    assert callers == ["app/routes/settings.py", _ERRORS]
