from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.models.contacts import Customer
from app.schemas.contacts import CustomerCreate, CustomerUpdate, CustomerResponse
from app.routes._helpers import get_or_404, reject_null_updates
from app.services.contact_balances import ZERO, customer_balances
from app.services.sub_customers import (
    check_parent,
    likely_duplicates,
    refuse_inactivating_a_parent,
    refuse_inactive_parent,
    refuse_unbilling_with_parent,
)

router = APIRouter(prefix="/api/customers", tags=["customers"])


def _responses(db: Session, customers: list[Customer]) -> list[CustomerResponse]:
    """The customers with what each one owes, summed from the open documents
    (the stored Customer.balance column is never written; see
    services/contact_balances). One set of grouped queries for the page.

    Sub-customers (#638): a sub's parent is read in one query for the page
    (so full_name costs no query per row), and a parent carries how many
    subs it has, the roll-up (its own balance plus every sub's) and, as
    every customer does, billed_balance: what sits on its A/R, its own
    balance plus that of the subs billed with it, the A/R Aging row's
    figure (2.31.1 gate, W-61)."""
    ids = [c.id for c in customers]
    balances = customer_balances(db, ids)
    parent_ids = {c.parent_customer_id for c in customers if c.parent_customer_id}
    if parent_ids:
        # into the identity map: c.parent then loads nothing
        db.query(Customer).filter(Customer.id.in_(parent_ids)).all()
    sub_ids: dict[int, list[int]] = defaultdict(list)
    billed_ids: dict[int, list[int]] = defaultdict(list)
    if ids:
        for sid, pid, billed in db.query(
            Customer.id, Customer.parent_customer_id, Customer.bill_with_parent
        ).filter(Customer.parent_customer_id.in_(ids)):
            sub_ids[pid].append(sid)
            if billed:
                billed_ids[pid].append(sid)
    sub_balances = customer_balances(
        db, [sid for subs in sub_ids.values() for sid in subs]
    )
    out = []
    for c in customers:
        resp = CustomerResponse.model_validate(c)
        resp.balance = balances.get(c.id, ZERO)
        resp.billed_balance = resp.balance + sum(
            (sub_balances.get(sid, ZERO) for sid in billed_ids.get(c.id, ())), ZERO
        )
        resp.full_name = c.full_name
        resp.parent_name = c.parent.name if c.parent_customer_id and c.parent else None
        resp.sub_count = len(sub_ids.get(c.id, ()))
        if resp.sub_count:
            resp.rollup_balance = resp.balance + sum(
                (sub_balances.get(sid, ZERO) for sid in sub_ids[c.id]), ZERO
            )
        out.append(resp)
    return out


def _tree_order(customers: list[Customer]) -> list[Customer]:
    """Parents in name order, each followed by its subs in name order. A
    sub whose parent is not in the list (filtered out, or inactive) sits
    where the parent would, under its "Parent: Sub" name."""

    def key(c: Customer):
        parent = c.parent if c.parent_customer_id else None
        head = (parent.name if parent else c.name).lower()
        return (head, 1 if parent else 0, c.name.lower(), c.id)

    return sorted(customers, key=key)


def _response(db: Session, customer: Customer) -> CustomerResponse:
    return _responses(db, [customer])[0]


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    active_only: bool = False,
    inactive_only: bool = False,
    search: str = None,
    tree: bool = False,
    db: Session = Depends(get_db),
):
    """``tree``: sub-customers under their parents (each parent in name
    order, then its subs in name order), the order the pickers and the
    Customer Center list them in. A search matches a customer's own name
    or its parent's, so a property's name finds its tenants (#638)."""
    q = db.query(Customer)
    if active_only:
        q = q.filter(Customer.is_active)
    if inactive_only:  # the list page's Inactive view (#210)
        q = q.filter(Customer.is_active.is_(False))
    if search:
        like = f"%{search}%"
        parent = aliased(Customer)
        q = q.outerjoin(parent, parent.id == Customer.parent_customer_id).filter(
            or_(Customer.name.ilike(like), parent.name.ilike(like))
        )
    rows = q.order_by(Customer.name).all()
    if tree:
        parent_ids = {c.parent_customer_id for c in rows if c.parent_customer_id}
        if parent_ids:
            db.query(Customer).filter(Customer.id.in_(parent_ids)).all()
        rows = _tree_order(rows)
    return _responses(db, rows)


@router.get("/check-duplicate")
def check_duplicate(
    name: str = Query(..., min_length=1),
    parent_customer_id: Optional[int] = Query(
        None,
        description="Checking a sub-customer's name: the same name among "
        "its siblings under this parent, and a likely match among the "
        "top-level customers (#638).",
    ),
    db: Session = Depends(get_db),
):
    """Phase 11: standalone duplicate-check endpoint the UI can call BEFORE
    submitting a create form to warn the user proactively."""
    return {"duplicates": likely_duplicates(db, name, parent_customer_id)}


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    return _response(db, get_or_404(db, Customer, customer_id))


@router.get("/{customer_id}/subs")
def customer_subs(customer_id: int, db: Session = Depends(get_db)):
    """A parent's sub-customers with each one's balance, and the roll-up
    (#638): what the customer's own documents leave open, what its subs'
    do, and the two together; and billed_balance, what sits on the
    parent's A/R (its own plus the subs billed with it: the A/R Aging
    row). Every sub is listed, inactive ones too (a tenant who moved out
    may still owe), each saying whether it is billed with the parent. A
    customer with no subs answers an empty list."""
    customer = get_or_404(db, Customer, customer_id)
    subs = (
        db.query(Customer)
        .filter(Customer.parent_customer_id == customer.id)
        .order_by(Customer.name)
        .all()
    )
    balances = customer_balances(db, [customer.id] + [s.id for s in subs])
    own = balances.get(customer.id, ZERO)
    rows = [
        {
            "id": s.id,
            "name": s.name,
            "full_name": f"{customer.name}: {s.name}",
            "is_active": bool(s.is_active),
            "bill_with_parent": bool(s.bill_with_parent),
            "balance": float(balances.get(s.id, ZERO)),
        }
        for s in subs
    ]
    subs_total = sum((balances.get(s.id, ZERO) for s in subs), ZERO)
    billed_total = sum(
        (balances.get(s.id, ZERO) for s in subs if s.bill_with_parent), ZERO
    )
    return {
        "customer_id": customer.id,
        "customer_name": customer.name,
        "parent_customer_id": customer.parent_customer_id,
        "parent_name": (
            customer.parent.name
            if customer.parent_customer_id and customer.parent
            else None
        ),
        "own_balance": float(own),
        "subs_balance": float(subs_total),
        "billed_balance": float(own + billed_total),
        "rollup_balance": float(own + subs_total),
        "subs": rows,
    }


@router.get("/{customer_id}/credits")
def customer_credits(customer_id: int, db: Session = Depends(get_db)):
    """Money the customer has with us that is not on an invoice yet — the
    unapplied part of their payments and credit memos not yet applied —
    each with what can still be applied, so Receive Payment and the
    customer page can offer to apply it (explore 2.17.3, F12 / W-H9: an
    overpayment or an unapplied payment could never be used afterwards).
    `total` is in home currency; each credit is in its own currency."""
    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.models.payments import Payment
    from app.services.contact_balances import unapplied_payments
    from app.services.currency import home_currency

    customer = get_or_404(db, Customer, customer_id)
    home = home_currency(db)
    rows = unapplied_payments(db, [customer_id])
    payments = (
        {
            p.id: p
            for p in db.query(Payment).filter(
                Payment.id.in_([r.payment_id for r in rows])
            )
        }
        if rows
        else {}
    )
    credits = []
    total = ZERO
    for r in rows:
        p = payments[r.payment_id]
        credits.append(
            {
                "kind": "payment",
                "id": r.payment_id,
                "date": r.date.isoformat(),
                "number": p.check_number or p.reference or "",
                "method": p.method or "",
                "currency": (r.currency or home).upper(),
                "amount": float(r.amount),
                "available": float(r.unapplied),
            }
        )
        total += r.unapplied_home
    memos = (
        db.query(CreditMemo)
        .filter(
            CreditMemo.customer_id == customer_id,
            CreditMemo.status != CreditMemoStatus.VOID,
            CreditMemo.balance_remaining > 0,
        )
        .all()
    )
    for m in memos:
        credits.append(
            {
                "kind": "credit_memo",
                "id": m.id,
                "date": m.date.isoformat(),
                "number": m.memo_number,
                "method": "",
                "currency": home,
                "amount": float(m.total),
                "available": float(m.balance_remaining),
            }
        )
        total += m.balance_remaining
    credits.sort(key=lambda c: (c["date"], c["kind"], c["id"]))
    return {
        "customer_id": customer.id,
        "customer_name": customer.name,
        "home_currency": home,
        "total": float(total),
        "credits": credits,
    }


@router.post("", response_model=CustomerResponse, status_code=201)
def create_customer(
    data: CustomerCreate,
    force: bool = Query(False, description="Bypass duplicate-name warning"),
    db: Session = Depends(get_db),
):
    # Phase 11: warn on likely duplicate names unless the caller explicitly
    # passes ?force=true to confirm. A sub-customer's name is checked the
    # fuzzy way against the top-level names and for the same name among
    # its siblings (#638): every property has a Unit 1, and Unit 2 beside
    # it is the next unit.
    if not force:
        dupes = likely_duplicates(db, data.name, data.parent_customer_id)
        if dupes:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "possible_duplicate",
                    "message": "A similarly-named customer already exists. Pass ?force=true to create anyway.",
                    "duplicates": dupes,
                },
            )
    values = data.model_dump()
    if not (values.get("terms") or "").strip():
        # A new customer gets the company's default terms (Settings), not a
        # hard-coded Net 30 (explore 2.17.3, macbase1 F5).
        from app.services.settings_service import get_setting_raw

        values["terms"] = get_setting_raw(db, "default_terms") or "Net 30"
    # A sub-customer sits one level under a top-level customer (#638);
    # "bill with parent" means nothing without a parent, and a new
    # (active) sub-customer needs an active parent.
    check_parent(db, None, values.get("parent_customer_id"))
    _check_bill_with_parent(values.get("parent_customer_id"), values)
    refuse_inactive_parent(db, values["name"], values.get("parent_customer_id"))
    customer = Customer(**values)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return _response(db, customer)


def _check_bill_with_parent(parent_id, values: dict) -> None:
    if values.get("bill_with_parent") and parent_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Bill with parent needs a parent customer: pick the customer "
                "this one sits under first."
            ),
        )


@router.put("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    with_subs: bool = Query(
        False,
        description="With is_active false on a parent: make its active "
        "sub-customers inactive too, instead of asking (409).",
    ),
    db: Session = Depends(get_db),
):
    customer = get_or_404(db, Customer, customer_id)
    update_data = reject_null_updates(Customer, data.model_dump(exclude_unset=True))
    if "parent_customer_id" in update_data:
        check_parent(db, customer, update_data["parent_customer_id"])
        if update_data["parent_customer_id"] is None:
            # no parent: nothing to bill with
            update_data["bill_with_parent"] = False
    parent_after = update_data.get("parent_customer_id", customer.parent_customer_id)
    if update_data.get("bill_with_parent"):
        _check_bill_with_parent(parent_after, update_data)
    # A sub-customer billed with its parent keeps that billing, and that
    # parent, while the parent's payments or credits are on its open
    # invoices (#638 review): 409, saying which.
    if customer.parent_customer_id and customer.bill_with_parent:
        parent_changes = parent_after != customer.parent_customer_id
        if parent_changes or update_data.get("bill_with_parent") is False:
            refuse_unbilling_with_parent(db, customer)
    # An active sub-customer sits under an active parent: a sub made
    # active again, or an active one moved, under an inactive parent is
    # refused (409 parent_inactive).
    active_after = update_data.get("is_active", customer.is_active)
    if active_after and parent_after is not None:
        reactivated = bool(update_data.get("is_active")) and not customer.is_active
        if reactivated or parent_after != customer.parent_customer_id:
            refuse_inactive_parent(db, customer.name, parent_after)
    subs_too = []
    if update_data.get("is_active") is False and customer.is_active:
        # A sub alone is fine (a tenant moves out, the property stays); a
        # parent with active subs asks first.
        subs_too = refuse_inactivating_a_parent(db, customer, with_subs)
    for key, val in update_data.items():
        setattr(customer, key, val)
    for sub in subs_too:
        sub.is_active = False
    db.commit()
    db.refresh(customer)
    return _response(db, customer)


@router.delete("/{customer_id}")
def delete_customer(
    customer_id: int, with_subs: bool = False, db: Session = Depends(get_db)
):
    customer = get_or_404(db, Customer, customer_id)
    subs_too = (
        refuse_inactivating_a_parent(db, customer, with_subs)
        if customer.is_active
        else []
    )
    customer.is_active = False
    for sub in subs_too:
        sub.is_active = False
    db.commit()
    return {"message": "Customer deactivated"}
