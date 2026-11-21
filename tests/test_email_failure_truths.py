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
    TO,
    UNREACHABLE,
    WANTS_SIGN_IN,
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
    not call it the mail server's reply."""
    canary = _canary()
    server = stub({step: [f"abc {canary} x"]})
    _configure(db_session, server.port)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502, r.text
    said = r.json()["detail"]
    assert said == f"Test email failed to send. {UNREACHABLE} {FULL_LIST}"
    assert "reply" not in said and "-1" not in said and canary not in said
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert "-1" not in row.error_message
    (shown,) = client.get("/api/email-log").json()["rows"]
    assert shown["failure"] == UNREACHABLE
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
    assert failure_sentence(stored) == UNREACHABLE


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
    reached" would be untrue: the general sentence, and the copy is said."""
    canary = _canary()
    server = stub({"RCPT": _refuse(TO, f"abc {canary}")})
    ok, row = _send(db_session, server, cc=[COPY])
    assert not ok and server.messages == 1
    seen = _read(db_session, row)
    assert seen.sentence == (
        f"The email could not be sent. A copy still went to: {COPY}."
    )
    assert seen.reason() == f"{seen.sentence} {POINTER}"
    mine = _read(db_session, row, admin=True)
    assert mine.reply == f"{TO}: {NOT_SMTP}{canary}"
    assert mine.with_reply() == mine.sentence  # not called a reply
    # the answers built from it say the copy too
    from app.services.email_errors import send_failed_detail, sentence_for_last_send

    assert sentence_for_last_send(db_session, "", 0) == (
        f"A copy still went to: {COPY}. {POINTER}"
    )
    assert send_failed_detail(db_session, "", 0, False) == (
        f"Email could not be sent. A copy still went to: {COPY}. {POINTER}"
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
