"""add payroll filing audit model

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-14 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payroll_filing_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.Enum("GENERATED", "FILED", "AMENDED", "SUPERSEDED", name="payrollfilingstatus"), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("pay_run_id", sa.Integer(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot", sa.Text(), nullable=False),
        sa.Column("export_filename", sa.String(length=255), nullable=False),
        sa.Column("export_reference", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("generated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("status_updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ),
        sa.ForeignKeyConstraint(["generated_by_user_id"], ["users.id"], ),
        sa.ForeignKeyConstraint(["pay_run_id"], ["pay_runs.id"], ),
        sa.ForeignKeyConstraint(["status_updated_by_user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_payroll_filing_audits_id"), "payroll_filing_audits", ["id"], unique=False)
    op.create_index(op.f("ix_payroll_filing_audits_filing_type"), "payroll_filing_audits", ["filing_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payroll_filing_audits_filing_type"), table_name="payroll_filing_audits")
    op.drop_index(op.f("ix_payroll_filing_audits_id"), table_name="payroll_filing_audits")
    op.drop_table("payroll_filing_audits")
    op.execute("DROP TYPE IF EXISTS payrollfilingstatus")
