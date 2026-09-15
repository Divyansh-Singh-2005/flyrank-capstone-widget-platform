"""Abuse protection: rate limiting (probe 3) and honeypot (probe 6)."""

from app.core.rate_limit import SlidingWindowLimiter, parse_rate
from tests.helpers import create_widget, email, job_rows, list_items, submit, visitor


def test_probe3_burst_gets_429_and_service_keeps_serving(client, owner, widget):
    flood = visitor(ip="203.0.113.10")
    codes = [submit(client, widget["public_id"], {"email": email("burst")}, headers=flood).status_code for _ in range(15)]
    assert codes == [201] * 10 + [429] * 5

    limited = submit(client, widget["public_id"], {"email": email("burst")}, headers=flood)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.headers["access-control-allow-origin"] == "*"

    assert submit(client, widget["public_id"], {"email": email("normal")}).status_code == 201
    assert client.get("/health").status_code == 200
    assert client.get(f"/public/widgets/{widget['public_id']}/config").status_code == 200
    assert len(list_items(client, owner, limit=200)) == 11


def test_per_widget_limit_protects_only_that_widget(client, owner, widget):
    other = create_widget(client, owner, title="Other widget")
    codes = [submit(client, widget["public_id"], {"email": email("w")}).status_code for _ in range(61)]
    assert codes == [201] * 60 + [429]
    blocked = submit(client, widget["public_id"], {"email": email("w")})
    assert "per-widget" in blocked.json()["error"]["message"]
    assert submit(client, other["public_id"], {"email": email("w")}).status_code == 201


def test_sliding_window_limiter():
    now = [1000.0]
    limiter = SlidingWindowLimiter(2, 60, clock=lambda: now[0])
    assert limiter.hit("ip") is None
    assert limiter.hit("ip") is None
    assert limiter.hit("ip") == 60
    assert limiter.hit("another-ip") is None
    now[0] += 30
    assert limiter.hit("ip") == 30
    now[0] += 30.5
    assert limiter.hit("ip") is None


def test_parse_rate():
    assert parse_rate("10/minute") == (10, 60)
    assert parse_rate("5 / second") == (5, 1)
    assert parse_rate("100/hour") == (100, 3600)


def test_probe6_honeypot_submission_is_silently_dropped(client, owner, widget):
    response = submit(
        client, widget["public_id"], {"email": email("bot"), "name": "Cheap Pills"}, website="http://spam.example"
    )
    assert response.status_code == 200
    assert response.json() == {"id": None, "status": "received"}
    assert list_items(client, owner) == []
    assert job_rows() == []


def test_honeypot_gives_bots_no_validation_feedback(client, owner, widget):
    response = submit(client, widget["public_id"], {"email": "not-an-email", "junk": "x"}, website="spam")
    assert response.status_code == 200
    assert list_items(client, owner) == []


def test_blank_honeypot_is_a_normal_submission(client, widget):
    assert submit(client, widget["public_id"], {"email": email()}, website="   ").status_code == 201