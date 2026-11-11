"""construction estimates priced by a markup on each line (#774):
estimate_lines.markup_pct, and the estimate_style setting for a company that
already bids the commercial way

A construction estimate was a commercial bid: lines at cost and four markups
under the subtotal. A small contractor prices each line as cost plus a
markup, so a line now carries its own markup percent. NULL is "no markup of
its own", which is every line that exists today: it stays priced at cost.

The estimate_style setting defaults to "line_markup". A construction company
that already has estimates has been using the commercial bid screen, so it
is written "commercial_bid" here and nothing changes under it.

Revision ID: a4c8e2f6b1d3
Revises: f3b9d2e6a1c7
Create Date: 2026-11-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4c8e2f6b1d3"
down_revision: Union[str, None] = "f3b9d2e6a1c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _keep_commercial_bid(bind, tables) -> None:
    """Write estimate_style = commercial_bid for a construction company that
    has an estimate and no style saved."""
    if "settings" not in tables or "estimates" not in tables:
        return
    company_type = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = 'company_type'")
    ).scalar()
    if company_type != "construction":
        return
    saved = bind.execute(
        sa.text("SELECT 1 FROM settings WHERE key = 'estimate_style'")
    ).first()
    if saved is not None:
        return
    if bind.execute(sa.text("SELECT 1 FROM estimates LIMIT 1")).first() is None:
        return
    bind.execute(
        sa.text(
            "INSERT INTO settings (key, value) VALUES ('estimate_style', 'commercial_bid')"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()
    if "estimate_lines" in tables:
        present = {c["name"] for c in insp.get_columns("estimate_lines")}
        if "markup_pct" not in present:
            op.add_column(
                "estimate_lines",
                sa.Column("markup_pct", sa.Numeric(9, 4), nullable=True),
            )
    _keep_commercial_bid(bind, tables)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()
    if "settings" in tables:
        bind.execute(sa.text("DELETE FROM settings WHERE key = 'estimate_style'"))
    if "estimate_lines" in tables:
        present = {c["name"] for c in insp.get_columns("estimate_lines")}
        if "markup_pct" in present:
            with op.batch_alter_table("estimate_lines") as batch:
                batch.drop_column("markup_pct")
