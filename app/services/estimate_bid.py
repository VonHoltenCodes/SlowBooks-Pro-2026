# ============================================================================
# The bid as the owner reads it (construction estimate PDF and view).
#
# "Show markup rows" off hides the four markup rows: each package's price
# carries its share instead, and one subtotal stands in for the rows. The
# math and the totals are unchanged. The share is the budget seeder's own
# split (job_costing.budgets_from_estimate): the markups spread pro rata to
# each row's share of the markup base, the last row taking the rounding
# remainder, so the prices foot to the bid total to the cent.
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q
from app.services.estimating import (
    included_lines,
    line_price,
    markup_base_amount,
)


def spread_markup_prices(estimate, markup_total) -> dict:
    """{"packages": {package_id (None = unassigned): price},
    "allowances": {line id: amount}} with the markups spread in.

    A package is every included base or accepted-alternate line sharing a
    trade package; each allowance is its own row (it has no package on the
    page). A row with no markup base (priced direct, or an allowance while
    allowance markup is off) takes no share and prints at its price.
    """
    markup_allowances = bool(getattr(estimate, "markup_allowances", False))
    rows: dict = {}  # key -> [price, markup base], in line order
    for line in included_lines(estimate):
        kind = line.line_kind or "base"
        key = (
            ("allowance", line.id)
            if kind == "allowance"
            else ("package", line.trade_package_id)
        )
        row = rows.setdefault(key, [Decimal("0"), Decimal("0")])
        row[0] += line_price(line)
        row[1] += markup_base_amount(line, markup_allowances)

    base_total = sum((r[1] for r in rows.values()), Decimal("0"))
    with_base = [k for k, r in rows.items() if r[1] > 0]
    assigned = Decimal("0")
    for n, key in enumerate(with_base):
        if n == len(with_base) - 1:
            share = markup_total - assigned
        else:
            share = _q(markup_total * rows[key][1] / base_total)
            assigned += share
        rows[key][0] += share

    out = {"packages": {}, "allowances": {}}
    for (kind, ident), (price, _base) in rows.items():
        out["packages" if kind == "package" else "allowances"][ident] = price
    return out
