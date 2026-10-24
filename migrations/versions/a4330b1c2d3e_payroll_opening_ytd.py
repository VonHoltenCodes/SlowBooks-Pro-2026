"""opening YTD balances per employee for a mid-year switch (#433)

Year-to-date payroll was summed from processed stubs only, and payroll
history does not come across from QuickBooks, so a company that switched to
SlowBooks mid-year had no way to say what was already paid: every W-2, 941
and wage-base cutoff (Social Security, FUTA, SUTA) was wrong for the rest of
the year.

Four tables:

- payroll_opening_balances: one row per employee per year, dated the day
  before the first SlowBooks pay run (as_of).
- payroll_opening_quarters: the old system's federal figures per calendar
  quarter (gross, withholding, Social Security / Medicare / FUTA wages and
  tax both sides, deductions, garnishments, net), the way QuickBooks takes
  year-to-date adjustments: each quarter's 941 carries only its own.
- payroll_opening_state_balances: per quarter and state, the state wages,
  withholding, SUTA wages and tax (the SUTA wage base is a state's own) and
  other state taxes.
- payroll_opening_benefits: the employee and employer amounts per benefit
  code, which the annual caps count.

And pay_runs.stale_reason: set on a draft whose employee's opening balance
changed after the draft was figured, so it is not processed unawares.

Not a pay run and not a journal entry: the old system booked that payroll,
and the opening balance sheet carries what it still owes. The readers that
count it (employee_ytd, the 941, 940, W-2/W-3, the benefit accumulators) read
these tables on purpose; nothing else sees them.

Data-only note: nothing is backfilled; there is no earlier representation of
an opening balance to migrate from.

Revision ID: a4330b1c2d3e
Revises: 4ac01efbd72a
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4330b1c2d3e"
down_revision: Union[str, None] = "4ac01efbd72a"
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
    "pretax_deductions",
    "posttax_deductions",
    "garnishments",
    "net",
)
STATE_AMOUNTS = (
    "wages",
    "withholding",
    "suta_wages",
    "suta_tax",
    "other_employee",
    "other_employer",
)


def _money(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(15, 2), nullable=False, server_default="0")


def _opening_fk() -> sa.Column:
    return sa.Column(
        "opening_id",
        sa.Integer(),
        sa.ForeignKey("payroll_opening_balances.id", ondelete="CASCADE"),
        nullable=False,
    )


def _indexes(table: str, *cols: str) -> None:
    for col in ("id",) + cols:
        op.create_index(f"ix_{table}_{col}", table, [col])


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
        _indexes("payroll_opening_balances", "employee_id")
    if "payroll_opening_quarters" not in tables:
        op.create_table(
            "payroll_opening_quarters",
            sa.Column("id", sa.Integer(), primary_key=True),
            _opening_fk(),
            sa.Column("quarter", sa.Integer(), nullable=False),
            *[_money(name) for name in AMOUNTS],
            sa.UniqueConstraint(
                "opening_id", "quarter", name="uq_payroll_opening_quarter"
            ),
        )
        _indexes("payroll_opening_quarters", "opening_id")
    if "payroll_opening_state_balances" not in tables:
        op.create_table(
            "payroll_opening_state_balances",
            sa.Column("id", sa.Integer(), primary_key=True),
            _opening_fk(),
            sa.Column("quarter", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(length=2), nullable=False),
            *[_money(name) for name in STATE_AMOUNTS],
            sa.UniqueConstraint(
                "opening_id", "quarter", "state", name="uq_payroll_opening_state"
            ),
        )
        _indexes("payroll_opening_state_balances", "opening_id")
    if "payroll_opening_benefits" not in tables:
        op.create_table(
            "payroll_opening_benefits",
            sa.Column("id", sa.Integer(), primary_key=True),
            _opening_fk(),
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
        _indexes("payroll_opening_benefits", "opening_id")
    if "pay_runs" in tables and "stale_reason" not in {
        c["name"] for c in insp.get_columns("pay_runs")
    }:
        with op.batch_alter_table("pay_runs") as batch:
            batch.add_column(
                sa.Column("stale_reason", sa.String(length=500), nullable=True)
            )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()
    if "pay_runs" in tables and "stale_reason" in {
        c["name"] for c in insp.get_columns("pay_runs")
    }:
        with op.batch_alter_table("pay_runs") as batch:
            batch.drop_column("stale_reason")
    for table in (
        "payroll_opening_benefits",
        "payroll_opening_state_balances",
        "payroll_opening_quarters",
        "payroll_opening_balances",
    ):
        if table in tables:
            op.drop_table(table)
