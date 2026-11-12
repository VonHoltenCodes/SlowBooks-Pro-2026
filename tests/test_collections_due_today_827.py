"""Collections (#827): a reminder sent on the due date says the invoice is
due today, not "0 days overdue".

Remind now pressed twice on an invoice due today sends the friendly reminder
and then the 7-day rule, whose starter text says "is {{ days_overdue }} days
overdue". On the due date that email now reads "Invoice C0001 is due today";
the day after, "1 day overdue"; before the due date, "due tomorrow" or "due
in N days". The count is the one the list and the rules use (today minus
the due date). The preview of a run shows the same subject.

The fixtures and helpers are test_collections_reminders' (send_email and the
invoice PDF are replaced; no SMTP, no WeasyPrint)."""

from app.services import collections as svc
from tests.test_collections_reminders import (  # noqa: F401  (the fixtures)
    _customer,
    _invoice,
    _today,
    mail,
    rules,
)

PAYMENT_NEEDED = svc.DEFAULT_RULES[2]
FINAL = svc.DEFAULT_RULES[3]


def _remind(client, inv):
    r = client.post(f"/api/collections/invoices/{inv.id}/remind")
    assert r.status_code == 200, r.text
    return r.json()


def _due(inv) -> str:
    return inv.due_date.strftime("%b %d, %Y")


def test_due_status_words():
    assert svc.due_status(-5) == "due in 5 days"
    assert svc.due_status(-2) == "due in 2 days"
    assert svc.due_status(-1) == "due tomorrow"
    assert svc.due_status(0) == "due today"
    assert svc.due_status(1) == "1 day overdue"
    assert svc.due_status(7) == "7 days overdue"


def test_remind_twice_on_the_due_date_says_due_today(
    client, db_session, rules, mail  # noqa: F811
):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=0)
    assert _remind(client, inv)["label"] == "3 days overdue"
    assert mail[-1]["subject"] == f"Friendly reminder: Invoice {inv.invoice_number}"

    assert _remind(client, inv)["label"] == "7 days overdue"
    sent = mail[-1]
    assert (
        sent["subject"] == f"Payment needed: Invoice {inv.invoice_number} is due today"
    )
    assert (
        f"Invoice {inv.invoice_number} for $100.00 is due today, {_due(inv)}. "
        "Please arrange payment promptly." in sent["html_body"]
    )
    assert "overdue" not in sent["html_body"]

    assert _remind(client, inv)["label"] == "15 days overdue"
    sent = mail[-1]
    assert (
        sent["subject"] == f"Final reminder: Invoice {inv.invoice_number} is due today"
    )
    assert f"due on {_due(inv)}, is due today. Please pay it now" in sent["html_body"]
    assert "overdue" not in sent["html_body"]


def test_the_day_after_the_overdue_wording_stays(
    client, db_session, rules, mail  # noqa: F811
):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-1)
    _remind(client, inv)  # the 3-day rule, the nearest one ahead
    _remind(client, inv)
    sent = mail[-1]
    assert sent["subject"] == (
        f"Payment needed: Invoice {inv.invoice_number} is 1 day overdue"
    )
    assert f"was due on {_due(inv)} and is now 1 day overdue." in sent["html_body"]


def test_further_overdue_reads_as_before(client, db_session, rules, mail):  # noqa: F811
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=-8)
    _remind(client, inv)  # the 7-day rule matches
    sent = mail[-1]
    assert sent["subject"] == (
        f"Payment needed: Invoice {inv.invoice_number} is 8 days overdue"
    )
    assert f"was due on {_due(inv)} and is now 8 days overdue." in sent["html_body"]


def test_the_day_before_is_the_friendly_reminder_then_due_tomorrow(
    client, db_session, rules, mail  # noqa: F811
):
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=1)
    assert _remind(client, inv)["label"] == "3 days before due"
    assert mail[-1]["subject"] == f"Upcoming due date: Invoice {inv.invoice_number}"
    assert _remind(client, inv)["label"] == "3 days overdue"
    assert mail[-1]["subject"] == f"Friendly reminder: Invoice {inv.invoice_number}"
    assert "overdue" not in mail[-1]["html_body"]
    _remind(client, inv)
    sent = mail[-1]
    assert sent["subject"] == (
        f"Payment needed: Invoice {inv.invoice_number} is due tomorrow"
    )
    assert f"is due tomorrow, {_due(inv)}." in sent["html_body"]
    assert "overdue" not in sent["html_body"]


def test_the_preview_shows_the_subject_the_customer_reads(
    client, db_session, rules, mail  # noqa: F811
):
    # rules on the due date and the day after, in the 7-day rule's words
    for days in (0, 1):
        r = client.post(
            "/api/collections/rules",
            json={
                "days_from_due": days,
                "enabled": True,
                "subject": PAYMENT_NEEDED[1],
                "body": PAYMENT_NEEDED[2],
            },
        )
        assert r.status_code == 201, r.text
    cust = _customer(db_session)
    today_inv = _invoice(db_session, cust, due_in=0)
    after_inv = _invoice(db_session, cust, due_in=-1)
    late_inv = _invoice(db_session, cust, due_in=-8)
    items = {
        i["invoice_id"]: i
        for i in client.get("/api/collections/reminders/due").json()["items"]
    }
    assert items[today_inv.id]["subject"] == (
        f"Payment needed: Invoice {today_inv.invoice_number} is due today"
    )
    assert items[today_inv.id]["due_status"] == "due today"
    assert items[today_inv.id]["label"] == "On the due date"
    assert items[after_inv.id]["subject"] == (
        f"Payment needed: Invoice {after_inv.invoice_number} is 1 day overdue"
    )
    assert items[late_inv.id]["subject"] == (
        f"Payment needed: Invoice {late_inv.invoice_number} is 8 days overdue"
    )

    # and the run sends what the preview showed
    assert client.post("/api/collections/reminders/run", json={}).json()["sent"] == 3
    subjects = {c["entity_id"]: c["subject"] for c in mail}
    assert subjects == {k: v["subject"] for k, v in items.items()}


def test_a_rule_may_use_due_status(client, db_session, rules, mail):  # noqa: F811
    r = client.post(
        "/api/collections/rules",
        json={
            "days_from_due": 0,
            "enabled": True,
            "subject": "{{ invoice_label }} {{ invoice_number }} is {{ due_status }}",
            "body": "Hello {{ customer_name }}, it is {{ due_status }}.",
        },
    )
    assert r.status_code == 201, r.text
    cust = _customer(db_session)
    inv = _invoice(db_session, cust, due_in=0)
    _remind(client, inv)
    assert mail[-1]["subject"] == f"Invoice {inv.invoice_number} is due today"
    assert "it is due today." in mail[-1]["html_body"]
    assert _today() == inv.due_date
