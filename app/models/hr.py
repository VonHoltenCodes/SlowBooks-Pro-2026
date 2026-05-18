# ============================================================================
# HR — new-hire onboarding checklist
# Tier 3: tracks the per-employee onboarding tasks (W-4, I-9, E-Verify, direct
# deposit, state new-hire reporting, policy acknowledgments).
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    Enum,
    Boolean,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class OnboardingTaskType(str, enum.Enum):
    W4 = "w4"  # Form W-4 withholding election
    I9_SECTION1 = "i9_section1"  # I-9 employee section
    I9_SECTION2 = "i9_section2"  # I-9 employer verification section
    EVERIFY = "everify"  # E-Verify case
    DIRECT_DEPOSIT = "direct_deposit"  # direct-deposit authorization
    STATE_NEW_HIRE_REPORT = "state_new_hire_report"  # required within 20 days of hire
    POLICY_ACKNOWLEDGMENT = "policy_acknowledgment"
    EMERGENCY_CONTACT = "emergency_contact"


class OnboardingTaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


# Default checklist created for every new hire.
DEFAULT_ONBOARDING_TASKS = [
    OnboardingTaskType.W4,
    OnboardingTaskType.I9_SECTION1,
    OnboardingTaskType.I9_SECTION2,
    OnboardingTaskType.EVERIFY,
    OnboardingTaskType.DIRECT_DEPOSIT,
    OnboardingTaskType.STATE_NEW_HIRE_REPORT,
    OnboardingTaskType.POLICY_ACKNOWLEDGMENT,
    OnboardingTaskType.EMERGENCY_CONTACT,
]


class OnboardingTask(Base):
    __tablename__ = "onboarding_tasks"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    task_type = Column(Enum(OnboardingTaskType), nullable=False)
    status = Column(Enum(OnboardingTaskStatus), default=OnboardingTaskStatus.PENDING)

    # Optional supporting document in the per-employee vault.
    document_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)
    # Lightweight e-sign acknowledgment (not a cryptographic signature).
    signed = Column(Boolean, default=False)
    signed_at = Column(DateTime(timezone=True), nullable=True)

    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by = Column(String(120), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee")
