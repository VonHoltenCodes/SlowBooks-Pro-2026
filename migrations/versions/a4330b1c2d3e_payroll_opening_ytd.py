"""opening YTD balances per employee for a mid-year switch (#433)

Year-to-date payroll was summed from processed stubs only, and payroll
history does not come across from QuickBooks, so a company that switched to
SlowBooks mid-year had no way to say what was already paid: every W-2, 941
and wage-base cutoff (Social Security, FUTA, SUTA) was wrong for the rest of
the year.

Three tables:

- payroll_opening_balances: one row per employee per year, dated the day
  before the first SlowBooks pay run (as_of), with the old system's gross,
  withholding, FICA wages and tax both sides, FUTA/SUTA wages and tax,
  deductions, garnishments and net.
- payroll_opening_state_balances: the state wages and withholding, one row
  per state (a stub has one work state; a year before the switch may have
  several).
- payroll_opening_benefits: the employee and employer amounts per benefit
  code, which the annual caps count.

Not a pay run and not a journal entry: the old system booked that payroll,
and the opening balance sheet carries what it still owes. The readers that
count it (employee_ytd, the 941, 940, W-2/W-3, the benefit accumulators) read
these tables on purpose; nothing else sees them.

Data-only note: nothing is backfilled; there is no earlier representation of
an opening balance to migrate from.

Revision ID: a4330b1c2d3e
Revises: d3e4f5a6b7c8
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4330b1c2d3e"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AMOUNTS = (
    "gross",
    "federal_tax",
    "ss_wages",
    "ss_tax",
    "employer_ss_tax",
    "medicare_wages",
    "medicare_tax",
    "employer_medicare_tax",
    "futa_wages",
    "futa_tax",
    "suta_wages",
    "suta_tax",
    "pretax_deductions",
    "posttax_deductions",
    "garnishments",
    "net",
)


def _money(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(15, 2), nullable=False, server_default="0")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()
    if "payroll_opening_balances" not in tables:
        op.create_table(
            "payroll_opening_balances",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "employee_id",
                sa.Integer(),
                sa.ForeignKey("employees.id"),
                nullable=False,
            ),
            sa.Column("year", sa.Integer(), nullable=False),
            sa.Column("as_of", sa.Date(), nullable=False),
            *[_money(name) for name in AMOUNTS],
            sa.Column("note", sa.String(length=300), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "employee_id", "year", name="uq_payroll_opening_emp_year"
            ),
        )
        op.create_index(
            "ix_payroll_opening_balances_id", "payroll_opening_balances", ["id"]
        )
        op.create_index(
            "ix_payroll_opening_balances_employee_id",
            "payroll_opening_balances",
            ["employee_id"],
        )
    if "payroll_opening_state_balances" not in tables:
        op.create_table(
            "payroll_opening_state_balances",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "opening_id",
                sa.Integer(),
                sa.ForeignKey("payroll_opening_balances.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("state", sa.String(length=2), nullable=False),
            _money("wages"),
            _money("withholding"),
            _money("other_employee"),
            _money("other_employer"),
            sa.UniqueConstraint("opening_id", "state", name="uq_payroll_opening_state"),
        )
        op.create_index(
            "ix_payroll_opening_state_balances_id",
            "payroll_opening_state_balances",
            ["id"],
        )
        op.create_index(
            "ix_payroll_opening_state_balances_opening_id",
            "payroll_opening_state_balances",
            ["opening_id"],
        )
    if "payroll_opening_benefits" not in tables:
        op.create_table(
            "payroll_opening_benefits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "opening_id",
                sa.Integer(),
                sa.ForeignKey("payroll_opening_balances.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "benefit_code_id",
                sa.Integer(),
                sa.ForeignKey("benefit_codes.id"),
                nullable=False,
            ),
            _money("employee_amount"),
            _money("employer_amount"),
            sa.UniqueConstraint(
                "opening_id", "benefit_code_id", name="uq_payroll_opening_benefit"
            ),
        )
        op.create_index(
            "ix_payroll_opening_benefits_id", "payroll_opening_benefits", ["id"]
        )
        op.create_index(
            "ix_payroll_opening_benefits_opening_id",
            "payroll_opening_benefits",
            ["opening_id"],
        )


def downgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    for table in (
        "payroll_opening_benefits",
        "payroll_opening_state_balances",
        "payroll_opening_balances",
    ):
        if table in tables:
            op.drop_table(table)
