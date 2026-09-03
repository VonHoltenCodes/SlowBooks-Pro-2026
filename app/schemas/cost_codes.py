from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


def _clean_code(v):
    v = (v or "").strip()
    if not v:
        raise ValueError("Cost code is required")
    if len(v) > 20:
        raise ValueError("Cost code must be 20 characters or fewer")
    return v


def _clean_name(v):
    v = (v or "").strip()
    if not v:
        raise ValueError("Cost code name is required")
    return v


def _check_type(v):
    # Codes must exist in the (user-editable) cost_types table; the route
    # checks that. Here: shape only.
    if v is None:
        return v
    v = v.strip().lower().replace(" ", "_")
    if not v or len(v) > 20:
        raise ValueError("cost_type is required")
    return v


class CostCodeCreate(BaseModel):
    code: str
    name: str
    cost_type: str = "other"
    account_id: Optional[int] = None
    parent_id: Optional[int] = None
    notes: Optional[str] = None

    _code = field_validator("code")(_clean_code)
    _name = field_validator("name")(_clean_name)
    _type = field_validator("cost_type")(_check_type)


class CostCodeUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    cost_type: Optional[str] = None
    account_id: Optional[int] = None
    parent_id: Optional[int] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("code")
    @classmethod
    def _code(cls, v):
        return None if v is None else _clean_code(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return None if v is None else _clean_name(v)

    _type = field_validator("cost_type")(_check_type)


class CostCodeResponse(BaseModel):
    id: int
    code: str
    name: str
    label: str = ""
    cost_type: str
    account_id: Optional[int] = None
    account_name: Optional[str] = None
    parent_id: Optional[int] = None
    parent_code: Optional[str] = None
    depth: int = 0
    children_count: int = 0
    notes: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
