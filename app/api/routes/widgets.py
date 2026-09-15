import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db import get_db
from app.schemas.widget import EmbedOut, WidgetCreate, WidgetOut, WidgetUpdate
from app.services import widget_service

router = APIRouter(prefix="/api/widgets", tags=["widgets"])


@router.post("", status_code=201, response_model=WidgetOut)
def create_widget(
    data: WidgetCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return widget_service.create_widget(db, user.tenant_id, data)


@router.get("", response_model=list[WidgetOut])
def list_widgets(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return widget_service.list_widgets(db, user.tenant_id)


@router.get("/{widget_id}", response_model=WidgetOut)
def get_widget(
    widget_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return widget_service.get_widget(db, user.tenant_id, widget_id)


@router.patch("/{widget_id}", response_model=WidgetOut)
def update_widget(
    widget_id: uuid.UUID,
    data: WidgetUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return widget_service.update_widget(db, user.tenant_id, widget_id, data)


@router.delete("/{widget_id}", status_code=204, response_class=Response)
def delete_widget(
    widget_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    widget_service.delete_widget(db, user.tenant_id, widget_id)
    return Response(status_code=204)


@router.get("/{widget_id}/embed", response_model=EmbedOut)
def get_embed_snippet(
    widget_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    widget = widget_service.get_widget(db, user.tenant_id, widget_id)
    return EmbedOut(
        widget_id=widget.id,
        public_id=widget.public_id,
        snippet=widget_service.build_embed_snippet(widget),
    )