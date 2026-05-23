"""add_qbo_mappings

Revision ID: c3f8a1b2d4e6
Revises: ae790370792b
Create Date: 2026-04-13 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f8a1b2d4e6'
down_revision: Union[str, None] = 'ae790370792b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'qbo_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('slowbooks_id', sa.Integer(), nullable=False),
        sa.Column('qbo_id', sa.String(length=100), nullable=False),
        sa.Column('qbo_sync_token', sa.String(length=50), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_qbo_mappings_id'), 'qbo_mappings', ['id'], unique=False)
    # Composite index for fast lookups by entity_type + qbo_id
    op.create_index('ix_qbo_mappings_entity_qbo', 'qbo_mappings',
                    ['entity_type', 'qbo_id'], unique=False)
    # Index for reverse lookups by entity_type + slowbooks_id
    op.create_index('ix_qbo_mappings_entity_slowbooks', 'qbo_mappings',
                    ['entity_type', 'slowbooks_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_qbo_mappings_entity_slowbooks', table_name='qbo_mappings')
    op.drop_index('ix_qbo_mappings_entity_qbo', table_name='qbo_mappings')
    op.drop_index(op.f('ix_qbo_mappings_id'), table_name='qbo_mappings')
    op.drop_table('qbo_mappings')
