"""Shared test helpers. App modules are imported lazily, after conftest has set the environment."""

import copy
import itertools

from sqlalchemy import text

VISITOR_ORIGIN = "http://localhost:5500"
EMAIL_DOMAIN = "@tests-widgets.dev"
WIDGET_BODY = {
    "type": "signup_form",
    "title": "Test signup",
    "button_text": "Join",
    "fields": [
        {"name": "email", "label": "Email", "type": "email", "required": True, "max_length": 254},
        {"name": "name", "label": "Name", "type": "text", "required": False, "max_length": 50},
    ],
}

_ips = itertools.count(1)
_names = itertools.count(1)


def widget_body(**overrides) -> dict:
    body = copy.deepcopy(WIDGET_BODY)
    body.update(overrides)
    return body


def new_ip() -> str:
    n = next(_ips)
    return f"198.18.{n // 250}.{n % 250 + 1}"


def visitor(ip: str | None = None, origin: str | None = VISITOR_ORIGIN, **extra) -> dict:
    headers = {"X-Forwarded-For": ip or new_ip()}
    if origin:
        headers["Origin"] = origin
    headers.update(extra)
    return headers


def email(tag: str = "visitor") -> str:
    return f"{tag}{next(_names)}{EMAIL_DOMAIN}"


def submit(client, public_id: str, fields: dict, website: str = "", headers: dict | None = None):
    return client.post(
        "/public/submissions",
        json={"widget_id": public_id, "fields": fields, "website": website},
        headers=headers or visitor(),
    )


def register(client, label: str = "owner") -> dict:
    n = next(_names)
    response = client.post(
        "/auth/register",
        json={"email": f"{label}{n}{EMAIL_DOMAIN}", "password": "Password123!", "tenant_name": f"{label} tenant {n}"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_widget(client, headers: dict, **overrides) -> dict:
    response = client.post("/api/widgets", json=widget_body(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def list_items(client, headers: dict, **params) -> list:
    response = client.get("/api/submissions", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def job_rows() -> list[dict]:
    from app.db import engine

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, attempts, last_error, run_after > now() AS delayed FROM jobs ORDER BY id")
        ).mappings().all()
    return [dict(row) for row in rows]


def submission_row(submission_id: str) -> dict | None:
    from app.db import engine

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT s.ip_hash, (s.widget_id = w.id AND s.tenant_id = w.tenant_id) AS linked "
                "FROM submissions s JOIN widgets w ON w.id = s.widget_id WHERE s.id = CAST(:sid AS uuid)"
            ),
            {"sid": submission_id},
        ).mappings().first()
    return dict(row) if row else None


def drain_jobs(max_jobs: int = 100) -> None:
    """Run the worker in-process until the queue is empty, skipping backoff waits."""
    from app import worker
    from app.db import engine

    for _ in range(max_jobs):
        with engine.begin() as conn:
            conn.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'pending'"))
        if not worker.process_one():
            return