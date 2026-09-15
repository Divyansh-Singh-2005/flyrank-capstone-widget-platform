"""Owner dashboard API: stats and pagination."""

from tests.helpers import create_widget, email, submit


def _points_sum(payload: dict) -> int:
    return sum(point["count"] for point in payload["points"])


def test_stats_summary_timeseries_geo_and_pagination(client, owner, widget):
    second = create_widget(client, owner, title="Second widget")
    for _ in range(3):
        assert submit(client, widget["public_id"], {"email": email()}).status_code == 201
    client.post("/dev/geo", json={"a_down": True, "b_down": True})
    assert submit(client, second["public_id"], {"email": email()}).status_code == 201

    summary = client.get("/api/stats/summary", headers=owner).json()
    assert (summary["total_submissions"], summary["last_24h"], summary["last_7d"], summary["widget_count"]) == (4, 4, 4, 2)
    assert {w["public_id"]: w["total"] for w in summary["widgets"]} == {widget["public_id"]: 3, second["public_id"]: 1}

    daily = client.get("/api/stats/timeseries", params={"bucket": "day", "days": 7}, headers=owner).json()
    assert len(daily["points"]) == 7
    assert daily["points"][-1]["count"] == 4 and _points_sum(daily) == 4

    hourly = client.get("/api/stats/timeseries", params={"bucket": "hour", "days": 1}, headers=owner).json()
    assert len(hourly["points"]) == 24 and _points_sum(hourly) == 4

    only_second = client.get("/api/stats/timeseries", params={"widget_id": second["id"]}, headers=owner).json()
    assert _points_sum(only_second) == 1

    geo = client.get("/api/stats/geo", headers=owner).json()
    assert (geo["total"], geo["enriched"], geo["not_enriched"]) == (4, 3, 1)
    assert {"country": "Mockland", "count": 3} in geo["countries"]
    assert {"country": "Unknown", "count": 1} in geo["countries"]

    page1 = client.get("/api/submissions", params={"limit": 3}, headers=owner).json()
    assert len(page1["items"]) == 3 and page1["next_before"]
    page2 = client.get("/api/submissions", params={"limit": 3, "before": page1["next_before"]}, headers=owner).json()
    assert len(page2["items"]) == 1 and page2["next_before"] is None
    assert len({i["id"] for i in page1["items"] + page2["items"]}) == 4


def test_stats_parameter_validation(client, owner):
    bad = [
        {"bucket": "hour", "days": 30},
        {"bucket": "week"},
        {"days": 0},
        {"days": 91},
        {"widget_id": "not-a-uuid"},
    ]
    for params in bad:
        assert client.get("/api/stats/timeseries", params=params, headers=owner).status_code == 422