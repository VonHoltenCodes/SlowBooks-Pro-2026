# ============================================================================
# Construction estimating (#551) — the bid math, in one pure place.
#
# A contractor's estimate is more than a priced list: lines belong to bid
# packages, some are allowances or add/deduct alternates or unit prices, and
# the bid carries four markups that compound in order (general conditions,
# insurance, fee, bond). This module turns an estimate into the figures the
# route, the PDF and the tests all read, with no database of its own — it
# reads only the estimate's own lines and markup percents.
#
# The lines stay AT COST. The four markups are their own figures under the
# subtotal — on the PDF, in the app, and (later, #552) as their own
# schedule-of-values lines. The bid total is subtotal + the markups, pre-tax;
# tax covers the taxable lines and their share of the markups.
#
# A line may instead carry its own markup (#774, markup per line): its rate
# is its unit cost plus that markup, and the estimate uses none of the bid
# tools above. presentation_style() says which layout an estimate gets.
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q, quantize_to, refuse_total_past_column

# A line's rate is stored to four places (estimate_lines.rate).
RATE_Q = Decimal("0.0001")

# The two ways a construction estimate is laid out (#774): each line priced at
# its cost plus its own markup, or the commercial bid (lines at cost, packages,
# kinds, the four markups under the subtotal).
LINE_MARKUP = "line_markup"
COMMERCIAL_BID = "commercial_bid"
ESTIMATE_STYLES = (LINE_MARKUP, COMMERCIAL_BID)

# A package with no id groups here; a real package named "Unassigned" keeps
# its own id, so grouping is by id, never by this label.
UNASSIGNED = -1


def priced_rate(unit_cost, markup_pct) -> Decimal:
    """A construction base or alternate line's rate from its unit cost (#774):
    unit cost × (1 + markup / 100) to the rate column's four places when the
    line carries a markup, else the unit cost itself (at cost, as a commercial
    bid's line is)."""
    cost = Decimal(str(unit_cost))
    if markup_pct is None:
        return cost
    return quantize_to(cost * (1 + Decimal(str(markup_pct)) / Decimal("100")), RATE_Q)


def uses_bid_tools(estimate) -> bool:
    """True when the estimate uses a commercial bid's tools: any of the four
    markups, a trade package on a line, or a line that is not a base line."""
    if any(
        Decimal(str(getattr(estimate, name, 0) or 0)) != 0
        for name in ("gc_pct", "insurance_pct", "fee_pct", "bond_pct")
    ):
        return True
    return any(
        ln.trade_package_id is not None or (ln.line_kind or "base") != "base"
        for ln in estimate.lines
    )


def presentation_style(estimate, company_style) -> str:
    """The layout an estimate gets on the form and the owner's PDF (#774): the
    commercial bid when it uses bid tools (nothing on it is hidden), else
    markup per line when a line carries its own markup, else the company's
    estimate_style setting."""
    if uses_bid_tools(estimate):
        return COMMERCIAL_BID
    if any(getattr(ln, "markup_pct", None) is not None for ln in estimate.lines):
        return LINE_MARKUP
    return COMMERCIAL_BID if company_style == COMMERCIAL_BID else LINE_MARKUP


def is_included_line(line) -> bool:
    """A line that is part of the bought scope: a base line, an allowance, or
    an ACCEPTED alternate. Unit-price lines (a rate card) and un-accepted
    alternates are not — they convert, seed and total as if absent."""
    kind = getattr(line, "line_kind", None) or "base"
    if kind == "unit_price":
        return False
    if kind == "alternate" and not getattr(line, "alternate_accepted", False):
        return False
    return True


def included_lines(estimate):
    """The bought-scope lines, in order (convert, accept and totals share
    this one predicate)."""
    return [ln for ln in estimate.lines if is_included_line(ln)]


def uncoded_lines(estimate) -> int:
    """How many bought-scope lines carry a price or cost but no cost code:
    Accept budgets them on no code (a whole-job row)."""
    return sum(
        1
        for ln in included_lines(estimate)
        if ln.cost_code_id is None
        and (Decimal(str(ln.amount or 0)) != 0 or ln.unit_cost is not None)
    )


def uncoded_warning(count: int) -> str:
    """The sentence Accept gives for lines with no cost code (empty for none)."""
    if not count:
        return ""
    if count == 1:
        return "1 line has no cost code, so its budget lands on no code."
    return f"{count} lines have no cost code, so their budget lands on no code."


# A line's cost is quantity × unit cost (the budget side); a line with no unit
# cost carries no cost. Rounded to the cent, as the budget seeder does.
def line_cost(line) -> Decimal:
    if line.unit_cost is None:
        return Decimal("0")
    qty = Decimal(str(line.quantity or 0))
    return _q(qty * Decimal(str(line.unit_cost)))


# A line's price is quantity × rate, to the cent (an ORM line's stored amount
# equals this; a request-schema line has no settled amount, so compute it).
def line_price(line) -> Decimal:
    qty = Decimal(str(getattr(line, "quantity", 0) or 0))
    rate = Decimal(str(getattr(line, "rate", 0) or 0))
    return _q(qty * rate)


def package_order_key(pkg) -> tuple:
    """Where a trade package sits in a bid book: by its lowest mapped section
    code (so 02 Demolition precedes 03 Concrete), then name. A package with no
    mapped section sorts after those that have one, and no package at all
    (Unassigned) sorts last. Shared by the estimate, its PDF, the schedule of
    values and the Buyout."""
    if pkg is None:
        return (2, "", "")
    codes = [m.cost_code.code for m in pkg.sections if m.cost_code is not None]
    name = (pkg.name or "").lower()
    if codes:
        return (0, min(codes), name)
    return (1, "", name)


def _pct(value) -> Decimal:
    """A stored percent (5.5 = 5.5%) as a fraction."""
    return Decimal(str(value or 0)) / Decimal("100")


def fmt_pct(pct) -> str:
    """A markup percent as it reads on a line description: 8, 1.5, 10 — the
    stored value with any trailing zeros dropped (#551). Shared by the convert
    route and the SOV builder (#552)."""
    s = format(Decimal(str(pct or 0)), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# A base line or an accepted alternate is markup-able scope; an allowance is
# only when the estimate says so (the GC decides, off by default); everything
# excluded never is.
def _markupable(line, markup_allowances: bool = False) -> bool:
    kind = getattr(line, "line_kind", None) or "base"
    if kind == "allowance":
        return bool(markup_allowances)
    return kind == "base" or (
        kind == "alternate" and getattr(line, "alternate_accepted", False)
    )


def markup_base_amount(line, markup_allowances: bool = False) -> Decimal:
    """What an INCLUDED line adds to the markup base: its price when it is
    markup-able and carries a unit cost (a flat-priced line is never marked
    up); an allowance has no unit cost, so with allowance markup on its whole
    amount counts. Zero otherwise. Shared by the totals and the budget seeder."""
    if not _markupable(line, markup_allowances):
        return Decimal("0")
    if (getattr(line, "line_kind", None) or "base") != "allowance":
        if line.unit_cost is None:
            return Decimal("0")
    return line_price(line)


def markup_factor(estimate) -> Decimal:
    """The multiplier that turns a line's price into a marked-up price:
    (1+gc)(1+ins)(1+fee)(1+bond). Unrounded — the caller rounds the price.
    Used to show an alternate's marked-up price to the customer."""
    one = Decimal("1")
    return (
        (one + _pct(estimate.gc_pct))
        * (one + _pct(estimate.insurance_pct))
        * (one + _pct(estimate.fee_pct))
        * (one + _pct(estimate.bond_pct))
    )


def _markups(markup_base, gc_pct, insurance_pct, fee_pct, bond_pct):
    """The four markups compounding in order on the markup base, each rounded
    to the cent: gc on the base, insurance on base+gc, fee on base+gc+ins,
    bond on base+gc+ins+fee. Returns (gc, insurance, fee, bond)."""
    gc = _q(markup_base * _pct(gc_pct))
    insurance = _q((markup_base + gc) * _pct(insurance_pct))
    fee = _q((markup_base + gc + insurance) * _pct(fee_pct))
    bond = _q((markup_base + gc + insurance + fee) * _pct(bond_pct))
    return gc, insurance, fee, bond


def taxable_markup_share(lines, markup_total, markup_allowances=False) -> Decimal:
    """The part of the markups that rides on the taxable lines: markup_total ×
    (the taxable lines' markup base ÷ the whole markup base), to the cent. The
    same pro-rata split the budget seeder and the owner's bid use, so a markup
    on an exempt line is never taxed. Shared by the estimate's tax and the
    schedule of values' markup lines (contracts.sov_from_estimate)."""
    included = [ln for ln in lines if is_included_line(ln)]
    base = sum(
        (markup_base_amount(ln, markup_allowances) for ln in included), Decimal("0")
    )
    if not base or not markup_total:
        return Decimal("0")
    taxed = sum(
        (
            markup_base_amount(ln, markup_allowances)
            for ln in included
            if getattr(ln, "is_taxable", None) is not False
        ),
        Decimal("0"),
    )
    return _q(Decimal(str(markup_total)) * taxed / base)


def estimate_document_totals(
    lines,
    tax_rate,
    gc_pct,
    insurance_pct,
    fee_pct,
    bond_pct,
    markup_allowances=False,
) -> tuple[Decimal, Decimal, Decimal]:
    """The stored (subtotal, tax_amount, total) for an estimate, lines at cost
    with the markups as their own figures under the subtotal.

    - subtotal = Σ price of the included lines (base + accepted alternates +
      allowances), each price being quantity × rate (an ORM line's amount).
    - the four markups compound in order on the markup base (Σ price of base /
      accepted-alternate lines that carry a unit cost, plus the allowance
      lines only when markup_allowances is on); markup_total is their sum.
    - tax covers the taxable lines and their share of the markups: tax_rate ×
      (taxable line subtotal + taxable_markup_share), so a markup on an exempt
      line is not taxed; 0 when no included line is taxable (an exempt
      customer pays no tax on anything).
    - total = subtotal + markup_total + tax.

    Works on both ORM lines and the request-schema lines (both carry quantity,
    rate, unit_cost, line_kind, alternate_accepted, is_taxable); price is
    computed from quantity × rate, so a schema line needs no stored amount.
    """
    included = [ln for ln in lines if is_included_line(ln)]
    subtotal = _q(sum((line_price(ln) for ln in included), Decimal("0")))
    markup_base = _q(
        sum(
            (markup_base_amount(ln, markup_allowances) for ln in included),
            Decimal("0"),
        )
    )
    gc, insurance, fee, bond = _markups(
        markup_base, gc_pct, insurance_pct, fee_pct, bond_pct
    )
    markup_total = gc + insurance + fee + bond

    taxable_lines = [
        ln for ln in included if getattr(ln, "is_taxable", None) is not False
    ]
    if taxable_lines:
        taxable_base = _q(sum((line_price(ln) for ln in taxable_lines), Decimal("0")))
        markup_share = taxable_markup_share(included, markup_total, markup_allowances)
        tax_amount = _q((taxable_base + markup_share) * Decimal(str(tax_rate or 0)))
    else:
        tax_amount = Decimal("0")

    total = _q(subtotal + markup_total + tax_amount)
    refuse_total_past_column(subtotal, markup_total, tax_amount, total)
    return subtotal, tax_amount, total


def markup_parts(estimate) -> list[dict]:
    """The estimate's non-zero markups in compounding order, each split into
    the part that rides on taxable lines and the rest: [{"kind",
    "description", "amount", "is_taxable"}]. The taxable parts are
    taxable_markup_share spread over the markups pro rata (the last one takes
    the rounding), so they sum to the estimate's own taxed figure to the cent.
    A markup wholly taxable or wholly not is one part, "General conditions
    (8%)"; a split one adds "General conditions (8%) (not taxable)". The
    schedule of values and the converted invoice both build from this."""
    totals = estimate_totals(estimate)
    specs = [
        (kind, label, totals[pct_key], totals[key])
        for kind, label, key, pct_key in (
            ("general_conditions", "General conditions", "gc", "gc_pct"),
            ("insurance", "Insurance", "insurance", "insurance_pct"),
            ("fee", "Fee", "fee", "fee_pct"),
            ("bond", "Bond", "bond", "bond_pct"),
        )
        if totals[key]
    ]
    markup_total = totals["markup_total"]
    taxed_total = taxable_markup_share(
        included_lines(estimate), markup_total, totals["markup_allowances"]
    )
    parts = []
    assigned = Decimal("0")
    for n, (kind, label, pct, amount) in enumerate(specs):
        if n == len(specs) - 1:
            taxed = taxed_total - assigned
        else:
            taxed = _q(amount * taxed_total / markup_total)
        assigned += taxed
        name = f"{label} ({fmt_pct(pct)}%)"
        pieces = [
            (flag, v) for flag, v in ((True, taxed), (False, amount - taxed)) if v
        ]
        for flag, value in pieces:
            parts.append(
                {
                    "kind": kind,
                    "description": (
                        f"{name} (not taxable)"
                        if len(pieces) > 1 and not flag
                        else name
                    ),
                    "amount": value,
                    "is_taxable": flag,
                }
            )
    return parts


def estimate_totals(estimate) -> dict:
    """Every bid figure for an estimate, from its own lines and markups.

    The lines stay at cost; the markups are their own figures under the
    subtotal:

    - markup_base — Σ price of base + accepted-alternate lines that HAVE a
      unit cost (priced at cost, so this is the cost the markups build on);
      allowances count only when the estimate's markup_allowances flag is on
      (off by default).
    - the four markups compound in order on markup_base; markup_total is their
      sum.
    - subtotal — Σ price of the included lines (base + accepted alternates +
      allowances).
    - priced_direct — Σ price of base / accepted-alternate lines priced with
      NO unit cost (a flat price, never marked up).
    - allowance_total — Σ price of the allowance lines.
    - bid_total — subtotal + markup_total (the PRE-TAX contract sum #552 uses).
    - bid_tax / bid_total_with_tax mirror the estimate's stored tax and total
      (total − tax == bid_total).
    - cost_total — Σ cost of the included lines, an allowance at its amount;
      profit — bid_total − budget_cost; margin_pct — profit ÷ bid_total × 100
      to two places (0 on a $0 bid); uses_bid_tools — see uses_bid_tools().

    by_package carries price and cost per package (allowances contribute price
    only, never a unit cost); alternates and unit_prices list the lines held
    out of the bid so the customer can choose them. An alternate that carries
    a unit cost shows its MARKED-UP price (price × the markup factor); one with
    no cost shows its rate price.
    """
    subtotal = Decimal("0")
    allowance_total = Decimal("0")
    markup_base = Decimal("0")
    priced_direct = Decimal("0")
    by_package: dict = {}
    package_objs: dict = {}
    alternates: list = []
    unit_prices: list = []
    factor = markup_factor(estimate)
    markup_allowances = bool(getattr(estimate, "markup_allowances", False))

    for line in estimate.lines:
        kind = line.line_kind or "base"
        cost = line_cost(line)
        price = line_price(line)

        if kind == "unit_price":
            unit_prices.append(
                {
                    "id": line.id,
                    "description": line.description,
                    "unit": line.unit,
                    "rate": _q(line.rate or 0),
                    "quantity": Decimal(str(line.quantity or 0)),
                }
            )
            continue

        if kind == "alternate":
            # An alternate with a unit cost is shown at its marked-up price;
            # one with no cost is shown at its rate price. Built on price
            # (quantity x rate), the same base the markups compound on when
            # the alternate is accepted, so the two cannot drift apart.
            shown = _q(price * factor) if line.unit_cost is not None else price
            alternates.append(
                {
                    "id": line.id,
                    "description": line.description,
                    "amount": shown,
                    "accepted": bool(line.alternate_accepted),
                }
            )
            if not line.alternate_accepted:
                continue

        # From here the line is included (base, allowance, or accepted alt).
        subtotal += price
        if kind == "allowance":
            allowance_total += price
        elif line.unit_cost is None:  # base or accepted alternate
            priced_direct += price
        markup_base += markup_base_amount(line, markup_allowances)

        key = line.trade_package_id if line.trade_package_id is not None else UNASSIGNED
        bucket = by_package.get(key)
        if bucket is None:
            bucket = {
                "package_id": line.trade_package_id,
                "package_name": (
                    line.trade_package.name
                    if line.trade_package is not None
                    else "Unassigned"
                ),
                "cost": Decimal("0"),
                "price": Decimal("0"),
                "line_count": 0,
            }
            by_package[key] = bucket
            package_objs[key] = line.trade_package
        # An allowance's amount is its bid cost: it budgets as cost on accept
        # (a budgeted figure is spent), whatever unit cost was typed on it.
        bucket["cost"] += price if kind == "allowance" else cost
        bucket["price"] += price
        bucket["line_count"] += 1

    gc, insurance, fee, bond = _markups(
        markup_base,
        estimate.gc_pct,
        estimate.insurance_pct,
        estimate.fee_pct,
        estimate.bond_pct,
    )
    markup_total = gc + insurance + fee + bond
    bid_total = subtotal + markup_total

    # Packages in bid-book order (lowest section code, then name), the
    # unassigned bucket last.
    packages = [
        by_package[k]
        for k in sorted(by_package, key=lambda k: package_order_key(package_objs[k]))
    ]
    # What accepting locks as cost: every package's cost (allowances at their
    # amount) plus the three markups that are also real job costs. The fee is
    # the only markup that stays margin.
    cost_total = sum((b["cost"] for b in by_package.values()), Decimal("0"))
    budget_cost = cost_total + gc + insurance + bond
    # What the contractor keeps: the bid less what accepting budgets as cost.
    profit = bid_total - budget_cost
    margin_pct = _q(profit / bid_total * 100) if bid_total else Decimal("0")

    return {
        "markup_allowances": markup_allowances,
        "markup_base": markup_base,
        "gc_pct": Decimal(str(estimate.gc_pct or 0)),
        "insurance_pct": Decimal(str(estimate.insurance_pct or 0)),
        "fee_pct": Decimal(str(estimate.fee_pct or 0)),
        "bond_pct": Decimal(str(estimate.bond_pct or 0)),
        "gc": gc,
        "insurance": insurance,
        "fee": fee,
        "bond": bond,
        "markup_total": markup_total,
        "priced_direct": priced_direct,
        "allowance_total": allowance_total,
        "subtotal": subtotal,
        # The contract sum and schedule of values (#552) are PRE-TAX, so
        # bid_total is subtotal + the markups; tax rides each invoice.
        # bid_tax / bid_total_with_tax mirror the estimate's stored tax and
        # total (total − tax == bid_total).
        "bid_total": bid_total,
        "budget_cost": budget_cost,
        # Σ cost of the included lines, an allowance at its amount (#774).
        "cost_total": cost_total,
        "profit": profit,
        "margin_pct": margin_pct,
        "uses_bid_tools": uses_bid_tools(estimate),
        "bid_tax": _q(getattr(estimate, "tax_amount", 0) or 0),
        "bid_total_with_tax": _q(getattr(estimate, "total", 0) or 0),
        "by_package": packages,
        "alternates": alternates,
        "unit_prices": unit_prices,
    }
