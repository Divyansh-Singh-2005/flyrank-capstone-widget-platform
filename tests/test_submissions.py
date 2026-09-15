"""Public submission API: CORS, storage (probe 1), hostile input (probe 2), idempotency, origins."""

import pytest

from tests.helpers import (
    VISITOR_ORIGIN,
    create_widget,
    email,
    job_rows,
    list_items,
    submission_row,
    submit,
    visitor,
)


def test_cors_preflight_for_public_submissions(client):
    response = client.options(
        "/public/submissions",
        headers={
            "Origin": "https://any-customer.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed and "idempotency-key" in allowed
    assert "access-control-allow-credentials" not in response.headers


def test_private_routes_are_not_exposed_cross_origin(client, owner):
    preflight = client.options(
        "/api/widgets",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in preflight.headers
    listing = client.get("/api/widgets", headers={**owner, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in listing.headers


def test_probe1_valid_submission_is_stored_and_visible(client, owner, widget):
    response = submit(client, widget["public_id"], {"email": "Ann.Visitor@Tests-Widgets.DEV", "name": " Ann "})
    assert response.status_code == 201
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "no-store"
    submission_id = response.json()["id"]

    items = list_items(client, owner, widget_id=widget["id"])
    assert [item["id"] for item in items] == [submission_id]
    item = items[0]
    assert item["data"] == {"email": "Ann.Visitor@tests-widgets.dev", "name": "Ann"}
    assert item["origin"] == VISITOR_ORIGIN
    assert (item["country"], item["city"], item["geo_provider"]) == ("Mockland", "Alpha City", "mock-a")

    row = submission_row(submission_id)
    assert row["linked"] is True
    assert len(row["ip_hash"]) == 64 and "198.18" not in row["ip_hash"]
    assert len(job_rows()) == 1


def test_idempotent_retry_creates_one_row(client, owner, widget):
    fields = {"email": email()}
    first = submit(client, widget["public_id"], fields, headers=visitor(**{"Idempotency-Key": "retry-key-0001"}))
    second = submit(client, widget["public_id"], fields, headers=visitor(**{"Idempotency-Key": "retry-key-0001"}))
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert len(list_items(client, owner)) == 1
    assert len(job_rows()) == 1
    other = submit(client, widget["public_id"], fields, headers=visitor(**{"Idempotency-Key": "retry-key-0002"}))
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]


def test_invalid_idempotency_key_is_rejected(client, widget):
    response = submit(client, widget["public_id"], {"email": email()}, headers=visitor(**{"Idempotency-Key": "bad key!"}))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


PREFIX = '{"widget_id":"__PID__",'
JSON = "application/json"
BAD_PAYLOADS = [
    pytest.param('{"widget_id": "wgt_', JSON, 422, "invalid_json", id="malformed-json"),
    pytest.param("", JSON, 422, "invalid_json", id="empty-body"),
    pytest.param("[" * 5000 + "]" * 5000, JSON, 422, "invalid_json", id="deeply-nested"),
    pytest.param("[1, 2, 3]", JSON, 422, "validation_error", id="json-array"),
    pytest.param(PREFIX + '"fields":{"name":"' + "x" * 20000 + '"}}', JSON, 413, "payload_too_large", id="oversized"),
    pytest.param(PREFIX + '"fields":{"email":"a@tests-widgets.dev"}}', "text/plain", 415, "unsupported_media_type", id="text-plain"),
    pytest.param(PREFIX + '"fields":{"email":"a@tests-widgets.dev"}}', "application/x-www-form-urlencoded", 415, "unsupported_media_type", id="form-encoded"),
    pytest.param(PREFIX + '"fields":{"email":"a@tests-widgets.dev","is_admin":"yes"}}', JSON, 422, "validation_error", id="unknown-field"),
    pytest.param(PREFIX + '"fields":{"name":"No Email"}}', JSON, 422, "validation_error", id="missing-required"),
    pytest.param(PREFIX + '"fields":{"email":"not-an-email"}}', JSON, 422, "validation_error", id="invalid-email"),
    pytest.param(PREFIX + '"fields":{"email":"a@tests-widgets.dev","name":"' + "y" * 51 + '"}}', JSON, 422, "validation_error", id="too-long"),
    pytest.param(PREFIX + '"fields":{"email":123}}', JSON, 422, "validation_error", id="non-string-value"),
    pytest.param(PREFIX + '"fields":{"email":"a@tests-widgets.dev","name":"a\\u0000b"}}', JSON, 422, "validation_error", id="nul-byte"),
    pytest.param(PREFIX + '"fields":{"email":"a@tests-widgets.dev","name":"\\ud800"}}', JSON, 422, "validation_error", id="lone-surrogate"),
    pytest.param(PREFIX + '"fields":{"email":"a@tests-widgets.dev","name":"line\\nbreak"}}', JSON, 422, "validation_error", id="newline-in-text-field"),
    pytest.param(PREFIX + '"fields":{},"admin":true}', JSON, 422, "validation_error", id="extra-envelope-key"),
    pytest.param('{"widget_id":"../../etc/passwd","fields":{}}', JSON, 422, "validation_error", id="bad-widget-id"),
    pytest.param('{"widget_id":"wgt_zzzzzzzzzz","fields":{}}', JSON, 404, "widget_not_found", id="unknown-widget"),
]


@pytest.mark.parametrize("raw, content_type, status, code", BAD_PAYLOADS)
def test_probe2_bad_payloads_get_clean_json_4xx(client, owner, widget, raw, content_type, status, code):
    response = client.post(
        "/public/submissions",
        content=raw.replace("__PID__", widget["public_id"]).encode("utf-8"),
        headers=visitor(**{"Content-Type": content_type}),
    )
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == code
    assert response.headers["access-control-allow-origin"] == "*"
    assert list_items(client, owner) == []


def test_oversized_chunked_body_without_content_length_is_rejected(client, widget):
    def chunks():
        yield b'{"widget_id":"' + widget["public_id"].encode() + b'","fields":{"name":"'
        for _ in range(4):
            yield b"x" * 8000
        yield b'"}}'

    response = client.post("/public/submissions", content=chunks(), headers=visitor(**{"Content-Type": JSON}))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_origin_allow_list(client, owner):
    restricted = create_widget(client, owner, allowed_origins=["http://localhost:5500"])
    fields = {"email": email()}
    evil = submit(client, restricted["public_id"], fields, headers=visitor(origin="https://evil.example"))
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "origin_not_allowed"
    assert submit(client, restricted["public_id"], fields, headers=visitor(origin=None)).status_code == 403
    allowed = submit(client, restricted["public_id"], fields, headers=visitor(origin="HTTP://LOCALHOST:5500"))
    assert allowed.status_code == 201
    assert len(list_items(client, owner)) == 1