from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Job


def enqueue(db: Session, kind: str, payload: dict, max_attempts: int) -> Job:
    job = Job(kind=kind, payload=payload, status="pending", attempts=0, max_attempts=max_attempts)
    db.add(job)
    return job


def claim_next(db: Session) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.status == "pending", Job.run_after <= func.now())
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return db.scalar(stmt)


def count_pending(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Job).where(Job.status == "pending")) or 0