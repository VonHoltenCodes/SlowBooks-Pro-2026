# ============================================================================
# Bank Rules — auto-categorize imported bank transactions
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bank_rules import BankRule
from app.models.banking import BankTransaction
from app.schemas.bank_rules import BankRuleCreate, BankRuleUpdate, BankRuleResponse

router = APIRouter(prefix="/api/bank-rules", tags=["bank-rules"])


@router.get("", response_model=list[BankRuleResponse])
def list_rules(db: Session = Depends(get_db)):
    return db.query(BankRule).order_by(BankRule.priority.desc(), BankRule.name).all()


@router.get("/{rule_id}", response_model=BankRuleResponse)
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(BankRule).filter(BankRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.post("", response_model=BankRuleResponse, status_code=201)
def create_rule(data: BankRuleCreate, db: Session = Depends(get_db)):
    rule = BankRule(**data.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=BankRuleResponse)
def update_rule(rule_id: int, data: BankRuleUpdate, db: Session = Depends(get_db)):
    rule = db.query(BankRule).filter(BankRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(rule, key, val)
    db.commit()
    db.refresh(rule)
    return rule


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
    """Apply all active rules to unmatched bank transactions."""
    rules = (
        db.query(BankRule)
        .filter(BankRule.is_active == True)
        .order_by(BankRule.priority.desc())
        .all()
    )
    unmatched = (
        db.query(BankTransaction)
        .filter(BankTransaction.match_status == "unmatched")
        .all()
    )

    matched = 0
    for txn in unmatched:
        payee = (txn.payee or "").lower()
        for rule in rules:
            pattern = rule.pattern.lower()
            hit = False
            if rule.rule_type == "contains" and pattern in payee:
                hit = True
            elif rule.rule_type == "starts_with" and payee.startswith(pattern):
                hit = True
            elif rule.rule_type == "exact" and payee == pattern:
                hit = True

            if hit:
                if rule.account_id:
                    txn.category_account_id = rule.account_id
                txn.match_status = "auto"
                matched += 1
                break

    db.commit()
    return {"matched": matched, "total_unmatched": len(unmatched)}
