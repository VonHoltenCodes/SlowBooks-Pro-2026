# ============================================================================
# Buyout and commitments (#553) — bid vs buyout per trade package.
#
# A commitment is a purchase order (plain PO or subcontract); its lines are
# its schedule of values. This builds the job's buyout table: what each trade
# package was bid at (from the accepted estimate), what has been bought (the
# commitments on the job), and the variance, with committed-remaining and
# billed-to-date alongside. The #556 buyout report foots job_buyout across
# jobs, and job cost by vendor reads the posted job cost beside it.
# ============================================================================

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bills import Bill, BillLine, BillStatus
from app.models.estimates import Estimate, EstimateStatus
from app.models.jobs import Job
from app.models.purchase_orders import POStatus, PurchaseOrder
from app.models.trade_packages import TradePackage, TradePackageSection
from app.services.accounting import _q
from app.services.compliance import vendor_compliance
from app.services.estimating import estimate_totals, package_order_key

# A commitment counts once it has left draft; its remaining (unbilled) part
# counts only while it is still open.
_NON_DRAFT = (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED, POStatus.CLOSED)
_OPEN = (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)

_UNASSIGNED = "Unassigned"

# The estimate's markups that are job costs, in the order they print. Their
# rows carry a string key so they never collide with a package id.
_MARKUP_ROWS = (
    ("gc", "General conditions"),
    ("insurance", "Insurance"),
    ("bond", "Bond"),
)


def _bid_estimate(db: Session, job: Job):
    """The estimate the bid figures come from: the contract's source estimate
    if the job names one, else an accepted estimate on the job, else none."""
    if job.contract_estimate_id:
        est = db.get(Estimate, job.contract_estimate_id)
        if est:
            return est
    return (
        db.query(Estimate)
        .filter(Estimate.job_id == job.id, Estimate.status == EstimateStatus.ACCEPTED)
        .order_by(Estimate.id)
        .first()
    )


def _billed_as_of(db: Session, po: PurchaseOrder, as_of) -> Decimal:
    """What has been billed against a commitment as of a date: the pre-tax
    subtotal of its non-void bills dated on or before `as_of`. With no date the
    caller uses the lines' cumulative billed_amount instead."""
    from app.models.bills import Bill, BillStatus

    bills = (
        db.query(Bill)
        .filter(
            Bill.po_id == po.id,
            Bill.status != BillStatus.VOID,
            Bill.date <= as_of,
        )
        .all()
    )
    return _q(sum((Decimal(str(b.subtotal or 0)) for b in bills), Decimal("0")))


def _package_by_code(db: Session, preferred: set) -> dict:
    """cost_code_id -> trade package id. A code in several packages goes to one
    the bid already names, else the first in bid-book order."""
    maps: dict = {}
    for m in db.query(TradePackageSection).all():
        maps.setdefault(m.cost_code_id, []).append(m.package_id)
    pkgs = {p.id: p for p in db.query(TradePackage).all()}
    out = {}
    for code_id, ids in maps.items():
        ids = [i for i in ids if i in pkgs]
        if not ids:
            continue
        ids.sort(key=lambda i: (i not in preferred, package_order_key(pkgs[i])))
        out[code_id] = ids[0]
    return out


def _changes_by_package(db: Session, job: Job, pkg_of: dict) -> dict:
    """Approved change-order cost per package (None = no package): the lines of
    the job's approved owner change orders and standalone sub change orders (a
    sub CO that carries an owner CO writes no budget of its own, so its money
    is counted once, on the owner CO). A line's own package wins, else the
    package its cost code maps to."""
    from app.models.change_orders import ChangeOrder
    from app.services.change_orders import _writes_budget

    out: dict = {}
    cos = db.query(ChangeOrder).filter(
        ChangeOrder.job_id == job.id,
        ChangeOrder.kind.in_(("owner", "sub")),
        ChangeOrder.status == "approved",
    )
    for co in cos:
        if not _writes_budget(co):
            continue
        for line in co.lines:
            key = line.trade_package_id
            if key is None:
                key = pkg_of.get(line.cost_code_id)
            out[key] = out.get(key, Decimal("0")) + Decimal(str(line.amount or 0))
    return out


def _spent_no_commitment(db: Session, job: Job, as_of, pkg_of: dict) -> dict:
    """Cost on the job from plain bills: coded lines of non-void, non-draft
    bills that name no commitment, per package (None = no package)."""
    q = (
        db.query(BillLine, Bill)
        .join(Bill, BillLine.bill_id == Bill.id)
        .filter(
            Bill.po_id.is_(None),
            Bill.status.notin_((BillStatus.VOID, BillStatus.DRAFT)),
            BillLine.cost_code_id.isnot(None),
            func.coalesce(BillLine.job_id, Bill.job_id) == job.id,
        )
    )
    if as_of is not None:
        q = q.filter(Bill.date <= as_of)
    out: dict = {}
    for line, _bill in q:
        key = pkg_of.get(line.cost_code_id)
        out[key] = out.get(key, Decimal("0")) + Decimal(str(line.amount or 0))
    return out


def job_buyout(db: Session, job: Job, as_of=None) -> dict:
    """One row per trade package: the bid cost, what has been bought, the
    variance, committed-remaining, billed-to-date and the commitments behind
    it. An 'Unassigned' row gathers commitments with no package (and any bid
    cost with no package); a totals row foots the table.

    With `as_of` (the buyout report, #556) only commitments dated that day or
    earlier count, and billed-to-date is the non-void bills dated by then; with
    the default None every non-draft commitment counts and billing is the
    lines' cumulative billed_amount."""
    # Bid cost per package from the accepted estimate.
    bid_by_pkg: dict = {}  # package_id (or None) -> {"name", "cost"}
    markup_costs: dict = {}  # "gc" / "insurance" / "bond" -> amount
    est = _bid_estimate(db, job)
    if est is not None:
        totals_ = estimate_totals(est)
        for row in totals_["by_package"]:
            bid_by_pkg[row["package_id"]] = {
                "name": row["package_name"],
                "cost": _q(row["cost"]),
            }
        for key, _label in _MARKUP_ROWS:
            if totals_[key]:
                markup_costs[key] = _q(totals_[key])

    # Commitments on the job (header job), non-draft; dated by as_of when given.
    q = db.query(PurchaseOrder).filter(
        PurchaseOrder.job_id == job.id,
        PurchaseOrder.status.in_(_NON_DRAFT),
    )
    if as_of is not None:
        q = q.filter(PurchaseOrder.date <= as_of)
    commitments = q.order_by(PurchaseOrder.po_number).all()

    # Accumulate per package.
    packages: dict = {}  # package_id (or None) -> row dict

    def row_for(pkg_id, name):
        row = packages.get(pkg_id)
        if row is None:
            row = {
                "package_id": pkg_id if isinstance(pkg_id, int) else None,
                "package_name": name,
                "kind": "package" if pkg_id is not None else "unassigned",
                "bid_cost": Decimal("0"),
                "changes": Decimal("0"),
                "bought": Decimal("0"),
                "spent_no_commitment": Decimal("0"),
                "committed_remaining": Decimal("0"),
                "billed_to_date": Decimal("0"),
                "commitments": [],
            }
            packages[pkg_id] = row
        return row

    # Seed the bid rows first so a package bid but not bought still shows.
    for pkg_id, info in bid_by_pkg.items():
        name = info["name"] if pkg_id is not None else _UNASSIGNED
        row_for(pkg_id, name)["bid_cost"] = info["cost"]
    # General conditions, insurance and bond are bid costs with no package.
    for key, label in _MARKUP_ROWS:
        if key in markup_costs:
            row = row_for(key, label)
            row["kind"] = "markup"
            row["bid_cost"] = markup_costs[key]

    # Approved change-order budget and plain bills, placed by cost code into
    # the package that maps it (the bid's own packages win a tie).
    pkg_of = _package_by_code(db, {k for k in bid_by_pkg if k is not None})
    pkg_names = {p.id: p.name for p in db.query(TradePackage)}
    for pkg_id, amount in _changes_by_package(db, job, pkg_of).items():
        row_for(pkg_id, pkg_names.get(pkg_id, _UNASSIGNED))["changes"] += amount
    for pkg_id, amount in _spent_no_commitment(db, job, as_of, pkg_of).items():
        row = row_for(pkg_id, pkg_names.get(pkg_id, _UNASSIGNED))
        row["spent_no_commitment"] += amount

    for po in commitments:
        pkg_id = po.trade_package_id
        name = (
            po.trade_package.name
            if po.trade_package is not None
            else (bid_by_pkg.get(None, {}).get("name") or _UNASSIGNED)
        )
        row = row_for(pkg_id, name if pkg_id is not None else _UNASSIGNED)
        # Bought is the pre-tax subtotal, so it compares like-for-like with the
        # bid cost; tax is payable in full and is not a buyout figure.
        subtotal = _q(po.subtotal or 0)
        remaining = Decimal("0")
        if as_of is None:
            billed = _q(
                sum(
                    (Decimal(str(ln.billed_amount or 0)) for ln in po.lines),
                    Decimal("0"),
                )
            )
            if po.status in _OPEN:
                remaining = _q(
                    sum(
                        (
                            Decimal(str(ln.amount or 0))
                            - Decimal(str(ln.billed_amount or 0))
                            for ln in po.lines
                        ),
                        Decimal("0"),
                    )
                )
        else:
            billed = _billed_as_of(db, po, as_of)
            if po.status in _OPEN:
                remaining = _q(subtotal - billed)
        row["bought"] += subtotal
        row["committed_remaining"] += remaining
        row["billed_to_date"] += billed
        row["commitments"].append(
            {
                "id": po.id,
                "number": po.po_number,
                "vendor_name": po.vendor.name if po.vendor else "",
                "type": po.commitment_type,
                "subtotal": float(subtotal),
                "billed": float(billed),
                "status": (
                    po.status.value if hasattr(po.status, "value") else po.status
                ),
                "compliance": vendor_compliance(po.vendor),
            }
        )

    # Packages in bid-book order, Unassigned next, the markup rows last.
    pkg_objs = {
        p.id: p
        for p in db.query(TradePackage).filter(
            TradePackage.id.in_([k for k in packages if isinstance(k, int)])
        )
    }
    markup_order = [k for k, _l in _MARKUP_ROWS]

    def order(item):
        key, _row = item
        if isinstance(key, str):
            return (3, markup_order.index(key), "", "")
        if key is None:
            return (2, 0, "", "")
        k = package_order_key(pkg_objs.get(key))
        return (0, 0, k[1], k[2]) if k[0] == 0 else (1, 0, "", k[2])

    ordered = [r for _k, r in sorted(packages.items(), key=order)]
    rows = []
    totals = {
        "bid_cost": Decimal("0"),
        "changes": Decimal("0"),
        "bought": Decimal("0"),
        "variance": Decimal("0"),
        "spent_no_commitment": Decimal("0"),
        "committed_remaining": Decimal("0"),
        "billed_to_date": Decimal("0"),
    }
    for r in ordered:
        variance = _q(r["bid_cost"] + r["changes"] - r["bought"])
        totals["bid_cost"] += r["bid_cost"]
        totals["changes"] += r["changes"]
        totals["bought"] += r["bought"]
        totals["variance"] += variance
        totals["spent_no_commitment"] += r["spent_no_commitment"]
        totals["committed_remaining"] += r["committed_remaining"]
        totals["billed_to_date"] += r["billed_to_date"]
        rows.append(
            {
                "package_id": r["package_id"],
                "package_name": r["package_name"],
                "kind": r["kind"],
                "bid_cost": float(r["bid_cost"]),
                "changes": float(_q(r["changes"])),
                "bought": float(r["bought"]),
                "variance": float(variance),
                "spent_no_commitment": float(_q(r["spent_no_commitment"])),
                "committed_remaining": float(r["committed_remaining"]),
                "billed_to_date": float(r["billed_to_date"]),
                "commitments": r["commitments"],
            }
        )

    return {
        "job_id": job.id,
        "bid_estimate_id": est.id if est is not None else None,
        "rows": rows,
        "totals": {k: float(_q(v)) for k, v in totals.items()},
    }
