import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db import get_db
from app.services import stats_service

router = APIRouter(prefix="/api/stats", tags=["dashboard"])


@router.get("/summary")
def summary(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return stats_service.summary(db, user.tenant_id)


@router.get("/timeseries")
def timeseries(
    bucket: Literal["day", "hour"] = "day",
    days: int = Query(default=14, ge=1, le=90),
    widget_id: uuid.UUID | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return stats_service.timeseries(db, user.tenant_id, bucket, days, widget_id)


@router.get("/geo")
def geo_breakdown(
    widget_id: uuid.UUID | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return stats_service.geo_breakdown(db, user.tenant_id, widget_id)