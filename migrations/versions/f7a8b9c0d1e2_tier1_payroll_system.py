"""tier1 payroll — modern W-4, pay frequency, time tracking, PTO, bank accounts

Revision ID: f7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-17 12:00:00.000000

The original ID `f6a7b8c9d0e1` collided with the Phase 11 inventory
migration (Alembic picked one alphabetically). Renamed during the
2026-05-21 repo cleanup so the chain is now linear:
inventory (f6a7b8c9d0e1) -> tier1 (f7a8b9c0d1e2) -> tier2 -> tier3.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum types introduced by this migration. Created up front with checkfirst so
# that tables/columns sharing a type (e.g. ptotype) don't double-create it.
_ENUMS = {
    'payfrequency': ('WEEKLY', 'BIWEEKLY', 'SEMI_MONTHLY', 'MONTHLY'),
    'payruntype': ('REGULAR', 'OFF_CYCLE', 'BONUS'),
    'timeentrystatus': ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED'),
    'ptotype': ('VACATION', 'SICK', 'PERSONAL'),
    'accrualmethod': ('PER_HOUR_WORKED', 'PER_PAY_PERIOD', 'ANNUAL_GRANT'),
    'ptorequeststatus': ('PENDING', 'APPROVED', 'DENIED'),
    'bankaccountkind': ('CHECKING', 'SAVINGS'),
    'deposittype': ('FULL', 'PERCENT', 'FIXED', 'REMAINDER'),
    'prenotestatus': ('NOT_SENT', 'PENDING', 'CONFIRMED'),
}


def _enum(name: str):
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    def enum_col(name):
        return _enum(name) if is_pg else sa.Enum(*_ENUMS[name], name=name)

    # -- Employees: drop pre-2020 allowances, add modern W-4 + pay frequency --
    op.add_column('employees', sa.Column('pay_frequency', enum_col('payfrequency'),
                                         server_default='BIWEEKLY', nullable=True))
    op.add_column('employees', sa.Column('multiple_jobs', sa.Boolean(), server_default='false', nullable=True))
    op.add_column('employees', sa.Column('dependents_amount', sa.Numeric(12, 2), server_default='0', nullable=True))
    op.add_column('employees', sa.Column('other_income_annual', sa.Numeric(12, 2), server_default='0', nullable=True))
    op.add_column('employees', sa.Column('deductions_annual', sa.Numeric(12, 2), server_default='0', nullable=True))
    op.add_column('employees', sa.Column('extra_withholding', sa.Numeric(12, 2), server_default='0', nullable=True))
    op.add_column('employees', sa.Column('work_state', sa.String(2), nullable=True))
    op.add_column('employees', sa.Column('wc_class_code', sa.String(20), nullable=True))
    op.drop_column('employees', 'allowances')

    # -- Pay runs: run type + employer tax total --
    op.add_column('pay_runs', sa.Column('run_type', enum_col('payruntype'),
                                        server_default='REGULAR', nullable=True))
    op.add_column('pay_runs', sa.Column('total_employer_taxes', sa.Numeric(12, 2), server_default='0', nullable=True))

    # -- Pay stubs: itemized hours, deductions, employer-side taxes, detail --
    for col in ('regular_hours', 'overtime_hours', 'doubletime_hours'):
        op.add_column('pay_stubs', sa.Column(col, sa.Numeric(10, 2), server_default='0', nullable=True))
    for col in ('state_other_employee', 'pretax_deductions', 'posttax_deductions',
                'employer_ss_tax', 'employer_medicare_tax', 'futa_tax', 'suta_tax',
                'state_other_employer'):
        op.add_column('pay_stubs', sa.Column(col, sa.Numeric(12, 2), server_default='0', nullable=True))
    op.add_column('pay_stubs', sa.Column('detail_json', sa.Text(), nullable=True))

    # -- Vendors: 1099-NEC workflow --
    op.add_column('vendors', sa.Column('is_1099_eligible', sa.Boolean(), server_default='false', nullable=True))
    op.add_column('vendors', sa.Column('w9_on_file', sa.Boolean(), server_default='false', nullable=True))
    op.add_column('vendors', sa.Column('w9_document_id', sa.Integer(),
                                       sa.ForeignKey('attachments.id'), nullable=True))

    # -- Time entries --
    op.create_table(
        'time_entries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('hours_regular', sa.Numeric(10, 2), server_default='0'),
        sa.Column('hours_overtime', sa.Numeric(10, 2), server_default='0'),
        sa.Column('hours_doubletime', sa.Numeric(10, 2), server_default='0'),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', enum_col('timeentrystatus'), server_default='DRAFT'),
        sa.Column('approved_by', sa.String(200), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pay_run_id', sa.Integer(), sa.ForeignKey('pay_runs.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_time_entries_id'), 'time_entries', ['id'])
    op.create_index(op.f('ix_time_entries_employee_id'), 'time_entries', ['employee_id'])
    op.create_index(op.f('ix_time_entries_date'), 'time_entries', ['date'])
    op.create_index(op.f('ix_time_entries_pay_run_id'), 'time_entries', ['pay_run_id'])

    # -- PTO policies / accruals / requests --
    op.create_table(
        'pto_policies',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('pto_type', enum_col('ptotype'), server_default='VACATION'),
        sa.Column('accrual_method', enum_col('accrualmethod'), server_default='PER_PAY_PERIOD'),
        sa.Column('accrual_rate', sa.Numeric(10, 4), server_default='0'),
        sa.Column('max_carryover', sa.Numeric(10, 2), nullable=True),
        sa.Column('max_balance', sa.Numeric(10, 2), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_pto_policies_id'), 'pto_policies', ['id'])

    op.create_table(
        'pto_accruals',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('policy_id', sa.Integer(), sa.ForeignKey('pto_policies.id'), nullable=False),
        sa.Column('balance', sa.Numeric(10, 2), server_default='0'),
        sa.Column('accrued_ytd', sa.Numeric(10, 2), server_default='0'),
        sa.Column('used_ytd', sa.Numeric(10, 2), server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_pto_accruals_id'), 'pto_accruals', ['id'])
    op.create_index(op.f('ix_pto_accruals_employee_id'), 'pto_accruals', ['employee_id'])

    op.create_table(
        'pto_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('hours', sa.Numeric(10, 2), server_default='0'),
        sa.Column('pto_type', enum_col('ptotype'), server_default='VACATION'),
        sa.Column('status', enum_col('ptorequeststatus'), server_default='PENDING'),
        sa.Column('approver_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_pto_requests_id'), 'pto_requests', ['id'])
    op.create_index(op.f('ix_pto_requests_employee_id'), 'pto_requests', ['employee_id'])

    # -- Employee bank accounts (direct deposit) --
    op.create_table(
        'employee_bank_accounts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('nickname', sa.String(100), nullable=True),
        sa.Column('account_kind', enum_col('bankaccountkind'), server_default='CHECKING'),
        sa.Column('routing_number_enc', sa.String(255), nullable=True),
        sa.Column('account_number_enc', sa.String(255), nullable=True),
        sa.Column('account_last_four', sa.String(4), nullable=True),
        sa.Column('deposit_type', enum_col('deposittype'), server_default='FULL'),
        sa.Column('deposit_value', sa.Numeric(12, 2), server_default='0'),
        sa.Column('priority', sa.Integer(), server_default='0'),
        sa.Column('prenote_status', enum_col('prenotestatus'), server_default='NOT_SENT'),
        sa.Column('prenote_sent_date', sa.Date(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_employee_bank_accounts_id'), 'employee_bank_accounts', ['id'])
    op.create_index(op.f('ix_employee_bank_accounts_employee_id'), 'employee_bank_accounts', ['employee_id'])


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    op.drop_table('employee_bank_accounts')
    op.drop_table('pto_requests')
    op.drop_table('pto_accruals')
    op.drop_table('pto_policies')
    op.drop_table('time_entries')

    op.drop_column('vendors', 'w9_document_id')
    op.drop_column('vendors', 'w9_on_file')
    op.drop_column('vendors', 'is_1099_eligible')

    op.drop_column('pay_stubs', 'detail_json')
    for col in ('state_other_employer', 'suta_tax', 'futa_tax', 'employer_medicare_tax',
                'employer_ss_tax', 'posttax_deductions', 'pretax_deductions',
                'state_other_employee', 'doubletime_hours', 'overtime_hours', 'regular_hours'):
        op.drop_column('pay_stubs', col)

    op.drop_column('pay_runs', 'total_employer_taxes')
    op.drop_column('pay_runs', 'run_type')

    op.add_column('employees', sa.Column('allowances', sa.Integer(), server_default='0', nullable=True))
    for col in ('wc_class_code', 'work_state', 'extra_withholding', 'deductions_annual',
                'other_income_annual', 'dependents_amount', 'multiple_jobs', 'pay_frequency'):
        op.drop_column('employees', col)

    if is_pg:
        for name in _ENUMS:
            postgresql.ENUM(name=name).drop(bind, checkfirst=True)
