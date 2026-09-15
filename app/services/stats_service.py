import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.repositories import stats as stats_repo

MAX_HOURLY_DAYS = 7


def summary(db: Session, tenant_id: uuid.UUID) -> dict:
    now = datetime.now(timezone.utc)
    since_7d = now - timedelta(days=7)
    total, last_24h, last_7d = stats_repo.totals(db, tenant_id, now - timedelta(days=1), since_7d)
    widgets = [
        {
            "id": row.id,
            "public_id": row.public_id,
            "title": row.title,
            "type": row.type,
            "is_active": row.is_active,
            "total": int(row.total),
            "last_7d": int(row.last_7d),
            "last_submission_at": row.last_submission_at,
        }
        for row in stats_repo.per_widget(db, tenant_id, since_7d)
    ]
    return {
        "generated_at": now,
        "total_submissions": total,
        "last_24h": last_24h,
        "last_7d": last_7d,
        "widget_count": len(widgets),
        "widgets": widgets,
    }


def timeseries(db: Session, tenant_id: uuid.UUID, bucket: str, days: int, widget_id: uuid.UUID | None) -> dict:
    if bucket == "hour" and days > MAX_HOURLY_DAYS:
        raise AppError(422, "validation_error", f"hour buckets support at most {MAX_HOURLY_DAYS} days")
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, matches date_trunc output
    if bucket == "hour":
        step = timedelta(hours=1)
        end = now.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=days * 24 - 1)
    else:
        step = timedelta(days=1)
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=days - 1)

    counts = dict(stats_repo.timeseries(db, tenant_id, bucket, start.replace(tzinfo=timezone.utc), widget_id))
    points = []
    cursor = start
    while cursor <= end:
        points.append({"t": cursor.replace(tzinfo=timezone.utc).isoformat(), "count": counts.get(cursor, 0)})
        cursor += step
    return {"bucket": bucket, "days": days, "widget_id": widget_id, "points": points}


def geo_breakdown(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID | None) -> dict:
    rows = stats_repo.geo_breakdown(db, tenant_id, widget_id)
    total = sum(count for _, count in rows)
    enriched = sum(count for country, count in rows if country)
    return {
        "widget_id": widget_id,
        "total": total,
        "enriched": enriched,
        "not_enriched": total - enriched,
        "countries": [{"country": country or "Unknown", "count": count} for country, count in rows],
    }