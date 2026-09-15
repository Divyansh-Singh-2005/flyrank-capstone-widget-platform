"""Graceful degradation: geo fallback (probe 4), email failure (probe 5), database outage."""

from sqlalchemy.exc import OperationalError

from app import worker
from app.config import get_settings
from app.providers import geo
from app.providers.geo import GeoChain, MockGeoProvider
from app.repositories import widgets as widgets_repo
from tests.helpers import drain_jobs, email, job_rows, list_items, submit, visitor


def _geo_of(client, owner, submission_id):
    item = next(i for i in list_items(client, owner, limit=200) if i["id"] == submission_id)
    return item["country"], item["city"], item["geo_provider"]


def test_probe4_geo_fallback_chain_degrades_but_never_fails(client, owner, widget):
    pid = widget["public_id"]
    assert client.post("/dev/geo", json={"mode": "mock", "a_down": False, "b_down": False}).status_code == 200
    first = submit(client, pid, {"email": email()})
    assert client.post("/dev/geo", json={"a_down": True}).json() == {"mode": "mock", "a_down": True, "b_down": False}
    second = submit(client, pid, {"email": email()})
    client.post("/dev/geo", json={"b_down": True})
    third = submit(client, pid, {"email": email()})

    assert [r.status_code for r in (first, second, third)] == [201, 201, 201]
    assert _geo_of(client, owner, first.json()["id"]) == ("Mockland", "Alpha City", "mock-a")
    assert _geo_of(client, owner, second.json()["id"]) == ("Mockland", "Beta City", "mock-b")
    assert _geo_of(client, owner, third.json()["id"]) == (None, None, None)


def test_dev_geo_endpoint_validates_input(client):
    assert client.post("/dev/geo", json={"mode": "satellite"}).status_code == 422


class _TimeoutProvider:
    name = "slow"

    def lookup(self, ip):
        raise TimeoutError("provider timed out")


class _GarbageProvider:
    name = "garbage"

    def lookup(self, ip):
        raise ValueError("unexpected JSON")


def test_geo_chain_survives_any_provider_error():
    chain = GeoChain([_TimeoutProvider(), _GarbageProvider(), MockGeoProvider("ok", False, "Testland", "Test City")])
    result = chain.lookup("192.0.2.1")
    assert (result.country, result.city, result.provider) == ("Testland", "Test City", "ok")
    assert GeoChain([_TimeoutProvider(), MockGeoProvider("down", True, "x", "y")]).lookup("192.0.2.1") is None


def test_real_mode_never_looks_up_private_ips(client, owner, widget, monkeypatch):
    calls = []
    monkeypatch.setattr(get_settings(), "geo_dev_ip_override", "")
    monkeypatch.setattr(geo, "build_chain", lambda state=None: calls.append(state) or GeoChain([]))
    client.post("/dev/geo", json={"mode": "real"})
    response = submit(client, widget["public_id"], {"email": email()}, headers=visitor(ip="10.1.2.3"))
    assert response.status_code == 201
    assert calls == []
    assert _geo_of(client, owner, response.json()["id"]) == (None, None, None)


def test_enrichment_crash_still_stores_the_submission(client, owner, widget, monkeypatch):
    def broken(state=None):
        raise RuntimeError("geo subsystem exploded")

    monkeypatch.setattr(geo, "build_chain", broken)
    response = submit(client, widget["public_id"], {"email": email()})
    assert response.status_code == 201
    assert _geo_of(client, owner, response.json()["id"]) == (None, None, None)


def test_probe5_email_failure_never_blocks_the_submission(client, owner, widget, monkeypatch):
    monkeypatch.setattr(get_settings(), "force_email_failure", True)
    response = submit(client, widget["public_id"], {"email": email("fails")})
    assert response.status_code == 201
    drain_jobs()
    jobs = job_rows()
    assert len(jobs) == 1
    assert (jobs[0]["status"], jobs[0]["attempts"]) == ("dead", 3)
    assert jobs[0]["last_error"].startswith("EmailSendError")
    assert [i["id"] for i in list_items(client, owner)] == [response.json()["id"]]


def test_failed_email_is_retried_with_backoff(client, widget, monkeypatch):
    monkeypatch.setattr(get_settings(), "force_email_failure", True)
    submit(client, widget["public_id"], {"email": email()})
    assert worker.process_one() is True
    job = job_rows()[0]
    assert (job["status"], job["attempts"], job["delayed"]) == ("pending", 1, True)
    assert worker.process_one() is False


def test_successful_email_marks_the_job_done(client, widget):
    submit(client, widget["public_id"], {"email": email()})
    drain_jobs()
    assert [(j["status"], j["attempts"]) for j in job_rows()] == [("done", 1)]


def test_email_job_for_a_deleted_submission_completes(client, owner, widget):
    submit(client, widget["public_id"], {"email": email()})
    assert client.delete(f"/api/widgets/{widget['id']}", headers=owner).status_code == 204
    drain_jobs()
    assert [j["status"] for j in job_rows()] == ["done"]


def test_database_outage_returns_503_not_500(client, widget, monkeypatch):
    def database_down(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, ConnectionError("database down"))

    monkeypatch.setattr(widgets_repo, "get_by_public_id", database_down)
    response = submit(client, widget["public_id"], {"email": email()})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert response.headers["retry-after"] == "5"