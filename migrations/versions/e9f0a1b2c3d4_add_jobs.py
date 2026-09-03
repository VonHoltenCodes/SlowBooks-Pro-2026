"""add_jobs

Projects / job costing, milestone 1: the `jobs` table (a job belongs to a
customer) and a nullable job_id FK on posted transactions, on their
lines, and on the source documents and their lines. Journal lines, invoice
lines and bill lines also gain class_id so the class dimension can be set
per line (the header value stays the default).

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every table that gets job_id.
_JOB_TABLES = (
    "transactions",
    "transaction_lines",
    "invoices",
    "invoice_lines",
    "bills",
    "bill_lines",
    "estimates",
    "estimate_lines",
    "purchase_orders",
    "purchase_order_lines",
    "credit_memos",
    "recurring_invoices",
    "time_entries",
)

# Line tables that also get class_id (headers already have it).
_CLASS_LINE_TABLES = (
    "transaction_lines",
    "invoice_lines",
    "bill_lines",
)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", name="fk_jobs_customer_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("job_number", sa.String(length=50), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("job_type", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("site_address", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("projected_end_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("contract_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    for table in _JOB_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("job_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_job_id", "jobs", ["job_id"], ["id"]
            )

    for table in _CLASS_LINE_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("class_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_class_id", "classes", ["class_id"], ["id"]
            )


def downgrade() -> None:
    for table in reversed(_CLASS_LINE_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_class_id", type_="foreignkey")
            batch_op.drop_column("class_id")
    for table in reversed(_JOB_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_job_id", type_="foreignkey")
            batch_op.drop_column("job_id")
    op.drop_table("jobs")
