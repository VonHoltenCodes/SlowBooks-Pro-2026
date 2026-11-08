"""The use-tax rate a bill line accrued at

- bill_lines.use_tax_rate: the rate (a fraction, like a document's
  tax_rate) a line's use tax was booked at, set when it first accrues. An
  edit of the bill re-posts at it instead of today's Use tax rate setting
  (skeptic round 2, L bug 3 / M bug 4: an 8/20 bill booked at 6.5% read 7%
  after a description-only edit).
  Backfill: each bill's 2220 Use Tax Payable credits are matched, in order,
  to the bill lines their memo names ("Use tax - <line description>"). A
  matched line takes the rate its memo ends with ("(6.5%)"), else the
  credit over the line's amount (home currency only), else the current
  setting. A line no credit names stays empty and, as before, accrues at
  today's setting the next time the bill is saved.

downgrade drops the column.

Revision ID: c7e4a9b2d1f5
Revises: b5d2e8f4a6c3
Create Date: 2026-11-08
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7e4a9b2d1f5"
down_revision: Union[str, None] = "b5d2e8f4a6c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RATE = sa.Numeric(7, 6)
_PLACES = Decimal("0.000001")
_TAG = re.compile(r" \(([\d.]+)%\)$")


def _columns(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except InvalidOperation:
        return Decimal("0")


def _setting_rate(conn) -> Decimal:
    """purchase_posting.use_tax_rate: the Use tax rate setting, else the
    default tax rate, as a fraction."""
    values = dict(
        conn.execute(
            sa.text(
                "SELECT key, value FROM settings "
                "WHERE key IN ('use_tax_rate', 'default_tax_rate')"
            )
        ).fetchall()
    )
    pct = (values.get("use_tax_rate") or "").strip() or (
        values.get("default_tax_rate") or "0"
    )
    return (_d(pct) / Decimal("100")).quantize(_PLACES)


def _backfill(conn) -> None:
    payable = conn.execute(
        sa.text("SELECT id FROM accounts WHERE name = 'Use Tax Payable'")
    ).scalar()
    if payable is None:
        return
    current = None
    bills = conn.execute(
        sa.text(
            "SELECT id, transaction_id, exchange_rate FROM bills "
            "WHERE transaction_id IS NOT NULL"
        )
    ).fetchall()
    for bill_id, txn_id, fx in bills:
        credits = conn.execute(
            sa.text(
                "SELECT description, credit FROM transaction_lines "
                "WHERE transaction_id = :t AND account_id = :a AND credit > 0 "
                "ORDER BY id"
            ),
            {"t": txn_id, "a": payable},
        ).fetchall()
        if not credits:
            continue
        lines = conn.execute(
            sa.text(
                "SELECT id, description, amount FROM bill_lines "
                "WHERE bill_id = :b ORDER BY line_order, id"
            ),
            {"b": bill_id},
        ).fetchall()
        home = fx is None or _d(fx) == 1
        at = 0
        for memo, credit in credits:
            memo = memo or ""
            tag = _TAG.search(memo)
            stem = _TAG.sub("", memo)
            for j in range(at, len(lines)):
                line_id, desc, amount = lines[j]
                name = f"Use tax - {desc or 'materials'}"
                # the memo cuts a long description to fit 200 characters
                cut = len(memo) >= 190 and stem and name.startswith(stem)
                if name != stem and not cut:
                    continue
                if tag is not None:
                    rate = _d(tag.group(1)) / Decimal("100")
                elif home and _d(amount) > 0:
                    rate = _d(credit) / _d(amount)
                else:
                    if current is None:
                        current = _setting_rate(conn)
                    rate = current
                conn.execute(
                    sa.text(
                        "UPDATE bill_lines SET use_tax_rate = :r WHERE id = :id"
                    ).bindparams(sa.bindparam("r", type_=_RATE)),
                    {"r": rate.quantize(_PLACES), "id": line_id},
                )
                at = j + 1
                break


def upgrade() -> None:
    # Books built by create_all already have the column: added only when
    # missing, so a re-run is a no-op (the backfill fills only empty rows'
    # matches again, to the same figures).
    lines = _columns("bill_lines")
    if not lines:
        return
    if "use_tax_rate" not in lines:
        with op.batch_alter_table("bill_lines") as batch:
            batch.add_column(sa.Column("use_tax_rate", _RATE, nullable=True))
    if "transaction_lines" in sa.inspect(op.get_bind()).get_table_names():
        _backfill(op.get_bind())


def downgrade() -> None:
    if "use_tax_rate" in _columns("bill_lines"):
        with op.batch_alter_table("bill_lines") as batch:
            batch.drop_column("use_tax_rate")
