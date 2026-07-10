"""add_forum_features — payment void + vendor default expense account

Revision ID: d4e5f6a7b8c9
Revises: c3f8a1b2d4e6
Create Date: 2026-04-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3f8a1b2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Payment void indicator
    op.add_column('payments', sa.Column('is_voided', sa.Boolean(), server_default='false', nullable=True))

    # Vendor default expense account.
    # Batch mode so this also runs on SQLite (native desktop installs):
    # SQLite cannot ALTER-add a constraint, so batch rebuilds the table
    # there via copy-and-move. On PostgreSQL batch emits the exact same
    # plain ALTER TABLE statements as before — no behavior change for
    # existing server installs.
    with op.batch_alter_table('vendors') as batch_op:
        batch_op.add_column(sa.Column('default_expense_account_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_vendor_default_expense_account',
            'accounts',
            ['default_expense_account_id'], ['id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('vendors') as batch_op:
        batch_op.drop_constraint('fk_vendor_default_expense_account', type_='foreignkey')
        batch_op.drop_column('default_expense_account_id')
    op.drop_column('payments', 'is_voided')
