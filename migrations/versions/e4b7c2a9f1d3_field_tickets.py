"""field tickets: T&M tickets, material slips, delivery tickets

Construction mode's record of the paper that comes back from the job site.
Additive: two new tables, nothing else changes, so a company that never
writes a ticket sees none of it.

- field_tickets — the ticket: job, kind (tm / material / delivery), its
  number per job and kind, date, status, default cost code, who submitted and
  who signed (typed name and date), the commitment a sub's T&M ticket is
  priced against, the purchase order a delivery ticket receives on and the
  supplier's own ticket number, and what it turned into (change order, sub
  change order, invoice, job cost entry).
- field_ticket_lines — labor / equipment / material / other lines with a
  quantity, cost rate and (T&M) bill rate, and the PO line a delivery line
  received against.

Each table is created only when it is missing, so books built by
create_all (or stamped back over the tables) upgrade as a no-op. downgrade
drops both.

Revision ID: e4b7c2a9f1d3
Revises: a4d8e2f6b9c3
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e4b7c2a9f1d3"
down_revision: Union[str, None] = "a4d8e2f6b9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()
    if "field_tickets" not in existing:
        _create_field_tickets()
    if "field_ticket_lines" not in existing:
        _create_field_ticket_lines()


def _create_field_tickets() -> None:
    op.create_table(
        "field_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("number", sa.String(length=80), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.String(length=10), nullable=False, server_default="draft"
        ),
        sa.Column("cost_code_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("submitted_by", sa.String(length=120), nullable=True),
        sa.Column("signed_by_name", sa.String(length=120), nullable=True),
        sa.Column("signed_at", sa.Date(), nullable=True),
        sa.Column("commitment_id", sa.Integer(), nullable=True),
        sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        sa.Column("vendor_ticket_ref", sa.String(length=60), nullable=True),
        sa.Column("change_order_id", sa.Integer(), nullable=True),
        sa.Column("sub_change_order_id", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("job_cost_id", sa.Integer(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["cost_code_id"], ["cost_codes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"], ["purchase_orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"], ["purchase_orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["change_order_id"], ["change_orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["sub_change_order_id"], ["change_orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_cost_id"], ["job_costs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number", name="uq_field_ticket_number"),
    )
    op.create_index("ix_field_tickets_id", "field_tickets", ["id"], unique=False)
    op.create_index(
        "ix_field_tickets_job_id", "field_tickets", ["job_id"], unique=False
    )


def _create_field_ticket_lines() -> None:
    op.create_table(
        "field_ticket_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("field_ticket_id", sa.Integer(), nullable=False),
        sa.Column("line_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "line_type", sa.String(length=10), nullable=False, server_default="other"
        ),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("equipment_id", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("po_line_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("cost_rate", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("bill_rate", sa.Numeric(15, 4), nullable=True),
        sa.Column("cost_code_id", sa.Integer(), nullable=True),
        sa.Column("cost_type", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(
            ["field_ticket_id"], ["field_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["equipment_id"], ["equipment.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["po_line_id"], ["purchase_order_lines.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["cost_code_id"], ["cost_codes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_field_ticket_lines_id", "field_ticket_lines", ["id"], unique=False
    )
    op.create_index(
        "ix_field_ticket_lines_field_ticket_id",
        "field_ticket_lines",
        ["field_ticket_id"],
        unique=False,
    )


def downgrade() -> None:
    # Dropping a table drops its indexes with it.
    existing = _tables()
    for table in ("field_ticket_lines", "field_tickets"):
        if table in existing:
            op.drop_table(table)
