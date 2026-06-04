# ============================================================================
# Onboarding service — seed and summarize a new hire's onboarding checklist.
# ============================================================================

from app.models.hr import (
    OnboardingTask,
    OnboardingTaskStatus,
    DEFAULT_ONBOARDING_TASKS,
)


def seed_onboarding_tasks(db, employee_id: int) -> list:
    """Create the default onboarding checklist for an employee.

    Idempotent — only adds task types that are not already present.
    """
    existing = {
        t.task_type
        for t in db.query(OnboardingTask)
        .filter(OnboardingTask.employee_id == employee_id)
        .all()
    }
    created = []
    for task_type in DEFAULT_ONBOARDING_TASKS:
        if task_type in existing:
            continue
        task = OnboardingTask(
            employee_id=employee_id,
            task_type=task_type,
            status=OnboardingTaskStatus.PENDING,
        )
        db.add(task)
        created.append(task)
    if created:
        db.flush()
    return created


def checklist_summary(tasks: list) -> dict:
    """Return progress counts for a list of OnboardingTask rows."""
    total = len(tasks)
    complete = sum(1 for t in tasks if t.status == OnboardingTaskStatus.COMPLETE)
    percent = round(100.0 * complete / total, 1) if total else 0.0
    return {"complete": complete, "total": total, "percent_complete": percent}
