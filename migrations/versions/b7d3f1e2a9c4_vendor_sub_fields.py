"""vendor subcontractor type and insurance fields (issue #431)

Construction mode tracks a subcontractor the way QuickBooks Contractor
Edition does: a vendor_type that marks the vendor a "sub", its trade
license, and the certificate of insurance (carrier, policy number, expiry)
a general contractor collects before letting a sub on the job. All
additive, all nullable — an ordinary vendor leaves every one blank, and a
business or nonprofit company never sees the fields. vendor_type is the
only gate; "sub" is its one value for now (the expiry warning in Pay Bills
is a later issue).

Data-only note: nothing is backfilled. No earlier representation of a
sub's license or COI exists to migrate from.

Revision ID: b7d3f1e2a9c4
Revises: 4ac01efbd72a
Create Date: 2026-10-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7d3f1e2a9c4"
down_revision: Union[str, None] = "4ac01efbd72a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vendors") as batch:
        batch.add_column(sa.Column("vendor_type", sa.String(length=20), nullable=True))
        batch.add_column(
            sa.Column("license_number", sa.String(length=100), nullable=True)
        )
        batch.add_column(sa.Column("coi_carrier", sa.String(length=200), nullable=True))
        batch.add_column(
            sa.Column("coi_policy_number", sa.String(length=100), nullable=True)
        )
        batch.add_column(sa.Column("coi_expires", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vendors") as batch:
        batch.drop_column("coi_expires")
        batch.drop_column("coi_policy_number")
        batch.drop_column("coi_carrier")
        batch.drop_column("license_number")
        batch.drop_column("vendor_type")
