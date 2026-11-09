"""bank_check_details: a bank account's details for blank check stock (#739)

The bank's name and city, the fractional routing number and the routing and
account numbers printed in the MICR line when a check prints on blank stock,
one row per bank account. The account number is stored encrypted, with its
last four in the clear. Books that already have the table (made by
create_all and stamped back) keep it as it is.

Revision ID: b4d2e7a9c6f1
Revises: c8e1a4d7f305
Create Date: 2026-11-09

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b4d2e7a9c6f1"
down_revision: Union[str, None] = "c8e1a4d7f305"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "bank_check_details" in insp.get_table_names():
        return
    op.create_table(
        "bank_check_details",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("bank_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("bank_city_state", sa.String(100), nullable=False, server_default=""),
        sa.Column(
            "fractional_routing", sa.String(30), nullable=False, server_default=""
        ),
        sa.Column("routing_number", sa.String(9), nullable=False, server_default=""),
        sa.Column("account_number_enc", sa.String(255), nullable=True),
        sa.Column("account_last_four", sa.String(4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_bank_check_details_id", "bank_check_details", ["id"])
    op.create_index(
        "ix_bank_check_details_account_id",
        "bank_check_details",
        ["account_id"],
        unique=True,
    )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "bank_check_details" not in insp.get_table_names():
        return
    have = {i["name"] for i in insp.get_indexes("bank_check_details")}
    for index in ("ix_bank_check_details_account_id", "ix_bank_check_details_id"):
        if index in have:
            op.drop_index(index, table_name="bank_check_details")
    op.drop_table("bank_check_details")
