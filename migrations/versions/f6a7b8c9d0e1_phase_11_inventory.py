"""phase_11 — inventory tracking, saved reports, duplicate detection

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Items: inventory tracking fields --
    op.add_column('items', sa.Column('track_inventory', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('items', sa.Column('quantity_on_hand', sa.Numeric(14, 4), server_default='0', nullable=False))
    op.add_column('items', sa.Column('reorder_point', sa.Numeric(14, 4), server_default='0', nullable=False))
    op.add_column('items', sa.Column('avg_cost', sa.Numeric(14, 4), server_default='0', nullable=False))
    op.add_column('items', sa.Column('asset_account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=True))

    # -- Inventory Movements ledger --
    op.create_table(
        'inventory_movements',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False, index=True),
        sa.Column('date', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('movement_type', sa.String(20), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 4), nullable=False),
        sa.Column('unit_cost', sa.Numeric(14, 4), nullable=False),
        sa.Column('balance_qty', sa.Numeric(14, 4), nullable=False),
        sa.Column('balance_avg_cost', sa.Numeric(14, 4), nullable=False),
        sa.Column('source_type', sa.String(32), nullable=True),
        sa.Column('source_id', sa.Integer(), nullable=True),
        sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id'), nullable=True),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_inventory_movements_source', 'inventory_movements', ['source_type', 'source_id'])

    # -- Saved Reports --
    op.create_table(
        'saved_reports',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('report_type', sa.String(50), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('ix_saved_reports_type', 'saved_reports', ['report_type'])


def downgrade() -> None:
    op.drop_index('ix_saved_reports_type', table_name='saved_reports')
    op.drop_table('saved_reports')
    op.drop_index('ix_inventory_movements_source', table_name='inventory_movements')
    op.drop_table('inventory_movements')
    op.drop_column('items', 'asset_account_id')
    op.drop_column('items', 'avg_cost')
    op.drop_column('items', 'reorder_point')
    op.drop_column('items', 'quantity_on_hand')
    op.drop_column('items', 'track_inventory')
