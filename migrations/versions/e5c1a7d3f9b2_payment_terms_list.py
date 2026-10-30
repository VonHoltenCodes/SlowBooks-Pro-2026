"""payment terms: a terms list with rules, and early-payment discounts (#664)

Terms were free text with one rule: "Net N", else 30 days. The list gives
each term a rule: standard (due in N days) or date-driven (due on a day of
the month), either with an optional early-payment discount. Documents keep
the term's name in their own terms column, so nothing on them changes.

The list starts with the terms the forms offered (Due on Receipt, Net 15,
30, 45 and 60), each with the rule the old parser gave it, plus a "Net N"
entry for any other Net N a customer, vendor, document or the default
terms already use. Text that is not Net N is left as it is: it keeps the
old 30 days, and the document now says so.

An invoice and a bill name the list entry their terms named when saved
(payment_term_id), and its rule gives their discount. A document whose text
names one of the entries made here is tied to it: the entry's rule is the
one the old parser gave that text, so nothing about it changes. Any other
document keeps NULL, and a term added to the list later never reaches it.

A discount taken in Receive Payments is a credit memo, and one taken in
Pay Bills a vendor credit, tied to the payment that took it
(discount_payment_id, discount_bill_payment_id) so voiding the payment
voids its discount. downgrade drops the table and the four columns.

Revision ID: e5c1a7d3f9b2
Revises: d7b3f9a1c5e2
Create Date: 2026-10-30
"""

import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5c1a7d3f9b2"
down_revision: Union[str, None] = "d7b3f9a1c5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEED = (
    ("Due on Receipt", 0),
    ("Net 15", 15),
    ("Net 30", 30),
    ("Net 45", 45),
    ("Net 60", 60),
)
# Where terms text lives today.
TERMS_COLUMNS = (
    ("customers", "terms"),
    ("vendors", "terms"),
    ("invoices", "terms"),
    ("bills", "terms"),
    ("recurring_invoices", "terms"),
    ("memorized_transactions", "terms"),
)
NET_N = re.compile(r"^net\s+(\d{1,4})$")

LINKS = (
    ("credit_memos", "discount_payment_id", "payments"),
    ("vendor_credits", "discount_bill_payment_id", "bill_payments"),
    ("invoices", "payment_term_id", "payment_terms"),
    ("bills", "payment_term_id", "payment_terms"),
)
# The documents tied to the entries made here, by their exact text.
TIED = ("invoices", "bills")


def _key(text):
    """Terms as the parser matches them: single-spaced (any whitespace),
    lower case (app/services/payment_terms.py _key)."""
    return " ".join((text or "").split()).lower()


def _fk(table, column):
    return f"fk_{table}_{column}"


def _texts(bind, insp):
    """Every distinct terms text in use, with the default terms setting."""
    tables = set(insp.get_table_names())
    found = set()
    for table, column in TERMS_COLUMNS:
        if table not in tables:
            continue
        if column not in {c["name"] for c in insp.get_columns(table)}:
            continue
        rows = bind.execute(
            sa.text(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL")
        )
        found.update(r[0] for r in rows)
    if "settings" in tables:
        rows = bind.execute(
            sa.text("SELECT value FROM settings WHERE key = 'default_terms'")
        )
        found.update(r[0] for r in rows if r[0])
    return found


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "payment_terms" not in tables:
        terms = op.create_table(
            "payment_terms",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=50), nullable=False, unique=True),
            sa.Column("kind", sa.String(length=12), nullable=False),
            sa.Column("net_days", sa.Integer(), nullable=True),
            sa.Column("due_day", sa.Integer(), nullable=True),
            sa.Column(
                "cutoff_days", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True),
            sa.Column("discount_days", sa.Integer(), nullable=True),
            sa.Column("discount_day", sa.Integer(), nullable=True),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
        )
        op.create_index("ix_payment_terms_id", "payment_terms", ["id"])

        rows = {name.lower(): (name, days) for name, days in SEED}
        for text in _texts(bind, insp):
            match = NET_N.match(_key(text))
            if match and int(match.group(1)) <= 3650:
                days = int(match.group(1))
                rows.setdefault(f"net {days}", (f"Net {days}", days))
        op.bulk_insert(
            terms,
            [
                {"name": name, "kind": "standard", "net_days": days, "cutoff_days": 0}
                for name, days in sorted(rows.values(), key=lambda r: r[1])
            ],
        )

    for table, column, target in LINKS:
        if table not in tables:
            continue
        if column in {c["name"] for c in insp.get_columns(table)}:
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch.create_foreign_key(_fk(table, column), target, [column], ["id"])

    # Tie each document to the entry its text names, matched in Python so
    # tabs and doubled spaces match as the parser matches them.
    ids = {
        _key(name): term_id
        for term_id, name in bind.execute(sa.text("SELECT id, name FROM payment_terms"))
    }
    for table in TIED:
        if table not in tables:
            continue
        for (text,) in bind.execute(
            sa.text(f"SELECT DISTINCT terms FROM {table} WHERE terms IS NOT NULL")
        ).all():
            term_id = ids.get(_key(text))
            if term_id is not None:
                bind.execute(
                    sa.text(
                        f"UPDATE {table} SET payment_term_id = :id WHERE terms = :text"
                    ),
                    {"id": term_id, "text": text},
                )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    for table, column, _target in LINKS:
        if table not in tables:
            continue
        if column not in {c["name"] for c in insp.get_columns(table)}:
            continue
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(_fk(table, column), type_="foreignkey")
            batch.drop_column(column)
    if "payment_terms" in tables:
        op.drop_index("ix_payment_terms_id", table_name="payment_terms")
        op.drop_table("payment_terms")
