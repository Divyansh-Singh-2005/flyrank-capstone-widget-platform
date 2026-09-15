import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import Widget


def add(db: Session, widget: Widget) -> Widget:
    db.add(widget)
    db.flush()
    return widget


def list_for_tenant(db: Session, tenant_id: uuid.UUID) -> list[Widget]:
    stmt = select(Widget).where(Widget.tenant_id == tenant_id).order_by(Widget.created_at.desc())
    return list(db.scalars(stmt))


def get_for_tenant(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID) -> Widget | None:
    stmt = select(Widget).where(Widget.id == widget_id, Widget.tenant_id == tenant_id)
    return db.scalar(stmt)


def get_by_public_id(db: Session, public_id: str) -> Widget | None:
    return db.scalar(select(Widget).where(Widget.public_id == public_id))


def public_id_exists(db: Session, public_id: str) -> bool:
    return bool(db.scalar(select(exists().where(Widget.public_id == public_id))))


def delete(db: Session, widget: Widget) -> None:
    db.delete(widget)