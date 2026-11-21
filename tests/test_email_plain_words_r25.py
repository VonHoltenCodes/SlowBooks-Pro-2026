"""Round 25: a failed send reads as plain sentences, never Python's text.

W-216: Test Email's answer to a 530 started with the server's reply mangled
out of the SMTPSenderRefused tuple ("…auth', 'from@x The mail server wants a
sign-in…"). Each smtplib failure is raised here through every path that
words one (Test Email, a document's Email button, a batch of statements,
collection letters) and no answer may carry a tuple, a b'' or a quote of it.

W-217: Use TLS off, a username set, and a server that offers AUTH only
after STARTTLS (Gmail on 587) read "SMTP AUTH extension not supported by
server."; it now says to turn on Use TLS, and nothing is sent.

W-218: a failed collection letter reads "could not be sent" like the other
four batch sends (tests/test_batch_send_reason_w211.py).

Since then the rule for the mail server's own words: they are in no
sentence. Test Email, an administrator's, answers with the sentence and
then the server's reply as plain text; every other answer is the sentence
alone, ending with where the reason can be read when it does not say why.
"""

import secrets
import smtplib

import pytest

from tests.test_email_log import _invoice
from tests.test_smtp_tls_verify import StubSMTP, _configure

FROM = "books@example.test"
GMAIL_530 = (
    b"5.7.0 Authentication Required. Learn more at\n5.7.0 "
    b"https://support.example.invalid/auth"
)

FAILURES = {
    "sender-530": smtplib.SMTPSenderRefused(530, GMAIL_530, FROM),
    "sender-553": smtplib.SMTPSenderRefused(553, b"5.7.1 Sender rejected", FROM),
    "recipients": smtplib.SMTPRecipientsRefused(
        {"a@b.com": (550, b"5.1.1 No such user")}
    ),
    "response-554": smtplib.SMTPResponseException(554, b"5.7.1 Message rejected"),
    "response-421": smtplib.SMTPResponseException(421, b"4.7.0 Try later"),
    "auth-535": smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials"),
    "data-550": smtplib.SMTPDataError(550, b"5.7.1 Content refused"),
}

# Python's wrapping, any piece of which in an answer is W-216
RAW = ("b'", 'b"', "', '", "{'", "('", "(5", "(4", "\\n")
# each failure's reply as the plain text the row keeps: what Test Email
# gives an administrator after the sentence, and no other answer gives
REPLIES = {
    "sender-530": (
        "530 5.7.0 Authentication Required. Learn more at 5.7.0 "
        "https://support.example.invalid/auth"
    ),
    "sender-553": "553 5.7.1 Sender rejected",
    "recipients": "a@b.com: 550 5.1.1 No such user",
    "response-554": "554 5.7.1 Message rejected",
    "response-421": "421 4.7.0 Try later",
    "auth-535": "535 5.7.8 Bad credentials",
    "data-550": "550 5.7.1 Content refused",
}
# a refusal with no sentence of its own is the stage and nothing after it
REFUSED = "The mail server refused the message."
NO_SENTENCE_OF_ITS_OWN = ("response-554", "data-550")
SEE_WHY = "The reason is under Settings → Email (SMTP) → Recent sends."


def _fake_smtp(error):
    class Fake:
        def __init__(self, *a, **k):
            pass

        def ehlo_or_helo_if_needed(self):
            pass

        def has_extn(self, name):
            return name in ("starttls", "auth")

        def starttls(self, context=None):
            pass

        def ehlo(self):
            pass

        def login(self, user, password):
            if isinstance(error, smtplib.SMTPAuthenticationError):
                raise error

        def sendmail(self, *a, **k):
            raise error

        def quit(self):
            pass

        def close(self):
            pass

    return Fake


def _settings(db):
    _configure(db, 587, secrets.token_urlsafe(12))


def _plain(text, name=None):
    """No piece of Python's wrapping and, for a failure by name, nothing of
    the server's reply: the sentence alone."""
    for piece in RAW:
        assert piece not in text, (piece, text)
    if name is not None:
        assert "5.7." not in text and "5.1.1" not in text, text
        assert REPLIES[name] not in text, text
        if name in NO_SENTENCE_OF_ITS_OWN:
            assert f"{REFUSED} {SEE_WHY}" in text, text


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_test_email_says_one_plain_sentence(client, db_session, monkeypatch, name):
    _settings(db_session)
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp(FAILURES[name]))
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Test email failed to send. The ")
    # the sentence, then the server's reply as plain text, then the pointer
    _plain(detail)
    assert detail.endswith(
        f" The mail server's reply: {REPLIES[name]}. The full list is under "
        "Settings → Email (SMTP) → Recent sends."
    ), detail
    assert detail.count(REPLIES[name]) == 1
    if name == "sender-530":
        assert detail == (
            "Test email failed to send. The mail server wants a sign-in, and "
            "none is set. Enter the username and password under Settings → "
            "Email (SMTP). The mail server's reply: 530 5.7.0 Authentication "
            "Required. Learn more at 5.7.0 https://support.example.invalid/auth. "
            "The full list is under Settings → Email (SMTP) → Recent sends."
        )
    if name in NO_SENTENCE_OF_ITS_OWN:
        assert detail.startswith(f"Test email failed to send. {REFUSED} The mail ")


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_a_single_send_says_one_plain_sentence(
    client, db_session, seed_accounts, monkeypatch, name
):
    import app.routes.invoices.documents as documents

    inv = _invoice(client)
    _settings(db_session)
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp(FAILURES[name]))
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "a@b.com", "subject": "Invoice", "message": ""},
    )
    assert r.status_code == 502, r.text
    _plain(r.json()["detail"], name)


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_batch_sends_say_one_plain_sentence(
    client, db_session, seed_accounts, monkeypatch, name
):
    import app.routes.reports.receivables as receivables
    from app.models.invoices import Invoice, InvoiceStatus

    inv = _invoice(client)
    row = db_session.get(Invoice, inv["id"])
    row.status = InvoiceStatus.SENT
    db_session.commit()
    _settings(db_session)
    for fn in ("generate_statement_pdf", "generate_collection_letter_pdf"):
        monkeypatch.setattr(receivables, fn, lambda *a, **k: b"%PDF-")
    monkeypatch.setattr(smtplib, "SMTP", _fake_smtp(FAILURES[name]))
    r = client.post("/api/reports/batch-email-statements")
    assert r.status_code == 200, r.text
    (statement,) = r.json()["errors"]
    r = client.post(
        "/api/reports/collection-letters",
        json={"letter_type": "30", "send_email": True},
    )
    assert r.status_code == 200, r.text
    (letter,) = r.json()["errors"]
    assert statement.startswith("Probe Co: the statement could not be sent")
    assert letter.startswith("Probe Co: the letter could not be sent")
    for text in (statement, letter):
        _plain(text, name)
    if name in NO_SENTENCE_OF_ITS_OWN:
        assert statement == (
            f"Probe Co: the statement could not be sent. {REFUSED} {SEE_WHY}"
        )
        assert letter == f"Probe Co: the letter could not be sent. {REFUSED} {SEE_WHY}"
    # and the list's sentence for every row, which never holds the
    # server's reply: that is the row's other field, an administrator's
    for row in client.get("/api/email-log?status=failed").json()["rows"]:
        _plain(row["failure"])
        assert "5.7." not in row["failure"] and "5.1.1" not in row["failure"]
        assert row["error_message"] == REPLIES[name]
        if name in NO_SENTENCE_OF_ITS_OWN:
            assert row["failure"] == REFUSED


def test_the_server_reply_is_read_back_without_python_wrapping():
    """A row of a release before 2.32 holds str() of the exception; an
    administrator reads the reply it held, and the same reply from a row
    stored now."""
    from app.services.email_errors import server_words, stored_text

    for name, error in FAILURES.items():
        _plain(server_words(str(error)))
        assert server_words(str(error)) == REPLIES[name]
        assert server_words(stored_text(error)) == REPLIES[name]


TLS_SENTENCE = (
    "Test email failed to send. The mail server offers sign-in only over a "
    "secure connection, so nothing was sent and your password was not given "
    "to it. Turn on Use TLS under Settings → Email (SMTP). (localhost) The "
    "full list is under Settings → Email (SMTP) → Recent sends."
)


def test_sign_in_offered_only_after_starttls_says_turn_on_use_tls(client, db_session):
    # a server that offers STARTTLS, and AUTH only once it is done
    server = StubSMTP(auth_after_tls=True)
    try:
        password = secrets.token_urlsafe(16)
        _configure(db_session, server.port, password, use_tls="false")
        r = client.post("/api/settings/test-email")
    finally:
        server.close()
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == TLS_SENTENCE
    verbs = [c.split(" ", 1)[0].upper() for c in server.commands]
    assert "AUTH" not in verbs and "MAIL" not in verbs and "DATA" not in verbs
    assert password.encode() not in server.raw
    (row,) = client.get("/api/email-log?status=failed").json()["rows"]
    assert row["failure"].startswith("The mail server offers sign-in only over")
