"""the default fixed asset type for books from before 2.9.0 (#662)

A company made since 2.9.0 gets one asset type at setup: Equipment, mapped
to the Equipment and Accumulated Depreciation accounts and to 6810
Depreciation Expense, straight-line over five years. Books made earlier had
none, and GET /api/fixed-assets/types made it the first time the page asked
for the list: a read that wrote. The read only reads now, so books with no
asset type get the same default here, when the chart has the Equipment and
Accumulated Depreciation accounts to map it to (a chart without them picks
its own on the form, as before). A chart with no account named Depreciation
Expense gets one, 6810 unless the number is taken, as the setup adds it.
downgrade leaves the rows: assets may already use the type.

Revision ID: 407d52f22d78
Revises: d7b3f9a1c5e2
Create Date: 2026-10-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "407d52f22d78"
down_revision: Union[str, None] = "d7b3f9a1c5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# app/seed/fixed_assets.py, frozen here
TYPE_NAME = "Equipment"
TYPE_DESCRIPTION = "General equipment, straight-line over five years"
EXPENSE_NAME = "Depreciation Expense"
EXPENSE_NUMBER = "6810"


def _first(bind, sql, **params):
    row = bind.execute(sa.text(sql), params).first()
    return row[0] if row else None


def _asset_account(bind, name):
    return _first(
        bind,
        "SELECT id FROM accounts WHERE name = :name AND account_type = 'ASSET' "
        "ORDER BY id",
        name=name,
    )


def _expense_account(bind):
    """The account named Depreciation Expense, made when there is none, as
    accounting.ensure_account makes it (the detail type its insert hook
    gives an expense account)."""
    found = _first(
        bind, "SELECT id FROM accounts WHERE name = :name ORDER BY id", name=EXPENSE_NAME
    )
    if found is not None:
        return found
    taken = _first(
        bind,
        "SELECT id FROM accounts WHERE account_number = :number",
        number=EXPENSE_NUMBER,
    )
    bind.execute(
        sa.text(
            "INSERT INTO accounts (name, account_number, account_type, is_active, "
            "is_system, balance, detail_type) VALUES (:name, :number, 'EXPENSE', "
            ":yes, :yes, 0, 'expense')"
        ),
        {
            "name": EXPENSE_NAME,
            "number": None if taken is not None else EXPENSE_NUMBER,
            "yes": True,
        },
    )
    return _first(
        bind,
        "SELECT id FROM accounts WHERE name = :name ORDER BY id",
        name=EXPENSE_NAME,
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not {"accounts", "fixed_asset_types"} <= set(
        sa.inspect(bind).get_table_names()
    ):
        return
    if _first(bind, "SELECT id FROM fixed_asset_types") is not None:
        return
    asset = _asset_account(bind, "Equipment")
    accumulated = _asset_account(bind, "Accumulated Depreciation")
    if asset is None or accumulated is None:
        return
    bind.execute(
        sa.text(
            "INSERT INTO fixed_asset_types (name, description, asset_account_id, "
            "accumulated_depreciation_account_id, depreciation_expense_account_id, "
            "depreciation_method, effective_life_years, is_active) VALUES (:name, "
            ":description, :asset, :accumulated, :expense, 'STRAIGHT_LINE', 5, :yes)"
        ),
        {
            "name": TYPE_NAME,
            "description": TYPE_DESCRIPTION,
            "asset": asset,
            "accumulated": accumulated,
            "expense": _expense_account(bind),
            "yes": True,
        },
    )


def downgrade() -> None:
    # The type and the account stay: an earlier build reads them as the ones
    # its first read would have made, and assets may already use the type.
    pass
