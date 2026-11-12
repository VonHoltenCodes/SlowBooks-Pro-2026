"""Activity on every document (#894): what happened, who did it, and when.

GET /api/activity?type=<record>&id=<n> reads the audit log (the document's
own rows, its lines', its attachments' and the rows of what touches it), the
email log and the document audits, and answers each event as a sentence,
newest first. Every role reads it; nothing writes through it.
"""

import smtplib
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.attachments import Attachment
from app.models.audit import AuditLog
from app.models.email_log import EmailLog
from app.models.users import ROLE_ACCOUNTANT, ROLE_BOOKKEEPER, ROLE_READONLY, User
from app.routes.invoices import documents
from app.services import auth as auth_service
from app.services.settings_service import set_setting

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "a-password-123"


def _age(db, minutes=1):
    """Move every row written so far back in time, so the next step's rows
    are newer by a clear margin (SQLite stamps to the second)."""
    for model, col in (
        (AuditLog, "timestamp"),
        (EmailLog, "sent_at"),
        (Attachment, "uploaded_at"),
    ):
        for row in db.query(model).all():
            stamp = getattr(row, col)
            if stamp is not None:
                setattr(row, col, stamp - timedelta(minutes=minutes))
    db.commit()


def _activity(client, type_, id_):
    r = client.get(f"/api/activity?type={type_}&id={id_}&tz_offset=0")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def smtp(monkeypatch, db_session):
    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, _from, to, message):
            pass

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")
    set_setting(db_session, "smtp_host", "mail.example.com")
    set_setting(db_session, "smtp_from_email", "books@example.com")
    db_session.commit()


def _texts(found):
    return [e["text"] for e in found["events"]]


def _customer(client, name="Probe Co"):
    r = client.post("/api/customers", json={"name": name, "email": "ap@probe.test"})
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(client, cust_id, **over):
    body = {
        "customer_id": cust_id,
        "date": "2026-06-01",
        "due_date": "2026-06-30",
        "lines": [{"description": "work", "quantity": 1, "rate": 100, "line_order": 0}],
        **over,
    }
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _attach(client, type_, id_, name="receipt.pdf"):
    r = client.post(
        f"/api/attachments/{type_}/{id_}",
        files={"file": (name, b"%PDF-1.4 probe", "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _token(client, role, label):
    r = client.post("/api/tokens", json={"label": label, "role": role})
    assert r.status_code in (200, 201), r.text
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return api


def _signed_in(db, role, display="Pat Reader"):
    name = f"user-{role}"
    db.add(
        User(
            username=name,
            display_name=display,
            password_hash=auth_service.hash_password(PASSWORD),
            role=role,
            is_active=True,
        )
    )
    db.commit()
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


# ---------------------------------------------------------------------------
# an invoice's life, in order
# ---------------------------------------------------------------------------


def test_an_invoices_activity_lists_each_event_in_order(
    client, seed_accounts, db_session, smtp
):
    cust = _customer(client)
    inv = _invoice(client, cust["id"])
    _age(db_session)
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "due_date": "2026-07-15",
            "lines": [
                {
                    "description": "more work",
                    "quantity": 2,
                    "rate": 100,
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    _age(db_session)
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "ap@probe.test", "subject": "Your invoice", "message": ""},
    )
    assert r.status_code == 200, r.text
    _age(db_session)
    _attach(client, "invoice", inv["id"])
    _age(db_session)
    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text

    found = _activity(client, "invoice", inv["id"])
    kinds = [e["kind"] for e in found["events"]]
    # newest first
    assert kinds == ["voided", "attachment", "emailed", "edited", "created"], kinds
    assert found["count"] == 5
    voided, attached, emailed, edited, created = found["events"]
    who = created["who"]
    assert who  # the sign-in's name
    assert created["text"] == f"Invoice {inv['invoice_number']} created by {who}."
    assert "Line: work, $100.00" in created["details"]
    assert edited["text"] == f"Edited by {who}."
    assert "Due date 2026-06-30 → 2026-07-15" in edited["details"]
    assert "Line removed: work, $100.00" in edited["details"]
    assert "Line added: more work, $200.00" in edited["details"]
    assert "Total $100.00 → $200.00" in edited["details"]
    assert emailed["text"] == f"Emailed to ap@probe.test by {who}."
    assert attached["text"] == f"receipt.pdf attached by {who}."
    assert voided["text"] == f"Voided by {who}."
    # each with when, on the clock asked for
    assert all(e["when"] and e["when_local"] for e in found["events"])
    stamps = [e["when"] for e in found["events"]]
    assert stamps == sorted(stamps, reverse=True)


def test_a_failed_send_says_so(client, seed_accounts, db_session, smtp, monkeypatch):
    class Refusing:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, _from, to, message):
            raise smtplib.SMTPRecipientsRefused({to[0]: (550, b"5.1.1 refused")})

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", Refusing)
    inv = _invoice(client, _customer(client)["id"])
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "ap@probe.test", "subject": "Your invoice", "message": ""},
    )
    assert r.status_code == 502
    [sent] = [
        e
        for e in _activity(client, "invoice", inv["id"])["events"]
        if e["kind"] == "emailed"
    ]
    assert sent["text"].startswith("An email to ap@probe.test by ")
    assert sent["text"].endswith("could not be sent.")
    assert "refused" in sent["details"][0]


def test_the_email_log_names_who_sent(client, seed_accounts, db_session, smtp):
    inv = _invoice(client, _customer(client)["id"])
    client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "ap@probe.test", "subject": "Your invoice", "message": ""},
    )
    db_session.expire_all()
    [row] = db_session.query(EmailLog).filter(EmailLog.entity_type == "invoice").all()
    assert row.username  # the acting name, as the audit log writes it


def test_payments_applied_and_unapplied_show_on_the_invoice(client, seed_accounts):
    cust = _customer(client)
    inv = _invoice(client, cust["id"])
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-05",
            "amount": "100.00",
            "method": "Check",
            "check_number": "4412",
            "allocations": [{"invoice_id": inv["id"], "amount": "100.00"}],
        },
    )
    assert r.status_code == 201, r.text
    pay = r.json()
    texts = _texts(_activity(client, "invoice", inv["id"]))
    assert any(
        t.startswith("Payment of $100.00 from Probe Co (check 4412) applied by")
        for t in texts
    ), texts
    # and the payment says where it went
    texts = _texts(_activity(client, "payment", pay["id"]))
    assert any(
        t.startswith(f"$100.00 applied to invoice {inv['invoice_number']}")
        for t in texts
    ), texts


# ---------------------------------------------------------------------------
# a bill, a deposit, a journal entry
# ---------------------------------------------------------------------------


def test_a_bills_activity(client, seed_accounts, db_session):
    vendor = client.post("/api/vendors", json={"name": "Parts Supply"}).json()
    exp = seed_accounts["6900"].id if "6900" in seed_accounts else None
    line = {
        "account_id": exp,
        "description": "parts",
        "amount": "50.00",
        "quantity": 1,
        "rate": "50.00",
    }
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-77",
            "date": "2026-04-01",
            "due_date": "2026-05-01",
            "terms": "Net 30",
            "lines": [line],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    _age(db_session)
    r = client.put(f"/api/bills/{bill['id']}", json={"due_date": "2026-05-15"})
    assert r.status_code == 200, r.text
    _age(db_session)
    _attach(client, "bill", bill["id"], "bill-scan.pdf")
    _age(db_session)
    r = client.post(f"/api/bills/{bill['id']}/void")
    assert r.status_code == 200, r.text
    events = _activity(client, "bill", bill["id"])["events"]
    assert [e["kind"] for e in events] == [
        "voided",
        "attachment",
        "edited",
        "created",
    ], events
    assert events[3]["text"].startswith("Bill B-77 created by ")
    assert "Due date 2026-05-01 → 2026-05-15" in events[2]["details"]
    assert events[1]["text"].startswith("bill-scan.pdf attached by ")
    assert events[0]["text"].startswith("Voided by ")


def test_a_deposits_activity(client, seed_accounts, db_session):
    cust = _customer(client)
    inv = _invoice(client, cust["id"])
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-05",
            "amount": "100.00",
            "method": "Check",
            "allocations": [{"invoice_id": inv["id"], "amount": "100.00"}],
        },
    )
    assert r.status_code == 201, r.text
    pay = r.json()
    _age(db_session)
    [row] = client.get("/api/deposits/pending").json()
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-06-06",
            "reference": "SLIP-1",
            "line_ids": [row["transaction_line_id"]],
        },
    )
    assert r.status_code in (200, 201), r.text
    dep = {"id": r.json()["transaction_id"]}
    _age(db_session)
    r = client.put(f"/api/deposits/{dep['id']}", json={"reference": "SLIP-2"})
    assert r.status_code == 200, r.text
    _age(db_session)
    r = client.post(f"/api/deposits/{dep['id']}/void")
    assert r.status_code == 200, r.text
    events = _activity(client, "deposit", dep["id"])["events"]
    kinds = [e["kind"] for e in events]
    # the void and the payment going back to the deposit list, one save
    assert set(kinds[:2]) == {"voided", "deposit"}, events
    assert any(
        e["text"].startswith(
            "Payment of $100.00 from Probe Co put back on the payments to deposit by "
        )
        for e in events[:2]
    ), events
    assert "edited" in kinds and "created" in kinds and "deposit" in kinds, kinds
    edited = next(e for e in events if e["kind"] == "edited")
    assert "Reference SLIP-1 → SLIP-2" in edited["details"]
    took = [e for e in events if e["kind"] == "deposit"][-1]
    assert took["text"].startswith("Took payment of $100.00 from Probe Co by "), took
    # and the payment says it was deposited
    texts = _texts(_activity(client, "payment", pay["id"]))
    assert any(
        t.startswith("Deposited in the deposit of 2026-06-06 by ") for t in texts
    ), texts


def _journal(client, accounts, debit=100, description="Accrue materials"):
    body = {
        "date": "2026-09-02",
        "description": description,
        "lines": [
            {"account_id": accounts["6900"].id, "debit": debit, "credit": 0},
            {"account_id": accounts["2500"].id, "debit": 0, "credit": debit},
        ],
    }
    return body


def test_a_journal_entrys_activity(client, seed_accounts, db_session):
    body = _journal(client, seed_accounts)
    r = client.post("/api/journal", json=body)
    assert r.status_code == 201, r.text
    je = r.json()
    _age(db_session)
    r = client.put(
        f"/api/journal/{je['id']}", json=_journal(client, seed_accounts, debit=150)
    )
    assert r.status_code == 200, r.text
    _age(db_session)
    _attach(client, "journal_entry", je["id"], "backup.pdf")
    _age(db_session)
    r = client.post(f"/api/journal/{je['id']}/void")
    assert r.status_code == 200, r.text
    events = _activity(client, "journal_entry", je["id"])["events"]
    assert [e["kind"] for e in events] == [
        "voided",
        "attachment",
        "edited",
        "created",
    ], events
    assert events[3]["text"].startswith("Journal Entry created by ")
    edited = events[2]["details"]
    assert any(
        d.startswith("Line removed: ") and "debit $100.00" in d for d in edited
    ), edited
    assert any(
        d.startswith("Line added: ") and "debit $150.00" in d for d in edited
    ), edited


# ---------------------------------------------------------------------------
# who: a token, a read-only sign-in, an accountant
# ---------------------------------------------------------------------------


def test_an_api_tokens_edit_names_the_token(client, seed_accounts, db_session):
    inv = _invoice(client, _customer(client)["id"])
    _age(db_session)
    bot = _token(client, ROLE_BOOKKEEPER, "probe-bot")
    r = bot.put(f"/api/invoices/{inv['id']}", json={"due_date": "2026-08-01"})
    assert r.status_code == 200, r.text
    edited = _activity(client, "invoice", inv["id"])["events"][0]
    assert edited["who"] == "an API token (probe-bot)"
    assert edited["text"] == "Edited by an API token (probe-bot)."
    assert "Due date 2026-06-30 → 2026-08-01" in edited["details"]


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_ACCOUNTANT])
def test_a_read_only_sign_in_and_an_accountant_read_it(
    client, seed_accounts, db_session, role
):
    inv = _invoice(client, _customer(client)["id"])
    before = db_session.query(AuditLog).count()
    reader = _signed_in(db_session, role)
    found = _activity(reader, "invoice", inv["id"])
    assert found["count"] >= 1
    assert found["events"][-1]["kind"] == "created"
    # a read writes nothing (the sign-in writes its own row; the read none)
    after_login = db_session.query(AuditLog).count()
    _activity(reader, "invoice", inv["id"])
    assert db_session.query(AuditLog).count() == after_login >= before


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_BOOKKEEPER])
def test_a_token_reads_it(client, seed_accounts, role):
    inv = _invoice(client, _customer(client)["id"])
    bot = _token(client, role, f"{role}-reader")
    assert _activity(bot, "invoice", inv["id"])["count"] >= 1


def test_the_closed_heading_asks_for_the_count_only(client, seed_accounts):
    inv = _invoice(client, _customer(client)["id"])
    r = client.get(f"/api/activity?type=invoice&id={inv['id']}&count_only=true")
    assert r.status_code == 200
    assert r.json()["count"] >= 1 and r.json()["events"] == []


def test_an_unknown_type_or_id_is_a_404_sentence(client, seed_accounts):
    r = client.get("/api/activity?type=spaceship&id=1")
    assert r.status_code == 404
    assert (
        r.json()["detail"]
        == "SlowBooks Pro keeps no activity for a record of type 'spaceship'."
    )
    r = client.get("/api/activity?type=invoice&id=999")
    assert r.status_code == 404
    assert r.json()["detail"] == "There is no invoice 999."
    # a journal entry's id is not an expense's
    je = client.post("/api/journal", json=_journal(client, seed_accounts)).json()
    r = client.get(f"/api/activity?type=expense&id={je['id']}")
    assert r.status_code == 404


def test_a_nonprofit_reads_its_own_words(client, seed_accounts, db_session):
    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()
    inv = _invoice(client, _customer(client)["id"])
    created = _activity(client, "invoice", inv["id"])["events"][-1]
    assert created["text"].startswith("Pledge ")


def test_an_imports_rows_name_the_import():
    from app.services.request_context import import_source

    assert import_source("POST", "/api/iif/import") == "import:IIF"
    assert import_source("POST", "/api/migration/xero/import") == "import:Xero"
    assert import_source("POST", "/api/migration/xero/dry-run") is None
    assert import_source("GET", "/api/iif/import") is None
    assert import_source("POST", "/api/invoices") is None


def test_the_import_and_the_system_read_as_the_system(db_session):
    from app.services.activity import _Words

    words = _Words(db_session)
    assert words.who("system") == "the system"
    assert words.who("token:sync") == "an API token (sync)"
    assert (
        words.who("alice", "import:IIF")
        == "the system, in the IIF import, run by alice"
    )
    assert words.who("system", "import:IIF") == "the system, in the IIF import"


# ---------------------------------------------------------------------------
# the administrator's audit log is as it was
# ---------------------------------------------------------------------------


def test_the_administrators_audit_log_page_is_unchanged(
    client, seed_accounts, db_session
):
    inv = _invoice(client, _customer(client)["id"])
    r = client.get("/api/audit?table_name=invoices")
    assert r.status_code == 200, r.text
    rows = [x for x in r.json() if x["record_id"] == inv["id"]]
    assert "INSERT" in {x["action"] for x in rows}
    assert {x["source"] for x in rows} == {"api"}
    reader = _signed_in(db_session, ROLE_READONLY)
    assert reader.get("/api/audit").status_code == 403
    src = (ROOT / "app/static/js/audit.js").read_text(encoding="utf-8")
    assert "Audit Log" in src


def test_the_feature_does_not_borrow_another_products_names():
    for path in (
        ROOT / "app/routes/activity.py",
        ROOT / "app/services/activity.py",
        ROOT / "app/static/js/utils.js",
    ):
        src = path.read_text(encoding="utf-8")
        for name in ("Audit History", "Audit Trail"):
            assert name not in src, (path.name, name)


# ---------------------------------------------------------------------------
# the one migration: the email log's sender
# ---------------------------------------------------------------------------


def test_the_email_log_sender_migration(tmp_path):
    import sqlite3

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "sent.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("c894a1e7d2b3").down_revision == "b8640c3e4a5b"
    assert script.get_heads() == ["d897a2c4e6f1"]  # #897 is the head above it
    command.upgrade(cfg, "b8640c3e4a5b")  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO email_log (id, entity_type, entity_id, recipient, status)"
            " VALUES (1, 'invoice', 1, 'ap@probe.test', 'sent')"
        )
    command.upgrade(cfg, "c894a1e7d2b3")  # its own step (#770)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(email_log)")}
        assert "username" in cols
        # a send from before says nobody was recorded
        assert conn.execute("SELECT username FROM email_log").fetchall() == [(None,)]
    command.downgrade(cfg, "b8640c3e4a5b")
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(email_log)")}
        assert "username" not in cols
