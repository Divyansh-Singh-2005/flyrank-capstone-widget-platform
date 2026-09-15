import uuid
from datetime import datetime

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.models import Submission, Widget

# Units are inlined as SQL literals: with psycopg3 server-side binding, a bound parameter in
# date_trunc() would make the SELECT and GROUP BY expressions differ and Postgres rejects it.
BUCKET_UNITS = {"day": "day", "hour": "hour"}


def totals(db: Session, tenant_id: uuid.UUID, since_24h: datetime, since_7d: datetime) -> tuple[int, int, int]:
    stmt = (
        select(
            func.count(),
            func.count().filter(Submission.created_at >= since_24h),
            func.count().filter(Submission.created_at >= since_7d),
        )
        .select_from(Submission)
        .where(Submission.tenant_id == tenant_id)
    )
    total, last_24h, last_7d = db.execute(stmt).one()
    return int(total), int(last_24h), int(last_7d)


def per_widget(db: Session, tenant_id: uuid.UUID, since_7d: datetime) -> list:
    stmt = (
        select(
            Widget.id,
            Widget.public_id,
            Widget.title,
            Widget.type,
            Widget.is_active,
            func.count(Submission.id).label("total"),
            func.count(Submission.id).filter(Submission.created_at >= since_7d).label("last_7d"),
            func.max(Submission.created_at).label("last_submission_at"),
        )
        .select_from(Widget)
        .outerjoin(Submission, Submission.widget_id == Widget.id)
        .where(Widget.tenant_id == tenant_id)
        .group_by(Widget.id)
        .order_by(func.count(Submission.id).desc(), Widget.created_at)
    )
    return list(db.execute(stmt).all())


def timeseries(
    db: Session,
    tenant_id: uuid.UUID,
    bucket: str,
    since: datetime,
    widget_id: uuid.UUID | None = None,
) -> list[tuple[datetime, int]]:
    unit = BUCKET_UNITS[bucket]
    expr = func.date_trunc(
        literal_column(f"'{unit}'"),
        func.timezone(literal_column("'UTC'"), Submission.created_at),
    )
    stmt = select(expr.label("bucket"), func.count().label("count")).where(
        Submission.tenant_id == tenant_id, Submission.created_at >= since
    )
    if widget_id is not None:
        stmt = stmt.where(Submission.widget_id == widget_id)
    stmt = stmt.group_by(expr).order_by(expr)
    return [(row.bucket, int(row.count)) for row in db.execute(stmt)]


def geo_breakdown(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID | None = None) -> list[tuple[str | None, int]]:
    stmt = select(Submission.country, func.count().label("count")).where(Submission.tenant_id == tenant_id)
    if widget_id is not None:
        stmt = stmt.where(Submission.widget_id == widget_id)
    stmt = stmt.group_by(Submission.country).order_by(func.count().desc(), Submission.country)
    return [(row.country, int(row.count)) for row in db.execute(stmt)]