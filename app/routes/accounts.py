from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.schemas.accounts import (
    AccountCreate,
    AccountUpdate,
    AccountResponse,
    SystemAccountRoleResponse,
    SystemAccountRoleUpdate,
)
from app.services.accounting import (
    get_system_account_role_status,
    list_system_account_role_statuses,
    set_system_account_role_mapping,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountResponse])
def list_accounts(active_only: bool = False, account_type: str = None, db: Session = Depends(get_db)):
    q = db.query(Account)
    if active_only:
        q = q.filter(Account.is_active == True)
    if account_type:
        q = q.filter(Account.account_type == account_type)
    return q.order_by(Account.account_number).all()


@router.get("/system-roles", response_model=list[SystemAccountRoleResponse])
def list_system_account_roles(db: Session = Depends(get_db)):
    return list_system_account_role_statuses(db)


@router.put("/system-roles/{role_key}", response_model=SystemAccountRoleResponse)
def update_system_account_role(role_key: str, data: SystemAccountRoleUpdate, db: Session = Depends(get_db)):
    current = get_system_account_role_status(db, role_key)
    if not current:
        raise HTTPException(status_code=404, detail="System account role not found")
    try:
        return set_system_account_role_mapping(db, role_key, data.account_id)
    except ValueError as err:
        message = str(err)
        status_code = 404 if message == "Account not found" else 400
        raise HTTPException(status_code=status_code, detail=message) from err


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.post("", response_model=AccountResponse, status_code=201)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    account = Account(**data.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(account, key, val)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system account")
    db.delete(account)
    db.commit()
    return {"message": "Account deleted"}
