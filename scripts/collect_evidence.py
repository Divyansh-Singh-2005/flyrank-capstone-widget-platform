"""Generate EVIDENCE.md from a live local run: python -m scripts.collect_evidence

Needs: the API on http://localhost:8000 (APP_ENV=development, TRUST_PROXY_HEADERS=true),
the customer site on http://localhost:5500, `python -m scripts.seed`, and NO other worker
process running (the email probe drives the worker in-process).
"""

import logging
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import text

from app import worker
from app.config import get_settings
from app.db import engine
from app.providers.email import mask_email
from scripts.seed import DEMO_EMAIL, DEMO_PASSWORD, DEMO_WIDGET_ID, OTHER_EMAIL, OTHER_PASSWORD

API = "http://localhost:8000"
DEMO_SITE = "http://localhost:5500"
ORIGIN = "http://localhost:5500"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DOMAIN = "@evidence-widgets.dev"
SHOWN_RESPONSE_HEADERS = (
    "cache-control",
    "etag",
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
    "access-control-max-age",
    "retry-after",
)
SHOWN_REQUEST_HEADERS = (
    "origin",
    "x-forwarded-for",
    "if-none-match",
    "idempotency-key",
    "content-type",
    "access-control-request-method",
    "access-control-request-headers",
)


class Evidence:
    def __init__(self):
        self.blocks: list = []
        self.results: list[tuple[str, bool]] = []
        self._box: dict | None = None

    def section(self, title: str) -> None:
        self._close()
        self.blocks.append(("h2", title))

    def box(self, title: str) -> None:
        self._close()
        self._box = {"title": title, "lines": [], "notes": [], "ok": True}

    def line(self, value: str) -> None:
        self._box["lines"].append(value)

    def note(self, value: str) -> None:
        self._box["notes"].append(value)

    def check(self, condition, what: str) -> bool:
        passed = bool(condition)
        self.line(f"CHECK {'PASS' if passed else 'FAIL'}: {what}")
        if not passed:
            self._box["ok"] = False
        return passed

    def _close(self) -> None:
        if self._box is not None:
            self.blocks.append(("box", self._box))
            self.results.append((self._box["title"], self._box["ok"]))
            self._box = None

    def render(self, header: str) -> str:
        self._close()
        out = [header, "", "## Checklist", ""]
        out += [f"- [{'x' if ok else ' '}] {title}" for title, ok in self.results]
        for kind, payload in self.blocks:
            if kind == "h2":
                out += ["", f"## {payload}"]
                continue
            out += ["", f"### {payload['title']}", "", "~~~text", *payload["lines"], "~~~"]
            for note in payload["notes"]:
                out += ["", note]
        return "\n".join(out) + "\n"


class Probe:
    def __init__(self, ev: Evidence, client: httpx.Client):
        self.ev = ev
        self.client = client

    def call(self, method, path, label, *, headers=None, json_body=None, content=None, body=True, limit=320):
        headers = dict(headers or {})
        kwargs = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if content is not None:
            kwargs["content"] = content
        response = self.client.request(method, path, headers=headers, **kwargs)
        self.ev.line(f"$ {method} {path}   # {label}")
        for name, value in headers.items():
            if name.lower() in SHOWN_REQUEST_HEADERS:
                self.ev.line(f"  > {name}: {value}")
        if content is not None and len(content) > 200:
            self.ev.line(f"  > (request body: {len(content)} bytes)")
        self.ev.line(f"  < {response.status_code}")
        for name in SHOWN_RESPONSE_HEADERS:
            if name in response.headers:
                self.ev.line(f"  < {name}: {response.headers[name]}")
        if body and response.content:
            shown = response.text.replace("\n", " ")
            self.ev.line("  " + (shown if len(shown) <= limit else shown[:limit] + "..."))
        return response


def visitor(ip: str, **extra) -> dict:
    headers = {"Origin": ORIGIN, "X-Forwarded-For": ip, "Content-Type": "application/json"}
    headers.update(extra)
    return headers


def drain_jobs() -> None:
    for _ in range(1000):
        with engine.begin() as conn:
            conn.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'pending' AND run_after > now()"))
        if not worker.process_one():
            return


def main() -> int:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if settings.app_env != "development" or not settings.trust_proxy_headers:
        print("EVIDENCE needs APP_ENV=development and TRUST_PROXY_HEADERS=true in .env")
        return 1
    client = httpx.Client(base_url=API, timeout=20)
    try:
        if client.get("/health").status_code != 200:
            print("API is not healthy")
            return 1
    except httpx.HTTPError as exc:
        print(f"API not reachable on {API}: {type(exc).__name__}")
        return 1

    ev = Evidence()
    p = Probe(ev, client)
    run = uuid.uuid4().hex[:6]
    ip_numbers = iter(range(1, 250))

    def ip() -> str:
        return f"198.51.100.{next(ip_numbers)}"

    def email(tag: str) -> str:
        return f"{tag}-{run}{EVIDENCE_DOMAIN}"

    def payload(fields: dict, website: str = "") -> dict:
        return {"widget_id": DEMO_WIDGET_ID, "fields": fields, "website": website}

    client.post("/dev/rate-limits/reset")
    client.post("/dev/geo", json={"mode": "mock", "a_down": False, "b_down": False})
    login_a = client.post("/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    login_b = client.post("/auth/login", json={"email": OTHER_EMAIL, "password": OTHER_PASSWORD})
    if login_a.status_code != 200 or login_b.status_code != 200:
        print("Demo logins failed - run `python -m scripts.seed` first")
        return 1
    A = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
    B = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
    demo = next((w for w in client.get("/api/widgets", headers=A).json() if w["public_id"] == DEMO_WIDGET_ID), None)
    if demo is None:
        print("Demo widget missing - run `python -m scripts.seed` first")
        return 1
    wid = demo["id"]

    def stored(submission_id):
        items = client.get(f"/api/submissions?widget_id={wid}&limit=200", headers=A).json()["items"]
        return next((i for i in items if i["id"] == submission_id), None)

    def show_row(row) -> None:
        if row is None:
            ev.line("  stored row: NOT FOUND")
            return
        ev.line(
            f"  stored row: email={row['data'].get('email')} origin={row['origin']} "
            f"country={row['country']} city={row['city']} geo_provider={row['geo_provider']}"
        )

    def widget_total() -> int:
        summary = client.get("/api/stats/summary", headers=A).json()
        return next(w["total"] for w in summary["widgets"] if w["public_id"] == DEMO_WIDGET_ID)

    def job_count() -> int:
        with engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM jobs")).scalar()

    # ------------------------------------------------------------------ widget management
    ev.section("Widget management")
    ev.box("Authenticated CRUD endpoints for widgets; requests without valid auth are rejected")
    no_token = p.call("GET", "/api/widgets", "no token")
    forged = p.call("GET", "/api/widgets", "forged bearer token", headers={"Authorization": "Bearer forged.token.value"})
    ev.check(no_token.status_code == 401 and forged.status_code == 401, "missing / forged token -> 401")
    created = p.call(
        "POST", "/api/widgets", "owner A creates a widget", headers=A, limit=160,
        json_body={"type": "signup_form", "title": f"Evidence temp {run}",
                   "fields": [{"name": "email", "label": "Email", "type": "email", "required": True}]},
    )
    temp_id = created.json().get("id", "missing") if created.status_code == 201 else "missing"
    read = p.call("GET", f"/api/widgets/{temp_id}", "read", headers=A, body=False)
    updated = p.call("PATCH", f"/api/widgets/{temp_id}", "update", headers=A, body=False,
                     json_body={"title": f"Evidence temp {run} renamed"})
    deleted = p.call("DELETE", f"/api/widgets/{temp_id}", "delete", headers=A)
    gone = p.call("GET", f"/api/widgets/{temp_id}", "read after delete", headers=A)
    ev.check(
        [created.status_code, read.status_code, updated.status_code, deleted.status_code, gone.status_code]
        == [201, 200, 200, 204, 404],
        "create 201, read 200, update 200, delete 204, then 404",
    )

    ev.box("Multi-tenant isolation proven: tenant A cannot read or modify tenant B's widgets or submissions")
    ev.line(f"# tenant A = {DEMO_EMAIL}, tenant B = {OTHER_EMAIL}; target = A's widget {DEMO_WIDGET_ID} ({wid})")
    attack_codes = [
        p.call("GET", f"/api/widgets/{wid}", "B reads A's widget", headers=B).status_code,
        p.call("PATCH", f"/api/widgets/{wid}", "B edits A's widget", headers=B, json_body={"title": "hacked"}).status_code,
        p.call("DELETE", f"/api/widgets/{wid}", "B deletes A's widget", headers=B).status_code,
    ]
    b_list = p.call("GET", "/api/widgets", "B lists widgets (only its own)", headers=B, limit=160)
    b_subs = p.call("GET", f"/api/submissions?widget_id={wid}", "B asks for A's submissions", headers=B)
    b_stats = p.call("GET", "/api/stats/summary", "B's stats", headers=B, limit=160)
    a_total = client.get("/api/stats/summary", headers=A).json()["total_submissions"]
    still = p.call("GET", f"/api/widgets/{wid}", "A's widget afterwards", headers=A, limit=120)
    ev.line(f"# A's own summary reports {a_total} submissions")
    ev.check(attack_codes == [404, 404, 404], "B gets 404 for A's widget on read / update / delete")
    ev.check(all(w["public_id"] != DEMO_WIDGET_ID for w in b_list.json()), "A's widget is absent from B's list")
    ev.check(b_subs.json().get("items") == [], "B sees none of A's submissions")
    ev.check(b_stats.json().get("total_submissions") == 0 and a_total > 0, "B's stats exclude A's data")
    ev.check(still.status_code == 200 and still.json()["title"] != "hacked", "A's widget is unchanged")

    # ------------------------------------------------------------------ delivery
    ev.section("Widget delivery")
    ev.box("Embed snippet generated per widget")
    embed = p.call("GET", f"/api/widgets/{wid}/embed", "owner A", headers=A)
    base = settings.api_base_url.rstrip("/")
    expected_snippet = f'<script src="{base}/widget.js?id={DEMO_WIDGET_ID}" async></script>'
    ev.check(embed.json().get("snippet") == expected_snippet, "one script tag carrying the widget id")

    ev.box("Public config endpoint serves a small payload with correct HTTP cache headers")
    config_path = f"/public/widgets/{DEMO_WIDGET_ID}/config"
    cfg = p.call("GET", config_path, "from the customer origin", headers={"Origin": ORIGIN}, limit=500)
    etag = cfg.headers.get("etag", "")
    ev.line(f"# payload size: {len(cfg.content)} bytes")
    again = p.call("GET", config_path, "browser revalidation", headers={"Origin": ORIGIN, "If-None-Match": etag})
    ev.check(cfg.status_code == 200 and cfg.headers.get("cache-control") == "public, max-age=60",
             "200 with Cache-Control: public, max-age=60")
    ev.check(bool(etag) and again.status_code == 304, "ETag present; If-None-Match -> 304 Not Modified")
    ev.check(len(cfg.content) < 2048 and "allowed_origins" not in cfg.json(), "small public payload, no tenant data")
    ev.check(cfg.headers.get("access-control-allow-origin") == "*", "readable cross-origin")

    ev.box("Widget JavaScript is served as a versioned bundle (new version = new URL or cache-bust)")
    version = settings.widget_bundle_version
    bundle_path = f"/static/widget.v{version}.js"
    loader = p.call("GET", f"/widget.js?id={DEMO_WIDGET_ID}", "loader referenced by the snippet", body=False)
    ev.line(f"# loader references {bundle_path}: {bundle_path in loader.text}")
    bundle = p.call("GET", bundle_path, "versioned bundle", body=False)
    ev.line(f"# bundle size: {len(bundle.content)} bytes")
    missing = p.call("GET", f"/static/widget.v{version + 998}.js", "a version that was never released")
    ev.check(loader.status_code == 200 and loader.headers.get("cache-control") == "public, max-age=300"
             and bundle_path in loader.text, "loader: 5 min cache, points at the versioned URL")
    ev.check(bundle.status_code == 200
             and bundle.headers.get("cache-control") == "public, max-age=31536000, immutable",
             "bundle: cached for a year, immutable")
    ev.check(missing.status_code == 404, "only released versions exist; a release bumps WIDGET_BUNDLE_VERSION -> new URL")

    ev.box("The widget renders on a page served from a different origin than your API")
    page = None
    has_snippet = False
    try:
        page = httpx.get(DEMO_SITE + "/", timeout=5)
        has_snippet = f"widget.js?id={DEMO_WIDGET_ID}" in page.text
        ev.line(f"$ GET {DEMO_SITE}/   # customer site (python -m http.server 5500)")
        ev.line(f"  < {page.status_code}")
        ev.line(f"# page embeds the snippet: {has_snippet}; page origin {ORIGIN} != API origin {API}")
    except httpx.HTTPError as exc:
        ev.line(f"# customer site not reachable: {type(exc).__name__}")
    items = client.get(f"/api/submissions?widget_id={wid}&limit=200", headers=A).json()["items"]
    browser_rows = [
        i for i in items
        if i["origin"] == ORIGIN and not str(i["data"].get("email", "")).endswith(EVIDENCE_DOMAIN)
    ]
    if browser_rows:
        latest = browser_rows[0]
        ev.line(f"# latest submission sent by a browser through the rendered widget on {ORIGIN}:")
        ev.line(
            f"  created_at={latest['created_at']} origin={latest['origin']} "
            f"email={mask_email(str(latest['data'].get('email', '')))} "
            f"country={latest['country']} geo_provider={latest['geo_provider']}"
        )
    else:
        ev.line(f"# no browser submission yet: open {DEMO_SITE}, submit the form, then re-run this script")
    ev.check(page is not None and page.status_code == 200 and has_snippet,
             "customer page on a second origin embeds only the snippet")
    ev.check(bool(browser_rows), "a submission made through the rendered widget reached the API from that origin")
    if (ROOT / "docs" / "evidence" / "widget-cross-origin.png").is_file():
        ev.note("![Widget rendered on http://localhost:5500](docs/evidence/widget-cross-origin.png)")

    # ------------------------------------------------------------------ public submission API
    ev.section("Public submission API")
    ev.box("Cross-origin submissions work: CORS headers correct, preflight (OPTIONS) handled")
    preflight = p.call("OPTIONS", "/public/submissions", "browser preflight", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,idempotency-key",
    })
    key = f"evidence-{run}-0001"
    body = payload({"email": email("valid"), "name": "Evidence Visitor"})
    valid = p.call("POST", "/public/submissions", "the actual request",
                   headers=visitor(ip(), **{"Idempotency-Key": key}), json_body=body)
    replay = p.call("POST", "/public/submissions", "client retry with the same Idempotency-Key",
                    headers=visitor(ip(), **{"Idempotency-Key": key}), json_body=body)
    valid_id = valid.json().get("id")
    ev.check(preflight.status_code == 204 and preflight.headers.get("access-control-allow-origin") == "*"
             and "Idempotency-Key" in preflight.headers.get("access-control-allow-headers", ""),
             "preflight 204 allows POST with Content-Type + Idempotency-Key")
    ev.check(valid.status_code == 201 and valid.headers.get("access-control-allow-origin") == "*",
             "cross-origin POST -> 201 with Access-Control-Allow-Origin")
    ev.check(replay.status_code == 200 and replay.json().get("id") == valid_id,
             "retry is idempotent: same id, no duplicate row")

    ev.box("All incoming input validated; malformed and oversized payloads rejected with appropriate 4xx codes and JSON errors")
    wid_json = '{"widget_id":"%s",' % DEMO_WIDGET_ID
    cases = [
        ("malformed JSON", '{"widget_id": "wgt_', "application/json", 422),
        ("oversized (20 KB)", wid_json + '"fields":{"name":"' + "x" * 20000 + '"}}', "application/json", 413),
        ("wrong content type", wid_json + '"fields":{}}', "text/plain", 415),
        ("unknown field", wid_json + '"fields":{"email":"%s","is_admin":"yes"}}' % email("x"), "application/json", 422),
        ("invalid email", wid_json + '"fields":{"email":"not-an-email"}}', "application/json", 422),
        ("NUL byte in a field", wid_json + '"fields":{"email":"%s","name":"a\\u0000b"}}' % email("x"), "application/json", 422),
        ("deeply nested JSON", "[" * 5000 + "]" * 5000, "application/json", 422),
        ("unknown widget", '{"widget_id":"wgt_zzzzzzzzzz","fields":{}}', "application/json", 404),
    ]
    outcomes = []
    for label, raw, content_type, expected in cases:
        response = p.call("POST", "/public/submissions", label, content=raw, limit=200,
                          headers=visitor(ip(), **{"Content-Type": content_type}))
        is_json_error = response.headers.get("content-type", "").startswith("application/json") and "error" in response.json()
        outcomes.append((response.status_code, response.status_code == expected and is_json_error))
    ev.check(all(ok for _, ok in outcomes), "every case returns the expected 4xx with a JSON error body")
    ev.check(all(code < 500 for code, _ in outcomes), "no case produced a 5xx")

    ev.box("Valid submissions stored safely, linked to the right widget and tenant")
    row = stored(valid_id)
    ev.line(f"$ GET /api/submissions?widget_id={wid}   # owner A's dashboard API")
    show_row(row)
    with engine.connect() as conn:
        link = conn.execute(
            text(
                "SELECT s.widget_id = w.id AND s.tenant_id = w.tenant_id AS linked, t.name AS tenant, "
                "length(s.ip_hash) AS ip_hash_length FROM submissions s "
                "JOIN widgets w ON w.id = s.widget_id JOIN tenants t ON t.id = s.tenant_id "
                "WHERE s.id = CAST(:sid AS uuid)"
            ),
            {"sid": valid_id},
        ).mappings().first()
    ev.line(f"$ SQL: submission -> widget/tenant link: {dict(link) if link else None}")
    b_view = p.call("GET", f"/api/submissions?widget_id={wid}", "tenant B looks for it", headers=B)
    ev.check(row is not None and row["data"].get("email") == email("valid"), "stored and visible via owner A's dashboard API")
    ev.check(bool(link and link["linked"]), "row carries the widget id and the widget's tenant id")
    ev.check(bool(link and link["ip_hash_length"] == 64), "visitor IP stored only as a 64-char keyed hash")
    ev.check(b_view.json().get("items") == [], "invisible to tenant B")

    # ------------------------------------------------------------------ abuse protection
    ev.section("Abuse protection")
    ev.box("Rate limiting per IP and/or per widget returns 429 under a burst - and the API keeps serving legitimate traffic")
    client.post("/dev/rate-limits/reset")
    ev.line(f"# limits: per IP {settings.rate_limit_per_ip}, per widget {settings.rate_limit_per_widget} (sliding window)")
    flood_ip = "203.0.113.77"
    codes = []
    last = None
    for i in range(15):
        last = client.post("/public/submissions", headers=visitor(flood_ip), json=payload({"email": email(f"burst{i}")}))
        codes.append(last.status_code)
    ev.line(f"$ 15 x POST /public/submissions from {flood_ip}")
    ev.line("  < status codes: " + " ".join(str(c) for c in codes))
    ev.line(f"  < last response: retry-after: {last.headers.get('retry-after')}  {last.text}")
    other = p.call("POST", "/public/submissions", "a different visitor right after the flood",
                   headers=visitor("203.0.113.88"), json_body=payload({"email": email("other")}))
    health = p.call("GET", "/health", "service health right after")
    config_again = p.call("GET", config_path, "widget config still served", body=False)
    ev.check(codes == [201] * 10 + [429] * 5, "first 10 accepted, the rest rejected with 429")
    ev.check(bool(last.headers.get("retry-after")), "429 carries Retry-After")
    ev.check(other.status_code == 201 and health.status_code == 200 and config_again.status_code == 200,
             "legitimate traffic keeps being served")
    ev.note("The flooding client itself stays limited until Retry-After; other visitors and all other endpoints keep working.")

    ev.box("At least one spam-prevention technique (honeypot field, token, or heuristic) demonstrably blocks a spam submission")
    rows_before, jobs_before = widget_total(), job_count()
    bot = p.call("POST", "/public/submissions", "a bot fills the hidden 'website' field", headers=visitor(ip()),
                 json_body=payload({"email": email("bot"), "name": "Cheap Pills"}, website="http://cheap-pills.test"))
    rows_after, jobs_after = widget_total(), job_count()
    ev.line(f"# rows for the widget: before={rows_before} after={rows_after}; jobs: before={jobs_before} after={jobs_after}")
    ev.check(bot.status_code == 200 and bot.json().get("id") is None, "bot receives a fake success (no hint why)")
    ev.check(rows_before == rows_after and jobs_before == jobs_after, "nothing stored and no email queued")

    # ------------------------------------------------------------------ enrichment and side effects
    ev.section("Enrichment & safe side effects")
    ev.box("IP->geo enrichment uses a provider fallback chain: provider A down -> provider B answers -> submission enriched")
    p.call("POST", "/dev/geo", "deterministic mock providers, both up", json_body={"mode": "mock", "a_down": False, "b_down": False})
    up = p.call("POST", "/public/submissions", "submit", headers=visitor(ip()),
                json_body=payload({"email": email("geo-a-up")}), body=False)
    row_a = stored(up.json().get("id"))
    show_row(row_a)
    p.call("POST", "/dev/geo", "switch provider A off", json_body={"a_down": True})
    a_down = p.call("POST", "/public/submissions", "submit", headers=visitor(ip()),
                    json_body=payload({"email": email("geo-a-down")}), body=False)
    row_b = stored(a_down.json().get("id"))
    show_row(row_b)
    ev.check(row_a is not None and row_a["geo_provider"] == "mock-a", "A up -> enriched by A")
    ev.check(row_b is not None and row_b["geo_provider"] == "mock-b" and bool(row_b["country"]),
             "A down -> B answers -> submission enriched by B")

    ev.box("All providers down -> submission still succeeds (without geo). Degrade, never fail.")
    p.call("POST", "/dev/geo", "switch provider B off too", json_body={"b_down": True})
    both_down = p.call("POST", "/public/submissions", "submit", headers=visitor(ip()),
                       json_body=payload({"email": email("geo-all-down")}))
    row_none = stored(both_down.json().get("id"))
    show_row(row_none)
    ev.check(both_down.status_code == 201, "still 201")
    ev.check(row_none is not None and row_none["country"] is None and row_none["geo_provider"] is None,
             "stored without geo data")

    ev.box("Real free providers, development sanity check (ip-api.com -> ipapi.co)")
    p.call("POST", "/dev/geo", "real providers", json_body={"mode": "real", "a_down": False, "b_down": False})
    real = p.call("POST", "/public/submissions", "visitor 8.8.8.8", headers=visitor("8.8.8.8"),
                  json_body=payload({"email": email("geo-real")}), body=False)
    show_row(stored(real.json().get("id")))
    ev.check(real.status_code == 201, "stored (enriched when the free APIs answer, without geo otherwise)")
    client.post("/dev/geo", json={"mode": settings.geo_provider_mode, "a_down": False, "b_down": False})

    ev.box("A failing confirmation email / webhook does not prevent the submission from being stored")
    drain_jobs()  # deliver everything already queued, normally
    settings.force_email_failure = True
    try:
        failing = p.call("POST", "/public/submissions", "submit while the email sender is forced to fail",
                         headers=visitor(ip()), json_body=payload({"email": email("email-fails")}))
        failing_id = failing.json().get("id")
        drain_jobs()
    finally:
        settings.force_email_failure = False
    with engine.connect() as conn:
        job = conn.execute(
            text("SELECT status, attempts, last_error FROM jobs WHERE payload->>'submission_id' = :sid"),
            {"sid": failing_id},
        ).mappings().first()
    ev.line(f"$ worker (in-process, FORCE_EMAIL_FAILURE=true) -> job {dict(job) if job else None}")
    row_fail = stored(failing_id)
    show_row(row_fail)
    ev.check(failing.status_code == 201, "API answered 201 at once (email runs later in the background job)")
    ev.check(bool(job) and job["status"] == "dead" and job["attempts"] == settings.job_max_attempts,
             "job retried with backoff, then marked dead with an ALERT log")
    ev.check(row_fail is not None, "submission stored and visible")

    # ------------------------------------------------------------------ documentation
    ev.section("Documentation")
    ev.box("README with architecture diagram, setup instructions, and API documentation; the required files from Section 11 present")
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").is_file() else ""
    required = ["README.md", "capstone.yaml", "EVIDENCE.md", "BUILDLOG.md", ".env.example", "docs/DESIGN.md", "LICENSE"]
    present = {name: (ROOT / name).is_file() or name == "EVIDENCE.md" for name in required}
    for name in required:
        ev.line(f"  {name}: {'present' if present[name] else 'MISSING'}")
    sections = ["## Architecture", "## Setup", "## API", "## Limitations"]
    ev.line("  README sections: " + ", ".join(f"{s[3:]}={'yes' if s in readme else 'no'}" for s in sections))
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    ev.check(all(present.values()), "required files present (EVIDENCE.md is this file)")
    ev.check(all(s in readme for s in sections), "README covers architecture, setup, API and limitations")
    ev.check(".env" in gitignore, ".env is git-ignored")

    # ------------------------------------------------------------------ tests
    ev.section("Automated tests")
    ev.box("Deterministic pytest suite: CORS, validation, rate limiting, spam control, provider fallback, email failure, isolation")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    ev.line("$ python -m pytest -q")
    for output_line in proc.stdout.strip().splitlines()[-12:]:
        ev.line("  " + output_line)
    ev.check(proc.returncode == 0, "all tests pass")

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    header = "\n".join([
        "# EVIDENCE",
        "",
        f"Generated by `python -m scripts.collect_evidence` against a live local run (API {API}, customer site "
        f"{DEMO_SITE}) on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, code at commit `{commit or 'unknown'}`.",
        "",
        "Every block is real request/response output; `CHECK` lines are evaluated by the script. Visitor IPs are",
        "simulated with `X-Forwarded-For` (`TRUST_PROXY_HEADERS=true`, local demo only). Re-run the script to refresh.",
    ])
    (ROOT / "EVIDENCE.md").write_text(ev.render(header), encoding="utf-8", newline="\n")
    passed = sum(ok for _, ok in ev.results)
    print(f"EVIDENCE.md written: {passed}/{len(ev.results)} boxes pass")
    for title, ok in ev.results:
        print(("PASS  " if ok else "FAIL  ") + title)
    return 0 if passed == len(ev.results) else 2


if __name__ == "__main__":
    raise SystemExit(main())