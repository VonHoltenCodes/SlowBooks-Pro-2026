"""tier2 payroll — pre/post-tax deductions, garnishments, multi-state stubs

Revision ID: a7b8c9d0e1f2
Revises: f7a8b9c0d1e2
Create Date: 2026-05-17 15:00:00.000000

down_revision was originally `f6a7b8c9d0e1`, the shared ID that the
tier1 and inventory migrations both claimed. After the cleanup that
renamed tier1 -> f7a8b9c0d1e2, this points at the new tier1 ID so the
chain is linear: inventory -> tier1 -> tier2 -> tier3.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENUMS = {
    'deductioncategory': ('PRETAX', 'POSTTAX'),
    'calcmethod': ('FIXED', 'PERCENT'),
    'garnishmenttype': ('CHILD_SUPPORT', 'FEDERAL_LEVY', 'STATE_TAX_LEVY',
                        'STUDENT_LOAN', 'BANKRUPTCY', 'CREDITOR'),
    'garnishmentmethod': ('FIXED', 'PERCENT_DISPOSABLE'),
}


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    def enum_col(name):
        if is_pg:
            return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)
        return sa.Enum(*_ENUMS[name], name=name)

    # -- Employees: state of residence (reciprocity) --
    op.add_column('employees', sa.Column('residence_state', sa.String(2), nullable=True))

    # -- Pay stubs: garnishments, reimbursements, per-stub work location --
    op.add_column('pay_stubs', sa.Column('garnishments', sa.Numeric(12, 2),
                                         server_default='0', nullable=True))
    op.add_column('pay_stubs', sa.Column('reimbursements', sa.Numeric(12, 2),
                                         server_default='0', nullable=True))
    op.add_column('pay_stubs', sa.Column('work_state', sa.String(2), nullable=True))

    # -- Deduction type catalog --
    op.create_table(
        'deduction_types',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('code', sa.String(30), nullable=True),
        sa.Column('category', enum_col('deductioncategory'), server_default='PRETAX'),
        sa.Column('reduces_federal', sa.Boolean(), server_default='false'),
        sa.Column('reduces_state', sa.Boolean(), server_default='false'),
        sa.Column('reduces_fica', sa.Boolean(), server_default='false'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_deduction_types_id'), 'deduction_types', ['id'])

    # -- Per-employee recurring deductions --
    op.create_table(
        'employee_deductions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('deduction_type_id', sa.Integer(),
                  sa.ForeignKey('deduction_types.id'), nullable=False),
        sa.Column('calc_method', enum_col('calcmethod'), server_default='FIXED'),
        sa.Column('amount', sa.Numeric(12, 2), server_default='0'),
        sa.Column('annual_limit', sa.Numeric(12, 2), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_employee_deductions_id'), 'employee_deductions', ['id'])
    op.create_index(op.f('ix_employee_deductions_employee_id'),
                    'employee_deductions', ['employee_id'])

    # -- Garnishment orders --
    op.create_table(
        'garnishment_orders',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('garnishment_type', enum_col('garnishmenttype'), server_default='CREDITOR'),
        sa.Column('calc_method', enum_col('garnishmentmethod'), server_default='FIXED'),
        sa.Column('amount', sa.Numeric(12, 2), server_default='0'),
        sa.Column('priority', sa.Integer(), server_default='0'),
        sa.Column('case_number', sa.String(80), nullable=True),
        sa.Column('supports_secondary_family', sa.Boolean(), server_default='false'),
        sa.Column('in_arrears_12_weeks', sa.Boolean(), server_default='false'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_garnishment_orders_id'), 'garnishment_orders', ['id'])
    op.create_index(op.f('ix_garnishment_orders_employee_id'),
                    'garnishment_orders', ['employee_id'])


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    op.drop_table('garnishment_orders')
    op.drop_table('employee_deductions')
    op.drop_table('deduction_types')

    op.drop_column('pay_stubs', 'work_state')
    op.drop_column('pay_stubs', 'reimbursements')
    op.drop_column('pay_stubs', 'garnishments')
    op.drop_column('employees', 'residence_state')

    if is_pg:
        for name in _ENUMS:
            postgresql.ENUM(name=name).drop(bind, checkfirst=True)
