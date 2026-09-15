"""Widget delivery: embed snippet, cached config, versioned bundle, dashboard page."""

from app.config import get_settings
from tests.helpers import VISITOR_ORIGIN, email, submit


def test_embed_snippet(client, owner, widget):
    response = client.get(f"/api/widgets/{widget['id']}/embed", headers=owner)
    assert response.status_code == 200
    expected = f'<script src="http://localhost:8000/widget.js?id={widget["public_id"]}" async></script>'
    assert response.json()["snippet"] == expected


def test_config_is_small_public_and_cached(client, owner, widget):
    path = f"/public/widgets/{widget['public_id']}/config"
    first = client.get(path, headers={"Origin": VISITOR_ORIGIN})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=60"
    assert first.headers["access-control-allow-origin"] == "*"
    assert len(first.content) < 2048
    body = first.json()
    assert body["public_id"] == widget["public_id"]
    assert body["submit_url"] == "http://localhost:8000/public/submissions"
    assert body["honeypot_field"] == "website"
    assert "allowed_origins" not in body and "tenant_id" not in body

    etag = first.headers["etag"]
    revalidated = client.get(path, headers={"If-None-Match": etag})
    assert revalidated.status_code == 304 and revalidated.content == b""
    assert revalidated.headers["etag"] == etag

    client.patch(f"/api/widgets/{widget['id']}", json={"title": "New title"}, headers=owner)
    changed = client.get(path, headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag


def test_unknown_and_inactive_widgets_are_hidden(client, owner, widget):
    assert client.get("/public/widgets/wgt_zzzzzzzzzz/config").status_code == 404
    assert client.get("/public/widgets/not-a-widget/config").status_code == 404
    client.patch(f"/api/widgets/{widget['id']}", json={"is_active": False}, headers=owner)
    assert client.get(f"/public/widgets/{widget['public_id']}/config").status_code == 404
    assert submit(client, widget["public_id"], {"email": email()}).status_code == 404


def test_loader_and_versioned_bundle(client, widget):
    loader = client.get(f"/widget.js?id={widget['public_id']}")
    assert loader.status_code == 200
    assert loader.headers["content-type"].startswith("application/javascript")
    assert loader.headers["cache-control"] == "public, max-age=300"
    assert "/static/widget.v1.js" in loader.text

    bundle = client.get("/static/widget.v1.js")
    assert bundle.status_code == 200
    assert bundle.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert bundle.headers["access-control-allow-origin"] == "*"
    assert client.get("/static/widget.v1.js", headers={"If-None-Match": bundle.headers["etag"]}).status_code == 304

    for path in ("/static/widget.v999.js", "/static/widget.vabc.js", "/static/widget.v0.js"):
        assert client.get(path).status_code == 404
    assert client.get("/widget.js?id=../../etc/passwd").status_code == 422
    assert client.get("/widget.js").status_code == 422


def test_release_changes_the_bundle_url(client, widget, monkeypatch):
    monkeypatch.setattr(get_settings(), "widget_bundle_version", 2)
    loader = client.get(f"/widget.js?id={widget['public_id']}")
    assert "/static/widget.v2.js" in loader.text and "/static/widget.v1.js" not in loader.text


def test_dashboard_page_has_safe_headers(client):
    page = client.get("/dashboard")
    assert page.status_code == 200 and "Owner dashboard" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"