"""Widget management: auth, CRUD, validation, tenant isolation."""

import pytest

from tests.helpers import WIDGET_BODY, create_widget, email, list_items, register, submit, widget_body


def test_owner_api_requires_a_valid_token(client):
    assert client.get("/api/widgets").status_code == 401
    forged = client.get("/api/widgets", headers={"Authorization": "Bearer forged.token.value"})
    assert forged.status_code == 401
    assert forged.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/submissions").status_code == 401
    assert client.get("/api/stats/summary").status_code == 401


def test_register_and_login_rules(client):
    body = {"email": "Owner@Tests-Widgets.dev", "password": "Password123!", "tenant_name": "T"}
    assert client.post("/auth/register", json=body).status_code == 201
    assert client.post("/auth/register", json={**body, "email": "owner@tests-widgets.dev"}).status_code == 409
    ok = client.post("/auth/login", json={"email": "OWNER@tests-widgets.dev", "password": "Password123!"})
    assert ok.status_code == 200 and ok.json()["access_token"]
    wrong = client.post("/auth/login", json={"email": "owner@tests-widgets.dev", "password": "wrong-password"})
    assert wrong.status_code == 401
    weak = client.post("/auth/register", json={**body, "email": "x@tests-widgets.dev", "password": "short"})
    assert weak.status_code == 422


def test_widget_crud_lifecycle(client, owner):
    created = client.post("/api/widgets", json=widget_body(), headers=owner)
    assert created.status_code == 201
    widget = created.json()
    assert widget["public_id"].startswith("wgt_") and len(widget["public_id"]) == 14
    wid = widget["id"]
    assert client.get(f"/api/widgets/{wid}", headers=owner).status_code == 200
    patched = client.patch(f"/api/widgets/{wid}", json={"title": "Renamed", "is_active": False}, headers=owner)
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed" and patched.json()["is_active"] is False
    listing = client.get("/api/widgets", headers=owner)
    assert [w["id"] for w in listing.json()] == [wid]
    assert listing.headers["cache-control"] == "no-store"
    assert client.delete(f"/api/widgets/{wid}", headers=owner).status_code == 204
    assert client.get(f"/api/widgets/{wid}", headers=owner).status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"type": "spaceship"}, id="unknown-type"),
        pytest.param({"title": ""}, id="empty-title"),
        pytest.param({"fields": []}, id="no-fields"),
        pytest.param({"fields": [{"name": "website", "label": "Trap"}]}, id="reserved-field-name"),
        pytest.param({"fields": [{"name": "Bad Name", "label": "x"}]}, id="bad-field-name"),
        pytest.param({"fields": [{"name": "a", "label": "A"}, {"name": "a", "label": "B"}]}, id="duplicate-fields"),
        pytest.param({"allowed_origins": ["not-an-origin"]}, id="bad-origin"),
        pytest.param({"unexpected": True}, id="unknown-key"),
    ],
)
def test_widget_validation(client, owner, overrides):
    response = client.post("/api/widgets", json=widget_body(**overrides), headers=owner)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_patch_rejects_null_and_bad_ids(client, owner, widget):
    assert client.patch(f"/api/widgets/{widget['id']}", json={"title": None}, headers=owner).status_code == 422
    assert client.get("/api/widgets/not-a-uuid", headers=owner).status_code == 422


def test_tenant_isolation(client):
    tenant_a, tenant_b = register(client, "a"), register(client, "b")
    widget_a = create_widget(client, tenant_a)
    assert submit(client, widget_a["public_id"], {"email": email()}).status_code == 201

    target = f"/api/widgets/{widget_a['id']}"
    assert client.get(target, headers=tenant_b).status_code == 404
    assert client.patch(target, json={"title": "hacked"}, headers=tenant_b).status_code == 404
    assert client.delete(target, headers=tenant_b).status_code == 404
    assert client.get(f"{target}/embed", headers=tenant_b).status_code == 404
    assert client.get("/api/widgets", headers=tenant_b).json() == []
    assert list_items(client, tenant_b, widget_id=widget_a["id"]) == []
    assert list_items(client, tenant_b) == []
    assert client.get("/api/stats/summary", headers=tenant_b).json()["total_submissions"] == 0
    assert client.get("/api/stats/geo", headers=tenant_b).json()["total"] == 0

    assert client.get(target, headers=tenant_a).json()["title"] == WIDGET_BODY["title"]
    assert len(list_items(client, tenant_a)) == 1