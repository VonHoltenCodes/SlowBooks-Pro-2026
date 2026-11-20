"""bank_rules.pattern: a star a rule had before the wildcard stays a star (NEW-259)

Until #1064 a bank rule's pattern was plain text: a star in it was the star
the bank prints ("SQ *JOES DELI", "AMZN Mktp US*2X3"). #1064 made `*` stand
for any run of characters, so a rule made before it, Contains `SQ *`, also
filed "MSQ LLC PARTS". A star is now written `\\*` when it is the bank's
own, and this step writes every existing rule's stars that way (a
backslash right before a star or another backslash is doubled, so it too
stays itself): every rule files exactly the lines it filed before.

Data only. A database built by create_all is stamped at the head and never
runs it; a new one has no rules. Downgrade writes the patterns back as the
plain text the older build reads, a wildcard as a star.

Revision ID: c1064a5e7b2d
Revises: a1075c4d8e2f
Create Date: 2026-11-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1064a5e7b2d"
down_revision: Union[str, None] = "a1075c4d8e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "bank_rules"


def _escape(text: str) -> str:
    """Plain text as a pattern that matches it as written (the engine's
    escape_pattern, kept here as it was when this step was written)."""
    out = []
    for i, c in enumerate(text):
        if c == "*":
            out.append("\\*")
        elif c == "\\" and i + 1 < len(text) and text[i + 1] in "*\\":
            out.append("\\\\")
        else:
            out.append(c)
    return "".join(out)


def _plain(pattern: str) -> str:
    """A pattern as the plain text the build before reads: an escaped
    character as itself, a wildcard as a star."""
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern) and pattern[i + 1] in "*\\":
            out.append(pattern[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _rewrite(change) -> None:
    bind = op.get_bind()
    if TABLE not in sa.inspect(bind).get_table_names():
        return
    rows = bind.execute(sa.text(f"SELECT id, pattern FROM {TABLE}")).fetchall()
    for rule_id, pattern in rows:
        if not pattern or ("*" not in pattern and "\\" not in pattern):
            continue
        new = change(pattern)
        if new != pattern:
            bind.execute(
                sa.text(f"UPDATE {TABLE} SET pattern = :p WHERE id = :id"),
                {"p": new, "id": rule_id},
            )


def upgrade() -> None:
    _rewrite(_escape)


def downgrade() -> None:
    _rewrite(_plain)
