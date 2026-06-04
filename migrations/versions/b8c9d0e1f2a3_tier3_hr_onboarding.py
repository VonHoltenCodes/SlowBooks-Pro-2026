"""tier3 HR — employee roles, self-service portal, onboarding, document vault

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-17 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENUMS = {
    'employeerole': ('ADMIN', 'MANAGER', 'EMPLOYEE'),
    'onboardingtasktype': ('W4', 'I9_SECTION1', 'I9_SECTION2', 'EVERIFY',
                           'DIRECT_DEPOSIT', 'STATE_NEW_HIRE_REPORT',
                           'POLICY_ACKNOWLEDGMENT', 'EMERGENCY_CONTACT'),
    'onboardingtaskstatus': ('PENDING', 'IN_PROGRESS', 'COMPLETE'),
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

    # -- Employees: HR / self-service fields --
    op.add_column('employees', sa.Column('email', sa.String(200), nullable=True))
    op.add_column('employees', sa.Column('role', enum_col('employeerole'),
                                         server_default='EMPLOYEE', nullable=True))
    op.add_column('employees', sa.Column('manager_id', sa.Integer(),
                                         sa.ForeignKey('employees.id'), nullable=True))
    op.add_column('employees', sa.Column('portal_token', sa.String(64), nullable=True))
    op.add_column('employees', sa.Column('everify_case_number', sa.String(30), nullable=True))
    op.create_index(op.f('ix_employees_portal_token'), 'employees',
                    ['portal_token'], unique=True)

    # -- Attachments: per-employee HR document vault --
    op.add_column('attachments', sa.Column('employee_id', sa.Integer(),
                                           sa.ForeignKey('employees.id'), nullable=True))
    op.add_column('attachments', sa.Column('doc_category', sa.String(50), nullable=True))
    op.create_index(op.f('ix_attachments_employee_id'), 'attachments', ['employee_id'])

    # -- Onboarding tasks --
    op.create_table(
        'onboarding_tasks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
        sa.Column('task_type', enum_col('onboardingtasktype'), nullable=False),
        sa.Column('status', enum_col('onboardingtaskstatus'), server_default='PENDING'),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('attachments.id'), nullable=True),
        sa.Column('signed', sa.Boolean(), server_default='false'),
        sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_by', sa.String(120), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f('ix_onboarding_tasks_id'), 'onboarding_tasks', ['id'])
    op.create_index(op.f('ix_onboarding_tasks_employee_id'),
                    'onboarding_tasks', ['employee_id'])


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    op.drop_table('onboarding_tasks')

    op.drop_index(op.f('ix_attachments_employee_id'), table_name='attachments')
    op.drop_column('attachments', 'doc_category')
    op.drop_column('attachments', 'employee_id')

    op.drop_index(op.f('ix_employees_portal_token'), table_name='employees')
    for col in ('everify_case_number', 'portal_token', 'manager_id', 'role', 'email'):
        op.drop_column('employees', col)

    if is_pg:
        for name in _ENUMS:
            postgresql.ENUM(name=name).drop(bind, checkfirst=True)
