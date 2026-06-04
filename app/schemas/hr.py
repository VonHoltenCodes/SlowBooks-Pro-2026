from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# --- Onboarding tasks ------------------------------------------------------
class OnboardingTaskCreate(BaseModel):
    employee_id: int
    task_type: str
    notes: Optional[str] = None


class OnboardingTaskUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    completed_by: Optional[str] = None
    signed: Optional[bool] = None
    document_id: Optional[int] = None


class OnboardingTaskResponse(BaseModel):
    id: int
    employee_id: int
    task_type: str
    status: str
    signed: bool = False
    signed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    completed_by: Optional[str] = None
    document_id: Optional[int] = None
    notes: Optional[str] = None
    model_config = {"from_attributes": True}


class OnboardingChecklistResponse(BaseModel):
    employee_id: int
    employee_name: Optional[str] = None
    complete: int = 0
    total: int = 0
    percent_complete: float = 0
    tasks: list[OnboardingTaskResponse] = []


# --- Employee document vault ----------------------------------------------
class EmployeeDocumentResponse(BaseModel):
    id: int
    employee_id: Optional[int] = None
    filename: str
    doc_category: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    uploaded_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
