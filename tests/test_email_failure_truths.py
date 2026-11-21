"""More of what a failed send is said to be, each through the real send
path with a loopback stub playing the mail server:

- an answer to MAIL FROM or RCPT that is not an SMTP reply is never shown
  as "-1 ..." nor called the mail server's reply;
- a sentence that names an address, and Test Email's answer with the
  server's reply, are shown as written in every kind of company (the
  vocabulary of a nonprofit rewords prose, never an address or a reply);
- a 530 after the server accepted our sign-in does not say none is set;
- a To address put off (4xx) is said beside a copy refused outright;
- an address SMTP cannot carry is said before anything is sent, by name.

Every sign-in and canary is generated when the test runs.
"""

# the fixtures (stub, no_pdfs) are imported, and named again as arguments
# ruff: noqa: F811

import secrets
import smtplib

import pytest

from app.models.email_log import EmailLog
from app.models.users import ROLE_BOOKKEEPER
from app.services import email_errors
from app.services.email_errors import (
    RecipientsPartlyRefused,
    failure_sentence,
    server_words,
    stored_text,
    where_to_see_why,
)
from app.services.settings_service import set_setting
from app.services.terminology import AsWritten, Terms
from tests.test_email_failure_rule import FROM, _configure, _session
from tests.test_email_failure_rule import no_pdfs  # noqa: F401
from tests.test_email_failure_sentences import (
    COPY,
    HUNG_UP,
    LATER,
    REFUSED_CONNECTION,
    SIGN_IN_ASKED,
    TO,
    UNREACHABLE,
    USE_TLS,
    WANTS_SIGN_IN,
    WRONG_SIGN_IN,
    _read,
    _refuse,
    _send,
    _sign_in,
    stub,  # noqa: F401
)

SEE_WHY = where_to_see_why(True)
POINTER = where_to_see_why(False)
BLIND = "blind@example.test"
NOT_SMTP = "The answer was not an SMTP reply: …"
GENERAL = "The email could not be sent."
FULL_LIST = "The full list is under Settings → Email (SMTP) → Recent sends."
SIGNED_IN_AND_ASKED_AGAIN = (
    "The mail server accepted the sign-in and then asked for one again, so "
    "the email was not sent. Ask your mail provider why."
)


def _canary():
    return "canary" + secrets.token_hex(8)


# -- an answer that is not an SMTP reply, at MAIL FROM and at RCPT ------------------


@pytest.mark.parametrize("step", ["MAIL", "RCPT"])
def test_an_answer_with_no_reply_code_is_not_called_a_reply(
    client, db_session, stub, step
):
    """smtplib reads a line with no three-digit code as code -1. The row
    keeps the line in words of ours, never "-1 ...", and Test Email does
    not call it the mail server's reply. The server had answered every
    command before this one, so it is not said to be out of reach."""
    canary = _canary()
    server = stub({step: [f"abc {canary} x"]})
    _configure(db_session, server.port)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    said = r.json()["detail"]
    assert said == f"Test email failed to send. {GENERAL} {FULL_LIST}"
    assert "reply" not in said and "-1" not in said and canary not in said
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert "-1" not in row.error_message
    (shown,) = client.get("/api/email-log").json()["rows"]
    assert shown["failure"] == GENERAL
    line = shown["error_message"]
    assert line.endswith(f"{NOT_SMTP}{canary} x") and "-1" not in line
    if step == "MAIL":
        assert row.error_message == f"[sender not-smtp] {NOT_SMTP}{canary} x"
        assert line == f"{NOT_SMTP}{canary} x"
    else:
        assert row.error_message == f"[recipients {FROM}] {FROM}: {NOT_SMTP}{canary} x"
        assert line == f"{FROM}: {NOT_SMTP}{canary} x"
    # nobody else is given the line
    keeper = _session(db_session, ROLE_BOOKKEEPER)
    assert canary not in keeper.get("/api/email-log").text


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPSenderRefused(-1, b"garbage line", FROM),
        smtplib.SMTPRecipientsRefused({TO: (-1, b"garbage line")}),
    ],
)
def test_no_stored_row_holds_smtplibs_minus_one(error):
    stored = stored_text(error)
    assert "-1" not in stored
    assert server_words(stored).endswith(f"{NOT_SMTP}garbage line")
    assert not email_errors._holds_reply(stored)
    assert failure_sentence(stored) == GENERAL


def test_a_row_of_an_earlier_release_with_minus_one_reads_the_same():
    """Python's own text for the same failures, as releases before 2.32
    stored it."""
    for stored in (
        repr((-1, b"garbage line", FROM)),
        repr({TO: (-1, b"garbage line")}),
    ):
        assert "-1" not in server_words(stored)
        assert server_words(stored).endswith(f"{NOT_SMTP}garbage line")
        assert not email_errors._holds_reply(stored)


def test_an_answer_with_no_code_for_the_to_address_while_a_copy_went(db_session, stub):
    """The server was reached (it took the copy), so "could not be
    reached" would be untrue: the general sentence, who the message did
    not go to, and the copy."""
    canary = _canary()
    server = stub({"RCPT": _refuse(TO, f"abc {canary}")})
    ok, row = _send(db_session, server, cc=[COPY])
    assert not ok and server.messages == 1
    seen = _read(db_session, row)
    assert seen.sentence == (
        f"The email could not be sent. The mail server did not take the email "
        f"for: {TO}. A copy still went to: {COPY}."
    )
    assert seen.reason() == f"{seen.sentence} {POINTER}"
    mine = _read(db_session, row, admin=True)
    assert mine.reply == f"{TO}: {NOT_SMTP}{canary}"
    assert mine.with_reply() == mine.sentence  # not called a reply
    # the answers built from it say the copy too
    from app.services.email_errors import send_failed_detail, sentence_for_last_send

    assert sentence_for_last_send(db_session, "", 0) == (
        f"The mail server did not take the email for: {TO}. A copy still "
        f"went to: {COPY}. {POINTER}"
    )
    assert send_failed_detail(db_session, "", 0, False) == (
        f"Email could not be sent. The mail server did not take the email "
        f"for: {TO}. A copy still went to: {COPY}. {POINTER}"
    )


# -- an address, and the server's reply, are shown as written -----------------------


def _nonprofit(client):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text


def test_a_nonprofits_vocabulary_never_rewords_an_address(
    client, db_session, seed_accounts, stub, no_pdfs
):
    """ "invoices@customer.example.test" holds two words a nonprofit calls
    something else. The answer names the address that was refused, as the
    log does."""
    from tests.test_document_activity_894 import _customer, _invoice

    address = "invoices@customer.example.test"
    server = stub({"RCPT": ["550 5.1.1 User unknown"]})
    _configure(db_session, server.port)
    inv = _invoice(client, _customer(client)["id"])
    _nonprofit(client)
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": address, "subject": "Probe", "message": ""},
    )
    sentence = (
        f"The address was refused by the mail server: {address}. Check it and "
        "send again."
    )
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == f"Email could not be sent. {sentence} {SEE_WHY}"
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["failure"] == sentence
    # prose of ours with no address in it is still worded for the company
    missing = client.post(
        "/api/invoices/999999/email",
        json={"recipient": address, "subject": "Probe", "message": ""},
    )
    assert missing.status_code == 404 and "Pledge" in missing.json()["detail"]


def test_a_nonprofits_test_email_shows_the_reply_as_received(client, db_session, stub):
    sender = "invoices@example.test"
    reply = (
        "553 5.1.8 Sender address rejected: customer invoice mailbox "
        f"<{sender}> has no class"
    )
    server = stub({"MAIL": [reply]})
    _configure(db_session, server.port)
    set_setting(db_session, "smtp_from_email", sender)
    db_session.commit()
    _nonprofit(client)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == (
        "Test email failed to send. The mail server refused the sender "
        f"address: {sender}. Check the From address under Settings → Email "
        f"(SMTP). The mail server's reply: {reply}. {FULL_LIST}"
    )
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["error_message"] == reply


def test_a_nonprofits_reminder_names_the_document_as_the_run_does(
    client, db_session, seed_accounts, stub, no_pdfs
):
    """One reminder by hand and Send reminders name the same invoice the
    same way (by the face it prints with), and the refused address as it
    is."""
    from app.services import collections as svc
    from tests.test_email_failure_rule import _overdue

    svc.seed_default_rules(db_session)
    customer, first = _overdue(db_session)
    address = "invoices@customer.example.test"
    customer.email = address
    db_session.commit()
    server = stub({"RCPT": ["550 5.1.1 User unknown"]})
    _configure(db_session, server.port)
    _nonprofit(client)
    by_hand = client.post(f"/api/collections/invoices/{first.id}/remind")
    assert by_hand.status_code == 400, by_hand.text
    said = by_hand.json()["detail"]
    assert address in said
    # Send reminders, for a second invoice of the same kind
    other, second = _overdue(db_session)
    other.email = address
    db_session.commit()
    run = client.post(
        "/api/collections/reminders/run", json={"invoice_ids": [second.id]}
    )
    assert run.status_code == 200, run.text
    lines = [e for e in run.json()["errors"] if second.invoice_number in e]
    assert lines, run.text
    assert address in lines[0]
    assert said.replace(first.invoice_number, second.invoice_number) == lines[0]


def test_the_answers_to_a_failed_send_are_marked_as_written(db_session):
    """Whatever a failed send answers with holds addresses of ours (and,
    for Test Email, the server's reply): none of it is prose for the
    vocabulary to reword."""
    from app.services.email_errors import failed_test_email_detail, send_failed_detail

    for admin in (True, False):
        assert isinstance(
            send_failed_detail(db_session, "invoice", 1, admin), AsWritten
        )
        assert isinstance(failed_test_email_detail(db_session, admin), AsWritten)


def test_no_sentence_of_ours_is_a_word_any_vocabulary_swaps():
    """The log and the batch results give the sentences without the swap
    and the error bodies give them as written: both read the same only
    while no fixed sentence holds a word a vocabulary renames."""
    fixed = {
        name: value
        for name, value in vars(email_errors).items()
        if name.startswith("_") and name.isupper() and isinstance(value, str)
    }
    assert len(fixed) > 25
    for mode in ("nonprofit", "construction"):
        terms = Terms(mode)
        for name, value in fixed.items():
            assert terms.text(value) == value, (mode, name)
        for admin in (True, False):
            assert terms.text(where_to_see_why(admin)) == where_to_see_why(admin)


# -- a 530 after the server accepted our sign-in ------------------------------------


@pytest.mark.parametrize("step", ["MAIL", "RCPT", "BODY"])
def test_a_530_after_an_accepted_sign_in_does_not_say_none_is_set(
    db_session, stub, step
):
    user, password = _sign_in()
    reply = "530 5.7.0 Authentication Required"
    server = stub({step: [reply]})
    ok, row = _send(db_session, server, user, password)
    assert not ok
    assert any(line.upper().startswith("AUTH") for line in server.lines)
    seen = _read(db_session, row)
    mine = _read(db_session, row, admin=True)
    assert seen.sentence == mine.sentence == SIGNED_IN_AND_ASKED_AGAIN
    assert "none is set" not in seen.reason()
    # the reply is still the administrator's, as it was received
    assert mine.reply.endswith(reply) and mine.with_reply().endswith(f"{reply}.")
    assert "signed-in" not in mine.reply


@pytest.mark.parametrize("step", ["MAIL", "RCPT"])
def test_a_530_with_no_sign_in_made_still_says_one_is_wanted(db_session, stub, step):
    ok, row = _send(db_session, stub({step: ["530 5.7.0 Authentication Required"]}))
    assert not ok
    assert _read(db_session, row).sentence == WANTS_SIGN_IN


def test_a_530_with_a_username_and_no_password_still_says_one_is_wanted(
    db_session, stub
):
    user, _ = _sign_in()
    server = stub({"MAIL": ["530 5.7.0 Authentication Required"]})
    ok, row = _send(db_session, server, user, "")
    assert not ok
    assert not any(line.upper().startswith("AUTH") for line in server.lines)
    assert _read(db_session, row).sentence == WANTS_SIGN_IN


# -- the To address put off, a copy refused, a blind copy taken ----------------------


def test_a_to_address_put_off_is_said_beside_a_refused_copy(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_document_activity_894 import _customer, _invoice

    canary = _canary()

    def rcpt(line):
        if TO in line:
            return [f"451 4.7.1 Greylisted {canary}"]
        if COPY in line:
            return [f"550 5.1.1 User unknown {canary}"]
        return ["250 2.1.5 OK"]

    server = stub({"RCPT": rcpt})
    _configure(db_session, server.port)
    set_setting(db_session, "smtp_always_cc", COPY)
    set_setting(db_session, "smtp_always_bcc", BLIND)
    db_session.commit()
    inv = _invoice(client, _customer(client)["id"])
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": TO, "subject": "Probe", "message": ""},
    )
    sentence = (
        f"The address was refused by the mail server: {COPY}. Check it and "
        f"send again. The mail server asked to try again later for: {TO}. A "
        f"copy still went to: {BLIND}."
    )
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == f"Email could not be sent. {sentence} {SEE_WHY}"
    assert canary not in r.text
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["status"] == "failed" and row["failure"] == sentence
    assert row["error_message"] == (
        f"{TO}: 451 4.7.1 Greylisted {canary}\n{COPY}: 550 5.1.1 User unknown {canary}"
    )


def test_the_same_mix_on_a_row_of_an_earlier_release():
    stored = repr({TO: (451, b"4.7.1 Greylisted"), COPY: (550, b"5.1.1 User unknown")})
    assert failure_sentence(stored) == (
        f"The address was refused by the mail server: {COPY}. Check it and "
        f"send again. The mail server asked to try again later for: {TO}."
    )
    # and a tagged row with every part
    error = RecipientsPartlyRefused(
        {TO: (451, b"4.7.1 Greylisted"), COPY: (550, b"5.7.1 Relay denied")}, [BLIND]
    )
    assert failure_sentence(stored_text(error)) == (
        f"The mail server would not take mail for: {COPY}. If the address is "
        "right, the server may want a sign-in (Settings → Email (SMTP)) or "
        "may not allow mail to it. The mail server asked to try again later "
        f"for: {TO}. A copy still went to: {BLIND}."
    )


# -- an address SMTP cannot carry ----------------------------------------------------


def _unsendable(address):
    return (
        "This address has a character that cannot be sent to the mail server "
        "(a letter with an accent, for example), so nothing was sent: "
        f"{address}. Change the address and send again."
    )


def test_an_address_with_an_accented_letter_is_said_by_name_before_sending(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_document_activity_894 import _customer, _invoice

    address = "josé@example.test"
    server = stub()
    _configure(db_session, server.port)
    inv = _invoice(client, _customer(client)["id"])
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": address, "subject": "Probe", "message": ""},
    )
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == (
        f"Email could not be sent. {_unsendable(address)} {SEE_WHY}"
    )
    # nothing was said to the server at all
    assert server.lines == [] and server.messages == 0
    (row,) = client.get("/api/email-log").json()["rows"]
    assert row["failure"] == _unsendable(address)
    # the administrator's line says the cause, not "Unexpected error"
    assert row["error_message"] == (
        "An address has a character that cannot be sent to the mail server: "
        f"{address}"
    )
    keeper = _session(db_session, ROLE_BOOKKEEPER)
    (theirs,) = keeper.get("/api/email-log").json()["rows"]
    assert theirs["failure"] == theirs["error_message"] == _unsendable(address)


def test_a_from_address_with_an_accented_letter_on_test_email(client, db_session, stub):
    sender = "bücher@example.test"
    server = stub()
    _configure(db_session, server.port)
    set_setting(db_session, "smtp_from_email", sender)
    db_session.commit()
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    # one address (the test goes from and to it), and no "server's reply"
    assert r.json()["detail"] == (
        f"Test email failed to send. {_unsendable(sender)} {FULL_LIST}"
    )
    assert server.lines == []


def test_a_batch_names_the_address_and_does_not_point_at_a_reason_elsewhere(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_email_failure_rule import _overdue

    address = "josé@example.test"
    customer, _inv = _overdue(db_session)
    customer.email = address
    db_session.commit()
    server = stub()
    _configure(db_session, server.port)
    r = client.post("/api/collections/statements/run", json={"force": True})
    assert r.status_code == 200, r.text
    (line,) = r.json()["errors"]
    assert line == (
        f"Mail Probe: The statement could not be sent. {_unsendable(address)}"
    )
    assert "Recent sends" not in line


def test_a_display_name_with_an_accent_is_no_reason_to_refuse(db_session, stub):
    """Only the address itself travels in the envelope."""
    server = stub()
    ok, row = _send(db_session, server, to="José <jose@example.test>")
    assert ok and row.status == "sent" and server.messages == 1


def test_any_other_fault_says_it_is_not_the_servers_and_where_its_cause_is(
    db_session, stub, monkeypatch
):
    """A fault of ours that is not an answer from the mail server: the
    pointer leads to a line that says so and where the cause was written,
    with none of Python's text."""
    secret = secrets.token_urlsafe(12)

    def broken(self, *a, **k):
        raise RuntimeError(f"something about {secret}")

    monkeypatch.setattr(smtplib.SMTP, "sendmail", broken)
    ok, row = _send(db_session, stub())
    assert not ok
    mine = _read(db_session, row, admin=True)
    assert mine.reason() == f"The email could not be sent. {SEE_WHY}"
    assert mine.reply == (
        "Unexpected error in SlowBooks Pro, not an answer from the mail "
        "server (the server log has the cause)"
    )
    assert mine.with_reply() == mine.sentence  # not called a reply
    assert secret not in row.error_message and "RuntimeError" not in row.error_message


# -- a refused address the tag cannot name ------------------------------------------

REFUSED_NO_NAME = (
    "An address was refused by the mail server. Check the addresses and send again."
)
NOT_TAKEN = "The mail server did not take the email for: {}."
COPY_WENT = "A copy still went to: {}."
LOST_AFTER_BODY = (
    "The connection to the mail server was lost after the message was handed "
    "to it, before it answered, so the email may or may not have gone out. "
    "Check with the recipient before sending it again."
)
ANSWER_UNREAD = (
    "The mail server's answer after the message was handed to it could not "
    "be read, so the email may or may not have gone out. Check with the "
    "recipient before sending it again."
)


@pytest.mark.parametrize("typed", ["];", ",", "; ;"])
@pytest.mark.parametrize("status", ["550", "400"])
def test_a_to_address_with_nothing_to_name_is_never_read_from_the_servers_line(
    db_session, stub, typed, status
):
    """Nothing of such a To address is left for our tag, and the answer
    here has no reply code: the name and the sentence are not taken from
    the line the server wrote, whatever ": NNN" stands in it."""
    canary = _canary()
    server = stub({"RCPT": [f"HTTP/1.1 400 Bad Request {canary} status: {status} Bad"]})
    ok, row = _send(db_session, server, to=typed)
    assert not ok
    for admin in (False, True):
        seen = _read(db_session, row, admin)
        assert seen.sentence == REFUSED_NO_NAME
        assert canary not in seen.reason()
    assert canary in _read(db_session, row, admin=True).reply


def test_a_nameless_address_keeps_its_reply_code_in_our_tag(db_session, stub):
    server = stub({"RCPT": ["451 4.7.1 Greylisted"]})
    ok, row = _send(db_session, server, to="];")
    assert not ok
    assert row.error_message == "[recipients  451 4.7.1] : 451 4.7.1 Greylisted"
    assert _read(db_session, row).sentence == (
        "The mail server asked to try again later."
    )
    assert email_errors._holds_reply(row.error_message)


@pytest.mark.parametrize("status", ["550", "400", "451"])
def test_a_row_whose_tag_names_nobody_reads_as_an_address_refused(status):
    """What unreleased builds stored for refused recipients: no address in
    the tag. It reads without a name, and never by the server's line."""
    canary = _canary()
    stored = (
        f"[recipients] {TO}: -1 TP/1.1 400 Bad Request {canary} status: {status} Bad"
    )
    said = failure_sentence(stored)
    assert said == REFUSED_NO_NAME
    assert not email_errors._holds_reply(stored)


# -- STARTTLS answered with something that is not a reply ----------------------------


def test_a_starttls_answer_with_no_reply_code_is_not_called_a_reply(
    client, db_session, stub
):
    canary = _canary()
    server = stub(
        {"EHLO": ["250-stub", "250 STARTTLS"], "STARTTLS": [f"nope {canary}"]}
    )
    _configure(db_session, server.port, use_tls="true")
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    said = r.json()["detail"]
    assert "reply" not in said and "-1" not in said and canary not in said
    assert said.startswith(
        "Test email failed to send. SlowBooks Pro could not set up a secure "
        "connection to the mail server, so nothing was sent"
    )
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.error_message == f"[starttls 127.0.0.1] {NOT_SMTP}{canary}"
    (shown,) = client.get("/api/email-log").json()["rows"]
    assert shown["error_message"] == f"{NOT_SMTP}{canary}"


# -- a reply with a byte no database text can hold -----------------------------------


def test_a_nul_byte_in_a_reply_is_not_stored(db_session, stub):
    """PostgreSQL refuses a NUL character in text: the row of the failed
    send would be lost, and the send would raise."""
    canary = _canary()
    server = stub({"BODY": [f"554 5.7.1 refused \x00{canary}\x00"]})
    ok, row = _send(db_session, server)
    assert not ok
    assert "\x00" not in row.error_message
    assert row.error_message == f"[data] 554 5.7.1 refused �{canary}�"


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPDataError(554, b"5.7.1 a\x00b"),
        smtplib.SMTPSenderRefused(553, b"a\x00b", FROM),
        smtplib.SMTPRecipientsRefused({TO: (550, b"a\x00b")}),
        smtplib.SMTPAuthenticationError(535, b"a\x00b"),
        smtplib.SMTPServerDisconnected("a\x00b"),
        OSError("a\x00b"),
        "words of ours a\x00b",
        email_errors.starttls_refused(
            smtplib.SMTPResponseException(454, b"a\x00b"), "mail.example.test"
        ),
        email_errors.MailTLSError("Secure connection failed (a\x00b)"),
    ],
)
def test_nothing_stored_holds_a_nul_character(error):
    assert "\x00" not in stored_text(error)


# -- the To address asked for a sign-in while a copy went ----------------------------


def _always_bcc(db, address=BLIND):
    set_setting(db, "smtp_always_bcc", address)
    db.commit()


def test_a_to_address_asked_for_a_sign_in_while_a_copy_went(db_session, stub):
    """A server that takes its own mailboxes without a sign-in and wants
    one to relay: the copy went, the To address did not, and both are
    said."""
    server = stub({"RCPT": _refuse(TO, "530 5.7.1 Authentication required for relay")})
    _always_bcc(db_session)
    ok, row = _send(db_session, server)
    assert not ok and server.messages == 1
    assert _read(db_session, row).sentence == (
        f"{WANTS_SIGN_IN} {NOT_TAKEN.format(TO)} {COPY_WENT.format(BLIND)}"
    )
    from app.services.email_errors import failed_send_line, send_failed_detail

    assert send_failed_detail(db_session, "", 0, False) == (
        f"Email could not be sent. {WANTS_SIGN_IN} {NOT_TAKEN.format(TO)} "
        f"{COPY_WENT.format(BLIND)} {POINTER}"
    )
    assert failed_send_line(db_session, "", 0, False, "Someone: the statement") == (
        f"Someone: the statement could not be sent. {WANTS_SIGN_IN} "
        f"{NOT_TAKEN.format(TO)} {COPY_WENT.format(BLIND)}"
    )


def test_the_same_530_after_a_sign_in_does_not_say_the_email_was_not_sent():
    refused = RecipientsPartlyRefused(
        {TO: (530, b"5.7.1 Authentication required")}, [BLIND]
    )
    said = failure_sentence(stored_text(refused, signed_in=True))
    assert "was not sent" not in said
    assert said == (
        "The mail server accepted the sign-in and then asked for one again. "
        f"Ask your mail provider why. {NOT_TAKEN.format(TO)} "
        f"{COPY_WENT.format(BLIND)}"
    )
    # with no copy taken, nothing went out and the sentence says so
    alone = smtplib.SMTPRecipientsRefused({TO: (530, b"5.7.1 Authentication required")})
    assert failure_sentence(stored_text(alone, signed_in=True)) == (
        SIGNED_IN_AND_ASKED_AGAIN
    )


def test_the_same_530_asking_for_starttls_says_the_copy_too():
    refused = RecipientsPartlyRefused(
        {TO: (530, b"5.7.0 Must issue a STARTTLS command first")}, [BLIND]
    )
    assert failure_sentence(stored_text(refused)) == (
        "The mail server takes mail only over a secure connection. Turn on "
        f"Use TLS under Settings → Email (SMTP). {NOT_TAKEN.format(TO)} "
        f"{COPY_WENT.format(BLIND)}"
    )


# -- Activity and a typed copy in a nonprofit ----------------------------------------


def test_a_nonprofits_activity_names_the_address_that_was_sent_to(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_document_activity_894 import _activity, _customer, _invoice

    refused = "invoices@customer.example.test"
    taken = "ok@customer.example.test"
    server = stub({"RCPT": _refuse(refused)})
    _configure(db_session, server.port)
    inv = _invoice(client, _customer(client)["id"])
    _nonprofit(client)
    url = f"/api/invoices/{inv['id']}/email"
    r = client.post(url, json={"recipient": refused, "subject": "Probe", "message": ""})
    assert r.status_code == 502, r.text
    r = client.post(url, json={"recipient": taken, "subject": "Probe", "message": ""})
    assert r.status_code == 200, r.text
    events = _activity(client, "invoice", inv["id"])["events"]
    texts = [e["text"] for e in events if e["kind"] == "emailed"]
    assert len(texts) == 2
    failed = next(t for t in texts if "could not be sent" in t)
    sent = next(t for t in texts if t.startswith("Emailed to"))
    assert failed.startswith(f"An email to {refused} by ")
    assert sent.startswith(f"Emailed to {taken} by ")


def test_a_nonprofits_answer_to_a_typed_copy_shows_what_was_typed(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_document_activity_894 import _customer, _invoice

    server = stub()
    _configure(db_session, server.port)
    customer = _customer(client)
    inv = _invoice(client, customer["id"])
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": customer["id"],
            "date": "2026-06-01",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    )
    assert est.status_code == 201, est.text
    _nonprofit(client)
    for url in (
        f"/api/invoices/{inv['id']}/email",
        f"/api/estimates/{est.json()['id']}/email",
    ):
        for typed in ("josé@invoice.example.test", "not a customer"):
            r = client.post(
                url,
                json={"recipient": TO, "subject": "Probe", "message": "", "cc": typed},
            )
            assert r.status_code == 400, r.text
            assert r.json()["detail"] == f"Not an email address: {typed}."
    assert server.lines == []


def test_an_address_in_any_error_is_not_reworded_and_the_prose_around_it_is(client):
    """The one place every error body is reworded leaves an address as it
    is, whoever wrote the sentence."""
    from fastapi import HTTPException

    from app.main import app

    address = "invoices@customer.example.test"

    @app.get("/api/_probe/worded")
    def _probe():
        raise HTTPException(status_code=400, detail=f"The invoice for {address} is.")

    try:
        _nonprofit(client)
        r = client.get("/api/_probe/worded")
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == f"The pledge for {address} is."
    finally:
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if getattr(route, "path", "") != "/api/_probe/worded"
        ]


# -- a message handed over, and no word whether it was taken -------------------------


def test_a_message_that_may_have_gone_out_is_never_said_flatly_not_sent(
    client, db_session, seed_accounts, stub, no_pdfs
):
    from tests.test_document_activity_894 import _activity, _customer, _invoice
    from tests.test_email_failure_rule import _overdue

    server = stub({"BODY": "hang-up"})
    _configure(db_session, server.port)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == (
        f"Test email may or may not have been sent. {LOST_AFTER_BODY} {FULL_LIST}"
    )
    inv = _invoice(client, _customer(client)["id"])
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": TO, "subject": "Probe", "message": ""},
    )
    assert r.status_code == 502, r.text
    # the sentence is the whole reason: no pointer to a line that has less
    assert r.json()["detail"] == (
        f"Email may or may not have been sent. {LOST_AFTER_BODY}"
    )
    events = _activity(client, "invoice", inv["id"])["events"]
    emailed = next(e for e in events if e["kind"] == "emailed")
    assert emailed["text"].startswith(f"An email to {TO} by ")
    assert emailed["text"].endswith(" may or may not have been sent.")
    assert emailed["details"] == [LOST_AFTER_BODY]
    _overdue(db_session)
    run = client.post("/api/collections/statements/run", json={"force": True})
    assert run.status_code == 200, run.text
    lines = [e for e in run.json()["errors"] if e.startswith("Mail Probe")]
    assert lines == [
        f"Mail Probe: The statement may or may not have been sent. {LOST_AFTER_BODY}"
    ]
    for body in (r.text, run.text):
        assert "could not be sent" not in body


# -- an answer that is neither a yes nor a refusal, from a server that was reached ----


def test_an_unreadable_answer_to_the_message_is_not_an_unreachable_server(
    db_session, stub
):
    canary = _canary()
    server = stub({"BODY": [f"hello {canary}"]})
    ok, row = _send(db_session, server)
    assert not ok and server.messages == 1
    assert row.error_message.startswith("[data not-smtp after-body] ")
    seen = _read(db_session, row, admin=True)
    assert seen.sentence == ANSWER_UNREAD and seen.unsure
    assert seen.with_reply() == ANSWER_UNREAD  # not called a reply


def test_an_unreadable_answer_to_the_data_command_is_before_the_message(
    db_session, stub
):
    server = stub({"DATA": ["hello there"]})
    ok, row = _send(db_session, server)
    assert not ok and server.messages == 0
    assert row.error_message.startswith("[data not-smtp] ")
    seen = _read(db_session, row)
    assert seen.sentence == GENERAL and not seen.unsure


def test_a_reply_that_is_no_refusal_is_not_an_unreachable_server(db_session, stub):
    server = stub({"RCPT": ["252 cannot verify"]})
    ok, row = _send(db_session, server)
    assert not ok
    assert row.error_message == f"[recipients {TO} 252] {TO}: 252 cannot verify"
    seen = _read(db_session, row)
    assert seen.sentence == GENERAL
    assert seen.reason() == f"{GENERAL} {POINTER}"


def test_a_to_address_with_no_usable_answer_is_said_beside_a_refused_copy(
    db_session, stub
):
    """To answered with something that is no reply, a Cc refused, a Bcc
    taken: the sentence does not leave out that the addressee was not
    sent it."""

    def rcpt(line):
        if TO in line:
            return ["abc huh"]
        if COPY in line:
            return ["550 5.1.1 User unknown"]
        return ["250 2.1.5 OK"]

    server = stub({"RCPT": rcpt})
    _always_bcc(db_session)
    ok, row = _send(db_session, server, cc=[COPY])
    assert not ok and server.messages == 1
    assert _read(db_session, row).sentence == (
        f"The address was refused by the mail server: {COPY}. Check it and "
        f"send again. {NOT_TAKEN.format(TO)} {COPY_WENT.format(BLIND)}"
    )


# -- the error handler's rewording is linear, and still leaves an address alone ------


def _probe_error(client, detail):
    """What the app answers when a route raises a 400 with `detail`, and
    how long it took."""
    import time

    from fastapi import HTTPException

    from app.main import app

    path = "/api/_probe/long-detail"

    @app.get(path)
    def _probe():
        raise HTTPException(status_code=400, detail=detail)

    try:
        started = time.monotonic()
        r = client.get(path)
        took = time.monotonic() - started
    finally:
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", "") != path
        ]
    assert r.status_code == 400
    return r.json()["detail"], took


def test_a_very_long_error_is_reworded_in_time_that_grows_with_its_length(client):
    """An error that repeats a long value back (one word of 400,000
    characters, or 400,000 characters of words) is answered at once: the
    rewording that keeps addresses as written makes one pass over the
    sentence. Before, each character of an unspaced run started a new
    search to the end of it, and the app answered nothing else meanwhile."""
    _nonprofit(client)
    address = "invoices@customer.example.test"
    unspaced = "x" * 400_000
    words = "the invoice is here " * 20_000
    with_addresses = f"an invoice for {address} " * 10_000
    for detail, expected in (
        (unspaced, unspaced),
        ("Invoice " + unspaced, "Pledge " + unspaced),
        (words, "the pledge is here " * 20_000),
        (with_addresses, f"an pledge for {address} " * 10_000),
        (unspaced + "@" + unspaced, unspaced + "@" + unspaced),
    ):
        answered, took = _probe_error(client, detail)
        assert answered == expected
        # the old pattern took minutes for the first of these
        assert took < 2, f"{took:.1f}s for {len(detail)} characters"


def test_rewording_around_an_address_reads_as_it_did():
    """The words around an address are reworded as a whole stretch (a
    phrase of two words is still one phrase), and a run of characters with
    an @ in it is left exactly as written."""
    terms = Terms("nonprofit")
    keep = terms.text_keeping_addresses
    address = "invoices@customer.example.test"
    assert keep("Invoice not found") == terms.text("Invoice not found")
    assert keep(f"Email to {address}: Invoice missing") == (
        f"Email to {address}: Pledge missing"
    )
    assert keep("Invoice,customer@x.example,Invoice missing") == (
        "Invoice,customer@x.example,Invoice missing"
    )
    assert keep("Customer @ Invoice") == "Donor @ Pledge"
    for sentence in (
        f"The Sales Tax on the invoice for {address}\n  and the customer",
        f"{address}",
        f"  {address}  invoice\tcustomer {address}",
        "",
    ):
        # the same as swapping each stretch between two addresses
        import re

        expected = "".join(
            part if "@" in part else terms.text(part)
            for part in re.split(r"(\S*@\S*)", sentence)
        )
        assert keep(sentence) == expected
    assert Terms("business").text_keeping_addresses(f"Invoice {address}") == (
        f"Invoice {address}"
    )


# -- a server that answered as a mail server and then hung up was reached ------------

CLOSED = "Connection unexpectedly closed"


@pytest.mark.parametrize(
    "step,at,signs_in",
    [
        ("EHLO", "helo", False),
        ("AUTH", "auth", True),
        ("MAIL", "mail", False),
        ("RCPT", "rcpt", False),
        ("DATA", "data", False),
    ],
)
def test_a_hang_up_after_the_greeting_is_not_a_server_out_of_reach(
    client, db_session, stub, step, at, signs_in
):
    """The server greeted us as a mail server and then closed the
    connection: it was reached, so the sentence does not send anyone to
    change the host, the port or Use TLS. The row keeps the step."""
    server = stub({step: "hang-up"})
    user, password = _sign_in() if signs_in else ("", "")
    ok, row = _send(db_session, server, user, password)
    assert not ok and server.messages == 0
    assert row.error_message == f"[disconnected at-{at}] {CLOSED}"
    seen, mine = _read(db_session, row), _read(db_session, row, admin=True)
    assert seen.sentence == mine.sentence == HUNG_UP
    # the sentence says what happened: no pointer, and nothing unsure
    assert seen.reason() == HUNG_UP and not seen.unsure
    assert mine.reply == CLOSED and seen.reply is None
    # Test Email: the sentence, and no "reply" (there was none)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == f"Test email failed to send. {HUNG_UP} {FULL_LIST}"


def test_nothing_that_answered_as_a_mail_server_is_still_out_of_reach(db_session, stub):
    """Closed before a greeting was read, or a greeting that is not SMTP:
    nothing answered as a mail server."""
    ok, row = _send(db_session, stub({"GREETING": "hang-up"}))
    assert not ok
    assert row.error_message == f"[disconnected] {CLOSED}"
    assert _read(db_session, row).sentence == UNREACHABLE
    ok, row = _send(db_session, stub({"GREETING": ["HTTP/1.1 400 Bad Request"]}))
    assert not ok
    assert row.error_message.startswith("[connect not-smtp] ")
    assert _read(db_session, row).sentence == UNREACHABLE


@pytest.mark.parametrize(
    "reply,sentence",
    [
        ("421 4.3.0 Service shutting down", LATER),
        ("454 4.7.0 Temporary authentication failure", LATER),
        ("535 5.7.8 Authentication credentials invalid", WRONG_SIGN_IN),
    ],
)
def test_a_refusal_at_sign_in_is_not_lost_when_the_server_then_hangs_up(
    client, db_session, stub, reply, sentence
):
    """A server that answers the sign-in with a refusal and closes the
    connection, as a 421 does. The next sign-in method is then tried on a
    closed connection; the refusal still decides the sentence, as it would
    have with the connection left open, and is the administrator's line."""
    user, password = _sign_in()
    server = stub({"AUTH": ("then-hang-up", [reply])})
    ok, row = _send(db_session, server, user, password)
    assert not ok and server.messages == 0
    code = " ".join(reply.split(" ")[:2])
    assert row.error_message == f"[disconnected at-auth {code}] {reply}"
    seen, mine = _read(db_session, row), _read(db_session, row, admin=True)
    assert seen.sentence == mine.sentence == sentence
    assert mine.reply == reply and seen.reply is None
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == (
        f"Test email failed to send. {sentence} The mail server's reply: "
        f"{reply}. {FULL_LIST}"
    )
    # neither the username nor the password is on the row
    assert user not in row.error_message and password not in row.error_message


@pytest.mark.parametrize(
    "reply,sentence",
    [
        ("421 4.3.2 Too many connections", LATER),
        ("554 5.7.1 No service for you", REFUSED_CONNECTION),
    ],
)
def test_a_refused_greeting_exchange_then_a_hang_up(db_session, stub, reply, sentence):
    """EHLO refused and the connection closed: the HELO that is tried
    next finds it closed, and the refusal is what is said."""
    server = stub({"EHLO": ("then-hang-up", [reply])})
    ok, row = _send(db_session, server)
    assert not ok
    code = " ".join(reply.split(" ")[:2])
    assert row.error_message == f"[disconnected at-helo {code}] {reply}"
    assert _read(db_session, row).sentence == sentence
    assert _read(db_session, row, admin=True).reply == reply


def test_an_answer_to_ehlo_that_is_no_reply_after_a_proper_greeting(db_session, stub):
    """A proper 220 was read, so a mail server answered: what came after
    is not "could not be reached"."""
    server = stub({"EHLO": ["hello there"]})
    ok, row = _send(db_session, server)
    assert not ok and server.messages == 0
    assert row.error_message.startswith("[helo not-smtp] ")
    seen = _read(db_session, row)
    assert seen.sentence == GENERAL
    assert seen.reason() == f"{GENERAL} {POINTER}"


def test_a_refusal_before_a_hang_up_is_each_commands_own(db_session, stub):
    """A refused To address is not what a hang-up at the next RCPT is read
    by: each command is judged by its own reply."""

    def rcpt(line):
        if TO in line:
            return ["550 5.1.1 User unknown"]
        return "hang-up"

    server = stub({"RCPT": rcpt})
    ok, row = _send(db_session, server, cc=[COPY])
    assert not ok and server.messages == 0
    assert row.error_message == f"[disconnected at-rcpt] {CLOSED}"
    assert _read(db_session, row).sentence == HUNG_UP


def test_what_the_writer_keeps_of_a_lost_connection():
    """The step and the refusal stand in our tag; a wait that timed out,
    a step that is not known and a message already handed over are stored
    as before."""
    closed = smtplib.SMTPServerDisconnected(CLOSED)
    assert stored_text(closed) == f"[disconnected] {CLOSED}"
    assert stored_text(closed, lost_at="mail") == f"[disconnected at-mail] {CLOSED}"
    # a reply that is no refusal is not what the hang-up is read by
    assert stored_text(closed, lost_at="data", last_reply=(354, b"go ahead")) == (
        f"[disconnected at-data] {CLOSED}"
    )
    assert stored_text(closed, lost_at="nonsense") == f"[disconnected] {CLOSED}"
    # handed over whole: it may have gone out, as before
    after = stored_text(closed, after_body=True, lost_at="data")
    assert after == f"[disconnected after-body] {CLOSED}"
    assert failure_sentence(after) == LOST_AFTER_BODY
    # a wait that ran out is not a hang-up (and not part of this rule)
    try:
        try:
            raise TimeoutError("timed out")
        except OSError as cause:
            raise smtplib.SMTPServerDisconnected(f"{CLOSED}: {cause}")
    except smtplib.SMTPServerDisconnected as error:
        silent = stored_text(error, lost_at="mail", last_reply=(250, b"OK"))
    assert silent == "[disconnected] timed out"
    assert failure_sentence(silent) == UNREACHABLE
    assert stored_text(TimeoutError("timed out"), lost_at="rcpt") == (
        "[socket] timed out"
    )
    # a 530 before the hang-up, after a sign-in the server had accepted
    signed = stored_text(
        closed,
        signed_in=True,
        lost_at="mail",
        last_reply=(530, b"5.7.0 Authentication Required"),
    )
    assert signed == (
        "[disconnected at-mail 530 5.7.0; signed-in] 530 5.7.0 Authentication Required"
    )
    assert failure_sentence(signed) == SIGNED_IN_AND_ASKED_AGAIN
    # a connection reset by the server after its greeting
    reset = stored_text(ConnectionResetError(104, "Connection reset by peer"))
    assert reset == "[socket] Connection reset by peer"
    assert failure_sentence(reset) == UNREACHABLE
    reset = stored_text(
        ConnectionResetError(104, "Connection reset by peer"), lost_at="starttls"
    )
    assert reset == "[socket at-starttls] Connection reset by peer"
    assert failure_sentence(reset) == HUNG_UP
    assert server_words(reset) == "Connection reset by peer"


@pytest.mark.parametrize(
    "stored",
    [
        f"[disconnected] {CLOSED}",
        "[disconnected] Server not connected",
        CLOSED,
        "Connection unexpectedly closed: timed out",
        "[socket] Connection refused",
        "[socket] timed out",
    ],
)
def test_a_row_that_does_not_say_how_far_it_got_reads_as_it_did(stored):
    """Rows of a gate build and of 2.31.14 keep no record of the step."""
    assert failure_sentence(stored) == UNREACHABLE


# -- a 530 on a row that does not say whether a sign-in was made --------------------


@pytest.mark.parametrize(
    "stored",
    [
        repr((530, b"5.7.0 Authentication Required", FROM)),
        repr((530, b"5.7.0 Authentication Required")),
        repr({TO: (530, b"5.7.0 Authentication Required")}),
        repr((530, b"Authentication required")),
    ],
)
def test_an_old_row_for_a_530_does_not_say_no_sign_in_is_set(stored):
    """A row of 2.31.14 keeps no record of whether a sign-in was made, and
    one may well have been: its sentence is true either way."""
    assert failure_sentence(stored) == SIGN_IN_ASKED
    assert "none is set" not in failure_sentence(stored)


def test_a_530_row_of_this_version_reads_as_it_recorded(db_session, stub):
    """This version records whether the server had accepted a sign-in, so
    its rows say which it was; and a 530 that asks for STARTTLS is about
    Use TLS on a row of any age."""
    server = stub({"MAIL": ["530 5.7.0 Authentication Required"]})
    ok, row = _send(db_session, server)
    assert not ok
    assert row.error_message == f"[sender {FROM}] 530 5.7.0 Authentication Required"
    assert _read(db_session, row).sentence == WANTS_SIGN_IN
    user, password = _sign_in()
    ok, row = _send(db_session, server, user, password)
    assert not ok and row.error_message.startswith(f"[sender {FROM}; signed-in] ")
    assert _read(db_session, row).sentence == SIGNED_IN_AND_ASKED_AGAIN
    starttls = repr((530, b"5.7.0 Must issue a STARTTLS command first", FROM))
    assert failure_sentence(starttls) == USE_TLS


# -- a reminder that reached someone is not sent again by the next run ---------------


def _reminder_setup(db):
    from app.services import collections as svc
    from tests.test_email_failure_rule import _overdue

    svc.seed_default_rules(db)
    return _overdue(db)


def _reminder_rows(db):
    from app.models.collections import InvoiceReminder

    db.expire_all()
    return db.query(InvoiceReminder).order_by(InvoiceReminder.id).all()


def _three_runs(client, db, server, automatic=False):
    """The run made three times (three days of the automatic run, or the
    Send due reminders button three times): each run's answer, and how
    many messages the server had taken after it."""
    from datetime import date, timedelta

    from app.services import collections as svc

    answers, taken = [], []
    for day in range(3):
        if automatic:
            set_setting(db, "invoice_reminders_auto", "true")
            db.commit()
            answers.append(svc.run_if_due(db, date.today() + timedelta(days=day)))
        else:
            r = client.post("/api/collections/reminders/run", json={})
            assert r.status_code == 200, r.text
            answers.append(r.json())
        taken.append(server.messages)
    return answers, taken


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize(
    "reply,copy_setting,copy",
    [
        ("550 5.1.1 User unknown", "smtp_always_bcc", BLIND),
        ("451 4.7.1 Greylisted, try again later", "smtp_always_cc", COPY),
    ],
)
def test_a_reminder_that_reached_a_copy_is_not_sent_again_by_the_run(
    client,
    db_session,
    seed_accounts,
    stub,
    no_pdfs,
    reply,
    copy_setting,
    copy,
    automatic,
):
    """The To address is turned away (refused, or put off) and a copy
    takes the message. The send is a failed one and says so, and the run
    does not send the same reminder to the copy again each day; Remind, by
    hand, still sends."""
    customer, inv = _reminder_setup(db_session)
    to = customer.email
    server = stub({"RCPT": _refuse(to, reply)})
    _configure(db_session, server.port)
    set_setting(db_session, copy_setting, copy)
    db_session.commit()
    answers, taken = _three_runs(client, db_session, server, automatic)
    assert taken == [1, 1, 1]
    first, second, third = answers
    assert (first["sent"], first["failed"]) == (0, 1)
    (line,) = first["errors"]
    assert line.startswith(f"Mail Probe, Invoice {inv.invoice_number}: The email ")
    assert line.endswith(f"A copy still went to: {copy}.")
    assert to in line
    for later in (second, third):
        assert (later["sent"], later["failed"], later["errors"]) == (0, 0, [])
    # one audit row, failed, with the sentence it was given
    (row,) = _reminder_rows(db_session)
    assert row.status == "failed" and row.error.endswith(
        f"A copy still went to: {copy}."
    )
    # the run offers nothing for it; Remind, by hand, is not held back
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"] == []
    by_hand = client.post(f"/api/collections/invoices/{inv.id}/remind")
    assert by_hand.status_code == 400, by_hand.text
    assert by_hand.json()["detail"] == line
    assert server.messages == 2
    assert [r.status for r in _reminder_rows(db_session)] == ["failed", "failed"]


def test_a_reminder_held_back_goes_when_remind_reaches_the_customer(
    client, db_session, seed_accounts, stub, no_pdfs
):
    """Once the address is fixed, Remind sends it and the rule is spent
    in the ordinary way."""
    customer, inv = _reminder_setup(db_session)
    refusing = stub({"RCPT": _refuse(customer.email)})
    _configure(db_session, refusing.port)
    set_setting(db_session, "smtp_always_bcc", BLIND)
    db_session.commit()
    _three_runs(client, db_session, refusing)
    assert refusing.messages == 1
    working = stub()
    _configure(db_session, working.port)
    r = client.post(f"/api/collections/invoices/{inv.id}/remind")
    assert r.status_code == 200, r.text
    assert working.messages == 1
    assert [r.status for r in _reminder_rows(db_session)] == ["failed", "sent"]
    _three_runs(client, db_session, working)
    assert working.messages == 1


def test_a_reminder_that_reached_nobody_is_tried_again_by_each_run(
    client, db_session, seed_accounts, stub, no_pdfs
):
    """To refused and no copy: nothing went out, so the next run tries
    again, as before."""
    _reminder_setup(db_session)
    server = stub({"RCPT": ["550 5.1.1 User unknown"]})
    _configure(db_session, server.port)
    answers, _taken = _three_runs(client, db_session, server)
    assert [(a["sent"], a["failed"]) for a in answers] == [(0, 1)] * 3
    rcpts = [line for line in server.lines if line.upper().startswith("RCPT")]
    assert len(rcpts) == 3 and server.messages == 0
    assert [r.status for r in _reminder_rows(db_session)] == ["failed"] * 3


def test_a_refused_copy_beside_a_reminder_that_went_is_sent_once(
    client, db_session, seed_accounts, stub, no_pdfs
):
    """To accepted and the copy refused: a sent reminder with a note, and
    the rule is spent, as before."""
    _reminder_setup(db_session)
    server = stub({"RCPT": _refuse(BLIND)})
    _configure(db_session, server.port)
    set_setting(db_session, "smtp_always_bcc", BLIND)
    db_session.commit()
    answers, taken = _three_runs(client, db_session, server)
    assert taken == [1, 1, 1]
    assert [(a["sent"], a["failed"]) for a in answers] == [(1, 0), (0, 0), (0, 0)]
    assert answers[0]["notes"] and BLIND in answers[0]["notes"][0]
    assert [r.status for r in _reminder_rows(db_session)] == ["sent"]


def test_what_a_failed_send_reached_is_read_from_our_own_record(db_session):
    """ "Reached anyone" is the copies our writer recorded in its tag,
    given by the one reader; a sentence or the server's lines are never
    read for it."""
    partly = RecipientsPartlyRefused({TO: (550, b"5.1.1 User unknown")}, [COPY, BLIND])
    row = EmailLog(
        entity_type="reminder",
        entity_id=1,
        recipient=TO,
        subject="s",
        status="failed",
        error_message=stored_text(partly),
    )
    assert _read(db_session, row).reached == [COPY, BLIND]
    # the server's own line naming a copy is not our record
    row.error_message = (
        f"[recipients {TO} 550 5.1.1] {TO}: 550 5.1.1 User unknown; sent {COPY}"
    )
    assert _read(db_session, row).reached == []
    row.error_message = f"[disconnected after-body] {CLOSED}"
    assert _read(db_session, row).reached == []
    row.error_message = repr({TO: (550, b"5.1.1 User unknown")})
    assert _read(db_session, row).reached == []
    # a send that went out reached its To address: this is about failures
    row.status, row.error_message = "sent", stored_text(
        RecipientsPartlyRefused({COPY: (550, b"5.1.1 User unknown")}, [TO])
    )
    assert _read(db_session, row).reached == []
