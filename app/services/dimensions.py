"""A class or a job named on a posting must exist, and the class must not be
archived (2.31.4: the feed's add, the register entry and the transfer took
any integer and posted it)."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.classes import TxnClass
from app.models.jobs import Job


def require_dimensions(
    db: Session, class_id: int | None = None, job_id: int | None = None
) -> None:
    if class_id is not None:
        cls = db.query(TxnClass).filter(TxnClass.id == class_id).first()
        if cls is None:
            raise HTTPException(status_code=404, detail=f"Class {class_id} not found")
        if cls.is_archived:
            raise HTTPException(
                status_code=400,
                detail=f"Class {cls.name} is archived; restore it under Classes or choose another.",
            )
    if job_id is not None:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job.is_active is False:
            raise HTTPException(
                status_code=400,
                detail=f"The job {job.full_name} is inactive; pick an active one.",
            )
