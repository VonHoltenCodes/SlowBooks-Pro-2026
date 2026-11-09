"""WIP as of its date: change-order budget rows linked to their change
order, the day a job closed, and one WIP entry per date

- job_budgets.change_order_id: the change order that wrote a source
  "change" row. Each change order now writes rows of its own, one per
  (code, cost type), so a report as of a date takes only the change orders
  in effect by then. The (job, code, type, source) unique key becomes a
  partial index over the rows no change order owns, and a second one keys
  a change order's rows on (change_order_id, code, type).
  Backfill: before this, every change order on a job wrote into one row per
  (code, type). A row is split into one row per change order when the
  approved change orders' own figures (their frozen lines, and an
  allowance-funded one's draw on its allowance's budget cell) add up to it
  to the cent in cost and revenue. A row that does not add up (a budget
  cell edited since, an allowance cell that is no longer the largest row on
  its code) is left unlinked and counts on every date, as before.
- jobs.closed_on: the day the job went to Closed. Not backfilled: a job
  closed before the upgrade keeps a null day, which reads as it always did,
  instead of a guessed day later than the real close.
- wip_entry_locks: one row per date a WIP adjusting entry stands for, so
  two posts of the same date at once cannot both land. Backfilled from the
  entries standing now (the earliest, if a date has more than one).

downgrade reverses (change rows split by the backfill stay split, unlinked).

Revision ID: b5d2e8f4a6c3
Revises: a3d8f1c6b9e4
Create Date: 2026-11-07
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b5d2e8f4a6c3"
down_revision: Union[str, None] = "a3d8f1c6b9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CENT = Decimal("0.01")
_MONEY = sa.Numeric(15, 2)
_WHERE_UNOWNED = sa.text("change_order_id IS NULL")
_WHERE_OWNED = sa.text("change_order_id IS NOT NULL")


def _q(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _insp():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set:
    insp = _insp()
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _index_names(table: str) -> set:
    return {i["name"] for i in _insp().get_indexes(table)}


def _unique_names(table: str) -> set:
    return {u["name"] for u in _insp().get_unique_constraints(table)}


def _day(value):
    """A Date or a stored DateTime (SQLite hands back text) as a date."""
    if value is None:
        return None
    if isinstance(value, date):
        return value if type(value) is date else value.date()
    return date.fromisoformat(str(value)[:10])


# ── backfills ────────────────────────────────────────────────────────────


def _co_contributions(conn, job_id: int) -> dict:
    """What each approved change order on the job wrote into the change
    rows, as the release that wrote them did, from the frozen figures:
    {(code, type): {co_id: [cost, revenue]}}. An allowance-funded change
    order's draw on its allowance's cell is not in them: that release never
    wrote it, so the shared row holds the lines alone."""
    out: dict = defaultdict(dict)
    cos = conn.execute(
        sa.text(
            "SELECT id, kind, funding, owner_change_order_id FROM change_orders "
            "WHERE job_id = :j AND status = 'approved'"
        ),
        {"j": job_id},
    ).fetchall()
    for co_id, kind, funding, owner_id in cos:
        if kind == "pco" or (kind == "sub" and owner_id is not None):
            continue  # a PCO and a sub CO under an owner CO write no budget
        with_revenue = kind == "owner" and funding == "contract"
        for code_id, ctype, amount, sell in conn.execute(
            sa.text(
                "SELECT cost_code_id, cost_type, amount, sell_amount "
                "FROM change_order_lines WHERE change_order_id = :c"
            ),
            {"c": co_id},
        ):
            cell = out[(code_id, ctype)].setdefault(co_id, [Decimal("0")] * 2)
            cell[0] += _q(amount)
            cell[1] += _q(sell) if with_revenue else Decimal("0")
    return out


def _link_change_budgets(conn) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT id, job_id, cost_code_id, cost_type, amount, revenue_amount, "
            "estimate_id, notes FROM job_budgets "
            "WHERE source = 'change' AND change_order_id IS NULL"
        )
    ).fetchall()
    by_job = defaultdict(list)
    for r in rows:
        by_job[r[1]].append(r)
    for job_id, job_rows in by_job.items():
        contributions = _co_contributions(conn, job_id)
        for row_id, _j, code_id, ctype, amount, revenue, est_id, notes in job_rows:
            parts = {
                co: figs
                for co, figs in contributions.get((code_id, ctype), {}).items()
                if figs[0] != 0 or figs[1] != 0
            }
            if not parts:
                continue
            cost = sum((f[0] for f in parts.values()), Decimal("0"))
            rev = sum((f[1] for f in parts.values()), Decimal("0"))
            if cost != _q(amount) or rev != _q(revenue):
                continue  # does not add up: left unlinked, counts on every date
            first = True
            for co_id, (c, r) in sorted(parts.items()):
                if first:
                    conn.execute(
                        sa.text(
                            "UPDATE job_budgets SET change_order_id = :co, "
                            "amount = :a, revenue_amount = :r WHERE id = :id"
                        ).bindparams(
                            sa.bindparam("a", type_=_MONEY),
                            sa.bindparam("r", type_=_MONEY),
                        ),
                        {"co": co_id, "a": c, "r": r, "id": row_id},
                    )
                    first = False
                    continue
                conn.execute(
                    sa.text(
                        "INSERT INTO job_budgets (job_id, cost_code_id, cost_type, "
                        "amount, revenue_amount, source, estimate_id, locked, "
                        "change_order_id, notes) VALUES (:j, :k, :t, :a, :r, "
                        "'change', :e, :locked, :co, :n)"
                    ).bindparams(
                        sa.bindparam("a", type_=_MONEY),
                        sa.bindparam("r", type_=_MONEY),
                        sa.bindparam("locked", type_=sa.Boolean()),
                    ),
                    {
                        "j": job_id,
                        "k": code_id,
                        "t": ctype,
                        "a": c,
                        "r": r,
                        "e": est_id,
                        "locked": True,
                        "co": co_id,
                        "n": notes,
                    },
                )


def _backfill_wip_locks(conn) -> None:
    voided = {
        r[0]
        for r in conn.execute(
            sa.text(
                "SELECT source_id FROM transactions "
                "WHERE source_type = 'wip_adjustment_void'"
            )
        )
    }
    taken = {
        _day(r[0]) for r in conn.execute(sa.text("SELECT as_of FROM wip_entry_locks"))
    }
    for txn_id, day in conn.execute(
        sa.text(
            "SELECT id, date FROM transactions "
            "WHERE source_type = 'wip_adjustment' ORDER BY id"
        )
    ).fetchall():
        day = _day(day)
        if txn_id in voided or day in taken:
            continue
        taken.add(day)
        conn.execute(
            sa.text(
                "INSERT INTO wip_entry_locks (as_of, transaction_id) VALUES (:d, :t)"
            ).bindparams(sa.bindparam("d", type_=sa.Date())),
            {"d": day, "t": txn_id},
        )


# ── upgrade / downgrade ──────────────────────────────────────────────────


def upgrade() -> None:
    # Books built by create_all already have the columns, indexes and table:
    # each is added only when missing, so a re-run is a no-op.
    conn = op.get_bind()
    jobs = _columns("jobs")
    if jobs and "closed_on" not in jobs:
        with op.batch_alter_table("jobs") as batch:
            batch.add_column(sa.Column("closed_on", sa.Date(), nullable=True))

    budgets = _columns("job_budgets")
    if budgets:
        if "change_order_id" not in budgets:
            with op.batch_alter_table("job_budgets") as batch:
                batch.add_column(
                    sa.Column("change_order_id", sa.Integer(), nullable=True)
                )
                batch.create_foreign_key(
                    "fk_job_budget_change_order",
                    "change_orders",
                    ["change_order_id"],
                    ["id"],
                )
        if "uq_job_budget" in _unique_names("job_budgets"):
            with op.batch_alter_table("job_budgets") as batch:
                batch.drop_constraint("uq_job_budget", type_="unique")
        have = _index_names("job_budgets")
        if "ix_job_budgets_change_order_id" not in have:
            op.create_index(
                "ix_job_budgets_change_order_id", "job_budgets", ["change_order_id"]
            )
        if "uq_job_budget" not in have:
            op.create_index(
                "uq_job_budget",
                "job_budgets",
                ["job_id", "cost_code_id", "cost_type", "source"],
                unique=True,
                sqlite_where=_WHERE_UNOWNED,
                postgresql_where=_WHERE_UNOWNED,
            )
        if "uq_job_budget_change_order" not in have:
            op.create_index(
                "uq_job_budget_change_order",
                "job_budgets",
                ["change_order_id", "cost_code_id", "cost_type"],
                unique=True,
                sqlite_where=_WHERE_OWNED,
                postgresql_where=_WHERE_OWNED,
            )

    if "wip_entry_locks" not in _insp().get_table_names():
        op.create_table(
            "wip_entry_locks",
            sa.Column("as_of", sa.Date(), primary_key=True),
            sa.Column(
                "transaction_id",
                sa.Integer(),
                sa.ForeignKey("transactions.id"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    if budgets:
        _link_change_budgets(conn)
    if "transactions" in _insp().get_table_names():
        _backfill_wip_locks(conn)


def downgrade() -> None:
    if "wip_entry_locks" in _insp().get_table_names():
        op.drop_table("wip_entry_locks")
    budgets = _columns("job_budgets")
    if "change_order_id" in budgets:
        have = _index_names("job_budgets")
        for name in (
            "uq_job_budget_change_order",
            "uq_job_budget",
            "ix_job_budgets_change_order_id",
        ):
            if name in have:
                op.drop_index(name, table_name="job_budgets")
        # The old key holds one change row per (job, code, type): fold a
        # change order's rows back into one.
        conn = op.get_bind()
        groups = defaultdict(list)
        for row in conn.execute(
            sa.text(
                "SELECT id, job_id, cost_code_id, cost_type, amount, revenue_amount "
                "FROM job_budgets WHERE source = 'change' ORDER BY id"
            )
        ).fetchall():
            groups[(row[1], row[2], row[3])].append(row)
        for rows in groups.values():
            if len(rows) < 2:
                continue
            keep = rows[0][0]
            cost = sum((_q(r[4]) for r in rows), Decimal("0"))
            rev = sum((_q(r[5]) for r in rows), Decimal("0"))
            conn.execute(
                sa.text(
                    "UPDATE job_budgets SET amount = :a, revenue_amount = :r "
                    "WHERE id = :id"
                ).bindparams(
                    sa.bindparam("a", type_=_MONEY),
                    sa.bindparam("r", type_=_MONEY),
                ),
                {"a": cost, "r": rev, "id": keep},
            )
            for r in rows[1:]:
                conn.execute(
                    sa.text("DELETE FROM job_budgets WHERE id = :id"), {"id": r[0]}
                )
        fks = {fk["name"] for fk in _insp().get_foreign_keys("job_budgets")}
        with op.batch_alter_table("job_budgets") as batch:
            if "fk_job_budget_change_order" in fks:
                batch.drop_constraint("fk_job_budget_change_order", type_="foreignkey")
            batch.drop_column("change_order_id")
            batch.create_unique_constraint(
                "uq_job_budget", ["job_id", "cost_code_id", "cost_type", "source"]
            )
    if "closed_on" in _columns("jobs"):
        with op.batch_alter_table("jobs") as batch:
            batch.drop_column("closed_on")
