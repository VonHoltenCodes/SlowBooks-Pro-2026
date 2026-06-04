#!/usr/bin/env python3
"""
Sub-cent drift detection and repair across stored invoice / bill / PO /
estimate rows. Use after deploying the rounding fixes if any pre-fix
records still have stored subtotal != sum(line.amount).

Usage:
    python3 scripts/repair_rounding_drift.py          # detect only (dry run)
    python3 scripts/repair_rounding_drift.py --apply  # write corrected totals
    python3 scripts/repair_rounding_drift.py --json   # machine-readable output

What gets fixed (header columns only):
    invoice/bill/PO/estimate.subtotal      -> sum(line.amount) over stored rows
    invoice/bill/PO/estimate.tax_amount    -> _q(subtotal * tax_rate)
    invoice/bill/PO/estimate.total         -> _q(subtotal + tax_amount)
    invoice/bill.balance_due               -> max(total - amount_paid, 0)

Line amounts themselves are NOT rewritten — they're whatever cents the
operator originally saw on the document, and overwriting them would change
historical line items. The fix realigns the header so reports reconcile.

Journal-entry drift (sum(debit) != sum(credit) on a stored transaction) is
REPORTED but never auto-rebuilt: that touches account balances and may
straddle a closing-date boundary. Operator review required.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.accounting import _q


@dataclass
class HeaderDrift:
    entity: str
    id: int
    identifier: str
    old_subtotal: str
    new_subtotal: str
    old_tax_amount: str
    new_tax_amount: str
    old_total: str
    new_total: str
    old_balance_due: str | None = None
    new_balance_due: str | None = None


@dataclass
class JEDrift:
    entity: str
    id: int
    identifier: str
    transaction_id: int
    debits: str
    credits: str


@dataclass
class Report:
    header_drift: list[HeaderDrift] = field(default_factory=list)
    journal_drift: list[JEDrift] = field(default_factory=list)
    scanned: dict[str, int] = field(default_factory=dict)


def _sum_line_amounts(lines) -> Decimal:
    return sum((Decimal(str(line.amount or 0)) for line in lines), Decimal("0"))


def _scan_entity(
    db: Session,
    report: Report,
    *,
    entity_name: str,
    model,
    identifier_attr: str,
    has_balance_due: bool,
):
    rows = db.query(model).all()
    report.scanned[entity_name] = len(rows)
    for row in rows:
        stored_subtotal = Decimal(str(row.subtotal or 0))
        stored_tax = Decimal(str(row.tax_amount or 0))
        stored_total = Decimal(str(row.total or 0))
        tax_rate = Decimal(str(row.tax_rate or 0))

        new_subtotal = _q(_sum_line_amounts(row.lines))
        new_tax = _q(new_subtotal * tax_rate)
        new_total = _q(new_subtotal + new_tax)

        drifted = (
            stored_subtotal != new_subtotal
            or stored_tax != new_tax
            or stored_total != new_total
        )

        new_balance_due = None
        old_balance_due = None
        if has_balance_due:
            old_balance_due = Decimal(str(row.balance_due or 0))
            amount_paid = Decimal(str(row.amount_paid or 0))
            # Clamp at 0: an over-correction shouldn't push balance_due negative
            new_balance_due = max(new_total - amount_paid, Decimal("0"))
            if old_balance_due != new_balance_due:
                drifted = True

        if drifted:
            report.header_drift.append(
                HeaderDrift(
                    entity=entity_name,
                    id=row.id,
                    identifier=str(getattr(row, identifier_attr, row.id)),
                    old_subtotal=str(stored_subtotal),
                    new_subtotal=str(new_subtotal),
                    old_tax_amount=str(stored_tax),
                    new_tax_amount=str(new_tax),
                    old_total=str(stored_total),
                    new_total=str(new_total),
                    old_balance_due=(
                        str(old_balance_due) if old_balance_due is not None else None
                    ),
                    new_balance_due=(
                        str(new_balance_due) if new_balance_due is not None else None
                    ),
                )
            )


def _scan_journal_drift(
    db: Session,
    report: Report,
    *,
    entity_name: str,
    model,
    identifier_attr: str,
):
    from app.models.transactions import TransactionLine

    rows = db.query(model).filter(model.transaction_id.isnot(None)).all()
    for row in rows:
        lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == row.transaction_id)
            .all()
        )
        if not lines:
            continue
        dr = sum((Decimal(str(ln.debit or 0)) for ln in lines), Decimal("0"))
        cr = sum((Decimal(str(ln.credit or 0)) for ln in lines), Decimal("0"))
        if dr != cr:
            report.journal_drift.append(
                JEDrift(
                    entity=entity_name,
                    id=row.id,
                    identifier=str(getattr(row, identifier_attr, row.id)),
                    transaction_id=row.transaction_id,
                    debits=str(dr),
                    credits=str(cr),
                )
            )


def detect(db: Session) -> Report:
    from app.models.invoices import Invoice
    from app.models.bills import Bill
    from app.models.estimates import Estimate
    from app.models.purchase_orders import PurchaseOrder

    report = Report()
    _scan_entity(
        db,
        report,
        entity_name="invoice",
        model=Invoice,
        identifier_attr="invoice_number",
        has_balance_due=True,
    )
    _scan_entity(
        db,
        report,
        entity_name="bill",
        model=Bill,
        identifier_attr="bill_number",
        has_balance_due=True,
    )
    _scan_entity(
        db,
        report,
        entity_name="estimate",
        model=Estimate,
        identifier_attr="estimate_number",
        has_balance_due=False,
    )
    _scan_entity(
        db,
        report,
        entity_name="purchase_order",
        model=PurchaseOrder,
        identifier_attr="po_number",
        has_balance_due=False,
    )

    _scan_journal_drift(
        db,
        report,
        entity_name="invoice",
        model=Invoice,
        identifier_attr="invoice_number",
    )
    _scan_journal_drift(
        db,
        report,
        entity_name="bill",
        model=Bill,
        identifier_attr="bill_number",
    )
    return report


def apply_repairs(db: Session, report: Report) -> int:
    """Apply header corrections from the report. Returns rows updated."""
    from app.models.invoices import Invoice
    from app.models.bills import Bill
    from app.models.estimates import Estimate
    from app.models.purchase_orders import PurchaseOrder

    model_by_entity = {
        "invoice": Invoice,
        "bill": Bill,
        "estimate": Estimate,
        "purchase_order": PurchaseOrder,
    }
    updated = 0
    for d in report.header_drift:
        model = model_by_entity[d.entity]
        row = db.query(model).filter(model.id == d.id).first()
        if not row:
            continue
        row.subtotal = Decimal(d.new_subtotal)
        row.tax_amount = Decimal(d.new_tax_amount)
        row.total = Decimal(d.new_total)
        if d.new_balance_due is not None:
            row.balance_due = Decimal(d.new_balance_due)
        updated += 1
    db.commit()
    return updated


def _format_text(report: Report) -> str:
    out: list[str] = []
    out.append("=== Rounding-drift detection report ===")
    for k, v in report.scanned.items():
        out.append(f"  scanned {v} {k}(s)")
    out.append("")
    out.append(f"Header-total drift: {len(report.header_drift)} row(s)")
    for d in report.header_drift:
        bd = ""
        if d.old_balance_due is not None:
            bd = f" balance_due {d.old_balance_due}->{d.new_balance_due}"
        out.append(
            f"  [{d.entity}#{d.id} {d.identifier}] "
            f"subtotal {d.old_subtotal}->{d.new_subtotal}  "
            f"tax {d.old_tax_amount}->{d.new_tax_amount}  "
            f"total {d.old_total}->{d.new_total}{bd}"
        )
    out.append("")
    out.append(
        f"Journal-entry drift: {len(report.journal_drift)} row(s) (NOT auto-repaired)"
    )
    for j in report.journal_drift:
        out.append(
            f"  [{j.entity}#{j.id} {j.identifier}] "
            f"txn {j.transaction_id} debits={j.debits} credits={j.credits}"
        )
    return "\n".join(out)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write corrected header totals. Without this flag, runs read-only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report instead of text.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    db = SessionLocal()
    try:
        report = detect(db)
        if args.apply:
            updated = apply_repairs(db, report)
            if args.json:
                print(
                    json.dumps(
                        {
                            "report": {
                                "header_drift": [
                                    asdict(d) for d in report.header_drift
                                ],
                                "journal_drift": [
                                    asdict(j) for j in report.journal_drift
                                ],
                                "scanned": report.scanned,
                            },
                            "applied": updated,
                        },
                        indent=2,
                    )
                )
            else:
                print(_format_text(report))
                print(f"\nApplied: updated {updated} row(s).")
        else:
            if args.json:
                print(
                    json.dumps(
                        {
                            "header_drift": [asdict(d) for d in report.header_drift],
                            "journal_drift": [asdict(j) for j in report.journal_drift],
                            "scanned": report.scanned,
                        },
                        indent=2,
                    )
                )
            else:
                print(_format_text(report))
                if report.header_drift or report.journal_drift:
                    print(
                        "\nDry run — re-run with --apply to write header corrections."
                    )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
