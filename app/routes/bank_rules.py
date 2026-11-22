# ============================================================================
# Bank Rules — auto-categorize imported bank transactions
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import reject_null_updates
from app.models.accounts import Account
from app.models.bank_rules import BankRule
from app.models.classes import TxnClass
from app.models.contacts import Customer, Vendor
from app.models.jobs import Job
from app.models.banking import BankTransaction
from app.schemas.bank_rules import BankRuleCreate, BankRuleUpdate, BankRuleResponse
from app.services.account_label import account_label
from app.services.dimensions import require_dimensions
from app.services.settings_service import use_account_numbers

router = APIRouter(prefix="/api/bank-rules", tags=["bank-rules"])


def _out(db: Session, rules: list[BankRule]) -> list[BankRuleResponse]:
    """The rules with their account's and vendor's names (#493), and their
    class's and job's (#675)."""
    acct_ids = {r.account_id for r in rules if r.account_id}
    vendor_ids = {r.vendor_id for r in rules if r.vendor_id}
    accounts = (
        {a.id: a for a in db.query(Account).filter(Account.id.in_(acct_ids))}
        if acct_ids
        else {}
    )
    vendors = (
        {v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids))}
        if vendor_ids
        else {}
    )
    class_ids = {r.class_id for r in rules if r.class_id}
    job_ids = {r.job_id for r in rules if r.job_id}
    classes = (
        {c.id: c.name for c in db.query(TxnClass).filter(TxnClass.id.in_(class_ids))}
        if class_ids
        else {}
    )
    jobs = (
        {j.id: j.full_name for j in db.query(Job).filter(Job.id.in_(job_ids))}
        if job_ids
        else {}
    )
    customer_ids = {r.customer_id for r in rules if r.customer_id}
    customers = (
        {c.id: c.name for c in db.query(Customer).filter(Customer.id.in_(customer_ids))}
        if customer_ids
        else {}
    )
    numbered = use_account_numbers(db)
    out = []
    for r in rules:
        resp = BankRuleResponse.model_validate(r)
        a = accounts.get(r.account_id)
        if a is not None:
            resp.account_name = account_label(a.account_number, a.name, numbered)
        resp.vendor_name = vendors.get(r.vendor_id)
        resp.class_name = classes.get(r.class_id)
        resp.job_name = jobs.get(r.job_id)
        resp.customer_name = customers.get(r.customer_id)
        out.append(resp)
    return out


# the most a rule's memo holds: the memo a To review line keeps for its
# entry (#1049) holds as much
MEMO_MAX = 500


def _check_refs(db: Session, fields: dict, rule: BankRule | None = None) -> None:
    """Each record a rule names exists. `rule` is the rule being edited, so
    a field the edit leaves out is checked as the rule has it."""
    if "pattern" in fields:
        # a pattern of only `*` or spaces matched nothing and was saved
        # anyway (W-193)
        from app.services.bank_rules_engine import pattern_problem

        problem = pattern_problem(fields["pattern"])
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    if fields.get("vendor_id") is not None and not db.get(Vendor, fields["vendor_id"]):
        raise HTTPException(status_code=404, detail="Vendor not found")
    if fields.get("account_id") is not None and not db.get(
        Account, fields["account_id"]
    ):
        raise HTTPException(status_code=404, detail="Account not found")
    if "memo" in fields:
        # a blank memo is no memo
        memo = (fields["memo"] or "").strip() or None
        if memo is not None and len(memo) > MEMO_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"A rule's memo can be at most {MEMO_MAX} characters; "
                f"this one has {len(memo)}.",
            )
        fields["memo"] = memo
    customer_id = fields.get("customer_id")
    if customer_id is not None:
        customer = db.get(Customer, customer_id)
        if customer is None:
            raise HTTPException(
                status_code=404,
                detail="That customer was not found; pick one from the list.",
            )
        # a rule keeps its own customer when it has since been made
        # inactive, as it keeps its vendor; a new pick must be active
        if not customer.is_active and (rule is None or rule.customer_id != customer_id):
            raise HTTPException(
                status_code=422,
                detail=f"{customer.name} is inactive; pick another customer.",
            )
    if rule is not None and "customer_id" not in fields:
        customer_id = rule.customer_id
    job_id = fields.get("job_id")
    if rule is not None and "job_id" not in fields:
        job_id = rule.job_id
    # a rule's class and job are refused in the words the feed and the
    # transfers use for the same class and job (#813); a job of another
    # customer than the rule's could never post on its line
    require_dimensions(db, class_id=fields.get("class_id"), job_id=fields.get("job_id"))
    if customer_id is not None and job_id is not None:
        job = db.get(Job, job_id)
        if job is not None and job.customer_id != customer_id:
            raise HTTPException(
                status_code=400,
                detail=f"The job {job.full_name} belongs to another customer "
                "than this rule's.",
            )


@router.get("", response_model=list[BankRuleResponse])
def list_rules(db: Session = Depends(get_db)):
    return _out(
        db, db.query(BankRule).order_by(BankRule.priority.desc(), BankRule.name).all()
    )


@router.get("/{rule_id}", response_model=BankRuleResponse)
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(BankRule).filter(BankRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _out(db, [rule])[0]


@router.post("", response_model=BankRuleResponse, status_code=201)
def create_rule(data: BankRuleCreate, db: Session = Depends(get_db)):
    fields = data.model_dump()
    _check_refs(db, fields)
    rule = BankRule(**fields)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _out(db, [rule])[0]


@router.put("/{rule_id}", response_model=BankRuleResponse)
def update_rule(rule_id: int, data: BankRuleUpdate, db: Session = Depends(get_db)):
    rule = db.query(BankRule).filter(BankRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    fields = reject_null_updates(BankRule, data.model_dump(exclude_unset=True))
    _check_refs(db, fields, rule)
    for key, val in fields.items():
        setattr(rule, key, val)
    db.commit()
    db.refresh(rule)
    return _out(db, [rule])[0]


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(BankRule).filter(BankRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"status": "deleted"}


@router.post("/apply")
def apply_rules(db: Session = Depends(get_db)):
    """Apply all active rules to every unmatched statement line (category
    only — adding to the books stays a click)."""
    from app.services.bank_rules_engine import apply_bank_rules

    matched = apply_bank_rules(db)
    total_unmatched = (
        db.query(BankTransaction)
        .filter(BankTransaction.match_status == "unmatched")
        .count()
    )
    return {"matched": matched, "total_unmatched": total_unmatched}
