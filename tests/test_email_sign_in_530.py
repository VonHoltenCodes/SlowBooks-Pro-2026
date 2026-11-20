"""Round 24, also seen on the Mac: a 530 (authentication required) is not a
refused sender.

Gmail answers MAIL FROM with "530 5.7.0 Authentication Required" when the
send gives no username; smtplib raises that as SMTPSenderRefused, so Test
Email said "The mail server refused the sender address: books@example.com.
Check the From address…". A 530 now says the server wants a sign-in and
none is set (or, when it asks for STARTTLS first, to turn on Use TLS), from
the exception and from the text stored on the email log row alike."""

import smtplib

import pytest

from app.services.email_errors import failure_sentence

SIGN_IN = (
    "The mail server wants a sign-in, and none is set. Enter the username and "
    "password under Settings → Email (SMTP)."
)
STARTTLS = (
    "The mail server takes mail only over a secure connection. Turn on Use "
    "TLS under Settings → Email (SMTP)."
)
GMAIL_530 = (
    b"5.7.0 Authentication Required. For more information, go to\n5.7.0 "
    b"https://support.google.com/mail/?p=WantAuthError"
)
STARTTLS_530 = b"5.7.0 Must issue a STARTTLS command first."


@pytest.mark.parametrize(
    "error, said",
    [
        (smtplib.SMTPSenderRefused(530, GMAIL_530, "books@example.com"), SIGN_IN),
        (smtplib.SMTPResponseException(530, GMAIL_530), SIGN_IN),
        (smtplib.SMTPSenderRefused(530, STARTTLS_530, "books@example.com"), STARTTLS),
    ],
)
def test_a_530_says_the_server_wants_a_sign_in(error, said):
    assert failure_sentence(error, "smtp.gmail.com") == said
    # the same from the text the email log keeps
    assert failure_sentence(str(error), "smtp.gmail.com") == said


def test_a_refused_sender_is_still_a_refused_sender():
    error = smtplib.SMTPSenderRefused(553, b"5.7.1 Sender rejected", "a@b.test")
    assert failure_sentence(error).startswith(
        "The mail server refused the sender address: a@b.test."
    )
    assert failure_sentence(str(error)).startswith(
        "The mail server refused the sender address: a@b.test."
    )


def test_a_wrong_password_is_still_the_credentials_sentence():
    error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password")
    assert failure_sentence(error) == (
        "The server rejected the username or password — check both."
    )
