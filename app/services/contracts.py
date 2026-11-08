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

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.estimates import Estimate, EstimateStatus
from app.models.jobs import Job, SovLine
from app.services.accounting import _q, get_contract_revenue_account_id
from app.services.estimating import (
    package_order_key,
    included_lines,
    line_price,
    markup_parts,
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


def _contract_cos(db: Session, job: Job) -> list:
    """The job's approved, contract-funded owner change orders."""
    from app.models.change_orders import ChangeOrder

    return (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id == job.id,
            ChangeOrder.kind == "owner",
            ChangeOrder.status == "approved",
            ChangeOrder.funding == "contract",
        )
        .all()
    )


def approved_contract_changes(db: Session, job: Job, through=None) -> Decimal:
    """Σ sell of the job's approved, contract-funded owner change orders — the
    "net change by change orders" (#723). With `through` (a timestamp, such as
    a pay application's approval) only those whose approve click came on or
    before it count: the way to read an application approved before it kept
    its original, whose sums were never snapshotted by date."""
    from app.services.change_orders import co_totals

    total = Decimal("0")
    for co in _contract_cos(db, job):
        if through is not None and (
            co.approved_at is None or _utc(co.approved_at) > _utc(through)
        ):
            continue
        total += co_totals(co, job)["sell_total"]
    return _q(total)


def contract_changes_after(db: Session, job: Job, on) -> Decimal:
    """Σ sell of the approved, contract-funded owner change orders that take
    effect after `on` — what to back out of the job's contract sum to read it
    as it stood that day. A pay application dated before a change order's
    approval does not carry it in its contract sum to date.

    A change order voided since `on` is left out too, unlike contract_on for
    reports: the void removed its schedule lines (and refuses one that was
    billed), so a pay application's header must leave it out to foot with
    its schedule."""
    from app.services.change_orders import co_effective_date, co_totals

    total = Decimal("0")
    for co in _contract_cos(db, job):
        effective = co_effective_date(co)
        if effective is not None and effective > on:
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
    it stands, less the change orders that take effect after its date."""
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
    # A draft reads the job as it stands, less the change orders that take
    # effect after the application's own date.
    to_date = _q(
        _q(job.contract_amount)
        - contract_changes_after(db, job, app.date or date.today())
    )
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


def contractor_pays_tax(job) -> bool:
    """True when the job's contract says the contractor pays the tax on its
    materials (the lump-sum real-property rule): its pay applications bill
    the owner no sales tax, and its material bills accrue use tax."""
    return job is not None and job.sales_tax_treatment == "contractor_pays"


def _taxable(line) -> bool:
    """An estimate line is taxable unless it says it is not (the estimate's
    own tax rule reads it the same way)."""
    return getattr(line, "is_taxable", None) is not False


def _package_section(package):
    """A package's lowest mapped section (cost code), or None."""
    sections = (
        [m.cost_code for m in package.sections if m.cost_code is not None]
        if package is not None
        else []
    )
    return min(sections, key=lambda c: c.code or "") if sections else None


def _line_section(line):
    """The section an estimate line's SOV line carries: the line's own cost
    code (where Accept budgets it), else its package's lowest section."""
    return line.cost_code if line.cost_code_id else _package_section(line.trade_package)


def _section_key(code) -> tuple:
    """Sections in code order within a package; no section last."""
    return (1, "") if code is None else (0, code.code or "")


def sov_from_estimate(estimate: Estimate) -> list[dict]:
    """The SOV lines an accepted estimate produces, in print order:

    - per trade package, one `work` line per section its base and
      accepted-alternate lines carry — the sum of those lines' PRICES, AT
      COST (the lines carry no markup). Packages in bid-book order, sections
      in code order. A package on one section is one line under the
      package's name; one on several reads "Concrete — 03 30 00 Cast-in-Place
      Concrete" per section. A section whose lines are partly taxable is two
      lines, the taxable part and the part that is not ("Electrical (not
      taxable)"), so the tax billed follows the bid's;
    - one `work` line per estimate line with no package, under the line's
      own description (an accepted deduct alternate reads as its own text);
    - one `allowance` line per allowance line (its own price);
    - one line per NON-ZERO markup, in order — general_conditions, insurance,
      fee, bond — each its own kind, named "General conditions (8%)" style.
      On a bid whose markup base is partly taxable each markup is two lines,
      the taxable part and "General conditions (8%) (not taxable)"
      (estimating.markup_parts), so a pay application at 100% bills exactly
      the bid's tax.

    Each line carries its cost code and whether it is taxable. A work line's
    code is the one its estimate lines carry, so its revenue lands on the
    section its budget and cost are on; a line with no code takes its
    package's lowest section. An allowance takes its own line's code, else
    its package's. A work or allowance line is taxable from its estimate
    lines; a markup line by the taxable share of the markup base, as the
    estimate taxes its markups.

    The work lines plus the allowance lines sum to the estimate's subtotal, and
    the markup lines add markup_total, so the whole set foots exactly to the
    estimate's pre-tax bid_total. The contingency line is added by build_sov,
    which knows the job. Returns plain dicts (no line_no / item_no yet)."""
    # package_id -> {"package", "sections": {section_id: {"code", "parts"}}}
    packages: dict = {}
    loose: list[dict] = []  # lines with no package, one SOV line each
    allowances: list[dict] = []
    for line in included_lines(estimate):
        kind = line.line_kind or "base"
        if kind == "allowance":
            code = _line_section(line)
            allowances.append(
                {
                    "item_no": None,
                    "description": line.description or "Allowance",
                    "trade_package_id": line.trade_package_id,
                    "cost_code_id": code.id if code is not None else None,
                    "is_taxable": _taxable(line),
                    "kind": "allowance",
                    "scheduled_value": line_price(line),
                    "source_estimate_id": estimate.id,
                }
            )
            continue
        # a base line or an accepted alternate — work scope
        code = _line_section(line)
        if line.trade_package_id is None:
            loose.append(
                {
                    "item_no": None,
                    "description": (
                        line.description or (code.label if code else "Work")
                    )[:200],
                    "trade_package_id": None,
                    "cost_code_id": code.id if code is not None else None,
                    "is_taxable": _taxable(line),
                    "kind": "work",
                    "scheduled_value": line_price(line),
                    "source_estimate_id": estimate.id,
                }
            )
            continue
        bucket = packages.setdefault(
            line.trade_package_id, {"package": line.trade_package, "sections": {}}
        )
        section = bucket["sections"].setdefault(
            code.id if code is not None else None, {"code": code, "parts": {}}
        )
        # an accepted deduct alternate carries a negative price; it nets in
        # here, inside its own section
        flag = _taxable(line)
        section["parts"][flag] = section["parts"].get(flag, Decimal("0")) + line_price(
            line
        )

    # Packages in bid-book order (lowest section code, then name) — the
    # estimate's order — then the lines with no package, then allowances.
    ordered = sorted(
        packages.items(), key=lambda kv: package_order_key(kv[1]["package"])
    )
    lines = []
    for pkg_id, bucket in ordered:
        name = bucket["package"].name if bucket["package"] is not None else ""
        sections = sorted(
            bucket["sections"].values(), key=lambda sec: _section_key(sec["code"])
        )
        for sec in sections:
            label = name
            if len(sections) > 1 and sec["code"] is not None:
                label = f"{name} — {sec['code'].label}"
            split = len(sec["parts"]) > 1
            for flag in sorted(sec["parts"], reverse=True):  # taxable part first
                lines.append(
                    {
                        "item_no": None,
                        "description": (
                            f"{label} (not taxable)" if split and not flag else label
                        )[:200],
                        "trade_package_id": pkg_id,
                        "cost_code_id": (
                            sec["code"].id if sec["code"] is not None else None
                        ),
                        "is_taxable": flag,
                        "kind": "work",
                        "scheduled_value": sec["parts"][flag],
                        "source_estimate_id": estimate.id,
                    }
                )
    lines.extend(loose)
    lines.extend(allowances)

    # One line per non-zero markup, in compounding order, each its own kind,
    # split into its taxable part and the rest. Work + allowance lines foot to
    # the subtotal; these add markup_total, so the full set foots to the
    # estimate's pre-tax bid_total.
    for part in markup_parts(estimate):
        lines.append(
            {
                "item_no": None,
                "description": part["description"],
                "trade_package_id": None,
                "cost_code_id": None,
                "is_taxable": part["is_taxable"],
                "kind": part["kind"],
                "scheduled_value": part["amount"],
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


def _unanimous(lines):
    """The taxable flag every line in `lines` shares, else None (none, or
    they differ)."""
    flags = {bool(ln.is_taxable) for ln in lines}
    return flags.pop() if len(flags) == 1 else None


def next_item_no(job: Job, line_no: int) -> str:
    """The item number a line appended to the schedule prints: one past the
    highest numeric item number on it (zero-padded, as the build stamps
    them), else its line number."""
    numbers = [int(ln.item_no) for ln in job.sov_lines if (ln.item_no or "").isdigit()]
    return f"{max(numbers, default=line_no - 1) + 1:02d}"


def change_order_sov_fields(db: Session, job: Job, co) -> dict:
    """The cost code, taxable flag, revenue account and item number of the
    SOV line an owner change order appends.

    The code is the one its lines share, else the lowest of them (none when
    no line has one). The line extends the schedule's line for that section,
    so it takes that line's revenue account (else 4050 Contract Revenue) and
    its Tax box: the section's lines' flag when they agree, else the flag of
    the lines of the change order's package, else the schedule's — taxed
    when every line on it is (or it is empty), not taxed when it is mixed or
    taxes nothing, so a change on exempt work is never taxed by default."""
    codes = [ln.cost_code for ln in co.lines if ln.cost_code is not None]
    code = min(codes, key=lambda c: c.code or "") if codes else None
    base = [ln for ln in job.sov_lines if ln.kind != "change_order"]
    section = (
        [ln for ln in base if ln.cost_code_id == code.id] if code is not None else []
    )
    packages = {ln.trade_package_id for ln in co.lines if ln.trade_package_id}
    taxable = _unanimous(section)
    if taxable is None:
        taxable = _unanimous([ln for ln in base if ln.trade_package_id in packages])
    if taxable is None:
        lines = list(job.sov_lines)
        taxable = all(ln.is_taxable for ln in lines)
    revenue = next(
        (ln.revenue_account_id for ln in section if ln.revenue_account_id), None
    ) or get_contract_revenue_account_id(db)
    line_no = max((ln.line_no for ln in job.sov_lines), default=0) + 1
    return {
        "cost_code_id": code.id if code is not None else None,
        "is_taxable": taxable,
        "revenue_account_id": revenue,
        "item_no": next_item_no(job, line_no),
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
        "sales_tax_treatment": job.sales_tax_treatment or "charge_owner",
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
