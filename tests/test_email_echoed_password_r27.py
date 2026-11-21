"""A mail server that repeats the sign-in in its reply (W-228).

A server may answer a refused send with the sign-in it was given: the
password as typed, its base64, the AUTH PLAIN argument. For a time the
server's words were put in the sentence everyone reads (Recent sends, a
document's Activity and GET /api/email-log, for every role), and then
masked form by form.

The rule now, as in every released version: the server's reply is an
administrator's, who set the sign-in, and is shown as it was received,
with nothing in it changed or hidden, in Recent sends (GET /api/email-log's
`error_message` for an administrator) and in Test Email's answer. Nobody
else is given any of it: every other reader, and every sentence, holds
none of the server's words, so nothing a server repeats can reach them.
Every stored error leaves storage through one reader
(email_errors.FailureReader); a test holds the app to that.

W-231: a row of a release before 2.32 stored the same tuple for a refused
greeting and a refused message, so such a row says neither ("The mail
server refused the email."); a new row keeps its stage.

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
    FailureReader,
    failure_sentence,
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


OLD_REFUSAL = "The mail server refused the email."
# what a reader who is not given the reply is told instead
ASK_AN_ADMINISTRATOR = "An administrator can see why under Settings → Recent sends."


def test_an_old_row_that_echoes_the_password_shows_it_to_an_administrator_alone(
    client, seed_accounts, db_session
):
    password = secrets.token_urlsafe(12)
    _sign_in(db_session, password)
    cust = _customer(client)
    inv = _invoice(client, cust["id"])
    _old_row(db_session, _echo(password), entity_id=inv["id"])

    admin = client.get("/api/email-log?status=failed").json()["rows"][0]
    keeper = _bookkeeper(db_session)
    bk = keeper.get("/api/email-log?status=failed").json()["rows"][0]
    emailed = [
        next(
            e
            for e in _activity(who, "invoice", inv["id"])["events"]
            if e["kind"] == "emailed"
        )
        for who in (client, keeper)
    ]
    # nobody but an administrator, and no sentence, holds any form of it:
    # the bookkeeper's whole row, Activity for both, the sentence for both
    for text in (
        admin["failure"],
        bk["error_message"],
        bk["failure"],
        *(d for e in emailed for d in e["details"]),
        *(e["text"] for e in emailed),
    ):
        _assert_clean(text, password)
    # no server words in the sentence, for anyone
    assert admin["failure"] == bk["failure"] == OLD_REFUSAL
    # where it is all a reader gets, it ends with who can read the reason
    for sentence in (bk["error_message"], *(d for e in emailed for d in e["details"])):
        assert sentence == f"{OLD_REFUSAL} {ASK_AN_ADMINISTRATOR}", sentence
    # the administrator reads the server's reply as it was received
    assert admin["error_message"] == "554 " + _echo(password)


def test_what_settings_holds_today_changes_nothing_in_how_a_row_reads(
    client, db_session
):
    old = secrets.token_urlsafe(12)
    _old_row(db_session, _echo(old))
    before = client.get("/api/email-log?status=failed").json()["rows"][0]
    _sign_in(db_session, secrets.token_urlsafe(12))  # changed since

    admin = client.get("/api/email-log?status=failed").json()["rows"][0]
    bk = _bookkeeper(db_session).get("/api/email-log?status=failed").json()
    # a non-administrator never sees a row's words
    _assert_clean(bk["rows"][0]["error_message"], old)
    _assert_clean(bk["rows"][0]["failure"], old)
    _assert_clean(admin["failure"], old)
    # the reply is not read against the sign-in at all: the same before
    # the change and after it, as it was received
    assert admin["error_message"] == before["error_message"] == "554 " + _echo(old)
    assert admin["failure"] == before["failure"] == OLD_REFUSAL


STAGE_ERRORS = {
    "the greeting": lambda said: smtplib.SMTPConnectError(554, said),
    "EHLO": lambda said: smtplib.SMTPHeloError(550, said),
    "sign-in": lambda said: smtplib.SMTPAuthenticationError(535, said),
    "MAIL FROM": lambda said: smtplib.SMTPSenderRefused(553, said, USER),
    "RCPT": lambda said: smtplib.SMTPRecipientsRefused(
        {"ap@example.test": (550, said)}
    ),
    "the message": lambda said: smtplib.SMTPDataError(554, said),
    "another reply": lambda said: smtplib.SMTPResponseException(554, said),
}


@pytest.mark.parametrize("stage", sorted(STAGE_ERRORS))
def test_no_form_a_server_can_echo_is_in_any_sentence(stage):
    """Whatever a server repeats, at whatever stage: the sentence is ours
    alone, from the exception, from the row stored now and from the row a
    release before 2.32 stored. The reply itself is kept as received."""
    password = secrets.token_urlsafe(12) + "+/="  # quoted forms differ
    canary = "canary" + secrets.token_hex(8)
    said = (
        f"5.7.1 {canary} plain {password} b64 {_b64(password)} plain-arg "
        f"{_b64(chr(0) + USER + chr(0) + password)} user-arg "
        f"{_b64(USER + chr(0) + USER + chr(0) + password)} login-user "
        f"{_b64(USER)} url {quote(password, safe='')} AUTH PLAIN "
        f"{_b64(chr(0) + USER + chr(0) + password)} done"
    )
    error = STAGE_ERRORS[stage](said.encode())
    stored = stored_text(error)
    for sentence in (
        failure_sentence(error),
        failure_sentence(stored),
        failure_sentence(str(error)),
    ):
        _assert_clean(sentence, password)
        assert canary not in sentence and "done" not in sentence, sentence
    # an ordinary reply and this one are kept the same way: as received
    assert said in stored
    assert said in server_words(stored)
    assert said in server_words(str(error))


def test_a_new_row_keeps_the_reply_and_only_an_administrator_reads_it(
    client, db_session
):
    password = secrets.token_urlsafe(12)
    error = smtplib.SMTPDataError(554, _echo(password).encode())
    stored = stored_text(error)
    assert stored == "[data] 554 " + _echo(password)
    _sign_in(db_session, password)
    db_session.add(
        EmailLog(
            entity_type="invoice",
            entity_id=0,
            recipient="ap@example.test",
            subject="New",
            status="failed",
            error_message=stored,
        )
    )
    db_session.commit()
    admin = client.get("/api/email-log?status=failed").json()["rows"][0]
    bk = _bookkeeper(db_session).get("/api/email-log?status=failed").json()
    bk = bk["rows"][0]
    for text in (admin["failure"], bk["failure"], bk["error_message"]):
        _assert_clean(text, password)
    # the sentence holds none of the server's words, for anyone
    assert admin["failure"] == bk["failure"] == "The mail server refused the message."
    assert bk["error_message"] == (
        f"The mail server refused the message. {ASK_AN_ADMINISTRATOR}"
    )
    # the administrator's line is the reply as it was received
    assert admin["error_message"] == "554 " + _echo(password)
    # and the reader gives the reply to an administrator alone
    row = db_session.query(EmailLog).one()
    reader = FailureReader(db_session)
    assert reader.read(row, admin=False).reply is None
    assert reader.read(row, admin=True).reply == "554 " + _echo(password)
    assert reader.read(row, admin=True).reason() == (
        "The mail server refused the message. The reason is under Settings → "
        "Email (SMTP) → Recent sends."
    )


# -- one reader for the stored text ---------------------------------------

_READER = "app/services/email_errors.py"
# writes only: EmailLog(..., error_message=...) on a new row, and a row's
# column set to stored_text(...) (what a write may be is held by
# tests/test_email_failure_rule.py)
_WRITE = re.compile(r"^\s*(?:error_message=|\w+\.error_message = stored_text\()")


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
                '"error_message": failure.reply if admin else failure.reason() or None,'
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
    # a new row keeps its stage, and the sentence is the stage alone
    greeting = stored_text(smtplib.SMTPConnectError(554, b"5.3.2 no service"))
    assert failure_sentence(greeting) == "The mail server refused the connection."
    body = stored_text(smtplib.SMTPDataError(554, b"5.7.1 spam"))
    assert failure_sentence(body) == "The mail server refused the message."


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
def test_the_macs_old_row_shows_the_reply_to_an_administrator_alone(
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
    # a non-administrator, and Activity for every reader, see no server
    # words at all
    assert bk["failure"] == admin["failure"] == OLD_REFUSAL
    details = [bk["error_message"]]
    for who in (bk_client, client):
        seen = _activity(who, "invoice", inv["id"])["events"]
        details += next(e for e in seen if e["kind"] == "emailed")["details"]
    assert len(details) == 3
    for text in details:
        assert text == f"{OLD_REFUSAL} {ASK_AN_ADMINISTRATOR}", text
    for form in forms:
        assert form not in bk_client.get("/api/email-log").text
    # an administrator reads the reply as it was received, whatever the
    # sign-in in Settings is today
    assert admin["error_message"] == f"554 {words}"
