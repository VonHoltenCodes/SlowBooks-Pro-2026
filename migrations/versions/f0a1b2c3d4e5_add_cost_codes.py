"""add_cost_codes

Projects milestone 2: the `cost_codes` table (job-costing chart with a
cost type and optional default account), cost_code_id on every cost- or
revenue-bearing line, and the billable flag on cost lines (bill lines and
posted journal lines) with the invoice-line back-reference milestone 3
uses to mark a cost as billed.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODE_TABLES = (
    "transaction_lines",
    "bill_lines",
    "invoice_lines",
    "estimate_lines",
    "purchase_order_lines",
    "time_entries",
)

_BILLABLE_TABLES = ("transaction_lines", "bill_lines")


def upgrade() -> None:
    op.create_table(
        "cost_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "cost_type", sa.String(length=20), nullable=False, server_default="other"
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_cost_codes_account_id"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    for table in _CODE_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("cost_code_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_cost_code_id", "cost_codes", ["cost_code_id"], ["id"]
            )

    for table in _BILLABLE_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_billable",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
            batch_op.add_column(
                sa.Column("billed_invoice_line_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                f"fk_{table}_billed_invoice_line_id",
                "invoice_lines",
                ["billed_invoice_line_id"],
                ["id"],
            )


def downgrade() -> None:
    for table in reversed(_BILLABLE_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table}_billed_invoice_line_id", type_="foreignkey"
            )
            batch_op.drop_column("billed_invoice_line_id")
            batch_op.drop_column("is_billable")
    for table in reversed(_CODE_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_cost_code_id", type_="foreignkey")
            batch_op.drop_column("cost_code_id")
    op.drop_table("cost_codes")
