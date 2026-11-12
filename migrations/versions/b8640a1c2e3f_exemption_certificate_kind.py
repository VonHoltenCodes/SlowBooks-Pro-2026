"""reseller_permits.kind and customers.exemption_certificate_id (#864)

The reseller-permit record holds every exemption certificate now: a kind of
resale (what every row was), public entity, nonprofit or other. A customer
names the certificate its exemption stands on. A non-taxable customer with
exactly one active certificate on file names that one at the upgrade; the
rest name none until someone picks.

Revision ID: b8640a1c2e3f
Revises: a4c8e2f6b0d3
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8640a1c2e3f"
down_revision: Union[str, None] = "a4c8e2f6b0d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()
    if "reseller_permits" in tables and "kind" not in _cols(insp, "reseller_permits"):
        op.add_column(
            "reseller_permits",
            sa.Column("kind", sa.String(20), nullable=False, server_default="resale"),
        )
    if "customers" in tables and "exemption_certificate_id" not in _cols(
        insp, "customers"
    ):
        op.add_column(
            "customers",
            sa.Column("exemption_certificate_id", sa.Integer(), nullable=True),
        )
    if "customers" not in tables or "reseller_permits" not in tables:
        return
    rows = bind.execute(
        sa.text(
            "SELECT c.id, MIN(p.id), COUNT(p.id) FROM customers c "
            "JOIN reseller_permits p ON p.entity_type = 'customer' "
            "AND p.entity_id = c.id AND p.is_active = :yes "
            "WHERE c.is_taxable = :no AND c.exemption_certificate_id IS NULL "
            "GROUP BY c.id"
        ),
        {"yes": True, "no": False},
    ).fetchall()
    for cust_id, permit_id, count in rows:
        if count == 1:
            bind.execute(
                sa.text(
                    "UPDATE customers SET exemption_certificate_id = :p "
                    "WHERE id = :c"
                ),
                {"p": permit_id, "c": cust_id},
            )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()
    if "customers" in tables and "exemption_certificate_id" in _cols(insp, "customers"):
        with op.batch_alter_table("customers") as batch:
            batch.drop_column("exemption_certificate_id")
    if "reseller_permits" in tables and "kind" in _cols(insp, "reseller_permits"):
        with op.batch_alter_table("reseller_permits") as batch:
            batch.drop_column("kind")
