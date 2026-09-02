"""Merchant OCR templates: learned field positions per merchant (v3)

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ocr_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("merchant_key", sa.String(120), nullable=False),
        sa.Column("merchant_name", sa.String(200), nullable=True),
        sa.Column("fields_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_ocr_templates_id", "ocr_templates", ["id"])
    op.create_index(
        "ix_ocr_templates_merchant_key", "ocr_templates", ["merchant_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_ocr_templates_merchant_key", table_name="ocr_templates")
    op.drop_index("ix_ocr_templates_id", table_name="ocr_templates")
    op.drop_table("ocr_templates")
