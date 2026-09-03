"""job_cost_model

Projects milestone 3: the cost model on top of the job dimension.

- cost_types: user-editable list with burden rule + posting/offset accounts
- cost_codes.parent_id: hierarchy (division > code > sub-code)
- equipment: owned machines charged to jobs by the hour
- job_costs / job_cost_lines: the Job Cost Entry document (internal labor,
  equipment, mileage, burden, allocations, corrections)
- job_budgets: budget per job x cost code / cost type (cost + revenue)
- employees.cost_rate / burden_pct: loaded labor cost
- estimate_lines.unit_cost: cost side of an estimate line (revenue is rate)
- time_entries.job_cost_id: marks an entry posted to its job
- transaction_lines.cost_type: type on the posted line for roll-ups

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_cols():
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    op.create_table(
        "cost_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_labor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("burden_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "default_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_cost_types_default_account_id"),
            nullable=True,
        ),
        sa.Column(
            "offset_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_cost_types_offset_account_id"),
            nullable=True,
        ),
        sa.Column(
            "burden_offset_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_cost_types_burden_offset_account_id"),
            nullable=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts_cols(),
    )
    cost_types = sa.table(
        "cost_types",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("is_labor", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        cost_types,
        [
            {"code": "labor", "name": "Labor", "is_labor": True, "sort_order": 1},
            {
                "code": "material",
                "name": "Material",
                "is_labor": False,
                "sort_order": 2,
            },
            {
                "code": "subcontract",
                "name": "Subcontract",
                "is_labor": False,
                "sort_order": 3,
            },
            {
                "code": "equipment",
                "name": "Equipment",
                "is_labor": False,
                "sort_order": 4,
            },
            {"code": "other", "name": "Other", "is_labor": False, "sort_order": 5},
        ],
    )

    with op.batch_alter_table("cost_codes") as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_cost_codes_parent_id", "cost_codes", ["parent_id"], ["id"]
        )

    op.create_table(
        "equipment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=30), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("hourly_rate", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "cost_code_id",
            sa.Integer(),
            sa.ForeignKey("cost_codes.id", name="fk_equipment_cost_code_id"),
            nullable=True,
        ),
        sa.Column(
            "recovery_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_equipment_recovery_account_id"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts_cols(),
    )

    op.create_table(
        "job_costs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(length=30), nullable=False, unique=True),
        sa.Column("date", sa.Date(), nullable=False, index=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", name="fk_job_costs_job_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column(
            "source", sa.String(length=20), nullable=False, server_default="manual"
        ),
        sa.Column(
            "status", sa.String(length=10), nullable=False, server_default="posted"
        ),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", name="fk_job_costs_transaction_id"),
            nullable=True,
        ),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        *_ts_cols(),
    )

    op.create_table(
        "job_cost_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_cost_id",
            sa.Integer(),
            sa.ForeignKey(
                "job_costs.id", name="fk_job_cost_lines_job_cost_id", ondelete="CASCADE"
            ),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", name="fk_job_cost_lines_job_id"),
            nullable=True,
        ),
        sa.Column(
            "cost_code_id",
            sa.Integer(),
            sa.ForeignKey("cost_codes.id", name="fk_job_cost_lines_cost_code_id"),
            nullable=True,
        ),
        sa.Column("cost_type", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("rate", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "debit_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_job_cost_lines_debit_account_id"),
            nullable=False,
        ),
        sa.Column(
            "credit_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", name="fk_job_cost_lines_credit_account_id"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id", name="fk_job_cost_lines_employee_id"),
            nullable=True,
        ),
        sa.Column(
            "equipment_id",
            sa.Integer(),
            sa.ForeignKey("equipment.id", name="fk_job_cost_lines_equipment_id"),
            nullable=True,
        ),
        sa.Column(
            "time_entry_id",
            sa.Integer(),
            sa.ForeignKey("time_entries.id", name="fk_job_cost_lines_time_entry_id"),
            nullable=True,
        ),
        sa.Column("is_burden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "is_billable", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("line_order", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "job_budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", name="fk_job_budgets_job_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "cost_code_id",
            sa.Integer(),
            sa.ForeignKey("cost_codes.id", name="fk_job_budgets_cost_code_id"),
            nullable=True,
        ),
        sa.Column("cost_type", sa.String(length=20), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "revenue_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "source", sa.String(length=20), nullable=False, server_default="manual"
        ),
        sa.Column(
            "estimate_id",
            sa.Integer(),
            sa.ForeignKey("estimates.id", name="fk_job_budgets_estimate_id"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint(
            "job_id", "cost_code_id", "cost_type", name="uq_job_budget"
        ),
    )

    with op.batch_alter_table("employees") as batch_op:
        batch_op.add_column(sa.Column("cost_rate", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("burden_pct", sa.Numeric(6, 2), nullable=True))
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.add_column(sa.Column("unit_cost", sa.Numeric(12, 4), nullable=True))
    with op.batch_alter_table("time_entries") as batch_op:
        batch_op.add_column(sa.Column("job_cost_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_time_entries_job_cost_id", "job_costs", ["job_cost_id"], ["id"]
        )
    with op.batch_alter_table("transaction_lines") as batch_op:
        batch_op.add_column(sa.Column("cost_type", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transaction_lines") as batch_op:
        batch_op.drop_column("cost_type")
    with op.batch_alter_table("time_entries") as batch_op:
        batch_op.drop_constraint("fk_time_entries_job_cost_id", type_="foreignkey")
        batch_op.drop_column("job_cost_id")
    with op.batch_alter_table("estimate_lines") as batch_op:
        batch_op.drop_column("unit_cost")
    with op.batch_alter_table("employees") as batch_op:
        batch_op.drop_column("burden_pct")
        batch_op.drop_column("cost_rate")
    op.drop_table("job_budgets")
    op.drop_table("job_cost_lines")
    op.drop_table("job_costs")
    op.drop_table("equipment")
    with op.batch_alter_table("cost_codes") as batch_op:
        batch_op.drop_constraint("fk_cost_codes_parent_id", type_="foreignkey")
        batch_op.drop_column("parent_id")
    op.drop_table("cost_types")
