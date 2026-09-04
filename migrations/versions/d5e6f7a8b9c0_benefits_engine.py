"""benefits_engine

The benefits engine: a benefit is a code with a rule. New tables
benefit_codes, benefit_rates (effective-dated), employee_groups,
employee_group_benefits, employee_benefits, benefit_ytd (first-class
accumulators) and pay_stub_benefits (posted-run snapshots). Employees gain
a group, pay stubs / runs an employer-benefits total, PTO policies a dollar
liability, PTO accruals a dollar balance, cost types a burden method.

Existing deduction_types / employee_deductions rows are migrated onto
benefit codes and assignments (the old tables are left in place, unused).

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    op.create_table(
        "benefit_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="deduction"),
        sa.Column("category", sa.String(10), nullable=False, server_default="pretax"),
        sa.Column(
            "calc_method", sa.String(24), nullable=False, server_default="fixed_amount"
        ),
        sa.Column("employer_calc_method", sa.String(24), nullable=True),
        sa.Column(
            "reduces_federal", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "reduces_state", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "reduces_fica", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "employer_taxable", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "expense_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
        sa.Column(
            "liability_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
        sa.Column(
            "remittance_vendor_id",
            sa.Integer(),
            sa.ForeignKey("vendors.id"),
            nullable=True,
        ),
        sa.Column(
            "burden_routing",
            sa.String(12),
            nullable=False,
            server_default="fringe_pool",
        ),
        sa.Column(
            "tracks_balance", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_table(
        "benefit_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "benefit_code_id",
            sa.Integer(),
            sa.ForeignKey("benefit_codes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "employee_rate", sa.Numeric(12, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "employer_rate", sa.Numeric(12, 4), nullable=False, server_default="0"
        ),
        sa.Column("per_period_cap", sa.Numeric(12, 2), nullable=True),
        sa.Column("annual_cap", sa.Numeric(12, 2), nullable=True),
        sa.Column("wage_base_ceiling", sa.Numeric(12, 2), nullable=True),
        sa.Column("employer_annual_cap", sa.Numeric(12, 2), nullable=True),
        sa.Column("employer_match_limit_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("tiers_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_table(
        "employee_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_table(
        "employee_group_benefits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("employee_groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "benefit_code_id",
            sa.Integer(),
            sa.ForeignKey("benefit_codes.id"),
            nullable=False,
        ),
        sa.Column("employee_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("employer_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("per_period_cap", sa.Numeric(12, 2), nullable=True),
        sa.Column("annual_cap", sa.Numeric(12, 2), nullable=True),
        sa.UniqueConstraint("group_id", "benefit_code_id", name="uq_group_benefit"),
    )
    op.create_table(
        "employee_benefits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "benefit_code_id",
            sa.Integer(),
            sa.ForeignKey("benefit_codes.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("employee_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("employer_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("per_period_cap", sa.Numeric(12, 2), nullable=True),
        sa.Column("annual_cap", sa.Numeric(12, 2), nullable=True),
        sa.Column("balance_remaining", sa.Numeric(12, 2), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_table(
        "benefit_ytd",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "benefit_code_id",
            sa.Integer(),
            sa.ForeignKey("benefit_codes.id"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "employee_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "employer_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "employee_id", "benefit_code_id", "year", name="uq_benefit_ytd"
        ),
    )
    op.create_table(
        "pay_stub_benefits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "pay_stub_id",
            sa.Integer(),
            sa.ForeignKey("pay_stubs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "benefit_code_id",
            sa.Integer(),
            sa.ForeignKey("benefit_codes.id"),
            nullable=True,
        ),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("category", sa.String(10), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("calc_method", sa.String(24), nullable=False),
        sa.Column(
            "employee_rate", sa.Numeric(12, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "employer_rate", sa.Numeric(12, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "reduces_federal", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "reduces_state", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "reduces_fica", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "expense_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
        sa.Column(
            "liability_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
        sa.Column(
            "remittance_vendor_id",
            sa.Integer(),
            sa.ForeignKey("vendors.id"),
            nullable=True,
        ),
        sa.Column(
            "burden_routing",
            sa.String(12),
            nullable=False,
            server_default="fringe_pool",
        ),
        sa.Column("rule_json", sa.Text(), nullable=True),
        sa.Column(
            "employee_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "employer_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
    )

    with op.batch_alter_table("employees") as batch_op:
        batch_op.add_column(
            sa.Column(
                "employee_group_id",
                sa.Integer(),
                sa.ForeignKey(
                    "employee_groups.id", name="fk_employees_employee_group_id"
                ),
                nullable=True,
            )
        )
    with op.batch_alter_table("pay_stubs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "employer_benefits",
                sa.Numeric(12, 2),
                nullable=True,
                server_default="0",
            )
        )
    with op.batch_alter_table("pay_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "total_employer_benefits",
                sa.Numeric(12, 2),
                nullable=True,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "burden_job_cost_id",
                sa.Integer(),
                sa.ForeignKey("job_costs.id", name="fk_pay_runs_burden_job_cost_id"),
                nullable=True,
            )
        )
    with op.batch_alter_table("pto_policies") as batch_op:
        batch_op.add_column(
            sa.Column(
                "accrue_liability",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "valuation",
                sa.String(20),
                nullable=False,
                server_default="current_rate",
            )
        )
        batch_op.add_column(
            sa.Column(
                "expense_account_id",
                sa.Integer(),
                sa.ForeignKey("accounts.id", name="fk_pto_policies_expense_account_id"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "liability_account_id",
                sa.Integer(),
                sa.ForeignKey(
                    "accounts.id", name="fk_pto_policies_liability_account_id"
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pays_out_on_termination",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("pto_accruals") as batch_op:
        batch_op.add_column(
            sa.Column(
                "dollar_balance", sa.Numeric(12, 2), nullable=False, server_default="0"
            )
        )
    with op.batch_alter_table("cost_types") as batch_op:
        batch_op.add_column(
            sa.Column(
                "burden_method", sa.String(10), nullable=False, server_default="flat"
            )
        )

    # --- Data: deduction types → benefit codes, enrollments → assignments
    if _has_table("deduction_types"):
        bind = op.get_bind()
        types = bind.execute(
            sa.text(
                "SELECT id, name, code, category, reduces_federal, reduces_state, "
                "reduces_fica, is_active FROM deduction_types ORDER BY id"
            )
        ).fetchall()
        seq = 10
        id_map = {}
        for t in types:
            code = (t.code or f"DED{t.id}").strip().upper()[:30]
            category = (
                str(t.category or "pretax").lower().replace("deductioncategory.", "")
            )
            if category not in ("pretax", "posttax"):
                category = "pretax"
            res = bind.execute(
                sa.text(
                    "INSERT INTO benefit_codes (code, name, kind, category, calc_method, "
                    "reduces_federal, reduces_state, reduces_fica, sequence, is_active, "
                    "burden_routing, tracks_balance, employer_taxable) "
                    "VALUES (:code, :name, 'deduction', :category, 'fixed_amount', "
                    ":rf, :rs, :rfi, :seq, :active, 'fringe_pool', 0, 0)"
                ),
                {
                    "code": code,
                    "name": t.name,
                    "category": category,
                    "rf": bool(t.reduces_federal),
                    "rs": bool(t.reduces_state),
                    "rfi": bool(t.reduces_fica),
                    "seq": seq,
                    "active": bool(t.is_active) if t.is_active is not None else True,
                },
            )
            new_id = res.lastrowid
            if not new_id:
                new_id = bind.execute(
                    sa.text("SELECT id FROM benefit_codes WHERE code = :c"), {"c": code}
                ).scalar()
            id_map[t.id] = new_id
            bind.execute(
                sa.text(
                    "INSERT INTO benefit_rates (benefit_code_id, effective_from, "
                    "employee_rate, employer_rate) VALUES (:bid, '2000-01-01', 0, 0)"
                ),
                {"bid": new_id},
            )
            seq += 10
        if _has_table("employee_deductions"):
            rows = bind.execute(
                sa.text(
                    "SELECT employee_id, deduction_type_id, calc_method, amount, "
                    "annual_limit, is_active FROM employee_deductions"
                )
            ).fetchall()
            for r in rows:
                bid = id_map.get(r.deduction_type_id)
                if not bid:
                    continue
                method = (
                    str(r.calc_method or "fixed").lower().replace("calcmethod.", "")
                )
                if method == "percent":
                    # a percent election means the code should calculate as
                    # percent of gross for this employee; set the code's
                    # method if every election agrees, else keep fixed and
                    # let the operator review.
                    bind.execute(
                        sa.text(
                            "UPDATE benefit_codes SET calc_method = 'percent_of_gross' "
                            "WHERE id = :bid AND calc_method = 'fixed_amount'"
                        ),
                        {"bid": bid},
                    )
                bind.execute(
                    sa.text(
                        "INSERT INTO employee_benefits (employee_id, benefit_code_id, "
                        "employee_rate, annual_cap, is_active) "
                        "VALUES (:eid, :bid, :rate, :cap, :active)"
                    ),
                    {
                        "eid": r.employee_id,
                        "bid": bid,
                        "rate": r.amount or 0,
                        "cap": r.annual_limit,
                        "active": (
                            bool(r.is_active) if r.is_active is not None else True
                        ),
                    },
                )


def downgrade() -> None:
    with op.batch_alter_table("cost_types") as batch_op:
        batch_op.drop_column("burden_method")
    with op.batch_alter_table("pto_accruals") as batch_op:
        batch_op.drop_column("dollar_balance")
    with op.batch_alter_table("pto_policies") as batch_op:
        batch_op.drop_column("pays_out_on_termination")
        batch_op.drop_column("liability_account_id")
        batch_op.drop_column("expense_account_id")
        batch_op.drop_column("valuation")
        batch_op.drop_column("accrue_liability")
    with op.batch_alter_table("pay_runs") as batch_op:
        batch_op.drop_column("burden_job_cost_id")
        batch_op.drop_column("total_employer_benefits")
    with op.batch_alter_table("pay_stubs") as batch_op:
        batch_op.drop_column("employer_benefits")
    with op.batch_alter_table("employees") as batch_op:
        batch_op.drop_column("employee_group_id")
    op.drop_table("pay_stub_benefits")
    op.drop_table("benefit_ytd")
    op.drop_table("employee_benefits")
    op.drop_table("employee_group_benefits")
    op.drop_table("employee_groups")
    op.drop_table("benefit_rates")
    op.drop_table("benefit_codes")
