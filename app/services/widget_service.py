import secrets
import string
import uuid

from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.config import get_settings
from app.models import Widget
from app.repositories import widgets as widgets_repo
from app.schemas.widget import WidgetCreate, WidgetUpdate

PUBLIC_ID_ALPHABET = string.ascii_lowercase + string.digits


def _new_public_id(db: Session) -> str:
    for _ in range(5):
        candidate = "wgt_" + "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(10))
        if not widgets_repo.public_id_exists(db, candidate):
            return candidate
    raise RuntimeError("could not allocate a unique public_id")


def create_widget(db: Session, tenant_id: uuid.UUID, data: WidgetCreate) -> Widget:
    widget = Widget(
        tenant_id=tenant_id,
        public_id=_new_public_id(db),
        type=data.type,
        title=data.title,
        description=data.description,
        button_text=data.button_text,
        fields=[f.model_dump() for f in data.fields],
        display_options=data.display_options.model_dump(),
        allowed_origins=data.allowed_origins,
        is_active=data.is_active,
    )
    widgets_repo.add(db, widget)
    db.commit()
    db.refresh(widget)
    return widget


def list_widgets(db: Session, tenant_id: uuid.UUID) -> list[Widget]:
    return widgets_repo.list_for_tenant(db, tenant_id)


def get_widget(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID) -> Widget:
    widget = widgets_repo.get_for_tenant(db, tenant_id, widget_id)
    if widget is None:
        # Same response whether the widget does not exist or belongs to another tenant.
        raise AppError(404, "widget_not_found", "Widget not found")
    return widget


def update_widget(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID, data: WidgetUpdate) -> Widget:
    widget = get_widget(db, tenant_id, widget_id)
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is None:
            raise AppError(422, "validation_error", f"'{key}' cannot be null")
    for key, value in changes.items():
        setattr(widget, key, value)
    db.commit()
    db.refresh(widget)
    return widget


def delete_widget(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID) -> None:
    widget = get_widget(db, tenant_id, widget_id)
    widgets_repo.delete(db, widget)
    db.commit()


def build_embed_snippet(widget: Widget) -> str:
    base = get_settings().api_base_url.rstrip("/")
    return f'<script src="{base}/widget.js?id={widget.public_id}" async></script>'