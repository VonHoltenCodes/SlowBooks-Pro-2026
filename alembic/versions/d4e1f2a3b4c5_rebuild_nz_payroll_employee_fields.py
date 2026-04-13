"""rebuild nz payroll employee fields

Revision ID: d4e1f2a3b4c5
Revises: c3d9e0f1a2b3
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e1f2a3b4c5"
down_revision: Union[str, None] = "c3d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch_op:
        batch_op.add_column(sa.Column("ird_number", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("tax_code", sa.String(length=20), server_default="M", nullable=True))
        batch_op.add_column(sa.Column("kiwisaver_enrolled", sa.Boolean(), server_default=sa.false(), nullable=True))
        batch_op.add_column(sa.Column("kiwisaver_rate", sa.Numeric(precision=6, scale=4), server_default="0.0300", nullable=True))
        batch_op.add_column(sa.Column("student_loan", sa.Boolean(), server_default=sa.false(), nullable=True))
        batch_op.add_column(sa.Column("child_support", sa.Boolean(), server_default=sa.false(), nullable=True))
        batch_op.add_column(sa.Column("esct_rate", sa.Numeric(precision=6, scale=4), server_default="0.0000", nullable=True))
        batch_op.add_column(sa.Column("pay_frequency", sa.String(length=20), server_default="fortnightly", nullable=True))
        batch_op.add_column(sa.Column("start_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("end_date", sa.Date(), nullable=True))

    op.execute("UPDATE employees SET start_date = hire_date WHERE start_date IS NULL AND hire_date IS NOT NULL")

    with op.batch_alter_table("employees") as batch_op:
        batch_op.alter_column("tax_code", server_default=None)
        batch_op.alter_column("kiwisaver_enrolled", server_default=None)
        batch_op.alter_column("kiwisaver_rate", server_default=None)
        batch_op.alter_column("student_loan", server_default=None)
        batch_op.alter_column("child_support", server_default=None)
        batch_op.alter_column("esct_rate", server_default=None)
        batch_op.alter_column("pay_frequency", server_default=None)
        batch_op.drop_column("ssn_last_four")
        batch_op.drop_column("filing_status")
        batch_op.drop_column("allowances")
        batch_op.drop_column("hire_date")


def downgrade() -> None:
    with op.batch_alter_table("employees") as batch_op:
        batch_op.add_column(sa.Column("hire_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("allowances", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("filing_status", sa.String(length=17), nullable=True))
        batch_op.add_column(sa.Column("ssn_last_four", sa.String(length=4), nullable=True))

    op.execute("UPDATE employees SET hire_date = start_date WHERE hire_date IS NULL AND start_date IS NOT NULL")

    with op.batch_alter_table("employees") as batch_op:
        batch_op.drop_column("end_date")
        batch_op.drop_column("start_date")
        batch_op.drop_column("pay_frequency")
        batch_op.drop_column("esct_rate")
        batch_op.drop_column("child_support")
        batch_op.drop_column("student_loan")
        batch_op.drop_column("kiwisaver_rate")
        batch_op.drop_column("kiwisaver_enrolled")
        batch_op.drop_column("tax_code")
        batch_op.drop_column("ird_number")
