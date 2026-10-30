# ============================================================================
# Collections (#653) — payment reminder rules, the audit of what was sent,
# and the month's statement per customer. A rule says "this many days from
# the due date, send this wording"; every send (or failed try) is one
# InvoiceReminder row, which is also what makes a rule "spent" for an
# invoice so it is never sent twice.
#
# A send is claimed before it goes (review of #654): the row is written as
# "sending" first, under a unique index that lets one live row (sending or
# sent) exist per invoice and rule, so two tabs, or the automatic run and
# Send due reminders together, cannot email the same reminder twice; the
# second insert fails and that run passes the invoice over. A failed row
# does not hold the index, so the next run tries again. The month's
# statement is claimed the same way, one row per customer and month.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)

from app.database import Base


class InvoiceReminderRule(Base):
    __tablename__ = "invoice_reminder_rules"

    id = Column(Integer, primary_key=True, index=True)
    # Negative = before the due date, 0 = on it, positive = after. One rule
    # per day offset.
    days_from_due = Column(Integer, nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default=true())
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# The live rows: one per invoice and rule, claimed or sent. SQLite and
# PostgreSQL both take a partial unique index; the migration creates the
# same one.
LIVE_STATUSES = ("sending", "sent")
_LIVE = "status IN ('sending', 'sent')"


class InvoiceReminder(Base):
    __tablename__ = "invoice_reminders"
    __table_args__ = (
        Index(
            "uq_invoice_reminders_live",
            "invoice_id",
            "rule_id",
            unique=True,
            sqlite_where=text(_LIVE),
            postgresql_where=text(_LIVE),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    rule_id = Column(Integer, ForeignKey("invoice_reminder_rules.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    recipient = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False)  # sending | sent | failed
    trigger = Column(String(20), nullable=False)  # run | manual | auto
    # The invoice's offset from its due date when this went out (the rule's
    # own offset can be earlier than that).
    days_from_due = Column(Integer, nullable=False)
    balance_due = Column(Numeric(15, 2), nullable=False)
    email_log_id = Column(Integer, ForeignKey("email_log.id"), nullable=True)
    error = Column(Text, nullable=True)
    # Written when the row is claimed, and again when the send finishes.
    sent_at = Column(DateTime(timezone=True), server_default=func.now())


class CustomerStatement(Base):
    """The month's statement to a customer: one row per customer and
    calendar month (``period`` is the local month, "2026-10", taken from
    the date of the send, never from a UTC timestamp), claimed as
    "sending" before the email goes, then "sent" or "failed". Sent again
    on purpose (force), the same row is claimed again."""

    __tablename__ = "customer_statements"
    __table_args__ = (
        UniqueConstraint("customer_id", "period", name="uq_customer_statements_month"),
    )

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    period = Column(String(7), nullable=False)
    status = Column(String(20), nullable=False)  # sending | sent | failed
    recipient = Column(String(200), nullable=False)
    balance = Column(Numeric(15, 2), nullable=False)
    email_log_id = Column(Integer, ForeignKey("email_log.id"), nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
