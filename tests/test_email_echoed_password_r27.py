"""Round 27: a password a mail server echoes is never shown (W-228).

A row the previous build stored kept the server's reply as Python wrote it
("(554, b'…')"). A server that repeats the sign-in in its refusal left the
password in that text, and round 27 put the server's words in the
sentence everyone reads: Recent sends, a document's Activity and
GET /api/email-log, for every role. The administrator's line was masked
with today's password alone, so the AUTH PLAIN form, and every form of a
password changed since, showed.

Every stored error now leaves storage through one reader
(email_errors.FailureReader), which scrubs each form of the sign-in it
knows, and any token after AUTH PLAIN / AUTH LOGIN / 334 whatever it
decodes to. A row stored the old way gives no server words in its sentence
to anyone; an administrator's line shows them, scrubbed. A test holds the
app to that one reader.

W-231: the old build stored the same tuple for a refused greeting and a
refused message, so such a row says neither ("The mail server refused the
email."); a new row keeps its precise stage.

W-232: a timeout read "Connection unexpectedly closed: timed out"; it reads
"timed out", and the other socket causes read as plain causes too.

Every credential here is generated per test; none is written down.
"""

import base64
import re
import secrets
import smtplib
import socket
import ssl
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.email_log import EmailLog
from app.models.users import ROLE_BOOKKEEPER, User
from app.services import auth as auth_service
from app.services.email_errors import (
    failure_sentence,
    scrub_secrets,
    server_words,
    stored_text,
    tls_failure_text,
)
from app.services.settings_service import set_setting
from tests.test_document_activity_894 import _activity, _customer, _invoice

USER = "books@example.test"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _forms(password: str, user: str = USER) -> list[str]:
    return [
        password,
        _b64(password),
        _b64(f"\0{user}\0{password}"),
        _b64(f"{user}\0{user}\0{password}"),
        quote(password, safe=""),
    ]


def _old_row(db, words: str, entity_id=0, entity_type="invoice") -> None:
    """A failed row as the previous build stored it: str() of the
    exception, a Python tuple."""
    db.add(
        EmailLog(
            entity_type=entity_type,
            entity_id=entity_id,
            recipient="ap@example.test",
            subject="Old",
            status="failed",
            error_message=str(smtplib.SMTPDataError(554, words.encode())),
        )
    )
    db.commit()


def _bookkeeper(db):
    name = "bk-" + secrets.token_hex(4)
    login = secrets.token_urlsafe(16)
    db.add(
        User(
            username=name,
            display_name=name,
            password_hash=auth_service.hash_password(login),
            role=ROLE_BOOKKEEPER,
            is_active=True,
        )
    )
    db.commit()
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": login})
    assert r.status_code == 200, r.text
    return c


def _sign_in(db, password):
    set_setting(db, "smtp_user", USER)
    set_setting(db, "smtp_password", password)
    db.commit()


def _echo(password):
    plain = _b64(f"\0{USER}\0{password}")
    return (
        f"5.7.1 Message rejected: credentials {USER} / {password} seen, "
        f"token {_b64(password)} seen, AUTH PLAIN {plain} seen"
    )


def _assert_clean(text, password):
    for form in _forms(password):
        assert form not in (text or ""), "a form of the password is shown"


def test_an_old_row_that_echoes_the_password_shows_it_nowhere(
    client, seed_accounts, db_session
):
    password = secrets.token_urlsafe(12)
    _sign_in(db_session, password)
    cust = _customer(client)
    inv = _invoice(client, cust["id"])
    _old_row(db_session, _echo(password), entity_id=inv["id"])

    admin = client.get("/api/email-log?status=failed").json()["rows"][0]
    bk = _bookkeeper(db_session).get("/api/email-log?status=failed").json()
    bk = bk["rows"][0]
    events = _activity(client, "invoice", inv["id"])["events"]
    emailed = next(e for e in events if e["kind"] == "emailed")
    for text in (
        admin["error_message"],
        admin["failure"],
        bk["error_message"],
        bk["failure"],
        *emailed["details"],
    ):
        _assert_clean(text, password)
    # no server words in an old row's sentence, for anyone (W-228, W-231)
    for sentence in (admin["failure"], bk["error_message"], *emailed["details"]):
        assert sentence == "The mail server refused the email.", sentence
    # the administrator still reads the server's words, masked
    assert admin["error_message"].startswith("554 5.7.1 Message rejected")
    assert admin["error_message"].count("••••") == 3, admin["error_message"]


def test_an_old_row_after_the_password_changed(client, db_session):
    old = secrets.token_urlsafe(12)
    _old_row(db_session, _echo(old))
    _sign_in(db_session, secrets.token_urlsafe(12))  # changed since

    admin = client.get("/api/email-log?status=failed").json()["rows"][0]
    bk = _bookkeeper(db_session).get("/api/email-log?status=failed").json()
    # a non-administrator never sees an old row's words
    _assert_clean(bk["rows"][0]["error_message"], old)
    _assert_clean(admin["failure"], old)
    # the AUTH PLAIN token is masked whatever it decodes to
    assert _b64(f"\0{USER}\0{old}") not in admin["error_message"]
    assert "AUTH PLAIN •••• seen" in admin["error_message"]


def test_every_form_a_server_can_echo_is_masked():
    password = secrets.token_urlsafe(12) + "+/="  # quoted forms differ
    login_user = _b64(USER)
    text = (
        f"plain {password} b64 {_b64(password)} plain-arg "
        f"{_b64(chr(0) + USER + chr(0) + password)} user-arg "
        f"{_b64(USER + chr(0) + USER + chr(0) + password)} login-user {login_user} "
        f"url {quote(password, safe='')} done"
    )
    out = scrub_secrets(text, [password], USER)
    _assert_clean(out, password)
    assert login_user not in out
    assert out.endswith("done")
    # any token after AUTH PLAIN, AUTH LOGIN or 334, whatever it decodes to
    other = _b64(secrets.token_urlsafe(9))
    for lead in ("AUTH PLAIN", "auth login", "334"):
        assert scrub_secrets(f"{lead} {other} seen", [], "") == f"{lead} •••• seen"
    # an ordinary reply is left alone
    reply = "554 5.7.1 Message rejected as spam"
    assert scrub_secrets(reply, [password], USER) == reply


def test_a_new_row_is_scrubbed_when_stored_and_again_when_read(client, db_session):
    password = secrets.token_urlsafe(12)
    error = smtplib.SMTPDataError(554, _echo(password).encode())
    stored = stored_text(error, hidden=[password], user=USER)
    _assert_clean(stored, password)
    # a tagged row holding the password (as if written unscrubbed) is still
    # scrubbed on the way out, in the sentence and the line
    _sign_in(db_session, password)
    db_session.add(
        EmailLog(
            entity_type="invoice",
            entity_id=0,
            recipient="ap@example.test",
            subject="New",
            status="failed",
            error_message="[data] 554 5.7.1 " + _echo(password),
        )
    )
    db_session.commit()
    admin = client.get("/api/email-log?status=failed").json()["rows"][0]
    bk = _bookkeeper(db_session).get("/api/email-log?status=failed").json()
    for text in (admin["error_message"], admin["failure"], bk["rows"][0]["failure"]):
        _assert_clean(text, password)
    assert admin["failure"].startswith("The mail server refused the message: 554")


# -- one reader for the stored text ---------------------------------------

_READER = "app/services/email_errors.py"
# writes only: the column, and EmailLog(..., error_message=...) on a new row
_WRITE = re.compile(r"^\s*error_message=")


def test_no_code_reads_the_stored_error_but_the_one_reader():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted((root / "app").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == _READER:
            continue
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if not re.search(r"\berror_message\b", line):
                continue
            if _WRITE.match(line) or line.lstrip().startswith("#"):
                continue
            if rel == "app/models/email_log.py" and "Column(" in line:
                continue
            if rel == "app/routes/email_log.py" and line.strip() == (
                '"error_message": words if admin else failure,'
            ):
                continue  # the API's field, filled from FailureReader
            offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "read EmailLog.error_message only through FailureReader"
    # and no raw SQL reads the table around the model
    for path in (root / "app").rglob("*.py"):
        text = path.read_text("utf-8")
        assert not re.search(r"(?i)\bfrom\s+email_log\b(?!\s+import)", text), path


# -- W-231: the stage an old row cannot know ----------------------------------


def test_an_old_refusal_reads_true_for_either_stage():
    for error in (
        smtplib.SMTPConnectError(554, b"5.3.2 no SMTP service here"),
        smtplib.SMTPDataError(554, b"5.7.1 Message rejected as spam"),
    ):
        old = str(error)  # what the previous build stored
        assert failure_sentence(old) == "The mail server refused the email."
        assert server_words(old).startswith("554 5.")
    # a new row keeps its stage
    greeting = stored_text(smtplib.SMTPConnectError(554, b"5.3.2 no service"))
    assert failure_sentence(greeting) == (
        "The mail server refused the connection: 554 5.3.2 no service."
    )
    body = stored_text(smtplib.SMTPDataError(554, b"5.7.1 spam"))
    assert (
        failure_sentence(body) == "The mail server refused the message: 554 5.7.1 spam."
    )


# -- W-232: a socket failure reads as its plain cause -----------------------


@pytest.mark.parametrize(
    "error,words",
    [
        (
            smtplib.SMTPServerDisconnected("Connection unexpectedly closed: timed out"),
            "timed out",
        ),
        (
            smtplib.SMTPServerDisconnected(
                "Connection unexpectedly closed: [Errno 104] Connection reset by peer"
            ),
            "Connection reset by peer",
        ),
        (
            smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
            "Connection unexpectedly closed",
        ),
        (socket.timeout("timed out"), "timed out"),
        (ConnectionRefusedError(111, "Connection refused"), "Connection refused"),
        (
            ConnectionResetError(104, "Connection reset by peer"),
            "Connection reset by peer",
        ),
        (socket.gaierror(-2, "Name or service not known"), "Name or service not known"),
    ],
)
def test_a_socket_failure_reads_as_its_plain_cause(error, words):
    stored = stored_text(error)
    assert server_words(stored) == words
    # a row the previous build stored (str() of the exception) reads the same
    assert server_words(str(error)) in (words, str(error)) or not str(error)
    assert failure_sentence(stored).startswith("The server could not be reached")


def test_an_old_timeout_row_reads_timed_out():
    assert server_words("Connection unexpectedly closed: timed out") == "timed out"
    assert server_words("[Errno 111] Connection refused") == "Connection refused"


def test_a_server_that_never_greets_reads_timed_out():
    quiet = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    quiet.bind(("127.0.0.1", 0))
    quiet.listen(1)
    try:
        with pytest.raises(Exception) as caught:
            smtplib.SMTP("127.0.0.1", quiet.getsockname()[1], timeout=0.5)
        assert server_words(stored_text(caught.value)) == "timed out"
    finally:
        quiet.close()


def test_a_tls_failure_reads_as_its_plain_cause():
    stored = tls_failure_text(ssl.SSLError("handshake failure"), "mail.example.test")
    assert server_words(stored) == "Secure connection failed (mail.example.test)"


@pytest.mark.parametrize("changed", [False, True])
def test_the_macs_old_row_shows_no_form_of_the_password(
    client, seed_accounts, db_session, changed
):
    """The row as the old build stored it (the Python repr) for a reply
    "554 5.7.1 Rejected: AUTH PLAIN <what the client sent> (password <it>)",
    with the AUTH LOGIN steps echoed too."""
    user = "qa-" + secrets.token_hex(4) + "@example.test"
    password = secrets.token_urlsafe(12)
    plain_arg = _b64(f"\0{user}\0{password}")
    words = (
        f"5.7.1 Rejected: AUTH PLAIN {plain_arg} (password {password}) "
        f"AUTH LOGIN {_b64(user)} 334 {_b64(password)}"
    )
    set_setting(db_session, "smtp_user", user)
    set_setting(
        db_session, "smtp_password", secrets.token_urlsafe(12) if changed else password
    )
    db_session.commit()
    cust = _customer(client)
    inv = _invoice(client, cust["id"])
    _old_row(db_session, words, entity_id=inv["id"])
    stored = db_session.query(EmailLog).one().error_message
    assert stored.startswith("(554, b'5.7.1 Rejected: AUTH PLAIN ")  # the old form

    forms = [password, plain_arg, _b64(user), _b64(password)]
    bk_client = _bookkeeper(db_session)
    bk = bk_client.get("/api/email-log?status=failed").json()["rows"][0]
    admin = client.get("/api/email-log?status=failed").json()["rows"][0]
    seen = _activity(bk_client, "invoice", inv["id"])["events"]
    emailed = next(e for e in seen if e["kind"] == "emailed")
    # a non-administrator, and Activity, see no server words at all
    for text in (bk["error_message"], bk["failure"], *emailed["details"]):
        assert text == "The mail server refused the email.", text
    # an administrator sees the words, every form of the password masked
    line = admin["error_message"]
    assert line.startswith("554 5.7.1 Rejected: AUTH PLAIN ••••"), line
    for form in forms:
        assert form not in line
        assert form not in admin["failure"]
