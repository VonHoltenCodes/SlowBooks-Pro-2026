"""Collections (#653): the monthly statement flag, who is due one, the
batch send, and the month's row per customer that is claimed before the
send and names the local month (never a UTC timestamp). WeasyPrint and SMTP
are replaced where the service imports them."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.collections import CustomerStatement
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.services import collections as svc
from app.services.settings_service import set_setting

TODAY = date.today()
NO_SMTP = (
    "Email isn't set up yet, so no statements were sent. Enter your mail "
    "server under Settings, Email, then try again."
)


@pytest.fixture
def mail(monkeypatch, db_session):
    calls = []

    def fake_send(**kw):
        calls.append(kw)
        return True

    monkeypatch.setattr("app.services.collections.send_email", fake_send)
    monkeypatch.setattr(
        "app.services.collections.generate_statement_pdf", lambda *a, **k: b"%PDF"
    )
    set_setting(db_session, "smtp_host", "smtp.test")
    db_session.commit()
    return calls


def _cust(db, name, email="x@example.com", balance=100, monthly=True, **kw):
    """A customer; `balance` is owed on an old open invoice (the balance is
    summed from documents, Customer.balance is never read), dated long ago
    so it is not recent activity."""
    c = Customer(
        name=name,
        email=email,
        is_active=True,
        monthly_statement=monthly,
        **kw,
    )
    db.add(c)
    db.commit()
    if balance:
        db.add(
            Invoice(
                invoice_number=f"OLD-{c.id}",
                customer_id=c.id,
                status=InvoiceStatus.SENT,
                date=date(2025, 1, 1),
                due_date=date(2025, 1, 31),
                total=Decimal(balance),
                balance_due=Decimal(balance),
            )
        )
        db.commit()
    return c


def _due(client):
    return {
        i["customer_name"]: i
        for i in client.get("/api/collections/statements/due").json()["items"]
    }


def _logged(db, cust, when, status="sent", period=None):
    """The month's row as a finished send leaves it: `period` is the local
    month of the send (`when`'s month unless given)."""
    row = CustomerStatement(
        customer_id=cust.id,
        period=period or svc.period_of(when.date()),
        status=status,
        recipient="x@example.com",
        balance=100,
        sent_at=when,
    )
    db.add(row)
    db.commit()
    return row


def test_candidates_need_the_flag_and_a_balance_or_activity(client, db_session):
    _cust(db_session, "Flagged owes", balance=50)
    _cust(db_session, "Not flagged", balance=50, monthly=False)
    _cust(db_session, "Flagged zero quiet", balance=0)
    busy = _cust(db_session, "Flagged zero busy", balance=0)
    old = _cust(db_session, "Flagged zero old", balance=0)
    gone = _cust(db_session, "Inactive", balance=50)
    gone.is_active = False
    for who, days in ((busy, 10), (old, 90)):
        db_session.add(
            Invoice(
                invoice_number=f"S-{who.id}",
                customer_id=who.id,
                status=InvoiceStatus.PAID,
                date=TODAY - timedelta(days=days),
                total=10,
                balance_due=0,
            )
        )
    db_session.commit()
    got = _due(client)
    assert sorted(got) == ["Flagged owes", "Flagged zero busy"]
    row = got["Flagged owes"]
    assert row["balance"] == 50 and row["will_send"] is True
    assert row["reason"] is None and row["already_sent_on"] is None
    assert row["email"] == "x@example.com"


def test_void_invoice_is_not_activity(client, db_session):
    c = _cust(db_session, "Void only", balance=0)
    db_session.add(
        Invoice(
            invoice_number="V-1",
            customer_id=c.id,
            status=InvoiceStatus.VOID,
            date=TODAY - timedelta(days=2),
            total=10,
            balance_due=0,
        )
    )
    db_session.commit()
    assert _due(client) == {}


def test_already_sent_this_month_and_no_email(client, db_session):
    sent = _cust(db_session, "Sent")
    _logged(
        db_session,
        sent,
        datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=12),
    )
    last_month = _cust(db_session, "Sent last month")
    _logged(
        db_session,
        last_month,
        datetime.combine(TODAY.replace(day=1) - timedelta(days=3), datetime.min.time()),
    )
    _cust(db_session, "Bare", email=None)
    got = _due(client)
    assert got["Sent"]["already_sent_on"] == TODAY.isoformat()
    assert got["Sent"]["will_send"] is False
    assert got["Sent"]["reason"] == "already sent this month"
    assert got["Sent last month"]["will_send"] is True
    assert got["Bare"]["will_send"] is False and got["Bare"]["reason"] == "no email"


def test_the_month_is_the_local_date_not_the_utc_timestamp(client, db_session):
    """A statement sent at 9 pm on October 31 in New York is October's: the
    row names the local month, and the UTC clock (02:00 on November 1) is
    never compared with month bounds."""
    cust = _cust(db_session, "Evening")
    late_oct_utc = datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)
    _logged(db_session, cust, late_oct_utc, period="2026-10")
    got = svc.statement_candidates(db_session, date(2026, 10, 31))
    assert got[0]["reason"] == "already sent this month"
    got = svc.statement_candidates(db_session, date(2026, 11, 1))
    assert got[0]["will_send"] is True


def test_a_property_whose_tenants_owe_is_on_the_list(client, db_session):
    """A tenant billed with its property: the property owes nothing of its
    own, yet its statement carries the tenant's invoice, so it is a
    candidate with the tenant's balance (billed_balances)."""
    prop = _cust(db_session, "Oak St", balance=0)
    tenant = Customer(
        name="Unit 1",
        email="u1@example.com",
        is_active=True,
        parent_customer_id=prop.id,
        bill_with_parent=True,
    )
    db_session.add(tenant)
    db_session.commit()
    db_session.add(
        Invoice(
            invoice_number="RENT-1",
            customer_id=tenant.id,
            status=InvoiceStatus.SENT,
            date=date(2025, 1, 1),
            due_date=date(2025, 1, 31),
            total=Decimal(650),
            balance_due=Decimal(650),
        )
    )
    db_session.commit()
    got = _due(client)
    assert got["Oak St"]["balance"] == 650 and got["Oak St"]["will_send"] is True
    assert "Unit 1" not in got  # not flagged itself
    # the tenant's recent activity counts as the property's too
    paid = _cust(db_session, "Elm St", balance=0)
    t2 = Customer(
        name="Unit 2",
        email="u2@example.com",
        is_active=True,
        parent_customer_id=paid.id,
        bill_with_parent=True,
    )
    db_session.add(t2)
    db_session.commit()
    db_session.add(
        Invoice(
            invoice_number="RENT-2",
            customer_id=t2.id,
            status=InvoiceStatus.PAID,
            date=TODAY - timedelta(days=3),
            due_date=TODAY,
            total=Decimal(10),
            balance_due=Decimal(0),
        )
    )
    db_session.commit()
    assert _due(client)["Elm St"]["balance"] == 0


def test_a_statement_another_window_is_sending_is_left_to_it(client, db_session, mail):
    busy = _cust(db_session, "Busy")
    _logged(db_session, busy, datetime.now(timezone.utc), status="sending")
    got = _due(client)
    assert got["Busy"]["will_send"] is False
    assert got["Busy"]["reason"] == "being sent right now"
    r = client.post("/api/collections/statements/run", json={"force": True}).json()
    assert (r["sent"], r["skipped"]) == (0, 1) and mail == []


def test_a_stale_statement_claim_is_failed_and_sent_again(client, db_session, mail):
    cust = _cust(db_session, "Stale")
    old = _logged(
        db_session,
        cust,
        datetime.now(timezone.utc) - svc.STALE_CLAIM - timedelta(minutes=1),
        status="sending",
    )
    r = client.post("/api/collections/statements/run", json={}).json()
    assert r["sent"] == 1 and len(mail) == 1
    db_session.expire_all()
    assert old.status == "sent" and old.email_log_id is not None or old.status == "sent"
    assert db_session.query(CustomerStatement).count() == 1  # the same row


def test_run_claims_then_records_the_month_row(client, db_session, mail):
    cust = _cust(db_session, "Fresh", email="fresh@example.com")
    r = client.post("/api/collections/statements/run", json={}).json()
    assert r["sent"] == 1
    row = db_session.query(CustomerStatement).one()
    assert (row.customer_id, row.period, row.status) == (
        cust.id,
        svc.period_of(TODAY),
        "sent",
    )
    assert row.recipient == "fresh@example.com" and float(row.balance) == 100
    assert _due(client)["Fresh"]["reason"] == "already sent this month"
    # the same month again is refused by the database, not only the code
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        _logged(db_session, cust, datetime.now(timezone.utc))
    db_session.rollback()


def test_run_sends_and_skips(client, db_session, mail):
    _cust(db_session, "Ready", email="ready@example.com")
    sent = _cust(db_session, "Done")
    _logged(
        db_session,
        sent,
        datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=12),
    )
    _cust(db_session, "Bare", email=None)
    r = client.post("/api/collections/statements/run", json={}).json()
    assert (r["sent"], r["failed"], r["skipped"]) == (1, 0, 2)
    assert r["errors"] == ["Bare: no email address on file"]
    assert [c["to_email"] for c in mail] == ["ready@example.com"]
    assert mail[0]["entity_type"] == "statement"
    assert mail[0]["attachment_bytes"] == b"%PDF"


def test_force_resends_to_already_sent(client, db_session, mail):
    done = _cust(db_session, "Done")
    first = _logged(
        db_session,
        done,
        datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=12),
    )
    r = client.post("/api/collections/statements/run", json={"force": True}).json()
    assert r["sent"] == 1 and r["skipped"] == 0
    assert len(mail) == 1
    # the month's row is claimed again, not doubled
    db_session.expire_all()
    assert db_session.query(CustomerStatement).count() == 1
    assert first.status == "sent"


def test_failed_send_is_counted_and_tried_again(client, db_session, mail, monkeypatch):
    _cust(db_session, "Unlucky")
    monkeypatch.setattr("app.services.collections.send_email", lambda **kw: False)
    r = client.post("/api/collections/statements/run", json={}).json()
    assert (r["sent"], r["failed"]) == (0, 1)
    assert "Unlucky" in r["errors"][0]
    row = db_session.query(CustomerStatement).one()
    assert row.status == "failed" and "could not be sent" in row.error
    # a failed row holds nothing: the next run claims it again and sends
    monkeypatch.setattr("app.services.collections.send_email", lambda **kw: True)
    assert _due(client)["Unlucky"]["will_send"] is True
    r = client.post("/api/collections/statements/run", json={}).json()
    assert r["sent"] == 1
    db_session.expire_all()
    assert row.status == "sent" and db_session.query(CustomerStatement).count() == 1


def test_run_needs_smtp_only_when_something_sends(client, db_session):
    assert client.post("/api/collections/statements/run", json={}).json()["sent"] == 0
    _cust(db_session, "Waiting")
    r = client.post("/api/collections/statements/run", json={})
    assert r.status_code == 400 and r.json()["detail"] == NO_SMTP
    # only a customer with no email: nothing would send, so no 400
    db_session.query(Customer).update({"email": None})
    db_session.commit()
    assert client.post("/api/collections/statements/run", json={}).status_code == 200
