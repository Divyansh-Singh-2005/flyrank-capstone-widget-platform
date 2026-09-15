import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db import get_db
from app.repositories import submissions as submissions_repo
from app.schemas.submission import SubmissionOut, SubmissionPage

router = APIRouter(prefix="/api/submissions", tags=["dashboard"])


@router.get("", response_model=SubmissionPage)
def list_submissions(
    widget_id: uuid.UUID | None = None,
    before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubmissionPage:
    items = submissions_repo.list_for_tenant(db, user.tenant_id, widget_id=widget_id, before=before, limit=limit)
    next_before = items[-1].created_at if len(items) == limit else None
    return SubmissionPage(items=[SubmissionOut.model_validate(i) for i in items], next_before=next_before)