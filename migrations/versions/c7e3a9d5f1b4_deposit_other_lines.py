"""deposit_other_lines: Make Deposits lines for money never invoiced (#792)

A reimbursement, a vendor's refund or a rebate goes on the same slip as the
day's customer checks: each line names the account it goes to, who it came
from, the check number, the method, its memo, class and job, and its
amount. The ledger holds the money (a credit per line on the deposit's own
entry); this table holds the line as the slip prints it. Books that already
have the table (made by create_all and stamped back) keep it as it is.

Revision ID: c7e3a9d5f1b4
Revises: b6e1d4a8c2f5
Create Date: 2026-11-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7e3a9d5f1b4"
down_revision: Union[str, None] = "b6e1d4a8c2f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "deposit_other_lines"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE in insp.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "deposit_transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True
        ),
        sa.Column(
            "vendor_id", sa.Integer(), sa.ForeignKey("vendors.id"), nullable=True
        ),
        sa.Column("received_from", sa.String(200), nullable=True),
        sa.Column("memo", sa.String(300), nullable=True),
        sa.Column("check_number", sa.String(50), nullable=True),
        sa.Column("method", sa.String(30), nullable=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
    )
    op.create_index(f"ix_{TABLE}_id", TABLE, ["id"])
    op.create_index(
        f"ix_{TABLE}_deposit_transaction_id", TABLE, ["deposit_transaction_id"]
    )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = {i["name"] for i in insp.get_indexes(TABLE)}
    for index in (f"ix_{TABLE}_deposit_transaction_id", f"ix_{TABLE}_id"):
        if index in have:
            op.drop_index(index, table_name=TABLE)
    op.drop_table(TABLE)
