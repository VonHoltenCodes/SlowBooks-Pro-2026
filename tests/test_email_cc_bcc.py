"""CC and BCC on outgoing email (#611, from Discussion #607).

Cc is a header the recipient reads; Bcc must never appear in the message,
only in the SMTP envelope. Both are recorded on the EmailLog row. Settings
smtp_always_cc / smtp_always_bcc are added to every send so batch sends
(statements, acknowledgments) carry them with no dialog.
"""

import smtplib
import ssl
from email import message_from_string

import pytest

from app.models.email_log import EmailLog
from app.routes.invoices import documents
from app.services.email_service import (
    MAX_ADDRESS_LENGTH,
    MAX_ADDRESSES,
    bad_addresses,
    clean_addresses,
    send_email,
)
from app.services.settings_service import set_setting


@pytest.fixture
def smtp(monkeypatch, db_session):
    """SMTP configured and faked: returns the list of (envelope, message)."""
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def ehlo_or_helo_if_needed(self):
            pass

        def has_extn(self, name):
            return name == "starttls"

        def starttls(self, context=None):
            assert context.verify_mode == ssl.CERT_REQUIRED

        def ehlo(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, _from, to, message):
            sent.append((list(to), message_from_string(message)))

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    set_setting(db_session, "smtp_host", "mail.example.com")
    set_setting(db_session, "smtp_from_email", "books@example.com")
    db_session.commit()
    return sent


def _set(db, key, value):
    set_setting(db, key, value)
    db.commit()


def _send(db, **kw):
    return send_email(
        db=db,
        to_email="customer@example.com",
        subject="Invoice 1",
        html_body="<p>hi</p>",
        entity_type="invoice",
        entity_id=1,
        **kw,
    )


# ---------------------------------------------------------------------------
# address parsing
# ---------------------------------------------------------------------------


def test_addresses_split_on_commas_and_semicolons_and_drop_blanks():
    assert clean_addresses("a@x.com, b@x.com;; c@x.com ,") == [
        "a@x.com",
        "b@x.com",
        "c@x.com",
    ]
    assert clean_addresses(["a@x.com", "", None, "A@X.com"]) == ["a@x.com"]
    assert clean_addresses(None) == [] and clean_addresses("") == []


def test_header_injection_is_dropped_not_sent():
    """A CR/LF can only be an injection attempt: the forged header text is
    not an address, so it is rejected, and the real one is kept."""
    assert clean_addresses("a@x.com\r\nBcc: evil@x.com") == []
    assert bad_addresses("a@x.com\r\nBcc: evil@x.com") == ["a@x.com Bcc: evil@x.com"]
    assert clean_addresses("a@x.com,\r\nb@x.com") == ["a@x.com", "b@x.com"]


def test_a_quoted_display_name_with_a_comma_is_one_address():
    """Pasted from Outlook: "Smith, Bob" <bob@x.com> must not split at its
    comma, wherever it sits in the list (sb-qa skeptic, round 2: the first
    pattern only honoured a quote at the start of an entry)."""
    cases = {
        '"Smith, Bob" <bob@x.com>, carol@x.com': ["bob@x.com", "carol@x.com"],
        '"Smith, Bob" <bob@x.com>, "Jones, Ann" <ann@x.com>': [
            "bob@x.com",
            "ann@x.com",
        ],
        'carol@x.com, "Smith, Bob" <bob@x.com>': ["carol@x.com", "bob@x.com"],
        ' "Smith, Bob" <bob@x.com>': ["bob@x.com"],
        '"Books, Inc" <books@b.com>; "Audit, LLP" <audit@b.com>': [
            "books@b.com",
            "audit@b.com",
        ],
        'Bob "The, Builder" <bob@x.com>': ["bob@x.com"],
        '"Smith; Bob" <bob@x.com>': ["bob@x.com"],
    }
    for typed, want in cases.items():
        assert clean_addresses(typed) == want, typed
        assert bad_addresses(typed) == [], typed


def test_a_non_ascii_or_malformed_address_is_rejected_not_encoded():
    kept = clean_addresses("Bob <bob@x.com>, jö@x.com, nobody, a b@x.com")
    assert kept == ["bob@x.com"]
    assert set(bad_addresses("Bob <bob@x.com>, jö@x.com, nobody")) == {
        "jö@x.com",
        "nobody",
    }


def test_the_log_row_is_cut_to_its_column(db_session, smtp):
    many = ", ".join(f"person{i:03d}@example.com" for i in range(40))
    assert _send(db_session, bcc=many)
    envelope, _ = smtp[0]
    assert len(envelope) == 41  # every address still goes out
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert len(row.bcc) <= 500 and row.bcc.endswith("…")


def test_a_missing_subject_still_logs_instead_of_raising(db_session):
    assert not send_email(
        db=db_session, to_email="a@x.com", subject=None, html_body="<p>x</p>"
    )
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.status == "failed" and row.subject == ""


def test_a_field_takes_at_most_fifty_addresses_each_no_longer_than_an_address():
    fifty = ", ".join(f"u{i}@x.com" for i in range(MAX_ADDRESSES))
    assert len(clean_addresses(fifty)) == MAX_ADDRESSES
    with pytest.raises(ValueError, match="Too many Cc addresses"):
        clean_addresses(fifty + ", one-more@x.com", "Cc")
    # duplicates and blanks do not count toward the fifty
    assert len(clean_addresses(fifty + ", " + fifty + ",,")) == MAX_ADDRESSES
    longest = "a" * (MAX_ADDRESS_LENGTH - len("@x.com")) + "@x.com"
    assert clean_addresses(longest) == [longest]
    with pytest.raises(ValueError, match="Bcc address is too long"):
        clean_addresses("b" + longest, "Bcc")


def test_an_always_copy_setting_over_the_cap_fails_the_send_and_says_why(
    db_session, smtp
):
    _set(db_session, "smtp_always_bcc", "x" * 300 + "@x.com")
    assert not _send(db_session)
    assert smtp == []
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.status == "failed"
    assert "Bcc address is too long" in row.error_message


# ---------------------------------------------------------------------------
# the message and the envelope
# ---------------------------------------------------------------------------


def test_cc_is_a_header_and_bcc_is_envelope_only(db_session, smtp):
    assert _send(db_session, cc="office@example.com", bcc="hidden@example.com")
    envelope, msg = smtp[0]
    assert envelope == [
        "customer@example.com",
        "office@example.com",
        "hidden@example.com",
    ]
    assert msg["To"] == "customer@example.com"
    assert msg["Cc"] == "office@example.com"
    assert msg["Bcc"] is None
    assert "hidden@example.com" not in msg.as_string()


def test_no_copies_means_no_cc_header(db_session, smtp):
    assert _send(db_session)
    envelope, msg = smtp[0]
    assert envelope == ["customer@example.com"]
    assert msg["Cc"] is None


def test_the_log_records_the_copies(db_session, smtp):
    _send(db_session, cc="a@x.com, b@x.com", bcc="c@x.com")
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.status == "sent"
    assert row.recipient == "customer@example.com"
    assert row.cc == "a@x.com, b@x.com"
    assert row.bcc == "c@x.com"


def test_the_log_records_copies_on_a_failed_send(db_session):
    # SMTP unconfigured: send_email logs "failed" and the copies are still there
    assert not _send(db_session, bcc="c@x.com")
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.status == "failed" and row.bcc == "c@x.com"


# ---------------------------------------------------------------------------
# always-copy settings
# ---------------------------------------------------------------------------


def test_always_cc_and_bcc_settings_apply_to_every_send(db_session, smtp):
    _set(db_session, "smtp_always_cc", "office@example.com")
    _set(db_session, "smtp_always_bcc", "books@example.com, audit@example.com")
    assert _send(db_session)
    envelope, msg = smtp[0]
    assert msg["Cc"] == "office@example.com"
    assert envelope == [
        "customer@example.com",
        "office@example.com",
        "books@example.com",
        "audit@example.com",
    ]
    assert "audit@example.com" not in msg.as_string()


def test_per_send_copies_come_before_the_always_copies(db_session, smtp):
    _set(db_session, "smtp_always_bcc", "books@example.com")
    assert _send(db_session, bcc="partner@example.com")
    envelope, _ = smtp[0]
    assert envelope == [
        "customer@example.com",
        "partner@example.com",
        "books@example.com",
    ]


def test_an_always_copy_that_is_also_typed_is_copied_once(db_session, smtp):
    """Found by the sb-qa browser flow: Always CC office@ plus office@ typed in
    the dialog put office@ in the envelope twice."""
    _set(db_session, "smtp_always_cc", "office@example.com")
    _set(db_session, "smtp_always_bcc", "Books@example.com")
    assert _send(
        db_session, cc="office@example.com, boss@example.com", bcc="books@example.com"
    )
    envelope, msg = smtp[0]
    assert envelope == [
        "customer@example.com",
        "office@example.com",
        "boss@example.com",
        "books@example.com",
    ]
    assert msg["Cc"] == "office@example.com, boss@example.com"


def test_an_address_already_on_the_mail_is_not_copied_again(db_session, smtp):
    _set(db_session, "smtp_always_bcc", "Customer@example.com")
    assert _send(
        db_session,
        cc="customer@example.com, office@example.com",
        bcc="office@example.com",
    )
    envelope, msg = smtp[0]
    assert envelope == ["customer@example.com", "office@example.com"]
    assert msg["Cc"] == "office@example.com"


# ---------------------------------------------------------------------------
# the invoice route and the settings API
# ---------------------------------------------------------------------------


def test_the_invoice_email_route_passes_cc_and_bcc_through(
    client, seed_accounts, monkeypatch
):
    captured = {}

    def fake_send(**kw):
        captured.update(kw)
        return True

    # The route imports send_email inside the handler, so patch the service.
    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    # The PDF is an attachment the fake never reads; stubbing the renderer
    # keeps the test off WeasyPrint, which the Windows job has not got.
    monkeypatch.setattr(
        documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-1.4 test"
    )
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "a@b.com"}
    ).json()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "due_date": "2026-06-30",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    ).json()
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "a@b.com", "cc": "x@b.com", "bcc": "y@b.com"},
    )
    assert r.status_code == 200, r.text
    assert captured["to_email"] == "a@b.com"
    assert captured["cc"] == "x@b.com" and captured["bcc"] == "y@b.com"

    # over the cap: refused in a sentence, nothing rendered or sent
    captured.clear()
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={
            "recipient": "a@b.com",
            "cc": ", ".join(f"u{i}@b.com" for i in range(MAX_ADDRESSES + 1)),
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Too many Cc addresses: at most 50 on one email."
    assert captured == {}
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "a@b.com", "bcc": "b" * 300 + "@b.com"},
    )
    assert r.status_code == 400, r.text
    assert "Bcc address is too long" in r.json()["detail"]


def test_always_copy_settings_round_trip_and_log_rows_carry_copies(client, db_session):
    r = client.put(
        "/api/settings",
        json={
            "smtp_always_cc": "office@example.com",
            "smtp_always_bcc": "books@example.com",
        },
    )
    assert r.status_code == 200, r.text
    s = client.get("/api/settings").json()
    assert s["smtp_always_cc"] == "office@example.com"
    assert s["smtp_always_bcc"] == "books@example.com"

    db_session.add(
        EmailLog(
            entity_type="invoice",
            entity_id=0,
            recipient="a@x.com",
            cc="b@x.com",
            bcc="c@x.com",
            subject="S",
            status="sent",
        )
    )
    db_session.commit()
    row = client.get("/api/email-log").json()["rows"][0]
    assert row["cc"] == "b@x.com" and row["bcc"] == "c@x.com"


def test_the_route_refuses_an_address_it_cannot_send_to(client, seed_accounts):
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "a@b.com"}
    ).json()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "due_date": "2026-06-30",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    ).json()
    r = client.post(f"/api/invoices/{inv['id']}/email", json={"cc": "office, x@b.com"})
    assert r.status_code == 400 and "office" in r.json()["detail"]
    assert db_count(client) == 0


def db_count(client):
    return client.get("/api/email-log").json()["total"]


def test_always_copy_settings_are_validated_and_normalised(client):
    r = client.put("/api/settings", json={"smtp_always_cc": "nobody, a@b.com"})
    assert r.status_code == 422 and "nobody" in r.json()["detail"]
    r = client.put(
        "/api/settings",
        json={"smtp_always_bcc": '"Books, Inc" <books@b.com>; A@B.com, a@b.com'},
    )
    assert r.status_code == 200, r.text
    assert (
        client.get("/api/settings").json()["smtp_always_bcc"] == "books@b.com, A@B.com"
    )


def test_the_route_counts_the_always_copies_toward_the_cap(
    client, seed_accounts, db_session, monkeypatch
):
    """W-52 (2.31.0 gate): two always-cc addresses in Settings plus forty-nine
    typed is fifty-one; the route refuses it in the limit's own words, not as
    a failed send blamed on the SMTP settings, and nothing is rendered or
    sent."""
    captured = {}

    def fake_send(**kw):
        captured.update(kw)
        return True

    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    monkeypatch.setattr(
        documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-1.4 test"
    )
    _set(db_session, "smtp_always_cc", "office@example.com, books@example.com")
    cust = client.post(
        "/api/customers", json={"name": "Cap Co", "email": "a@b.com"}
    ).json()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "due_date": "2026-06-30",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    ).json()
    typed = ", ".join(f"u{i}@b.com" for i in range(MAX_ADDRESSES - 1))
    r = client.post(
        f"/api/invoices/{inv['id']}/email", json={"recipient": "a@b.com", "cc": typed}
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Too many Cc addresses: at most 50 on one email."
    assert captured == {}
    # forty-eight typed and the two from Settings is fifty: it goes
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "a@b.com", "cc": ", ".join(typed.split(", ")[:-1])},
    )
    assert r.status_code == 200, r.text
    assert captured["to_email"] == "a@b.com"
