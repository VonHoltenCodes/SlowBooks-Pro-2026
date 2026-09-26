"""deposits remember the payments they took (explore 2.17.3, W-H4)

Make Deposits posted one journal entry — DR bank, CR Undeposited Funds —
and dropped the list of payments the user had ticked, so nothing linked a
payment to the deposit that took it. A payment that was already in a
deposit (even one reconciled against a bank statement) could then be
voided: its reversal credited Undeposited Funds a second time and drove
the account negative, while the reconciled deposit still claimed the
money. The Make Deposits list, rebuilt by netting every credit on the
account against every debit, also showed the wrong payments as waiting
once a deposit or a void had happened out of date order.

- transaction_lines.deposit_transaction_id: on an Undeposited Funds debit
  (the money-in line of a payment or sales receipt), the journal entry of
  the deposit that took it. Set when a deposit is made from the list,
  cleared when that deposit is voided. NULL = still waiting to be
  deposited, or deposited before this revision.

Nothing is backfilled. A deposit made before this revision named no
payments, and guessing which ones it took would be inventing history; the
Make Deposits list keeps netting those old deposits the way it always did.

Revision ID: 697f63b2975e
Revises: a9b0c1d2e3f4
Create Date: 2026-09-26

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "697f63b2975e"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("transaction_lines") as batch:
        batch.add_column(
            sa.Column("deposit_transaction_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_transaction_lines_deposit_transaction_id",
            "transactions",
            ["deposit_transaction_id"],
            ["id"],
        )
        batch.create_index(
            "ix_transaction_lines_deposit_transaction_id",
            ["deposit_transaction_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("transaction_lines") as batch:
        batch.drop_index("ix_transaction_lines_deposit_transaction_id")
        batch.drop_constraint(
            "fk_transaction_lines_deposit_transaction_id", type_="foreignkey"
        )
        batch.drop_column("deposit_transaction_id")
