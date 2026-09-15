"""Idempotent demo data: python -m scripts.seed"""

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.core.ip import hash_ip
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import Submission, Tenant, User, Widget
from app.schemas.widget import WidgetCreate

DEMO_EMAIL = "owner@demo-widgets.dev"
DEMO_PASSWORD = "DemoOwner123!"
OTHER_EMAIL = "owner@other-widgets.dev"
OTHER_PASSWORD = "OtherOwner123!"

DEMO_WIDGET_ID = "wgt_demo000001"
DEMO_POPUP_ID = "wgt_demopopup1"
OTHER_WIDGET_ID = "wgt_other00001"

EMAIL_FIELD = {"name": "email", "label": "Email", "type": "email", "required": True, "max_length": 254}

DEMO_WIDGETS = {
    DEMO_WIDGET_ID: {
        "type": "signup_form",
        "title": "Join the Acme Bakery newsletter",
        "description": "Fresh recipes and offers, once a month.",
        "button_text": "Subscribe",
        "fields": [EMAIL_FIELD, {"name": "name", "label": "Name", "type": "text", "required": False, "max_length": 80}],
        "display_options": {"position": "inline", "theme": "light"},
    },
    DEMO_POPUP_ID: {
        "type": "cta_popover",
        "title": "Planning an event?",
        "description": "Tell us what you need and we will call you back.",
        "button_text": "Request a quote",
        "fields": [
            EMAIL_FIELD,
            {"name": "message", "label": "What do you need?", "type": "textarea", "required": False, "max_length": 500},
        ],
        "display_options": {"position": "bottom-right", "theme": "dark"},
    },
}
OTHER_WIDGETS = {
    OTHER_WIDGET_ID: {
        "type": "signup_form",
        "title": "Other Co waitlist",
        "button_text": "Join",
        "fields": [EMAIL_FIELD],
    },
}
SAMPLE_GEO = [
    ("Germany", "Berlin"),
    ("India", "Bengaluru"),
    ("United States", "Austin"),
    ("Brazil", "Sao Paulo"),
    ("Japan", "Osaka"),
    (None, None),
]


def ensure_owner(db, email: str, password: str, tenant_name: str):
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        return user.tenant_id, False
    tenant = Tenant(name=tenant_name)
    db.add(tenant)
    db.flush()
    db.add(User(tenant_id=tenant.id, email=email, password_hash=hash_password(password)))
    db.flush()
    return tenant.id, True


def ensure_widget(db, tenant_id, public_id: str, spec: dict):
    widget = db.scalar(select(Widget).where(Widget.public_id == public_id))
    if widget is not None:
        if widget.tenant_id != tenant_id:
            raise SystemExit(f"{public_id} already belongs to another tenant")
        return widget, False
    data = WidgetCreate(**spec)
    widget = Widget(
        tenant_id=tenant_id,
        public_id=public_id,
        type=data.type,
        title=data.title,
        description=data.description,
        button_text=data.button_text,
        fields=[f.model_dump() for f in data.fields],
        display_options=data.display_options.model_dump(),
        allowed_origins=data.allowed_origins,
        is_active=True,
    )
    db.add(widget)
    db.flush()
    return widget, True


def ensure_sample_submissions(db, widget: Widget, count: int, rng: random.Random) -> int:
    existing = db.scalar(select(func.count()).select_from(Submission).where(Submission.widget_id == widget.id))
    if existing:
        return 0
    now = datetime.now(timezone.utc)
    for i in range(count):
        country, city = rng.choice(SAMPLE_GEO)
        db.add(
            Submission(
                widget_id=widget.id,
                tenant_id=widget.tenant_id,
                data={"email": f"sample{i + 1}@seed-widgets.dev", "name": f"Sample {i + 1}"},
                origin="https://www.acme-bakery.test",
                ip_hash=hash_ip(f"192.0.2.{i % 250 + 1}"),
                country=country,
                city=city,
                geo_provider="seed" if country else None,
                created_at=now - timedelta(days=rng.randint(0, 13), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)),
            )
        )
    return count


def main() -> None:
    rng = random.Random(42)
    with SessionLocal() as db:
        demo_tenant, demo_new = ensure_owner(db, DEMO_EMAIL, DEMO_PASSWORD, "Acme Bakery (demo)")
        other_tenant, other_new = ensure_owner(db, OTHER_EMAIL, OTHER_PASSWORD, "Other Co (isolation demo)")
        created = []
        for public_id, spec in DEMO_WIDGETS.items():
            _, is_new = ensure_widget(db, demo_tenant, public_id, spec)
            created += [public_id] if is_new else []
        for public_id, spec in OTHER_WIDGETS.items():
            _, is_new = ensure_widget(db, other_tenant, public_id, spec)
            created += [public_id] if is_new else []
        demo_widget = db.scalar(select(Widget).where(Widget.public_id == DEMO_WIDGET_ID))
        samples = ensure_sample_submissions(db, demo_widget, 30, rng)
        db.commit()

    base = get_settings().api_base_url.rstrip("/")
    print(f"tenants created: demo={demo_new} other={other_new}")
    print(f"widgets created: {created or 'none (already present)'}; sample submissions added: {samples}")
    print(f"tenant A login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"tenant B login: {OTHER_EMAIL} / {OTHER_PASSWORD}")
    print(f'snippet: <script src="{base}/widget.js?id={DEMO_WIDGET_ID}" async></script>')
    print("customer site: http://localhost:5500   dashboard: " + base + "/dashboard")


if __name__ == "__main__":
    main()