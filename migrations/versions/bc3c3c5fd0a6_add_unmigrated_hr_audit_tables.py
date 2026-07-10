"""add unmigrated hr/audit tables

Revision ID: bc3c3c5fd0a6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-05 09:41:27.017405

document_audits, login_attempts, reseller_permits, and portal_accesses were
introduced model-only — no migration ever created them. Startup create_all()
masked the gap (it creates missing tables, unlike missing columns), so
running deployments may already have them; each create is guarded with an
existence check so the migration works on both fresh and masked databases.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "bc3c3c5fd0a6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "document_audits" not in existing:
        op.create_table(
            "document_audits",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("doc_type", sa.String(length=20), nullable=False),
            sa.Column("doc_key", sa.String(length=80), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_document_audits_content_hash"),
            "document_audits",
            ["content_hash"],
        )
        op.create_index(
            op.f("ix_document_audits_created_at"), "document_audits", ["created_at"]
        )
        op.create_index(
            op.f("ix_document_audits_doc_key"), "document_audits", ["doc_key"]
        )
        op.create_index(
            op.f("ix_document_audits_doc_type"), "document_audits", ["doc_type"]
        )

    if "login_attempts" not in existing:
        op.create_table(
            "login_attempts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ip", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=255), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_login_attempts_created_at"), "login_attempts", ["created_at"]
        )
        op.create_index(
            op.f("ix_login_attempts_success"), "login_attempts", ["success"]
        )

    if "reseller_permits" not in existing:
        op.create_table(
            "reseller_permits",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("entity_type", sa.String(length=20), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("jurisdiction", sa.String(length=20), nullable=False),
            sa.Column("permit_number", sa.String(length=50), nullable=False),
            sa.Column("issued_at", sa.Date(), nullable=True),
            sa.Column("expires_at", sa.Date(), nullable=True),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verified_by", sa.String(length=100), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_reseller_permits_entity_id"), "reseller_permits", ["entity_id"]
        )
        op.create_index(
            op.f("ix_reseller_permits_entity_type"),
            "reseller_permits",
            ["entity_type"],
        )
        op.create_index(
            op.f("ix_reseller_permits_expires_at"), "reseller_permits", ["expires_at"]
        )
        op.create_index(
            op.f("ix_reseller_permits_is_active"), "reseller_permits", ["is_active"]
        )
        op.create_index(
            op.f("ix_reseller_permits_jurisdiction"),
            "reseller_permits",
            ["jurisdiction"],
        )
        op.create_index(
            op.f("ix_reseller_permits_permit_number"),
            "reseller_permits",
            ["permit_number"],
        )

    if "portal_accesses" not in existing:
        op.create_table(
            "portal_accesses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ip", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=255), nullable=True),
            sa.Column("path", sa.String(length=200), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=True),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_portal_accesses_created_at"), "portal_accesses", ["created_at"]
        )
        op.create_index(
            op.f("ix_portal_accesses_employee_id"), "portal_accesses", ["employee_id"]
        )
        op.create_index(
            op.f("ix_portal_accesses_success"), "portal_accesses", ["success"]
        )


def downgrade() -> None:
    op.drop_table("portal_accesses")
    op.drop_table("reseller_permits")
    op.drop_table("login_attempts")
    op.drop_table("document_audits")
