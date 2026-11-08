"""construction contract — the contract on the job and its schedule of values (#552)

Step 3 of the commercial GC layer, on top of #551. Additive and nullable
throughout, so a company with no contract terms sees none of it:

- jobs gains the contract terms — contract_type (lump_sum / gmp / cost_plus,
  null = no contract terms, today's behaviour), original_contract_amount (the
  sum when the contract was first set; #554 shows original + changes = revised,
  while contract_amount stays the current sum), fee_pct / fee_amount (a fixed
  fee wins over the percent), cap_amount (GMP maximum / cost-plus NTE),
  contingency_amount (owner contingency inside the sum), retainage_pct (held
  per pay application, used by #555), contract_date and contract_estimate_id
  (SET NULL).
- new table sov_lines — the owner's schedule of values, one scheduled value
  per line with a kind (work / allowance / contingency / fee /
  general_conditions). #555 adds the billed / stored / retainage columns.

Nothing is backfilled. downgrade reverses.

Revision ID: e2a4c6d8f0b1
Revises: d9f6b2c4e7a1
Create Date: 2026-10-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e2a4c6d8f0b1"
down_revision: Union[str, None] = "d9f6b2c4e7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("contract_type", sa.String(length=12), nullable=True))
        batch.add_column(
            sa.Column("original_contract_amount", sa.Numeric(15, 2), nullable=True)
        )
        batch.add_column(sa.Column("fee_pct", sa.Numeric(7, 4), nullable=True))
        batch.add_column(sa.Column("fee_amount", sa.Numeric(15, 2), nullable=True))
        batch.add_column(sa.Column("cap_amount", sa.Numeric(15, 2), nullable=True))
        batch.add_column(
            sa.Column("contingency_amount", sa.Numeric(15, 2), nullable=True)
        )
        batch.add_column(sa.Column("retainage_pct", sa.Numeric(5, 2), nullable=True))
        batch.add_column(sa.Column("contract_date", sa.Date(), nullable=True))
        batch.add_column(
            sa.Column("contract_estimate_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_jobs_contract_estimate",
            "estimates",
            ["contract_estimate_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "sov_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item_no", sa.String(length=10), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column(
            "trade_package_id",
            sa.Integer(),
            sa.ForeignKey("trade_packages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cost_code_id",
            sa.Integer(),
            sa.ForeignKey("cost_codes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="work"),
        sa.Column(
            "scheduled_value", sa.Numeric(15, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "source_estimate_id",
            sa.Integer(),
            sa.ForeignKey("estimates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "line_no", name="uq_sov_line_no"),
    )
    op.create_index("ix_sov_lines_job_id", "sov_lines", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_sov_lines_job_id", table_name="sov_lines")
    op.drop_table("sov_lines")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_contract_estimate", type_="foreignkey")
        batch.drop_column("contract_estimate_id")
        batch.drop_column("contract_date")
        batch.drop_column("retainage_pct")
        batch.drop_column("contingency_amount")
        batch.drop_column("cap_amount")
        batch.drop_column("fee_amount")
        batch.drop_column("fee_pct")
        batch.drop_column("original_contract_amount")
        batch.drop_column("contract_type")
