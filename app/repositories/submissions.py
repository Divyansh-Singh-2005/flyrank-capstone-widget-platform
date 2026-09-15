import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Submission


def get_by_idempotency_key(db: Session, widget_id: uuid.UUID, key: str) -> Submission | None:
    stmt = select(Submission).where(Submission.widget_id == widget_id, Submission.idempotency_key == key)
    return db.scalar(stmt)


def list_for_tenant(
    db: Session,
    tenant_id: uuid.UUID,
    widget_id: uuid.UUID | None = None,
    before: datetime | None = None,
    limit: int = 50,
) -> list[Submission]:
    stmt = select(Submission).where(Submission.tenant_id == tenant_id)
    if widget_id is not None:
        stmt = stmt.where(Submission.widget_id == widget_id)
    if before is not None:
        stmt = stmt.where(Submission.created_at < before)
    stmt = stmt.order_by(Submission.created_at.desc()).limit(limit)
    return list(db.scalars(stmt))