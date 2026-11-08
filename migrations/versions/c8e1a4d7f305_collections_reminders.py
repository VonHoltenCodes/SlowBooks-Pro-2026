"""collections: reminder rules, the reminder audit, statements, flags (#653)

Payment reminders that send once per rule: invoice_reminder_rules holds the
schedule (days from the due date and the wording), invoice_reminders records
every send or failed try, which is also what marks a rule as already sent
for an invoice. A send is claimed first: the row is written as "sending"
under a partial unique index that allows one live row (sending or sent) per
invoice and rule, so two tabs cannot send the same reminder twice.
customer_statements is the month's statement per customer, one row per
customer and local month, claimed the same way. customers gains
invoice_reminders_enabled (on for everyone, so existing customers are
reminded unless opted out) and monthly_statement (off for everyone until
chosen).

The four starter rules are seeded here, once: a read never writes them, and
a company that deletes them all keeps an empty list (review of #654). The
wording is the same as app/services/collections.DEFAULT_RULES at this
revision; the migration keeps its own copy so it never depends on app code.

Revision ID: c8e1a4d7f305
Revises: d7b3f9a1c5e2
Create Date: 2026-10-29

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8e1a4d7f305"
down_revision: Union[str, None] = "d7b3f9a1c5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LIVE = "status IN ('sending', 'sent')"

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


def upgrade() -> None:
    op.create_table(
        "invoice_reminder_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("days_from_due", sa.Integer(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_invoice_reminder_rules_id", "invoice_reminder_rules", ["id"])
    rules = sa.table(
        "invoice_reminder_rules",
        sa.column("days_from_due", sa.Integer()),
        sa.column("enabled", sa.Boolean()),
        sa.column("subject", sa.String()),
        sa.column("body", sa.Text()),
    )
    op.bulk_insert(
        rules,
        [
            {"days_from_due": d, "enabled": True, "subject": subj, "body": body}
            for d, subj, body in DEFAULT_RULES
        ],
    )
    op.create_table(
        "invoice_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False
        ),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("invoice_reminder_rules.id"),
            nullable=False,
        ),
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False
        ),
        sa.Column("recipient", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("days_from_due", sa.Integer(), nullable=False),
        sa.Column("balance_due", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "email_log_id", sa.Integer(), sa.ForeignKey("email_log.id"), nullable=True
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_invoice_reminders_id", "invoice_reminders", ["id"])
    op.create_index(
        "ix_invoice_reminders_invoice_id", "invoice_reminders", ["invoice_id"]
    )
    op.create_index(
        "uq_invoice_reminders_live",
        "invoice_reminders",
        ["invoice_id", "rule_id"],
        unique=True,
        sqlite_where=sa.text(_LIVE),
        postgresql_where=sa.text(_LIVE),
    )
    op.create_table(
        "customer_statements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False
        ),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("recipient", sa.String(200), nullable=False),
        sa.Column("balance", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "email_log_id", sa.Integer(), sa.ForeignKey("email_log.id"), nullable=True
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "customer_id", "period", name="uq_customer_statements_month"
        ),
    )
    op.create_index("ix_customer_statements_id", "customer_statements", ["id"])
    with op.batch_alter_table("customers") as batch:
        batch.add_column(
            sa.Column(
                "invoice_reminders_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_statement",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.drop_column("monthly_statement")
        batch.drop_column("invoice_reminders_enabled")
    op.drop_index("ix_customer_statements_id", table_name="customer_statements")
    op.drop_table("customer_statements")
    op.drop_index("uq_invoice_reminders_live", table_name="invoice_reminders")
    op.drop_index("ix_invoice_reminders_invoice_id", table_name="invoice_reminders")
    op.drop_index("ix_invoice_reminders_id", table_name="invoice_reminders")
    op.drop_table("invoice_reminders")
    op.drop_index("ix_invoice_reminder_rules_id", table_name="invoice_reminder_rules")
    op.drop_table("invoice_reminder_rules")
