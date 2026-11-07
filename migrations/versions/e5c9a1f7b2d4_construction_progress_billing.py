"""construction progress billing and retainage (#555)

Step 6 of the commercial GC layer, on top of #554. Owner pay applications
from the schedule of values, retainage held and released on both the owner
and the sub side, owner deposits, and lien waivers. Additive and nullable
(or defaulted) throughout, so a company with no pay applications sees none
of it:

- pay_applications / pay_application_lines — the owner's progress billing
  against the SOV (previous / this period / stored / retainage, with a
  release and a deposit applied); approve creates the invoice.
- job_deposits — owner money held on Customer Deposits (2210) until applied.
- invoices gains retainage_held, retainage_released and pay_application_id.
- bills gains retainage_released (a sub retainage-release bill).
- purchase_orders gains retainage_released (retainage released to the sub).

downgrade reverses.

Revision ID: e5c9a1f7b2d4
Revises: b8d0f2a4c6e8
Create Date: 2026-10-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5c9a1f7b2d4"
down_revision: Union[str, None] = "b8d0f2a4c6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pay_applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("application_no", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column(
            "status", sa.String(length=10), nullable=False, server_default="draft"
        ),
        sa.Column("retainage_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "tax_rate", sa.Numeric(7, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "release_retainage", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "deposit_applied", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column("lien_waiver_status", sa.String(length=16), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("deposit_payment_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["deposit_payment_id"], ["payments.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number", name="uq_pay_app_number"),
        sa.UniqueConstraint("job_id", "application_no", name="uq_pay_app_no"),
    )
    op.create_index(
        "ix_pay_applications_job_id", "pay_applications", ["job_id"], unique=False
    )

    op.create_table(
        "pay_application_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pay_application_id", sa.Integer(), nullable=False),
        sa.Column("sov_line_id", sa.Integer(), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item_no", sa.String(length=10), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column(
            "scheduled_value", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column("this_period", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("stored", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["pay_application_id"], ["pay_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sov_line_id"], ["sov_lines.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pay_application_lines_pay_application_id",
        "pay_application_lines",
        ["pay_application_id"],
        unique=False,
    )

    op.create_table(
        "job_deposits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("deposit_to_account_id", sa.Integer(), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deposit_to_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_deposits_job_id", "job_deposits", ["job_id"], unique=False
    )

    with op.batch_alter_table("invoices") as batch:
        batch.add_column(
            sa.Column(
                "retainage_held", sa.Numeric(15, 2), nullable=False, server_default="0"
            )
        )
        batch.add_column(
            sa.Column(
                "retainage_released",
                sa.Numeric(15, 2),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("pay_application_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_invoice_pay_application",
            "pay_applications",
            ["pay_application_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("bills") as batch:
        batch.add_column(
            sa.Column(
                "retainage_released",
                sa.Numeric(15, 2),
                nullable=False,
                server_default="0",
            )
        )

    with op.batch_alter_table("purchase_orders") as batch:
        batch.add_column(
            sa.Column(
                "retainage_released",
                sa.Numeric(15, 2),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("purchase_orders") as batch:
        batch.drop_column("retainage_released")

    with op.batch_alter_table("bills") as batch:
        batch.drop_column("retainage_released")

    with op.batch_alter_table("invoices") as batch:
        batch.drop_constraint("fk_invoice_pay_application", type_="foreignkey")
        batch.drop_column("pay_application_id")
        batch.drop_column("retainage_released")
        batch.drop_column("retainage_held")

    op.drop_index("ix_job_deposits_job_id", table_name="job_deposits")
    op.drop_table("job_deposits")
    op.drop_index(
        "ix_pay_application_lines_pay_application_id",
        table_name="pay_application_lines",
    )
    op.drop_table("pay_application_lines")
    op.drop_index("ix_pay_applications_job_id", table_name="pay_applications")
    op.drop_table("pay_applications")
