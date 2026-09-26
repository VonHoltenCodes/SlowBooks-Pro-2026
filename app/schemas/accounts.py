from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from typing import Literal

from pydantic import model_validator, BaseModel, field_validator
from app.schemas.common import StrictModel

from app.models.accounts import AccountType

BankKind = Literal["bank", "credit_card"]


@lru_cache(maxsize=1)
def _nonprofit_only_accounts() -> dict:
    """{seeded name (casefolded): number} for the nonprofit accounts a
    business never uses: accounting.NONPROFIT_ACCOUNTS without Bad Debt
    Expense, which every chart has and a business writes off to as well."""
    from app.services.accounting import NONPROFIT_ACCOUNTS

    return {
        name.casefold(): number
        for number, name, _type in NONPROFIT_ACCOUNTS
        if name != "Bad Debt Expense"
    }


class AccountCreate(StrictModel):
    name: str
    account_number: Optional[str] = None
    account_type: AccountType
    parent_id: Optional[int] = None
    description: Optional[str] = None
    bank_kind: Optional[BankKind] = None

    @field_validator("account_number")
    @classmethod
    def blank_account_number_to_none(cls, v):
        # account_number is unique; storing "" would collide across accounts
        return v.strip() or None if isinstance(v, str) else v


class AccountUpdate(StrictModel):
    name: Optional[str] = None
    account_number: Optional[str] = None
    account_type: Optional[AccountType] = None
    parent_id: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    bank_kind: Optional[BankKind] = None

    @field_validator("account_number")
    @classmethod
    def blank_account_number_to_none(cls, v):
        return v.strip() or None if isinstance(v, str) else v


class AccountResponse(BaseModel):
    id: int
    name: str
    account_number: Optional[str]
    account_type: AccountType
    parent_id: Optional[int]
    description: Optional[str]
    is_active: bool
    is_system: bool
    bank_kind: Optional[str] = None
    # Issue #122: the posting code finds these by number, so the number and
    # the type cannot change (PUT refuses with 400). The UI reads this rather
    # than keeping its own copy of the registry, which would drift.
    is_control: bool = False
    control_purpose: Optional[str] = None
    # An account only a nonprofit posts to (net assets, in-kind gifts). Every
    # new company's chart carries 4400 In-Kind Contributions, so a business's
    # item form offered it as an income account (W-L13); the pickers leave
    # these out for a business. The account itself stays in the chart.
    nonprofit_only: bool = False
    balance: Decimal
    created_at: datetime

    @field_validator("balance", mode="before")
    @classmethod
    def null_balance_to_zero(cls, v):
        # legacy/imported rows can carry NULL balances; don't 500 on read
        return Decimal("0") if v is None else v

    @model_validator(mode="after")
    def mark_control_account(self):
        """Derived from the number, so it can never drift from the registry
        the posting code and the route guard actually read (issue #122)."""
        from app.services import control_accounts

        if control_accounts.is_control_number(self.account_number):
            self.is_control = True
            if self.control_purpose is None:
                # The route may already have said it in the company's
                # words; response validation runs this again and must
                # not put the business words back.
                _name, purpose = control_accounts.describe(self.account_number)
                self.control_purpose = purpose
        return self

    @model_validator(mode="after")
    def mark_nonprofit_only(self):
        """Matched on the name the nonprofit setup gives the account as well
        as its number, so a business that renamed 4400 to something of its
        own — or imported a chart with its own 4400 — is never hidden."""
        seeded = _nonprofit_only_accounts().get(" ".join(self.name.split()).casefold())
        if seeded and self.account_number in (None, seeded):
            self.nonprofit_only = True
        return self

    updated_at: datetime

    model_config = {"from_attributes": True}
