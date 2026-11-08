"""Collections (#653): reminder rules that send once, the open-invoice list,
the run / auto-run / single-invoice endpoints, the claim before a send, and
who is reminded about a sub-customer billed with its parent.

No test reaches WeasyPrint or SMTP: the invoice PDF and send_email are
replaced where the collections service imports them. The suite's database
is built by create_all, so the four starter rules that migration
c8e1a4d7f305 seeds are written by the `rules` fixture instead."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.collections import InvoiceReminder, InvoiceReminderRule
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.services import collections as svc
from app.services.settings_service import get_setting_raw, set_setting

TODAY = date.today()
NO_SMTP = (
    "Email isn't set up yet, so no reminders were sent. Enter your mail "
    "server under Settings, Email, then try again."
)


@pytest.fixture
def mail(monkeypatch, db_session):
    """SMTP configured; send_email records calls and succeeds."""
    calls = []

    def fake_send(**kw):
        calls.append(kw)
        return True

    monkeypatch.setattr("app.services.collections.send_email", fake_send)
    monkeypatch.setattr(
        "app.services.collections.generate_invoice_pdf", lambda *a, **k: b"%PDF"
    )
    set_setting(db_session, "smtp_host", "smtp.test")
    db_session.commit()
    mail.calls = calls
    return calls


@pytest.fixture
def rules(client, db_session):
    """The four starter rules, seeded as the migration seeds them, as
    {days_from_due: rule}."""
    svc.seed_default_rules(db_session)
    got = client.get("/api/collections/rules").json()
    return {r["days_from_due"]: r for r in got}


def _customer(db, name="Acme", email="acme@example.com", **kw):
    c = Customer(name=name, email=email, is_active=True, **kw)
    db.add(c)
    db.commit()
    return c


_n = [0]


def _invoice(db, cust, due_in=-5, balance=100, status=InvoiceStatus.SENT, **kw):
    """An invoice due `due_in` days from today (negative = overdue)."""
    _n[0] += 1
    inv = Invoice(
        invoice_number=f"C{_n[0]:04d}",
        customer_id=cust.id,
        status=status,
        date=TODAY - timedelta(days=60),
        due_date=TODAY + timedelta(days=due_in),
        total=balance,
        balance_due=balance,
        **kw,
    )
    db.add(inv)
    db.commit()
    return inv


def _run(client, **body):
    return client.post("/api/collections/reminders/run", json=body)


# ---- rules ----


def test_a_read_never_seeds_and_the_seed_runs_once(client, db_session):
    """The four defaults come from the migration, once (review of #654):
    a GET writes nothing, a read-only token's included, and a company that
    deleted every rule keeps an empty list."""
    assert client.get("/api/collections/rules").json() == []
    assert db_session.query(InvoiceReminderRule).count() == 0
    assert svc.seed_default_rules(db_session) == 4
    got = client.get("/api/collections/rules").json()
    assert [x["days_from_due"] for x in got] == [-3, 3, 7, 15]
    assert [x["label"] for x in got] == [
        "3 days before due",
        "3 days overdue",
        "7 days overdue",
        "15 days overdue",
    ]
    assert all(x["enabled"] for x in got)
    assert svc.seed_default_rules(db_session) == 0  # a second seed adds nothing
    for r in got:
        assert client.delete(f"/api/collections/rules/{r['id']}").status_code == 200
    assert client.get("/api/collections/rules").json() == []
    assert db_session.query(InvoiceReminderRule).count() == 0


def test_migration_seeds_the_rules_once(tmp_path):
    """A company file walked to head by alembic has the four rules, the same
    text the service names, and a second walk from there adds none."""
    import sqlite3

    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{(tmp_path / 'c.db').as_posix()}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    with sqlite3.connect(tmp_path / "c.db") as con:
        rows = con.execute(
            "SELECT days_from_due, enabled, subject, body FROM "
            "invoice_reminder_rules ORDER BY days_from_due"
        ).fetchall()
    assert [(d, bool(e), s, b) for d, e, s, b in rows] == [
        (d, True, s, b) for d, s, b in svc.DEFAULT_RULES
    ]


def test_rule_labels():
    from app.services.collections import rule_label

    assert rule_label(-3) == "3 days before due"
    assert rule_label(0) == "On the due date"
    assert rule_label(1) == "1 day overdue"
    assert rule_label(7) == "7 days overdue"


def test_rule_create_update_validation(client, rules):
    ok = {"days_from_due": 30, "subject": "Late {{ invoice_number }}", "body": "Hi"}
    r = client.post("/api/collections/rules", json=ok)
    assert r.status_code == 201
    assert r.json()["label"] == "30 days overdue"
    rid = r.json()["id"]

    dup = client.post("/api/collections/rules", json=ok)
    assert dup.status_code == 400
    assert dup.json()["detail"] == "A rule for that day already exists"

    bad = client.post(
        "/api/collections/rules", json={**ok, "days_from_due": 40, "body": "{{ nope }}"}
    )
    assert bad.status_code == 400
    assert "nope" in bad.json()["detail"]

    blank = client.post(
        "/api/collections/rules", json={**ok, "days_from_due": 41, "subject": "  "}
    )
    assert blank.status_code == 400

    # update: same day as itself is fine, another rule's day is not
    assert (
        client.put(
            f"/api/collections/rules/{rid}", json={"days_from_due": 30}
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/collections/rules/{rid}", json={"days_from_due": 7}
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/api/collections/rules/{rid}", json={"body": "{{ x }}"}
        ).status_code
        == 400
    )
    off = client.put(f"/api/collections/rules/{rid}", json={"enabled": False})
    assert off.json()["enabled"] is False
    assert client.put("/api/collections/rules/9999", json={}).status_code == 404


def test_delete_rule_refused_after_sends(client, db_session, rules, mail):
    cust = _customer(db_session)
    _invoice(db_session, cust, due_in=-5)  # 5 days overdue: the 3-day rule
    assert _run(client).json()["sent"] == 1
    sent_rule = rules[3]["id"]
    r = client.delete(f"/api/collections/rules/{sent_rule}")
    assert r.status_code == 409
    assert "disable it instead" in r.json()["detail"]
    # an untouched rule deletes
    assert client.delete(f"/api/collections/rules/{rules[15]['id']}").json() == {
        "deleted": True
    }


def test_readonly_cannot_run_or_edit_rules(client):
    tok = client.post("/api/tokens", json={"label": "ro", "role": "readonly"}).json()[
        "token"
    ]
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {tok}"
    assert api.get("/api/collections/rules").status_code == 200
    assert api.post("/api/collections/reminders/run", json={}).status_code == 403
    assert api.post("/api/collections/rules", json={}).status_code == 403


def test_bookkeeper_cannot_write_rules_but_can_run(client):
    tok = client.post("/api/tokens", json={"label": "bk", "role": "bookkeeper"}).json()[
        "token"
    ]
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {tok}"
    body = {"days_from_due": 99, "subject": "s", "body": "b"}
    assert api.post("/api/collections/rules", json=body).status_code == 403
    assert api.post("/api/collections/reminders/run", json={}).status_code == 200


# ---- matching ----


def test_before_due_rule_only_while_not_yet_due(client, db_session, rules, mail):
    cust = _customer(db_session)
    ahead = _invoice(db_session, cust, due_in=2)  # inside the -3 window
    _invoice(db_session, cust, due_in=10)  # too early for any rule
    due = client.get("/api/collections/reminders/due").json()
    assert [i["invoice_id"] for i in due["items"]] == [ahead.id]
    assert due["items"][0]["days_from_due"] == -2
    assert due["items"][0]["label"] == "3 days before due"
    # once it is due, the -3 rule no longer matches and nothing else does yet
    on_due = _invoice(db_session, cust, due_in=0)
    items = client.get("/api/collections/reminders/due").json()["items"]
    assert on_due.id not in [i["invoice_id"] for i in items]


def test_largest_matching_rule_wins_and_one_email(client, db_session, rules, mail):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-20)
    due = client.get("/api/collections/reminders/due").json()
    assert len(due["items"]) == 1
    assert due["items"][0]["rule_id"] == rules[15]["id"]
    assert _run(client).json()["sent"] == 1
    assert len(mail) == 1
    # next run: the firmest matching rule is spent, softer ones stay quiet
    again = _run(client).json()
    assert again["sent"] == 0
    assert len(mail) == 1
    assert db_session.query(InvoiceReminder).filter_by(invoice_id=inv.id).count() == 1


def test_spent_rule_not_resent_next_rule_follows(client, db_session, rules, mail):
    cust = _customer(db_session)
    _invoice(db_session, cust, due_in=-4)
    assert _run(client).json()["sent"] == 1
    assert _run(client).json()["sent"] == 0
    # three days on (the invoice's due date moved back, since a send is
    # always dated today) the 7 day rule matches and goes
    inv = db_session.query(Invoice).one()
    inv.due_date = TODAY - timedelta(days=7)
    db_session.commit()
    assert _run(client).json()["sent"] == 1
    assert len(mail) == 2


def test_a_send_is_dated_today_never_the_as_of_date(client, db_session, rules, mail):
    """Looking a month ahead on the Collections page must not send the final
    reminder early: the run body takes no date (a 422, as every unknown
    field is), the preview is today's, and only today's rule goes."""
    cust = _customer(db_session)
    _invoice(db_session, cust, due_in=-4)  # the 3 day rule today
    later = (TODAY + timedelta(days=30)).isoformat()
    assert _run(client, as_of_date=later).status_code == 422
    assert (
        client.post("/api/collections/statements/run", json={"as_of_date": later})
    ).status_code == 422
    due = client.get(f"/api/collections/reminders/due?as_of_date={later}").json()
    assert due["as_of_date"] == TODAY.isoformat()
    assert [i["label"] for i in due["items"]] == ["3 days overdue"]
    assert _run(client).json()["sent"] == 1
    assert "3 days" not in mail[0]["subject"] and "Friendly" in mail[0]["subject"]


def test_failed_row_is_retried(client, db_session, rules, monkeypatch, mail):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-5)
    outcomes = iter([False, True])
    monkeypatch.setattr(
        "app.services.collections.send_email", lambda **kw: next(outcomes)
    )
    first = _run(client).json()
    assert (first["sent"], first["failed"]) == (0, 1)
    assert cust.name in first["errors"][0] and inv.invoice_number in first["errors"][0]
    second = _run(client).json()
    assert (second["sent"], second["failed"]) == (1, 0)
    statuses = [
        r.status for r in db_session.query(InvoiceReminder).order_by(InvoiceReminder.id)
    ]
    assert statuses == ["failed", "sent"]


def test_opt_out_inactive_and_no_email_skipped(client, db_session, rules, mail):
    optout = _customer(db_session, "Opt", invoice_reminders_enabled=False)
    noemail = _customer(db_session, "None", email=None)
    blank = _customer(db_session, "Blank", email="  ")
    gone = _customer(db_session, "Gone")
    gone.is_active = False
    db_session.commit()
    for c in (optout, noemail, blank, gone):
        _invoice(db_session, c, due_in=-5)
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"] == []
    reasons = {s["customer_name"]: s["reason"] for s in due["skipped"]}
    assert reasons["Opt"] == "reminders are turned off for this customer"
    assert reasons["None"] == "no email address on file"
    assert reasons["Blank"] == "no email address on file"
    assert reasons["Gone"] == "customer is inactive"
    r = _run(client).json()
    assert (r["sent"], r["skipped"]) == (0, 4)
    assert mail == []


def test_only_open_invoices_are_considered(client, db_session, rules, mail):
    cust = _customer(db_session)
    for status in (InvoiceStatus.DRAFT, InvoiceStatus.VOID, InvoiceStatus.PAID):
        _invoice(db_session, cust, due_in=-30, status=status)
    _invoice(db_session, cust, due_in=-30, is_sales_receipt=True)
    _invoice(db_session, cust, due_in=-30, balance=0)
    no_due = _invoice(db_session, cust, due_in=-30)
    no_due.due_date = None
    db_session.commit()
    assert client.get("/api/collections/invoices").json()["items"] == []
    assert client.get("/api/collections/reminders/due").json()["items"] == []
    partial = _invoice(db_session, cust, due_in=-30, status=InvoiceStatus.PARTIAL)
    ids = [
        i["invoice_id"] for i in client.get("/api/collections/invoices").json()["items"]
    ]
    assert ids == [partial.id]


# ---- run ----


def test_run_writes_audit_rows_and_counts(client, db_session, rules, mail):
    cust = _customer(db_session)
    a = _invoice(db_session, cust, due_in=-8, balance=250)
    b = _invoice(db_session, cust, due_in=-1, balance=40)  # nothing matches yet
    r = _run(client).json()
    assert r == {
        "sent": 1,
        "failed": 0,
        "skipped": 0,
        "taken": 0,
        "errors": [],
        "ran_on": TODAY.isoformat(),
    }
    row = db_session.query(InvoiceReminder).one()
    assert (row.invoice_id, row.rule_id) == (a.id, rules[7]["id"])
    assert (row.status, row.trigger, row.days_from_due) == ("sent", "run", 8)
    assert float(row.balance_due) == 250
    assert row.recipient == "acme@example.com"
    call = mail[0]
    assert call["to_email"] == "acme@example.com"
    assert call["attachment_bytes"] == b"%PDF"
    assert call["entity_type"] == "reminder" and call["entity_id"] == a.id
    assert a.invoice_number in call["subject"] and "8 days" in call["subject"]
    assert "$250.00" in call["html_body"] and "Acme" in call["html_body"]
    assert b.id not in {r.invoice_id for r in db_session.query(InvoiceReminder)}


def test_run_limited_to_invoice_ids(client, db_session, rules, mail):
    cust = _customer(db_session)
    a = _invoice(db_session, cust, due_in=-8)
    _invoice(db_session, cust, due_in=-8)
    assert _run(client, invoice_ids=[a.id]).json()["sent"] == 1
    assert [r.invoice_id for r in db_session.query(InvoiceReminder)] == [a.id]


def test_body_is_escaped(client, db_session, rules, mail):
    client.put(
        f"/api/collections/rules/{rules[3]['id']}",
        json={"body": "Hi {{ customer_name }}\nline two"},
    )
    cust = _customer(db_session, "A <b>& Co")
    _invoice(db_session, cust, due_in=-4)
    _run(client)
    body = mail[0]["html_body"]
    assert "A &lt;b&gt;&amp; Co" in body and "<br>line two" in body


def test_run_needs_smtp_only_when_something_sends(client, db_session, rules):
    # no smtp_host set and nothing due: fine
    assert _run(client).json()["sent"] == 0
    cust = _customer(db_session)
    _invoice(db_session, cust, due_in=-5)
    r = _run(client)
    assert r.status_code == 400 and r.json()["detail"] == NO_SMTP


# ---- run-if-due ----


def test_run_if_due_off_by_default(client, rules):
    assert client.post("/api/collections/reminders/run-if-due").json() == {
        "ran": False,
        "reason": "off",
    }


def test_run_if_due_runs_once_a_day_and_stamps(client, db_session, rules, mail):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-5)
    client.put("/api/settings", json={"invoice_reminders_auto": "true"})
    first = client.post("/api/collections/reminders/run-if-due").json()
    assert first["ran"] is True and first["sent"] == 1
    db_session.expire_all()
    assert (
        get_setting_raw(db_session, "invoice_reminders_last_run") == TODAY.isoformat()
    )
    assert db_session.query(InvoiceReminder).one().trigger == "auto"
    assert client.post("/api/collections/reminders/run-if-due").json() == {
        "ran": False,
        "reason": "already_today",
    }
    assert inv.id and len(mail) == 1


def test_run_if_due_without_smtp_does_not_stamp(client, db_session, rules):
    cust = _customer(db_session)
    _invoice(db_session, cust, due_in=-5)
    client.put("/api/settings", json={"invoice_reminders_auto": "true"})
    r = client.post("/api/collections/reminders/run-if-due")
    assert r.status_code == 200
    assert r.json() == {"ran": False, "reason": "no_smtp"}
    assert not get_setting_raw(db_session, "invoice_reminders_last_run")


# ---- one invoice ----


def test_remind_picks_largest_matching_then_next(client, db_session, rules, mail):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-8)
    url = f"/api/collections/invoices/{inv.id}/remind"
    assert client.post(url).json() == {
        "sent": True,
        "label": "7 days overdue",
        "to": "acme@example.com",
    }
    # the matching rule is spent: the nearest one ahead goes next
    assert client.post(url).json() == {
        "sent": True,
        "label": "15 days overdue",
        "to": "acme@example.com",
    }
    r = client.post(url)
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Every reminder rule has already been sent for this invoice."
    )
    assert {x.trigger for x in db_session.query(InvoiceReminder)} == {"manual"}


def test_remind_before_the_first_after_due_rule(client, db_session, rules, mail):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-1)  # 1 day overdue; first rule is 3
    assert (
        client.post(f"/api/collections/invoices/{inv.id}/remind").json()["label"]
        == "3 days overdue"
    )


def test_remind_refusals(client, db_session, rules, mail):
    assert client.post("/api/collections/invoices/9999/remind").status_code == 404
    optout = _customer(db_session, "Opt", invoice_reminders_enabled=False)
    noemail = _customer(db_session, "NoMail", email=None)
    paid = _invoice(db_session, _customer(db_session, "P"), status=InvoiceStatus.PAID)
    for inv in (_invoice(db_session, optout), _invoice(db_session, noemail), paid):
        r = client.post(f"/api/collections/invoices/{inv.id}/remind")
        assert r.status_code == 400 and r.json()["detail"]
    assert mail == []


def test_remind_without_smtp(client, db_session, rules):
    inv = _invoice(db_session, _customer(db_session), due_in=-8)
    r = client.post(f"/api/collections/invoices/{inv.id}/remind")
    assert r.status_code == 400 and r.json()["detail"] == NO_SMTP


# ---- the list ----


def test_invoice_list_shape_totals_and_filters(client, db_session, rules, mail):
    a = _customer(db_session, "Alpha")
    b = _customer(db_session, "Beta", invoice_reminders_enabled=False)
    late = _invoice(db_session, a, due_in=-8, balance=200)
    soon = _invoice(db_session, b, due_in=2, balance=50)
    _run(client)  # sends the 7 day reminder for `late`
    got = client.get("/api/collections/invoices").json()
    assert got["as_of_date"] == TODAY.isoformat()
    assert [i["invoice_id"] for i in got["items"]] == [late.id, soon.id]
    first, second = got["items"]
    assert first["customer_name"] == "Alpha"
    assert first["customer_email"] == "acme@example.com"
    assert first["days_overdue"] == 8 and first["balance_due"] == 200
    assert first["due_date"] == late.due_date.isoformat()
    assert first["reminders_enabled"] is True
    assert first["last_reminder"]["label"] == "7 days overdue"
    assert first["last_reminder"]["status"] == "sent"
    assert first["last_reminder"]["rule_id"] == rules[7]["id"]
    nxt = first["next_reminder"]
    assert nxt["label"] == "15 days overdue" and nxt["ready"] is False
    assert nxt["due_on"] == (late.due_date + timedelta(days=15)).isoformat()
    assert second["days_overdue"] == -2 and second["reminders_enabled"] is False
    assert second["last_reminder"] is None
    assert second["next_reminder"]["label"] == "3 days before due"
    assert second["next_reminder"]["ready"] is True
    assert got["totals"] == {
        "count": 2,
        "balance_due": 250,
        "overdue_count": 1,
        "overdue_balance": 200,
    }
    only = client.get("/api/collections/invoices?overdue_only=true").json()
    assert [i["invoice_id"] for i in only["items"]] == [late.id]
    mine = client.get(f"/api/collections/invoices?customer_id={b.id}").json()
    assert [i["invoice_id"] for i in mine["items"]] == [soon.id]
    future = (TODAY + timedelta(days=30)).isoformat()
    far = client.get(f"/api/collections/invoices?as_of_date={future}").json()
    assert all(i["days_overdue"] > 0 for i in far["items"])
    assert far["as_of_date"] == future


def test_customer_flags_round_trip(client):
    r = client.post(
        "/api/customers",
        json={"name": "Flags Inc", "monthly_statement": True},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["monthly_statement"] is True
    assert body["invoice_reminders_enabled"] is True
    r = client.put(
        f"/api/customers/{body['id']}",
        json={"invoice_reminders_enabled": False, "monthly_statement": False},
    )
    assert r.json()["invoice_reminders_enabled"] is False
    assert r.json()["monthly_statement"] is False
    assert (
        client.get(f"/api/customers/{body['id']}").json()["invoice_reminders_enabled"]
        is False
    )


def test_settings_keys_default(client):
    s = client.get("/api/settings").json()
    assert s["invoice_reminders_auto"] == "false"
    assert s["invoice_reminders_last_run"] == ""


def test_rule_table_starts_empty_in_a_create_all_database(db_session):
    assert db_session.query(InvoiceReminderRule).count() == 0


# ---- the claim before a send ----


def _claimed(db, inv, rule_id, when=None, status="sending"):
    row = InvoiceReminder(
        invoice_id=inv.id,
        rule_id=rule_id,
        customer_id=inv.customer_id,
        recipient="other@example.com",
        status=status,
        trigger="run",
        days_from_due=5,
        balance_due=inv.balance_due,
        sent_at=when or datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


def test_a_reminder_another_window_claimed_is_not_sent_again(
    client, db_session, rules, mail
):
    """Two tabs, or the automatic run beside Send due reminders: the first
    writes the audit row as 'sending' and the second finds it there."""
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-5)
    _claimed(db_session, inv, rules[3]["id"])
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"] == []  # a live claim is spent, not due
    assert _run(client).json()["sent"] == 0 and mail == []
    # Remind now treats the claim as sent, as it treats a sent row: it moves
    # on to the next rule rather than sending the claimed one twice
    r = client.post(f"/api/collections/invoices/{inv.id}/remind")
    assert r.status_code == 200 and r.json()["label"] == "7 days overdue"


def test_two_live_rows_for_one_invoice_and_rule_are_refused(db_session, rules):
    """The database holds the rule: one live row (sending or sent) per
    invoice and rule, however the second insert is attempted."""
    from sqlalchemy.exc import IntegrityError

    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-5)
    _claimed(db_session, inv, rules[3]["id"], status="sent")
    with pytest.raises(IntegrityError):
        _claimed(db_session, inv, rules[3]["id"])
    db_session.rollback()
    # a failed row holds nothing: a retry's claim goes in beside it
    _claimed(db_session, inv, rules[7]["id"], status="failed")
    _claimed(db_session, inv, rules[7]["id"])


def test_a_stale_claim_is_marked_failed_and_tried_again(
    client, db_session, rules, mail
):
    """The app closed mid-send: a 'sending' row older than ten minutes is a
    send that never finished, so the next run marks it failed and sends."""
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-5)
    old = _claimed(
        db_session,
        inv,
        rules[3]["id"],
        when=datetime.now(timezone.utc) - svc.STALE_CLAIM - timedelta(minutes=1),
    )
    assert _run(client).json()["sent"] == 1 and len(mail) == 1
    db_session.expire_all()
    assert (old.status, old.error) == ("failed", "The send did not finish.")
    fresh = db_session.query(InvoiceReminder).filter(InvoiceReminder.id != old.id).one()
    assert fresh.status == "sent" and fresh.rule_id == rules[3]["id"]


def test_the_claimed_row_is_what_the_send_finishes(client, db_session, rules, mail):
    """One row per send: claimed as 'sending', finished as 'sent' with the
    email log's id, or 'failed' with the sentence."""
    cust = _customer(db_session)
    _invoice(db_session, cust, due_in=-5)
    assert _run(client).json()["sent"] == 1
    row = db_session.query(InvoiceReminder).one()
    assert row.status == "sent" and row.error is None


def test_auto_run_claims_the_day_once(client, db_session, rules, mail):
    """Two windows opened together: the day's stamp is taken in one guarded
    update, so the second finds it taken and sends nothing."""
    cust = _customer(db_session)
    _invoice(db_session, cust, due_in=-5)
    client.put("/api/settings", json={"invoice_reminders_auto": "true"})
    assert svc._claim_day(db_session, TODAY) is True
    assert svc._claim_day(db_session, TODAY) is False
    assert client.post("/api/collections/reminders/run-if-due").json() == {
        "ran": False,
        "reason": "already_today",
    }
    assert mail == []


# ---- a sub-customer billed with its parent ----


def _property_and_tenant(db, **tenant_flags):
    prop = _customer(db, "Oak St", email="oak@example.com")
    tenant = Customer(
        name="Unit 1",
        email="unit1@example.com",
        is_active=True,
        parent_customer_id=prop.id,
        bill_with_parent=True,
        **tenant_flags,
    )
    db.add(tenant)
    db.commit()
    inv = _invoice(db, tenant, due_in=-5, balance=900)
    return prop, tenant, inv


def _choose(client, who):
    client.put("/api/settings", json={"invoice_reminders_billed_sub_to": who})


def test_billed_sub_reminder_goes_to_the_property_by_default(
    client, db_session, rules, mail
):
    prop, tenant, inv = _property_and_tenant(db_session)
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"][0]["recipient"] == "oak@example.com"
    assert due["items"][0]["recipient_names"] == ["Oak St"]
    assert _run(client).json()["sent"] == 1
    call = mail[0]
    assert call["to_email"] == "oak@example.com" and call["cc"] is None
    assert "Hello Oak St" in call["html_body"]
    row = db_session.query(InvoiceReminder).one()
    assert row.recipient == "oak@example.com" and row.customer_id == tenant.id
    listed = client.get("/api/collections/invoices").json()["items"][0]
    assert listed["billed_to_name"] == "Oak St"
    assert listed["reminder_to"] == "oak@example.com"
    # the property's filter takes in the tenant billed with it
    mine = client.get(f"/api/collections/invoices?customer_id={prop.id}").json()
    assert [i["invoice_id"] for i in mine["items"]] == [inv.id]
    assert mine["totals"]["balance_due"] == 900


def test_billed_sub_reminder_to_the_tenant_or_both(client, db_session, rules, mail):
    _property_and_tenant(db_session)
    _choose(client, "tenant")
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"][0]["recipient"] == "unit1@example.com"
    _choose(client, "both")
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"][0]["recipient"] == "unit1@example.com, oak@example.com"
    assert _run(client).json()["sent"] == 1
    call = mail[0]
    assert call["to_email"] == "unit1@example.com" and call["cc"] == ["oak@example.com"]
    assert "Hello Unit 1" in call["html_body"]
    assert db_session.query(InvoiceReminder).one().recipient == (
        "unit1@example.com, oak@example.com"
    )


def test_the_property_opt_out_counts_when_it_is_written_to(
    client, db_session, rules, mail
):
    prop, tenant, inv = _property_and_tenant(db_session)
    prop.invoice_reminders_enabled = False
    db_session.commit()
    # addressed to the property: nothing goes, and the reason names it
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"] == []
    assert due["skipped"][0]["reason"] == "reminders are turned off for this customer"
    listed = client.get("/api/collections/invoices").json()["items"][0]
    assert listed["reminders_enabled"] is False
    # both: whichever may be written to is, the tenant here
    _choose(client, "both")
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"][0]["recipient"] == "unit1@example.com"
    # the tenant opted out too: nobody, and the reasons name each
    tenant.invoice_reminders_enabled = False
    db_session.commit()
    due = client.get("/api/collections/reminders/due").json()
    assert due["items"] == []
    assert due["skipped"][0]["reason"] == (
        "Unit 1: reminders are turned off for this customer; "
        "Oak St: reminders are turned off for this customer"
    )
    # the tenant's own opt-out does not hold back a reminder to the property
    tenant.invoice_reminders_enabled = False
    prop.invoice_reminders_enabled = True
    db_session.commit()
    _choose(client, "property")
    assert _run(client).json()["sent"] == 1 and mail[0]["to_email"] == "oak@example.com"


def test_billed_sub_setting_defaults_and_unknown_values(client):
    assert client.get("/api/settings").json()["invoice_reminders_billed_sub_to"] == (
        "property"
    )
    assert svc.billed_sub_choice({"invoice_reminders_billed_sub_to": "nobody"}) == (
        "property"
    )
