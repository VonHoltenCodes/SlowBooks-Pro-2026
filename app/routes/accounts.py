from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.transactions import TransactionLine
from app.schemas.accounts import AccountCreate, AccountUpdate, AccountResponse
from app.routes._helpers import get_or_404

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _reject_duplicate_number(db: Session, number, exclude_id=None):
    """account_number is UNIQUE. Without this the violation surfaces from the
    database as an unhandled IntegrityError, i.e. an opaque HTTP 500 that
    names neither the field nor the account already using it."""
    if not number:
        return
    q = db.query(Account).filter(Account.account_number == number)
    if exclude_id is not None:
        q = q.filter(Account.id != exclude_id)
    clash = q.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Account number {number} is already used by "
                f"'{clash.name}'. Account numbers must be unique."
            ),
        )


@router.get("", response_model=list[AccountResponse])
def list_accounts(
    active_only: bool = False, account_type: str = None, db: Session = Depends(get_db)
):
    q = db.query(Account)
    if active_only:
        q = q.filter(Account.is_active)
    if account_type:
        q = q.filter(Account.account_type == account_type)
    return q.order_by(Account.account_number).all()


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, Account, account_id)


@router.post("", response_model=AccountResponse, status_code=201)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    _reject_duplicate_number(db, data.account_number)
    account = Account(**data.model_dump())
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Account violates a uniqueness or reference constraint.",
        )
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    account = get_or_404(db, Account, account_id)
    fields = data.model_dump(exclude_unset=True)

    if "account_number" in fields:
        _reject_duplicate_number(db, fields["account_number"], exclude_id=account_id)
    if fields.get("parent_id") == account_id:
        raise HTTPException(
            status_code=400, detail="An account cannot be its own parent."
        )

    for key, val in fields.items():
        setattr(account, key, val)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Update violates a uniqueness or reference constraint.",
        )
    db.refresh(account)
    return account


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = get_or_404(db, Account, account_id)
    if account.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system account")

    # An account carrying ledger history must not be deleted — the postings
    # would lose their anchor. Refusing is correct; the bug was that the
    # refusal arrived as a raw foreign-key violation (HTTP 500) instead of a
    # business rule the caller can act on.
    posted = (
        db.query(TransactionLine)
        .filter(TransactionLine.account_id == account_id)
        .count()
    )
    if posted:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' has {posted} posted transaction line(s) and "
                f"cannot be deleted. Deactivate it instead (set is_active=false) "
                f"to hide it from new entries while preserving history."
            ),
        )

    db.delete(account)
    try:
        db.commit()
    except IntegrityError:
        # Any of the other tables referencing accounts.id — belt and braces so
        # no constraint can ever leak as a 500 from this endpoint again.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' is still referenced by other records and "
                f"cannot be deleted. Deactivate it instead."
            ),
        )
    return {"message": "Account deleted"}
