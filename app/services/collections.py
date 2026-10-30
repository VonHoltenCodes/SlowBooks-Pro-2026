"""Collections (#653): payment reminders that send once, and monthly statements.

The rules, in one place:

* An open invoice is sent/partial, owes money, has a due date and is not a
  sales receipt.
* An enabled rule with offset d matches an invoice when the invoice is at
  least d days from its due date; a before-due rule (d < 0) only while the
  invoice is not yet due.
* The invoice gets at most one email per run: the matching rule with the
  largest d. If that rule has already gone out, nothing is sent. Softer
  rules are never sent after a firmer one has matched, so turning rules on
  for an invoice 20 days late sends one email, not four.
* A rule is spent for an invoice once a 'sent' audit row exists; a 'failed'
  row does not spend it, so the next run tries again.
* Every send is dated today. The Collections page's As of date is a way of
  looking at the list, never a date to send from: looking ahead a month
  must not send the final reminder early (review of #654).
* A send is claimed before it goes: the audit row is written as 'sending'
  first, under a unique index that allows one live row (sending or sent)
  per invoice and rule, so two windows, or the automatic run and Send due
  reminders together, send each reminder once. A claim older than ten
  minutes is a send that never finished (the app closed mid-run); it is
  marked failed and tried again.
* A sub-customer billed with its parent (a tenant billed with its property)
  is reminded wherever Settings says (``invoice_reminders_billed_sub_to``):
  the property, the tenant, or both, each one's own opt-out and email
  counting.
* The month's statement is one row per customer and calendar month
  (``customer_statements``), the month taken from today's local date, so a
  statement sent on the evening of the 31st is that month's, whatever the
  clock reads in UTC.
"""

import html
import logging
import re
from datetime import date, datetime, timedelta, timezone

from jinja2 import StrictUndefined, TemplateError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.collections import (
    CustomerStatement,
    InvoiceReminder,
    InvoiceReminderRule,
)
from app.models.contacts import Customer
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.email_log import EmailLog
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.settings import Settings
from app.services.donor_documents import document_label, invoice_doc_kind
from app.services.email_service import send_email
from app.services.pdf_service import generate_invoice_pdf, generate_statement_pdf
from app.services.settings_service import get_all_settings, set_setting
from app.services.sub_customers import billed_balances, billed_subs, billed_to
from app.services.terminology import terms_from_db

logger = logging.getLogger(__name__)

# Statement candidates: recent activity means this many days back.
STATEMENT_ACTIVITY_DAYS = 31

# A 'sending' row older than this never finished; it is marked failed.
STALE_CLAIM = timedelta(minutes=10)

# Who a reminder about a sub-customer billed with its parent goes to.
BILLED_SUB_TO = ("property", "tenant", "both")
LAST_RUN_KEY = "invoice_reminders_last_run"

# Another window claimed the send first: not sent here, not an error.
TAKEN = object()

# Wording gets firmer at each step. Placeholders: customer_name,
# invoice_number, invoice_label, balance_due, due_date, days_overdue,
# company_name. Migration c8e1a4d7f305 seeds these four, once; a company
# that deletes them keeps an empty list.
DEFAULT_RULES = [
    (
        -3,
        "Upcoming due date: {{ invoice_label }} {{ invoice_number }}",
        "Hello {{ customer_name }},\n\n"
        "A quick heads-up that {{ invoice_label }} {{ invoice_number }} for "
        "{{ balance_due }} is due on {{ due_date }}. A copy is attached.\n\n"
        "Thank you,\n{{ company_name }}",
    ),
    (
        3,
        "Friendly reminder: {{ invoice_label }} {{ invoice_number }}",
        "Hello {{ customer_name }},\n\n"
        "Our records show {{ invoice_label }} {{ invoice_number }} for "
        "{{ balance_due }}, due on {{ due_date }}, is still open. If you have "
        "already sent payment, thank you and please disregard this note. A "
        "copy is attached.\n\n"
        "Thank you,\n{{ company_name }}",
    ),
    (
        7,
        "Payment needed: {{ invoice_label }} {{ invoice_number }} is "
        "{{ days_overdue }} days overdue",
        "Hello {{ customer_name }},\n\n"
        "{{ invoice_label }} {{ invoice_number }} for {{ balance_due }} was "
        "due on {{ due_date }} and is now {{ days_overdue }} days overdue. "
        "Please arrange payment promptly. A copy is attached.\n\n"
        "If something is holding it up, reply and let us know.\n\n"
        "{{ company_name }}",
    ),
    (
        15,
        "Final reminder: {{ invoice_label }} {{ invoice_number }} is "
        "{{ days_overdue }} days overdue",
        "Hello {{ customer_name }},\n\n"
        "This is our final reminder that {{ invoice_label }} "
        "{{ invoice_number }} for {{ balance_due }}, due on {{ due_date }}, "
        "is {{ days_overdue }} days overdue. Please pay it now or contact us "
        "today. Without a response we will need to follow up further. A copy "
        "is attached.\n\n"
        "{{ company_name }}",
    ),
]

_SAMPLE_CONTEXT = {
    "customer_name": "Sample Customer",
    "invoice_number": "1001",
    "invoice_label": "Invoice",
    "balance_due": "$1,250.00",
    "due_date": "Jan 15, 2026",
    "days_overdue": 7,
    "company_name": "Sample Company",
}

# Sandboxed so a rule's text, which an admin edits, cannot reach into Python.
# Strict so a misspelled placeholder is an error, not a blank in an email.
_env = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


class EmailNotSetUp(Exception):
    """Something would send but no mail server is configured. ``message``
    is the sentence for the screen; the routes answer with that and nothing
    else."""

    def __init__(self, what: str):
        self.what = what
        self.message = (
            f"Email isn't set up yet, so no {what} were sent. Enter your mail "
            "server under Settings, Email, then try again."
        )
        super().__init__(self.message)


class RemindError(ValueError):
    """A single-invoice reminder that cannot go; ``message`` is for the person."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class TemplateInvalid(ValueError):
    """A rule's subject or body that cannot be used; ``message`` says why."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc(dt):
    """A stored timestamp as an aware UTC datetime (SQLite hands them back
    naive; they were written in UTC)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local_date(dt) -> date | None:
    dt = _utc(dt)
    return dt.astimezone().date() if dt else None


def _release_stale(db: Session, model) -> None:
    """A 'sending' row older than STALE_CLAIM is a send that never finished:
    marked failed, so the next run tries it again instead of waiting on a
    claim nobody holds."""
    cutoff = _now() - STALE_CLAIM
    changed = False
    for row in db.query(model).filter(model.status == "sending").all():
        when = _utc(row.sent_at)
        if when is None or when < cutoff:
            row.status = "failed"
            row.error = "The send did not finish."
            changed = True
    if changed:
        db.commit()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def rule_label(days: int) -> str:
    """How a rule reads on screen: '3 days before due', 'On the due date'."""
    if days == 0:
        return "On the due date"
    n = abs(days)
    unit = "day" if n == 1 else "days"
    return f"{n} {unit} before due" if days < 0 else f"{n} {unit} overdue"


def rule_dict(rule: InvoiceReminderRule) -> dict:
    return {
        "id": rule.id,
        "days_from_due": rule.days_from_due,
        "label": rule_label(rule.days_from_due),
        "enabled": bool(rule.enabled),
        "subject": rule.subject,
        "body": rule.body,
    }


def seed_default_rules(db: Session) -> int:
    """Write the four starter rules into an empty table and return how many
    were added. Migration c8e1a4d7f305 does this for every company file;
    this is for a database built by create_all (the test suite). No route
    calls it: a read never writes, and a company that deleted every rule
    keeps an empty list (review of #654)."""
    if db.query(InvoiceReminderRule.id).first():
        return 0
    for days, subject, body in DEFAULT_RULES:
        db.add(
            InvoiceReminderRule(
                days_from_due=days, enabled=True, subject=subject, body=body
            )
        )
    db.commit()
    return len(DEFAULT_RULES)


def list_rules(db: Session) -> list[InvoiceReminderRule]:
    """Every rule, softest first."""
    return (
        db.query(InvoiceReminderRule).order_by(InvoiceReminderRule.days_from_due).all()
    )


def validate_template(subject: str, body: str) -> None:
    """Render both texts once against sample values; raise TemplateInvalid
    saying what is wrong (a blank, a bad tag, or a placeholder we do not
    have). Its message names the placeholder or Jinja's own sentence about
    the text, never a stack."""
    if not (subject or "").strip():
        raise TemplateInvalid("The subject can't be blank.")
    if not (body or "").strip():
        raise TemplateInvalid("The message can't be blank.")
    for text in (subject, body):
        try:
            _env.from_string(text).render(**_SAMPLE_CONTEXT)
        except UndefinedError as e:
            m = re.match(r"'(\w+)' is undefined", e.message or "")
            name = m.group(1) if m else "that one"
            allowed = ", ".join("{{ %s }}" % k for k in _SAMPLE_CONTEXT)
            raise TemplateInvalid(
                f"Unknown placeholder: {name}. You can use {allowed}."
            ) from e
        except TemplateError as e:
            raise TemplateInvalid(
                f"The text has a formatting error: {e.message or 'check the braces'}"
            ) from e
        except Exception as e:  # sandbox refusals and the like
            raise TemplateInvalid("The text can't be used as a template.") from e


def _render(text: str, ctx: dict) -> str:
    return _env.from_string(text).render(**ctx)


# ---------------------------------------------------------------------------
# Which invoice gets which rule, and who is told
# ---------------------------------------------------------------------------


def open_invoices(db: Session, invoice_ids=None, customer_ids=None) -> list[Invoice]:
    """Invoices a reminder could be about (see the module docstring)."""
    q = (
        db.query(Invoice)
        .options(joinedload(Invoice.customer).joinedload(Customer.parent))
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date.isnot(None))
        .filter(Invoice.is_sales_receipt.is_(False))
    )
    if invoice_ids is not None:
        q = q.filter(Invoice.id.in_(invoice_ids))
    if customer_ids is not None:
        q = q.filter(Invoice.customer_id.in_(customer_ids))
    return q.order_by(Invoice.due_date, Invoice.id).all()


def rule_matches(offset: int, days: int) -> bool:
    """Has an invoice `days` from its due date reached a rule at `offset`?
    A before-due rule stops matching once the invoice is due."""
    if offset < 0:
        return offset <= days < 0
    return days >= offset


def next_rule(rules, days: int, spent: set):
    """(rule, ready) for the invoice: the firmest matching rule if it has not
    gone out (ready), else the nearest rule still ahead (not ready), else
    (None, False). `rules` are the enabled ones."""
    matching = [r for r in rules if rule_matches(r.days_from_due, days)]
    if matching:
        top = max(matching, key=lambda r: r.days_from_due)
        if top.id not in spent:
            return top, True
    ahead = [r for r in rules if r.days_from_due > days and r.id not in spent]
    if ahead:
        return min(ahead, key=lambda r: r.days_from_due), False
    return None, False


def _spent_by_invoice(db: Session, invoice_ids) -> dict:
    """invoice id -> the rule ids that have gone, or are going right now
    (a live claim counts: the other window is sending it)."""
    spent: dict = {}
    if not invoice_ids:
        return spent
    rows = (
        db.query(InvoiceReminder.invoice_id, InvoiceReminder.rule_id)
        .filter(InvoiceReminder.invoice_id.in_(list(invoice_ids)))
        .filter(InvoiceReminder.status.in_(["sent", "sending"]))
        .all()
    )
    for inv_id, rule_id in rows:
        spent.setdefault(inv_id, set()).add(rule_id)
    return spent


def billed_sub_choice(settings: dict) -> str:
    choice = (settings.get("invoice_reminders_billed_sub_to") or "").strip()
    return choice if choice in BILLED_SUB_TO else "property"


def _billed_parent(customer):
    if (
        customer is not None
        and customer.parent_customer_id
        and customer.bill_with_parent
    ):
        return customer.parent
    return None


def _addressees(customer, settings: dict) -> list:
    """Who a reminder about this customer's invoice is written to: the
    customer, or, for a sub-customer billed with its parent, whichever of
    the two Settings says: the property (whose A/R the invoice sits on),
    the tenant, or both."""
    parent = _billed_parent(customer)
    if parent is None:
        return [customer]
    choice = billed_sub_choice(settings)
    if choice == "tenant":
        return [customer]
    if choice == "both":
        return [customer, parent]
    return [parent]


def _block_reason(customer) -> str | None:
    """Why a reminder must not go to this customer, or None."""
    if customer is None or not customer.is_active:
        return "customer is inactive"
    if not customer.invoice_reminders_enabled:
        return "reminders are turned off for this customer"
    if not (customer.email or "").strip():
        return "no email address on file"
    return None


def recipients_for(customer, settings: dict):
    """(the customers the email goes to, why nobody does). Each addressee's
    own inactive flag, opt-out and email count, so a property that opted
    out is not written to about its tenant's invoice; with both named,
    whichever may be written to is."""
    people = _addressees(customer, settings)
    ok, reasons = [], []
    for person in people:
        reason = _block_reason(person)
        if reason is None:
            ok.append(person)
        elif len(people) > 1:
            reasons.append(f"{person.name}: {reason}")
        else:
            reasons.append(reason)
    if ok:
        return ok, None
    return [], "; ".join(reasons)


def _emails(people) -> str:
    return ", ".join(p.email.strip() for p in people)


def plan_reminders(db: Session, today: date, invoice_ids=None, settings=None):
    """(sends, skipped): what a run would email and what it would pass over
    and why. Only invoices with a rule ready to go are listed in either."""
    settings = settings or get_all_settings(db)
    rules = [r for r in list_rules(db) if r.enabled]
    invoices = open_invoices(db, invoice_ids)
    spent = _spent_by_invoice(db, [i.id for i in invoices])
    sends, skipped = [], []
    for inv in invoices:
        days = (today - inv.due_date).days
        rule, ready = next_rule(rules, days, spent.get(inv.id, set()))
        if not ready:
            continue
        to, reason = recipients_for(inv.customer, settings)
        if reason:
            skipped.append({"invoice": inv, "reason": reason})
        else:
            sends.append({"invoice": inv, "rule": rule, "days": days, "to": to})
    return sends, skipped


def preview_reminders(db: Session, invoice_ids=None) -> dict:
    """Exactly what run_reminders would send today, without sending."""
    _release_stale(db, InvoiceReminder)
    today = date.today()
    sends, skipped = plan_reminders(db, today, invoice_ids)
    return {
        "as_of_date": today.isoformat(),
        "items": [
            {
                "invoice_id": s["invoice"].id,
                "invoice_number": s["invoice"].invoice_number,
                "customer_id": s["invoice"].customer_id,
                "customer_name": s["invoice"].customer.name,
                "recipient": _emails(s["to"]),
                "recipient_names": [p.name for p in s["to"]],
                "rule_id": s["rule"].id,
                "label": rule_label(s["rule"].days_from_due),
                "days_from_due": s["days"],
                "balance_due": float(s["invoice"].balance_due),
            }
            for s in sends
        ],
        "skipped": [
            {
                "invoice_id": k["invoice"].id,
                "invoice_number": k["invoice"].invoice_number,
                "customer_name": k["invoice"].customer.name,
                "reason": k["reason"],
            }
            for k in skipped
        ],
    }


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _smtp_ready(settings: dict) -> bool:
    return bool((settings.get("smtp_host") or "").strip())


def _money(amount, invoice, home: str) -> str:
    code = (invoice.currency or "").strip().upper()
    text = f"{float(amount or 0):,.2f}"
    return f"{code} {text}" if code and code != home else f"${text}"


def _context(inv: Invoice, to, days: int, settings: dict, words, home: str) -> dict:
    return {
        "customer_name": to[0].name,
        "invoice_number": inv.invoice_number,
        "invoice_label": document_label(inv, words),
        "balance_due": _money(inv.balance_due, inv, home),
        "due_date": inv.due_date.strftime("%b %d, %Y"),
        "days_overdue": max(0, days),
        "company_name": settings.get("company_name", "") or "",
    }


def _html_body(text: str) -> str:
    """Plain text to an email body: escaped, line breaks kept."""
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"


def _latest_log_id(db: Session, entity_type: str, entity_id: int):
    log = (
        db.query(EmailLog.id)
        .filter(EmailLog.entity_type == entity_type)
        .filter(EmailLog.entity_id == entity_id)
        .order_by(EmailLog.id.desc())
        .first()
    )
    return log[0] if log else None


def _claim_reminder(db: Session, plan, trigger: str):
    """Write the audit row as 'sending' and commit. None when another
    window holds a live row for this invoice and rule (the unique index
    refused the insert): that window is sending it."""
    inv, rule = plan["invoice"], plan["rule"]
    row = InvoiceReminder(
        invoice_id=inv.id,
        rule_id=rule.id,
        customer_id=inv.customer_id,
        recipient=_emails(plan["to"]),
        status="sending",
        trigger=trigger,
        days_from_due=plan["days"],
        balance_due=inv.balance_due,
        sent_at=_now(),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row


def _send_one(db, plan, settings, words, home, trigger):
    """Claim, email and record one reminder. Returns None when it went out,
    TAKEN when another window had claimed it, else the sentence to show.
    Commits either way, so a crash later in a run keeps what has gone."""
    row = _claim_reminder(db, plan, trigger)
    if row is None:
        return TAKEN
    inv, rule, days, to = plan["invoice"], plan["rule"], plan["days"], plan["to"]
    label = f"{document_label(inv, words)} {inv.invoice_number}"
    error = None
    ok = False
    log_id = None
    try:
        ctx = _context(inv, to, days, settings, words, home)
        subject = " ".join(_render(rule.subject, ctx).split())
        body = _render(rule.body, ctx)
        pdf = generate_invoice_pdf(inv, settings)
        ok = send_email(
            db=db,
            to_email=to[0].email.strip(),
            cc=[p.email.strip() for p in to[1:]] or None,
            subject=subject,
            html_body=_html_body(body),
            attachment_bytes=pdf,
            attachment_name=f"{invoice_doc_kind(inv, words)}_{inv.invoice_number}.pdf",
            entity_type="reminder",
            entity_id=inv.id,
        )
        if ok:
            log_id = _latest_log_id(db, "reminder", inv.id)
        else:
            error = "The email could not be sent (the email log has the reason)."
    except Exception:
        # The exception's own text stays in the log and the audit row; the
        # sentence that goes back to the screen names no internals.
        logger.exception("Reminder for invoice %s failed", inv.id)
        db.rollback()
        error = "The reminder could not be prepared (the server log has the reason)."
    row.status = "sent" if ok else "failed"
    row.email_log_id = log_id
    row.error = error
    row.sent_at = _now()
    db.commit()
    if ok:
        return None
    return f"{to[0].name}, {label}: {error}"


def run_reminders(db: Session, trigger: str, invoice_ids=None) -> dict:
    """Send every reminder due today (see plan_reminders) and report the
    counts. Raises EmailNotSetUp when something would send and mail is not
    set up. A reminder another window claimed first counts as skipped."""
    _release_stale(db, InvoiceReminder)
    today = date.today()
    settings = get_all_settings(db)
    sends, skipped = plan_reminders(db, today, invoice_ids, settings)
    if sends and not _smtp_ready(settings):
        raise EmailNotSetUp("reminders")
    words = terms_from_db(db)
    from app.services.currency import home_currency

    home = home_currency(db)
    sent, taken, errors = 0, 0, []
    for plan in sends:
        result = _send_one(db, plan, settings, words, home, trigger)
        if result is None:
            sent += 1
        elif result is TAKEN:
            taken += 1
        else:
            errors.append(result)
    return {
        "sent": sent,
        "failed": len(errors),
        "skipped": len(skipped) + taken,
        "taken": taken,
        "errors": errors,
        "ran_on": today.isoformat(),
    }


def _claim_day(db: Session, today: date) -> bool:
    """Stamp today as the automatic run's date in one guarded statement
    that succeeds only while the stamp is not already today, so two windows
    opened together run once between them."""
    iso = today.isoformat()
    row = db.query(Settings).filter(Settings.key == LAST_RUN_KEY).first()
    if row is None:
        set_setting(db, LAST_RUN_KEY, iso)
        try:
            db.commit()
        except IntegrityError:  # the other window wrote the row first
            db.rollback()
            return False
        return True
    moved = (
        db.query(Settings)
        .filter(
            Settings.key == LAST_RUN_KEY,
            or_(Settings.value.is_(None), Settings.value != iso),
        )
        .update({Settings.value: iso}, synchronize_session=False)
    )
    db.commit()
    return moved == 1


def run_if_due(db: Session, today: date) -> dict:
    """The once-a-day automatic run. The date is claimed before sending so
    a crash does not retry on every page load, but not when mail is not
    set up, so the run happens the day it is."""
    settings = get_all_settings(db)
    if settings.get("invoice_reminders_auto") != "true":
        return {"ran": False, "reason": "off"}
    if settings.get(LAST_RUN_KEY) == today.isoformat():
        return {"ran": False, "reason": "already_today"}
    sends, _ = plan_reminders(db, today, settings=settings)
    if sends and not _smtp_ready(settings):
        return {"ran": False, "reason": "no_smtp"}
    if not _claim_day(db, today):
        return {"ran": False, "reason": "already_today"}
    return {"ran": True, **run_reminders(db, "auto")}


def remind_invoice(db: Session, invoice_id: int) -> dict:
    """Send one invoice its next reminder by hand, dated today: the firmest
    matching rule, else the nearest one ahead. Raises LookupError /
    RemindError / EmailNotSetUp."""
    _release_stale(db, InvoiceReminder)
    today = date.today()
    inv = (
        db.query(Invoice)
        .options(joinedload(Invoice.customer).joinedload(Customer.parent))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not inv:
        raise LookupError("Invoice not found")
    if not open_invoices(db, [invoice_id]):
        raise RemindError("This invoice isn't open, so there is nothing to remind.")
    settings = get_all_settings(db)
    to, reason = recipients_for(inv.customer, settings)
    if reason:
        raise RemindError(f"No reminder can be sent: {reason}.")
    rules = [r for r in list_rules(db) if r.enabled]
    days = (today - inv.due_date).days
    spent = _spent_by_invoice(db, [invoice_id]).get(invoice_id, set())
    rule, _ready = next_rule(rules, days, spent)
    if rule is None:
        raise RemindError("Every reminder rule has already been sent for this invoice.")
    if not _smtp_ready(settings):
        raise EmailNotSetUp("reminders")
    from app.services.currency import home_currency

    result = _send_one(
        db,
        {"invoice": inv, "rule": rule, "days": days, "to": to},
        settings,
        terms_from_db(db),
        home_currency(db),
        "manual",
    )
    if result is TAKEN:
        raise RemindError("Another window is sending this reminder right now.")
    if result:
        raise RemindError(result)
    return {"sent": True, "label": rule_label(rule.days_from_due), "to": _emails(to)}


def invoice_rows(
    db: Session, as_of: date, customer_id=None, overdue_only: bool = False
) -> dict:
    """The Collections list: every open invoice with where it stands as of
    the date asked for (a way of looking, not a date to send from). A
    customer filter on a parent takes in the sub-customers billed with it,
    the way its A/R Aging row does."""
    settings = get_all_settings(db)
    rules = [r for r in list_rules(db) if r.enabled]
    all_rules = {r.id: r for r in list_rules(db)}
    customer_ids = None
    if customer_id is not None:
        customer_ids = [customer_id] + [s.id for s in billed_subs(db, customer_id)]
    invoices = open_invoices(db, customer_ids=customer_ids)
    ids = [i.id for i in invoices]
    spent = _spent_by_invoice(db, ids)
    last: dict = {}
    if ids:
        rows = (
            db.query(InvoiceReminder)
            .filter(InvoiceReminder.invoice_id.in_(ids))
            .order_by(InvoiceReminder.id)
            .all()
        )
        for row in rows:  # ascending, so the last one written wins
            last[row.invoice_id] = row
    items = []
    for inv in invoices:
        days = (as_of - inv.due_date).days
        if overdue_only and days <= 0:
            continue
        rule, ready = next_rule(rules, days, spent.get(inv.id, set()))
        row = last.get(inv.id)
        last_rule = all_rules.get(row.rule_id) if row else None
        to, reason = recipients_for(inv.customer, settings)
        parent = _billed_parent(inv.customer)
        items.append(
            {
                "invoice_id": inv.id,
                "invoice_number": inv.invoice_number,
                "customer_id": inv.customer_id,
                "customer_name": inv.customer.name,
                "customer_email": inv.customer.email,
                "billed_to_name": parent.name if parent is not None else None,
                "date": inv.date.isoformat(),
                "due_date": inv.due_date.isoformat(),
                "days_overdue": days,
                "balance_due": float(inv.balance_due),
                "reminders_enabled": reason is None,
                "reminder_to": _emails(to) if to else None,
                "skip_reason": reason,
                "last_reminder": (
                    {
                        "rule_id": row.rule_id,
                        "label": rule_label(
                            last_rule.days_from_due if last_rule else row.days_from_due
                        ),
                        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
                        "status": row.status,
                    }
                    if row
                    else None
                ),
                "next_reminder": (
                    {
                        "rule_id": rule.id,
                        "label": rule_label(rule.days_from_due),
                        "due_on": (
                            inv.due_date + timedelta(days=rule.days_from_due)
                        ).isoformat(),
                        "ready": ready,
                    }
                    if rule
                    else None
                ),
            }
        )
    items.sort(key=lambda i: -i["days_overdue"])
    overdue = [i for i in items if i["days_overdue"] > 0]
    return {
        "as_of_date": as_of.isoformat(),
        "items": items,
        "totals": {
            "count": len(items),
            "balance_due": round(sum(i["balance_due"] for i in items), 2),
            "overdue_count": len(overdue),
            "overdue_balance": round(sum(i["balance_due"] for i in overdue), 2),
        },
    }


# ---------------------------------------------------------------------------
# Monthly statements
# ---------------------------------------------------------------------------


def period_of(day: date) -> str:
    """The statement month a date falls in, "2026-10"."""
    return day.strftime("%Y-%m")


def _active_ids(db: Session, model, since: date, until: date, *extra) -> set:
    q = db.query(model.customer_id).filter(model.date >= since, model.date <= until)
    for cond in extra:
        q = q.filter(cond)
    return {r[0] for r in q.distinct()}


def _month_rows(db: Session, period: str) -> dict:
    return {
        row.customer_id: row
        for row in db.query(CustomerStatement).filter(
            CustomerStatement.period == period
        )
    }


def statement_candidates(db: Session, today: date) -> list[dict]:
    """Customers on monthly statements who owe something or had activity in
    the last month, with whether this month's statement has gone. What a
    customer owes, and its activity, take in the sub-customers billed with
    it: a property whose tenants owe is on the list though it owes nothing
    of its own."""
    _release_stale(db, CustomerStatement)
    since = today - timedelta(days=STATEMENT_ACTIVITY_DAYS)
    recent = (
        _active_ids(db, Invoice, since, today, Invoice.status != InvoiceStatus.VOID)
        | _active_ids(
            db,
            Payment,
            since,
            today,
            or_(Payment.is_voided.is_(False), Payment.is_voided.is_(None)),
        )
        | _active_ids(
            db, CreditMemo, since, today, CreditMemo.status != CreditMemoStatus.VOID
        )
    )
    routed = billed_to(db)  # sub billed with parent -> parent
    recent |= {routed[cid] for cid in recent if cid in routed}
    customers = (
        db.query(Customer)
        .filter(Customer.is_active.is_(True), Customer.monthly_statement.is_(True))
        .order_by(Customer.name)
        .all()
    )
    # Customer.balance is a stored column nothing keeps up; what a customer
    # owes is summed from the open documents, its billed sub-customers' too.
    owed = billed_balances(db, [c.id for c in customers])
    month = _month_rows(db, period_of(today))
    out = []
    for c in customers:
        balance = float(owed.get(c.id, 0))
        if balance == 0 and c.id not in recent:
            continue
        email = (c.email or "").strip()
        row = month.get(c.id)
        sent_on = _local_date(row.sent_at) if row and row.status == "sent" else None
        if not email:
            reason = "no email"
        elif sent_on:
            reason = "already sent this month"
        elif row and row.status == "sending":
            reason = "being sent right now"
        else:
            reason = None
        out.append(
            {
                "customer": c,
                "customer_id": c.id,
                "customer_name": c.name,
                "email": email or None,
                "balance": balance,
                "already_sent_on": sent_on.isoformat() if sent_on else None,
                "will_send": reason is None,
                "reason": reason,
            }
        )
    return out


def statements_due(db: Session) -> dict:
    today = date.today()
    keys = (
        "customer_id",
        "customer_name",
        "email",
        "balance",
        "already_sent_on",
        "will_send",
        "reason",
    )
    return {
        "as_of_date": today.isoformat(),
        "items": [{k: c[k] for k in keys} for c in statement_candidates(db, today)],
    }


def _claim_statement(db: Session, cand: dict, period: str, force: bool):
    """The month's row for this customer, claimed as 'sending', or None
    when another window has it. A new row is inserted; a failed one (or,
    with force, a sent one) is taken over in one guarded update."""
    customer = cand["customer"]
    row = CustomerStatement(
        customer_id=customer.id,
        period=period,
        status="sending",
        recipient=cand["email"],
        balance=cand["balance"],
        sent_at=_now(),
    )
    db.add(row)
    try:
        db.commit()
        return row
    except IntegrityError:
        db.rollback()
    existing = (
        db.query(CustomerStatement)
        .filter(
            CustomerStatement.customer_id == customer.id,
            CustomerStatement.period == period,
        )
        .first()
    )
    if existing is None:
        return None
    allowed = ("failed", "sent") if force else ("failed",)
    if existing.status not in allowed:
        return None
    moved = (
        db.query(CustomerStatement)
        .filter(
            CustomerStatement.id == existing.id,
            CustomerStatement.status == existing.status,
        )
        .update(
            {
                CustomerStatement.status: "sending",
                CustomerStatement.recipient: cand["email"],
                CustomerStatement.balance: cand["balance"],
                CustomerStatement.sent_at: _now(),
                CustomerStatement.error: None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if moved != 1:
        return None
    db.refresh(existing)
    return existing


def run_statements(db: Session, force: bool = False) -> dict:
    """Email each customer's statement PDF as of today. `force` also resends
    to those who already had one this month; a customer with no email is
    never sent. A statement another window is sending counts as skipped."""
    from app.routes.reports.receivables import statement_activity

    today = date.today()
    period = period_of(today)
    todo, skipped, errors = [], 0, []
    for c in statement_candidates(db, today):
        if c["reason"] == "no email":
            skipped += 1
            errors.append(f"{c['customer_name']}: no email address on file")
        elif c["will_send"] or (force and c["reason"] == "already sent this month"):
            todo.append(c)
        else:
            skipped += 1
    settings = get_all_settings(db)
    if todo and not _smtp_ready(settings):
        raise EmailNotSetUp("statements")
    company_name = settings.get("company_name", "") or ""
    sent = failed = 0
    for c in todo:
        customer = c["customer"]
        row = _claim_statement(db, c, period, force)
        if row is None:
            skipped += 1
            continue
        ok = False
        try:
            pdf = generate_statement_pdf(
                customer, statement_activity(db, customer, today), settings, today
            )
            ok = send_email(
                db=db,
                to_email=c["email"],
                subject=f"Account Statement — {company_name or 'Our Company'}",
                html_body=(
                    f"<p>Dear {html.escape(customer.name)},</p>"
                    "<p>Please find your account statement attached.</p>"
                    f"<p>{html.escape(company_name)}</p>"
                ),
                attachment_bytes=pdf,
                attachment_name=f"Statement_{customer.name}.pdf",
                entity_type="statement",
                entity_id=customer.id,
            )
        except Exception:
            logger.exception("Failed to send statement to customer %s", customer.id)
            db.rollback()
        row.status = "sent" if ok else "failed"
        row.email_log_id = _latest_log_id(db, "statement", customer.id) if ok else None
        row.error = (
            None
            if ok
            else "The statement could not be sent (the email log has the reason)."
        )
        row.sent_at = _now()
        db.commit()
        if ok:
            sent += 1
        else:
            failed += 1
            errors.append(
                f"{customer.name}: the statement could not be sent (the email "
                "log has the reason)"
            )
    return {"sent": sent, "failed": failed, "skipped": skipped, "errors": errors}
