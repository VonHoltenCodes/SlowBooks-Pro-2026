# ============================================================================
# The contract on the job (#552) — the owner's side of a construction job.
#
# A construction job can carry a contract: a lump sum, a GMP (guaranteed
# maximum price) or cost-plus, with a fee, a cap, contingency and retainage.
# The owner's schedule of values (SOV) breaks the contract sum into the lines
# a pay application bills against. This module builds the SOV from an accepted
# estimate and rolls the contract up into the figures the Contract tab reads.
#
# The contract sum is PRE-TAX — the SOV foots to the estimate's pre-tax bid
# (tax is added on each invoice, #555). The math here is pure where it can be
# (sov_from_estimate, the summary arithmetic); build_sov is the one DB writer.
# Later steps plug in here: #553 buyout/commitments, #554 change orders (fills
# "changes" and "contingency_used"), #555 progress billing. The #556 WIP
# schedule reads contract_summary for each job's contract and billing.
# ============================================================================

from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.estimates import Estimate, EstimateStatus
from app.models.jobs import Job, SovLine
from app.services.accounting import _q, get_contract_revenue_account_id
from app.services.estimating import (
    estimate_totals,
    fmt_pct,
    included_lines,
    line_price,
)


def contract_is_locked(db: Session, job: Job) -> bool:
    """True once the contract sum and contingency can only move by change
    order: the job has billing against its schedule of values or an approved
    owner change order. Until then both edit freely, so a sum typed before the
    bid (or a contingency set after the build) is just an edit.

    Only an approved owner change order counts: a draft or pending one, a
    potential change order and a sub change order do not move the sum, and a
    void puts it back. Billing counts from the first pay application that is
    not void: a draft already carries this period's amounts against the
    schedule."""
    from app.models.change_orders import ChangeOrder
    from app.models.pay_applications import PayApplication

    live_co = db.query(ChangeOrder.id).filter(
        ChangeOrder.job_id == job.id,
        ChangeOrder.kind == "owner",
        ChangeOrder.status == "approved",
    )
    billing = db.query(PayApplication.id).filter(
        PayApplication.job_id == job.id, PayApplication.status != "void"
    )
    return bool(
        db.query(live_co.exists()).scalar() or db.query(billing.exists()).scalar()
    )


def _utc(ts):
    """A stored timestamp as an aware UTC one (a naive one is UTC here)."""
    from datetime import timezone

    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def approved_contract_changes(db: Session, job: Job, through=None) -> Decimal:
    """Σ sell of the job's approved, contract-funded owner change orders — the
    "net change by change orders" (#723). With `through` (a timestamp, such as
    a pay application's approval) only those approved on or before it count,
    so an application reads the change orders as they stood when it was
    approved."""
    from app.models.change_orders import ChangeOrder
    from app.services.change_orders import co_totals

    cos = (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id == job.id,
            ChangeOrder.kind == "owner",
            ChangeOrder.status == "approved",
            ChangeOrder.funding == "contract",
        )
        .all()
    )
    total = Decimal("0")
    for co in cos:
        if through is not None and (
            co.approved_at is None or _utc(co.approved_at) > _utc(through)
        ):
            continue
        total += co_totals(co, job)["sell_total"]
    return _q(total)


def original_contract_sum(db: Session, job: Job) -> Optional[Decimal]:
    """The original contract sum: the stamped original when there is one, else
    the contract sum less the approved change orders (#723). A sum typed on
    the job form with no contract type saved was never stamped, and reading
    the missing original as zero printed the whole contract as a change. None
    only when the job has no contract sum at all."""
    if job.original_contract_amount is not None:
        return _q(job.original_contract_amount)
    if job.contract_amount is None:
        return None
    return _q(_q(job.contract_amount) - approved_contract_changes(db, job))


def application_contract_sums(db: Session, app, job: Optional[Job]) -> dict:
    """The contract rows of a pay application's header (#723): the original
    contract sum, the net change by change orders and the contract sum to
    date, which always foot (original + net change = to date).

    An approved application reads the sums it snapshotted at approval, so a
    reprint does not pick up change orders approved later. A snapshot with no
    original (approved while the job had no original stamped) reads the net
    change as the change orders approved on or before the application's
    approval, and the original as the sum less them. A draft reads the job as
    it stands."""
    zero = Decimal("0")
    if app.contract_sum_to_date is not None:
        to_date = _q(app.contract_sum_to_date)
        if app.contract_sum_original is not None:
            original = _q(app.contract_sum_original)
            net = _q(to_date - original)
        else:
            net = (
                approved_contract_changes(db, job, through=app.approved_at)
                if job is not None
                else zero
            )
            original = _q(to_date - net)
        return {"original": original, "net_change": net, "to_date": to_date}
    if job is None or job.contract_amount is None:
        return {"original": zero, "net_change": zero, "to_date": zero}
    to_date = _q(job.contract_amount)
    original = original_contract_sum(db, job)
    return {
        "original": original,
        "net_change": _q(to_date - original),
        "to_date": to_date,
    }


def settle_contract_type(db: Session, job: Job) -> None:
    """A contract sum becomes billable once the job has a schedule of values:
    with no type saved it is taken as a lump sum, and its original is stamped,
    as building the schedule from an estimate already does (#723). Called when
    the schedule is saved and when a pay application is made or approved; a
    job with no sum, or with a type, is left alone."""
    if job.contract_amount is None:
        return
    if job.contract_type is None:
        job.contract_type = "lump_sum"
    if job.original_contract_amount is None:
        job.original_contract_amount = original_contract_sum(db, job)


def _taxable(line) -> bool:
    """An estimate line is taxable unless it says it is not (the estimate's
    own tax rule reads it the same way)."""
    return getattr(line, "is_taxable", None) is not False


def package_section_id(package, line_codes) -> Optional[int]:
    """The cost code (section) a package's SOV line carries: the package's
    one mapped section, else its lowest; with no section mapped, the code
    every one of its estimate lines shares, else none. `line_codes` is the
    set of the lines' cost code ids (None for a line with no code)."""
    sections = (
        [m.cost_code for m in package.sections if m.cost_code is not None]
        if package is not None
        else []
    )
    if sections:
        return min(sections, key=lambda c: c.code or "").id
    if len(line_codes) == 1:
        return next(iter(line_codes))
    return None


def sov_from_estimate(estimate: Estimate) -> list[dict]:
    """The SOV lines an accepted estimate produces, in print order:

    - one `work` line per trade package — the sum of that package's base and
      accepted-alternate line PRICES, now AT COST (the lines carry no markup),
      packages named first then "Unassigned". A package whose lines are
      partly taxable is two lines, the taxable part and the part that is not
      ("Electrical (not taxable)"), so the tax billed follows the bid's;
    - one `allowance` line per allowance line (its own price);
    - one line per NON-ZERO markup, in order — general_conditions, insurance,
      fee, bond — each its own kind, named "General conditions (8%)" style.

    Each line carries its cost code (a work line its package's section, see
    package_section_id; an allowance its own line's code, else its
    package's) and whether it is taxable (a work or allowance line from its
    estimate lines; a markup line when any included line is, as the
    estimate taxes its markups).

    The work lines plus the allowance lines sum to the estimate's subtotal, and
    the markup lines add markup_total, so the whole set foots exactly to the
    estimate's pre-tax bid_total. The contingency line is added by build_sov,
    which knows the job. Returns plain dicts (no line_no / item_no yet)."""
    work: dict = {}  # package_id -> {"name", "value", "parts", "codes", "package"}
    allowances: list[dict] = []
    any_taxable = False
    for line in included_lines(estimate):
        kind = line.line_kind or "base"
        any_taxable = any_taxable or _taxable(line)
        if kind == "allowance":
            allowances.append(
                {
                    "item_no": None,
                    "description": line.description or "Allowance",
                    "trade_package_id": line.trade_package_id,
                    "cost_code_id": line.cost_code_id
                    or package_section_id(line.trade_package, set()),
                    "is_taxable": _taxable(line),
                    "kind": "allowance",
                    "scheduled_value": line_price(line),
                    "source_estimate_id": estimate.id,
                }
            )
            continue
        # a base line or an accepted alternate — work scope, grouped by package
        key = line.trade_package_id
        bucket = work.get(key)
        if bucket is None:
            bucket = {
                "name": (
                    line.trade_package.name
                    if line.trade_package is not None
                    else "Unassigned"
                ),
                "value": Decimal("0"),
                "parts": {},  # taxable flag -> value
                "codes": set(),
                "package": line.trade_package,
            }
            work[key] = bucket
        # an accepted deduct alternate carries a negative price; it nets in here
        bucket["value"] += line_price(line)
        flag = _taxable(line)
        bucket["parts"][flag] = bucket["parts"].get(flag, Decimal("0")) + line_price(
            line
        )
        bucket["codes"].add(line.cost_code_id)

    # Named packages by name, Unassigned (None key) last — the estimate's order.
    ordered = sorted(
        work.items(),
        key=lambda kv: (kv[0] is None, kv[1]["name"].lower()),
    )
    lines = []
    for pkg_id, bucket in ordered:
        code_id = package_section_id(bucket["package"], bucket["codes"])
        split = len(bucket["parts"]) > 1
        for flag in sorted(bucket["parts"], reverse=True):  # taxable part first
            lines.append(
                {
                    "item_no": None,
                    "description": (
                        f"{bucket['name']} (not taxable)"
                        if split and not flag
                        else bucket["name"]
                    ),
                    "trade_package_id": pkg_id,
                    "cost_code_id": code_id,
                    "is_taxable": flag,
                    "kind": "work",
                    "scheduled_value": bucket["parts"][flag],
                    "source_estimate_id": estimate.id,
                }
            )
    lines.extend(allowances)

    # One line per non-zero markup, in compounding order, each its own kind.
    # Work + allowance lines foot to the subtotal; these add markup_total, so
    # the full set foots to the estimate's pre-tax bid_total.
    totals = estimate_totals(estimate)
    markup_specs = (
        ("general_conditions", "General conditions", totals["gc_pct"], totals["gc"]),
        ("insurance", "Insurance", totals["insurance_pct"], totals["insurance"]),
        ("fee", "Fee", totals["fee_pct"], totals["fee"]),
        ("bond", "Bond", totals["bond_pct"], totals["bond"]),
    )
    for kind, label, pct, amount in markup_specs:
        if not amount:
            continue
        lines.append(
            {
                "item_no": None,
                "description": f"{label} ({fmt_pct(pct)}%)",
                "trade_package_id": None,
                "cost_code_id": None,
                "is_taxable": any_taxable,
                "kind": kind,
                "scheduled_value": amount,
                "source_estimate_id": estimate.id,
            }
        )
    return lines


def _numbered(lines: list[dict]) -> list[dict]:
    """Stamp 1-based line_no and a zero-padded item_no where blank."""
    for n, line in enumerate(lines, start=1):
        line["line_no"] = n
        if not line.get("item_no"):
            line["item_no"] = f"{n:02d}"
    return lines


def build_sov(db: Session, job: Job, estimate: Estimate, replace: bool = True) -> list:
    """Write the SOV from an accepted estimate. 409 if the estimate isn't
    this job's or isn't accepted. Replaces any existing SOV lines by
    default."""
    if estimate.status != EstimateStatus.ACCEPTED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "estimate_not_accepted",
                "message": "Build the schedule of values from an accepted estimate.",
            },
        )
    if estimate.job_id != job.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "estimate_job_mismatch",
                "message": "That estimate is not on this job.",
            },
        )
    # #555: rebuilding the schedule would orphan what has been billed against
    # it, so an approved pay application blocks a replace.
    if replace and _has_approved_application(db, job.id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "sov_has_billings",
                "message": "A pay application has billed this schedule; it can no longer be rebuilt.",
            },
        )

    lines = sov_from_estimate(estimate)
    # The owner contingency carried inside the contract is its own SOV line.
    if job.contingency_amount is not None:
        lines.append(
            {
                "item_no": None,
                "description": "Contingency",
                "trade_package_id": None,
                "cost_code_id": None,
                # taxed like the markups: when the bid taxed anything
                "is_taxable": any(ln["is_taxable"] for ln in lines),
                "kind": "contingency",
                "scheduled_value": _q(job.contingency_amount),
                "source_estimate_id": estimate.id,
            }
        )
    _numbered(lines)
    if replace:
        # TODO (#555): refuse 409 sov_has_billings once a pay application has
        # billed these lines — replacing a billed schedule would orphan the
        # pay history. Nothing bills the SOV yet, so a plain replace is safe.
        db.query(SovLine).filter(SovLine.job_id == job.id).delete()
        db.flush()
    # Every built line credits 4050 Contract Revenue until it is changed.
    revenue_id = get_contract_revenue_account_id(db)
    rows = []
    for line in lines:
        row = SovLine(job_id=job.id, revenue_account_id=revenue_id, **line)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def schedule_is_taxed(job: Job) -> bool:
    """Whether a line added to the schedule (a contingency or change-order
    line) is taxable: when any line already on it is, or the schedule is
    empty. A job whose bid taxed nothing bills no tax on its changes."""
    lines = list(job.sov_lines)
    return not lines or any(ln.is_taxable for ln in lines)


def change_order_sov_fields(job: Job, co) -> dict:
    """The cost code and taxable flag of the SOV line an owner change order
    appends: the code its lines share, else the lowest of them (none when
    no line has one), taxed as the rest of the schedule is."""
    codes = [ln.cost_code for ln in co.lines if ln.cost_code is not None]
    code = min(codes, key=lambda c: c.code or "") if codes else None
    return {
        "cost_code_id": code.id if code is not None else None,
        "is_taxable": schedule_is_taxed(job),
    }


def sync_contingency_line(db: Session, job: Job) -> None:
    """Keep the SOV's contingency line in step with the contract term: update
    its value, create it when a contingency is set and there is none, remove
    it when the contingency is cleared. Renumbers the schedule afterwards."""
    existing = [ln for ln in job.sov_lines if ln.kind == "contingency"]
    if job.contingency_amount is None:
        for ln in existing:
            db.delete(ln)
        remaining = [ln for ln in job.sov_lines if ln.kind != "contingency"]
    else:
        value = _q(job.contingency_amount)
        if existing:
            existing[0].scheduled_value = value
            for extra in existing[1:]:  # never keep more than one
                db.delete(extra)
            remaining = [ln for ln in job.sov_lines if ln.kind != "contingency"]
            remaining.append(existing[0])
        else:
            next_no = (max((ln.line_no for ln in job.sov_lines), default=0)) + 1
            row = SovLine(
                job_id=job.id,
                line_no=next_no,
                item_no=f"{next_no:02d}",
                description="Contingency",
                kind="contingency",
                scheduled_value=value,
                is_taxable=schedule_is_taxed(job),
            )
            db.add(row)
            remaining = list(job.sov_lines) + [row]
    db.flush()
    # Renumber 1..n in current order so a removal leaves no gap.
    for n, ln in enumerate(sorted(remaining, key=lambda x: x.line_no), start=1):
        ln.line_no = n
    db.flush()


def _has_approved_application(db: Session, job_id: int) -> bool:
    from app.models.pay_applications import PayApplication

    return (
        db.query(PayApplication.id)
        .filter(PayApplication.job_id == job_id, PayApplication.status == "approved")
        .first()
        is not None
    )


def contract_summary(
    db: Session,
    job: Job,
    cost: Optional[Decimal] = None,
    billed: Optional[Decimal] = None,
    as_of=None,
) -> dict:
    """The contract roll-up for one job. `cost` (actual job-to-date cost) and
    `billed` (posted revenue carrying the job) may be passed in by a caller
    that already has them — the job detail hands over the profitability
    figures it computed, so a plain job fetch costs no extra query. When they
    are None the cost tree supplies them, so the Contract tab always agrees
    with the rest of the job page.

    `as_of` scopes the billing and retainage to the approved applications dated
    on or before that day (the WIP schedule reads the books as of a date); with
    the default None they are read to date. The caller passes `billed` already
    scoped to the same date when it wants a clean as-of figure."""
    if cost is None or billed is None:
        from app.services.job_costing import job_cost_tree

        totals = job_cost_tree(db, job.id)["totals"]
        cost = Decimal(str(totals["actual"])) if cost is None else cost
        billed = Decimal(str(totals["act_revenue"])) if billed is None else billed
    cost = Decimal(str(cost))
    billed = Decimal(str(billed))

    ctype = job.contract_type
    amount = job.contract_amount
    original = job.original_contract_amount
    # Changes is the sum less the original; with no original stamped (a sum
    # from the job form, no type saved) the original is the sum less the
    # approved change orders, so Changes is those change orders (#723).
    figured_original = original_contract_sum(db, job)
    changes = (
        _q(amount - figured_original)
        if amount is not None and figured_original is not None
        else Decimal("0")
    )

    # Fee: a fixed fee wins; otherwise a percent of actual cost for cost-plus
    # and GMP; lump sum earns no separate fee.
    if ctype in ("cost_plus", "gmp") and job.fee_amount is not None:
        fee = _q(job.fee_amount)
    elif ctype in ("cost_plus", "gmp") and job.fee_pct is not None:
        fee = _q(cost * Decimal(str(job.fee_pct)) / Decimal("100"))
    else:
        fee = Decimal("0")

    cap = job.cap_amount
    cap_remaining = _q(cap - (cost + fee)) if cap is not None else None

    from app.services.change_orders import (
        allowances_summary,
        change_orders_summary,
        contingency_used as co_contingency_used,
    )

    contingency = job.contingency_amount
    # Σ sell_total of approved contingency-funded owner change orders (#554).
    contingency_used = co_contingency_used(db, job)
    contingency_remaining = (
        _q(contingency - contingency_used) if contingency is not None else None
    )

    sov_total = sum(
        (Decimal(str(r.scheduled_value)) for r in job.sov_lines), Decimal("0")
    )
    sov_balance = _q((amount or Decimal("0")) - sov_total)

    # Progress billing (#555): billed-to-date switches to Σ approved
    # applications' completed_to_date once the job has any application;
    # otherwise the posted revenue figure above stands. Deposits and
    # owner-side retainage ride alongside.
    from app.services.pay_applications import (
        billed_to_date as pa_billed_to_date,
        deposits_figures,
        has_approved_application,
        retainage_held_to_date,
        retainage_released_to_date,
    )

    # Only an approved application moves billed-to-date (a draft leaves the
    # posted revenue figure standing); as of a date, one dated that day or
    # earlier.
    if has_approved_application(db, job.id, as_of):
        billed = pa_billed_to_date(db, job.id, as_of)
    deposits = deposits_figures(db, job.id)
    ret_held = retainage_held_to_date(db, job.id, as_of)
    ret_released = retainage_released_to_date(db, job.id, as_of)
    billed_pct = float(billed / amount * 100) if amount else None

    def f(v):
        return float(v) if v is not None else None

    return {
        "contract_type": ctype,
        "contract_amount": f(amount),
        "original_contract_amount": f(original),
        "changes": float(changes),
        "fee": float(fee),
        "fee_pct": f(job.fee_pct),
        "fee_amount": f(job.fee_amount),
        "cost_to_date": float(cost),
        "cap_amount": f(cap),
        "cap_remaining": f(cap_remaining),
        "contingency_amount": f(contingency),
        "contingency_used": float(contingency_used),
        "contingency_remaining": f(contingency_remaining),
        "retainage_pct": f(job.retainage_pct),
        "contract_date": job.contract_date,
        "contract_estimate_id": job.contract_estimate_id,
        "co_markup_pct": f(job.co_markup_pct),
        "sov_total": float(sov_total),
        "sov_balance": float(sov_balance),
        "billed_to_date": float(billed),
        "billed_pct": billed_pct,
        # Change orders (#554): the allowance draw-down per line and the CO
        # counts/total the Contract tab's Changes tile reads.
        "allowances": allowances_summary(db, job),
        "change_orders": change_orders_summary(db, job),
        # Progress billing (#555): owner deposits and the owner-side retainage.
        "deposits": {
            "received": float(deposits["received"]),
            "applied": float(deposits["applied"]),
            "unapplied": float(deposits["unapplied"]),
        },
        "retainage": {
            "held_to_date": float(ret_held),
            "released_to_date": float(ret_released),
            "outstanding": float(_q(ret_held - ret_released)),
        },
    }
