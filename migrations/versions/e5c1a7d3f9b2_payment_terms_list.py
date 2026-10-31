"""payment terms: a terms list with rules, and early-payment discounts (#664)

Terms were free text with one rule: "Net N", else 30 days. The list gives
each term a rule: standard (due in N days) or date-driven (due on a day of
the month), either with an optional early-payment discount. Documents keep
the term's name in their own terms column, so nothing on them changes.

The list starts with the terms the forms offered (Due on Receipt, Net 15,
30, 45 and 60), each with the rule the old parser gave it, plus a "Net N"
entry for any other number of days the old parser read from text a
customer, vendor, document, template or the default terms already use.

Invoices, bills and recurring and memorized templates name the list entry
their terms named when saved (payment_term_id), and its rule gives their
discount. Here, one is tied to an entry only where the old parser read its
text, exactly as it read it (main_days below), as that entry's number of
days, so nothing about it changes: "net   21" and "Net 030" are tied, but
"net<TAB>45" was 30 days on main and stays untied. Anything untied is read
as the old parser read it, never through the list, so a term added later
never reaches it.

A discount taken in Receive Payments is a credit memo, and one taken in
Pay Bills a vendor credit, tied to the payment that took it
(discount_payment_id, discount_bill_payment_id) so voiding the payment
voids its discount. A discount taken against the terms is recorded on the
payment (discount_override and its note) and on the memo or credit, and
an allocation applied from a payment's remainder later is marked
(from_remainder): no discount goes with it.
downgrade drops the table and every column added here.

Revision ID: e5c1a7d3f9b2
Revises: d7b3f9a1c5e2
Create Date: 2026-10-30
"""

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
# The ones tied to an entry.
TIED = ("invoices", "bills", "recurring_invoices", "memorized_transactions")
RECEIPT_WORDS = ("due on receipt", "due upon receipt", "cod", "net 0")
# The longest a list entry may run (app/services/payment_terms.py MAX_DAYS).
MAX_DAYS = 3650

# (table, column, the table its foreign key names)
LINKS = (
    ("credit_memos", "discount_payment_id", "payments"),
    ("vendor_credits", "discount_bill_payment_id", "bill_payments"),
    ("invoices", "payment_term_id", "payment_terms"),
    ("bills", "payment_term_id", "payment_terms"),
    ("recurring_invoices", "payment_term_id", "payment_terms"),
    ("memorized_transactions", "payment_term_id", "payment_terms"),
)
# (table, column) without a foreign key
FLAGS = (
    ("payments", sa.Column("discount_override", sa.Boolean(), nullable=False,
                           server_default=sa.false())),
    ("payments", sa.Column("discount_override_note", sa.Text(), nullable=True)),
    ("bill_payments", sa.Column("discount_override", sa.Boolean(), nullable=False,
                                server_default=sa.false())),
    ("bill_payments", sa.Column("discount_override_note", sa.Text(), nullable=True)),
    ("credit_memos", sa.Column("discount_override", sa.Boolean(), nullable=False,
                               server_default=sa.false())),
    ("vendor_credits", sa.Column("discount_override", sa.Boolean(), nullable=False,
                                 server_default=sa.false())),
    # an allocation applied later from a payment's remainder takes no
    # early-payment discount
    ("payment_allocations", sa.Column("from_remainder", sa.Boolean(), nullable=False,
                                      server_default=sa.false())),
)


def _fk(table, column):
    return f"fk_{table}_{column}"


def main_days(text):
    """The days main's parser (routes/invoices/helpers.py
    _due_date_from_terms) gave the text, exactly as it read it, or None
    where it fell back to 30."""
    if not text:
        return None
    t = text.strip().lower()
    if t in RECEIPT_WORDS:
        return 0
    try:
        return int(t.replace("net ", "").strip())
    except ValueError:
        return None


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


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


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

        # one entry per number of days: the seed, then what the old parser
        # read from text in use (0 is Due on Receipt)
        rows = {days: name for name, days in SEED}
        for text in _texts(bind, insp):
            days = main_days(text)
            if days is not None and 0 < days <= MAX_DAYS:
                rows.setdefault(days, f"Net {days}")
        op.bulk_insert(
            terms,
            [
                {"name": name, "kind": "standard", "net_days": days, "cutoff_days": 0}
                for days, name in sorted(rows.items())
            ],
        )

    for table, column, target in LINKS:
        if table not in tables or column in _columns(insp, table):
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch.create_foreign_key(_fk(table, column), target, [column], ["id"])
    for table, column in FLAGS:
        if table in tables and column.name not in _columns(insp, table):
            op.add_column(table, column.copy())

    # Tie each document and template to the entry for the days the old
    # parser read from its exact text. Only the standard, discount-free
    # entries this migration made carry those days, so the rule is the one
    # the text always had.
    by_days = {
        days: term_id
        for term_id, days in bind.execute(
            sa.text(
                "SELECT id, net_days FROM payment_terms WHERE kind = 'standard' "
                "AND discount_percent IS NULL"
            )
        )
    }
    for table in TIED:
        if table not in tables:
            continue
        for (text,) in bind.execute(
            sa.text(f"SELECT DISTINCT terms FROM {table} WHERE terms IS NOT NULL")
        ).all():
            term_id = by_days.get(main_days(text))
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
    for table, column in FLAGS:
        if table in tables and column.name in _columns(insp, table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column.name)
    for table, column, _target in LINKS:
        if table not in tables or column not in _columns(insp, table):
            continue
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(_fk(table, column), type_="foreignkey")
            batch.drop_column(column)
    if "payment_terms" in tables:
        op.drop_index("ix_payment_terms_id", table_name="payment_terms")
        op.drop_table("payment_terms")
