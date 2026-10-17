"""time entries from QuickBooks: their source, billing status, service item
and the customer or job label they came with

QuickBooks Desktop's timesheets come across from its Time by Name by Job
(or by Job by Name) detail report (#323). A time entry here had an
employee, a date, hours, a job and notes; the report also carries the
service item ("Software Development", "Indirect - Admin:Email/Phone"),
the billing status (Not Billable, Unbilled, Billed) and the customer or
job label, which may not be a job on the Jobs list. Five nullable columns
on time_entries: source (the importer's name, so a pay run never sweeps
imported history and a second upload posts nothing twice), source_ref
(the row's fingerprint), billing, service and customer_job. Existing rows
keep NULLs and behave as before.

Revision ID: b1c2d3e4f5a6
Revises: e8f9a0b1c2d3
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    sa.Column("source", sa.String(length=40), nullable=True),
    sa.Column("source_ref", sa.String(length=64), nullable=True),
    sa.Column("billing", sa.String(length=20), nullable=True),
    sa.Column("service", sa.String(length=200), nullable=True),
    sa.Column("customer_job", sa.String(length=300), nullable=True),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "time_entries" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("time_entries")}
    for column in COLUMNS:
        if column.name not in present:
            op.add_column("time_entries", column.copy())
    indexes = {i["name"] for i in insp.get_indexes("time_entries")}
    if "ix_time_entries_source_ref" not in indexes:
        op.create_index(
            "ix_time_entries_source_ref", "time_entries", ["source_ref"], unique=False
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "time_entries" not in insp.get_table_names():
        return
    indexes = {i["name"] for i in insp.get_indexes("time_entries")}
    with op.batch_alter_table("time_entries") as batch:
        if "ix_time_entries_source_ref" in indexes:
            batch.drop_index("ix_time_entries_source_ref")
        for column in COLUMNS:
            batch.drop_column(column.name)
