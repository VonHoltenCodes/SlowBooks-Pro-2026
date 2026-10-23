# ============================================================================
# Classes — CRUD for the QB-style tracking dimension.
#
# The system-default "Uncategorized" row is immutable: no rename, no
# archive, no delete — by-class reports rely on it always existing as the
# bucket for untagged activity. Classes referenced by any document or
# transaction can be archived (hidden from dropdowns) but never deleted,
# so historical reports stay stable.
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.classes import TxnClass
from app.models.transactions import Transaction, TransactionLine
from app.schemas.classes import ClassCreate, ClassResponse, ClassUpdate
from app.services.classes_service import class_attribution, uncategorized_class_id
from app.services.fiscal_year import fiscal_year_start

router = APIRouter(prefix="/api/classes", tags=["classes"])


def _class_or_404(db: Session, class_id: int) -> TxnClass:
    row = db.get(TxnClass, class_id)
    if not row:
        raise HTTPException(status_code=404, detail="Class not found")
    return row


def _class_dict(c: TxnClass) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "is_archived": bool(c.is_archived),
        "is_system_default": bool(c.is_system_default),
        "restriction": c.restriction,
        "default_function": c.default_function,
        "donor_name": c.donor_name,
        "purpose": c.purpose,
    }


def _dates(db, start_date, end_date):
    # the report defaults: this fiscal year to today (#444)
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    return start_date, end_date


@router.get("", response_model=list[ClassResponse])
def list_classes(include_archived: bool = False, db: Session = Depends(get_db)):
    # Ensure the default row exists before the first listing renders.
    uncategorized_class_id(db)
    db.commit()
    q = db.query(TxnClass)
    if not include_archived:
        q = q.filter(TxnClass.is_archived.is_(False))
    # System default first, then alphabetical — matches dropdown order.
    return q.order_by(TxnClass.is_system_default.desc(), TxnClass.name).all()


# Every class with its income, cost of goods, expenses and net for a period
# (#234): the Classes list page. Built on P&L by Class, so each row is that
# report's column and the rows together are the plain Profit & Loss; a
# class with no activity in the period is a row of zeros, archived ones
# included on request (they keep their history). `totals` is the sum of
# the rows returned, `company` the whole P&L for the dates — a list the
# page filters (Active only) says which it is showing.
@router.get("/activity")
def classes_activity(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    from app.routes.reports.financial import profit_loss_by_class

    start_date, end_date = _dates(db, start_date, end_date)
    uncategorized_class_id(db)
    db.commit()
    report = profit_loss_by_class(start_date, end_date, db)
    by_id = {c["class_id"]: c for c in report["classes"]}
    keys = ("income", "cogs", "gross_profit", "expenses", "net_income")
    rows = []
    known = set()
    for c in db.query(TxnClass).order_by(
        TxnClass.is_system_default.desc(), TxnClass.name
    ):
        known.add(c.id)
        if c.is_archived and not include_archived:
            continue
        figures = by_id.get(c.id) or {k: 0.0 for k in keys}
        rows.append({**_class_dict(c), **{k: float(figures[k]) for k in keys}})
    # a class deleted since it was used would be a column with no row: kept
    # as "Unknown", so the rows still add up to the P&L
    for cid, figures in by_id.items():
        if cid not in known:
            rows.append(
                {
                    "id": cid,
                    "name": figures["class_name"],
                    "is_archived": False,
                    "is_system_default": False,
                    **{k: float(figures[k]) for k in keys},
                }
            )
    totals = {k: float(sum(Decimal(str(r[k])) for r in rows)) for k in keys}
    company = {
        "income": float(report["total_income"]),
        "cogs": float(report["total_cogs"]),
        "gross_profit": float(report["total_gross_profit"]),
        "expenses": float(report["total_expenses"]),
        "net_income": float(report["total_net_income"]),
    }
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "include_archived": include_archived,
        "classes": rows,
        "totals": {**totals, "count": len(rows)},
        "company": company,
    }


@router.get("/{class_id}", response_model=ClassResponse)
def get_class(class_id: int, db: Session = Depends(get_db)):
    return _class_or_404(db, class_id)


# The class's own P&L for a period (#234): the figures the by-class column
# shows, account by account, with the company's net income beside it so the
# page can say what share of the whole this class is. The same query as
# /api/reports/profit-loss?class_id=, so the two always agree.
@router.get("/{class_id}/summary")
def class_summary(
    class_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.routes.reports.financial import profit_loss

    cls = _class_or_404(db, class_id)
    start_date, end_date = _dates(db, start_date, end_date)
    mine = profit_loss(start_date, end_date, db, class_id=class_id)
    company = profit_loss(start_date, end_date, db)
    out = {k: v for k, v in mine.items() if k not in ("class_id", "class_name")}
    out["class"] = _class_dict(cls)
    out["company_net_income"] = company["net_income"]
    out["company_total_income"] = company["total_income"]
    out["company_total_expenses"] = company["total_expenses"]
    return out


# Every posted line attributed to the class, across every account (#234):
# the QuickReport. A line belongs to its own class, else its transaction's,
# else Uncategorized, as every by-class report groups them. Each line
# carries the document it came from (source_link, as the register does);
# `net_income` is the P&L lines' natural total, which equals the class's
# net on P&L by Class for the same dates. Not the register service: that
# computes one account's running balance, which means nothing across
# accounts (R4 review).
@router.get("/{class_id}/transactions")
def class_transactions(
    class_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.bank_register import (
        references_for,
        source_link,
        voided_transaction_ids,
    )

    cls = _class_or_404(db, class_id)
    uncat_id = uncategorized_class_id(db)
    db.commit()
    q = (
        db.query(TransactionLine, Transaction, Account)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(class_attribution(uncat_id) == class_id)
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    rows = q.order_by(Transaction.date, Transaction.id, TransactionLine.id).all()

    txns = {txn.id: txn for _, txn, _ in rows}
    voided = voided_transaction_ids(db, txns.keys())
    refs = references_for(db, list(txns.values()))
    total_debit = total_credit = net = Decimal("0")
    entries = []
    for tl, txn, acct in rows:
        dr = Decimal(str(tl.debit or 0))
        cr = Decimal(str(tl.credit or 0))
        total_debit += dr
        total_credit += cr
        pl_amount = None
        if acct.account_type == AccountType.INCOME:
            pl_amount = cr - dr
            net += pl_amount
        elif acct.account_type in (AccountType.COGS, AccountType.EXPENSE):
            pl_amount = dr - cr
            net -= pl_amount
        entries.append(
            {
                "line_id": tl.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "source_type": txn.source_type or "journal",
                "source_id": txn.source_id,
                "source_link": source_link(txn),
                "reference": refs.get(txn.id, ""),
                "description": tl.description or txn.description or "",
                "account_id": acct.id,
                "account_number": acct.account_number,
                "account_name": acct.name,
                "account_type": acct.account_type.value,
                "debit": float(dr),
                "credit": float(cr),
                "pl_amount": None if pl_amount is None else float(pl_amount),
                "voided": txn.id in voided
                or bool((txn.source_type or "").endswith("_void")),
            }
        )
    return {
        "class": _class_dict(cls),
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "entries": entries,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "net_income": float(net),
    }


@router.post("", response_model=ClassResponse, status_code=201)
def create_class(data: ClassCreate, db: Session = Depends(get_db)):
    existing = db.query(TxnClass).filter(TxnClass.name.ilike(data.name)).first()
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Class '{existing.name}' already exists"
        )
    row = TxnClass(
        name=data.name,
        restriction=data.restriction,
        default_function=data.default_function,
        donor_name=data.donor_name,
        purpose=data.purpose,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/{class_id}", response_model=ClassResponse)
def update_class(class_id: int, data: ClassUpdate, db: Session = Depends(get_db)):
    row = db.get(TxnClass, class_id)
    if not row:
        raise HTTPException(status_code=404, detail="Class not found")
    if row.is_system_default and (
        data.name is not None or data.is_archived is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="The system default class cannot be renamed or archived",
        )
    if data.name is not None:
        clash = (
            db.query(TxnClass)
            .filter(TxnClass.name.ilike(data.name), TxnClass.id != class_id)
            .first()
        )
        if clash:
            raise HTTPException(
                status_code=409, detail=f"Class '{clash.name}' already exists"
            )
        row.name = data.name
    if data.is_archived is not None:
        row.is_archived = data.is_archived
    # Fund fields: the system default stays unrestricted (it is the bucket
    # for untagged activity) but may carry a default function.
    if data.restriction is not None:
        if row.is_system_default and data.restriction != "unrestricted":
            raise HTTPException(
                status_code=400, detail="The system default class is unrestricted"
            )
        row.restriction = data.restriction
    if "default_function" in data.model_fields_set:
        row.default_function = data.default_function
    if "donor_name" in data.model_fields_set:
        row.donor_name = data.donor_name
    if "purpose" in data.model_fields_set:
        row.purpose = data.purpose
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{class_id}")
def delete_class(class_id: int, db: Session = Depends(get_db)):
    row = db.get(TxnClass, class_id)
    if not row:
        raise HTTPException(status_code=404, detail="Class not found")
    if row.is_system_default:
        raise HTTPException(
            status_code=400, detail="The system default class cannot be deleted"
        )
    in_use = (
        db.query(Transaction).filter(Transaction.class_id == class_id).first()
        is not None
    )
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Class is used by posted transactions — archive it instead",
        )
    db.delete(row)
    db.commit()
    return {"message": "Class deleted"}
