# FlyRank Capstone - Embeddable Widget & Lead-Capture Platform

A customer defines a widget (signup form or call-to-action popover), pastes one `<script>` tag into
any website, and this backend safely handles everything the public internet sends back: submissions
are validated, rate limited, spam filtered, geo-enriched through a provider fallback chain, stored per
tenant, confirmed by a background email job, and shown to the owner through a dashboard API.

Every requirement of the brief has a generated proof in [EVIDENCE.md](EVIDENCE.md).
Design notes: [docs/DESIGN.md](docs/DESIGN.md). AI usage log: [BUILDLOG.md](BUILDLOG.md).

## Architecture

~~~text
 Widget owner (JWT)        Customer website (any origin)               Website visitor
       |                              |                                        |
       | /auth/*  /api/*              | <script src=".../widget.js?id=wgt_..."> | POST /public/submissions
       v                              v                                        v
 +-------------------------------------------------------------------------------------+
 | FastAPI  app/api: routers, CORS + cache policy middleware, one JSON error envelope   |
 |  owner API ....... JWT, tenant scoped, no CORS, Cache-Control: no-store              |
 |  delivery ........ /widget.js (5 min) -> /static/widget.vN.js (1 year, immutable)    |
 |                    /public/widgets/{id}/config (60 s + ETag / 304)                   |
 |  submissions ..... size + type guard -> per-IP limit -> envelope validation          |
 |                    -> widget + origin check -> per-widget limit -> honeypot          |
 |                    -> field validation -> idempotency -> geo chain -> store + job    |
 +--------------------------+----------------------------------------------------------+
                            | app/services -> app/repositories (tenant_id in every owner query)
                            v
   PostgreSQL (Alembic migrations)                  geo chain: ip-api.com -> ipapi.co -> none
   tenants, users, widgets, submissions, jobs       (deterministic mock A/B for tests + probes)
                            ^
                            | SELECT ... FOR UPDATE SKIP LOCKED
   worker (app/worker.py): confirmation email, retries with backoff, dead + ALERT log --> Mailpit
~~~

| Actor | Paths | Auth | CORS | Caching |
|-------|-------|------|------|---------|
| Owner | `/auth/*`, `/api/*`, `/dashboard` | JWT bearer | none | `no-store` |
| Customer site | `/widget.js`, `/static/widget.v{N}.js`, `/public/widgets/{id}/config` | none | `*` | 5 min / 1 year immutable / 60 s + ETag |
| Visitor | `POST /public/submissions` (+ `OPTIONS` preflight) | none | `*`, no credentials | `no-store` |

Layers: `app/api` (HTTP) -> `app/services` (logic) -> `app/repositories` (SQL), with `app/models`,
`app/schemas`, `app/providers` (geo, email) and `app/worker.py` (background job runner).

## Setup

### Quick start (Docker)

~~~bash
cp .env.example .env              # optional; PowerShell: Copy-Item .env.example .env
docker compose up -d --build      # db, mailpit, migrate (one-shot), api, worker, demo site
docker compose exec -T api python -m scripts.seed
~~~

If you create `.env`, replace the placeholder secrets first. Without it, compose uses insecure demo defaults.

| URL | What |
|-----|------|
| http://localhost:5500 | Customer site with the embedded widgets (a different origin from the API) |
| http://localhost:5500/abuse.html | Browser page firing malformed, oversized, honeypot and burst requests |
| http://localhost:8000/dashboard | Owner dashboard (table view of the stats API) |
| http://localhost:8000/docs | OpenAPI docs |
| http://localhost:8025 | Mailpit inbox with the confirmation emails |

Demo accounts created by the seed:

| Tenant | Email | Password | Widgets |
|--------|-------|----------|---------|
| A (Acme Bakery) | owner@demo-widgets.dev | DemoOwner123! | `wgt_demo000001` (form), `wgt_demopopup1` (popover), 30 sample submissions |
| B (isolation check) | owner@other-widgets.dev | OtherOwner123! | `wgt_other00001` |

Stop with `docker compose down`, or `docker compose down -v` to also delete the data.

### Local development

~~~bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                  # then set real secrets
docker compose up -d db mailpit
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload --port 8000             # terminal 1
python -m http.server 5500 --directory demo-site      # terminal 2
python -m app.worker                                  # terminal 3 (EMAIL_MODE=smtp sends to Mailpit)
~~~

Postgres is published on host port 5433 so it does not clash with a local PostgreSQL on 5432.

## API

All errors use one envelope: `{"error": {"code": "...", "message": "...", "details": [...]}}`.
Bad input never produces a 500. Interactive docs: `/docs`.

### Auth
| Method | Path | Result |
|--------|------|--------|
| POST | `/auth/register` `{email, password, tenant_name}` | 201 `{access_token}`, 409 email taken, 422 |
| POST | `/auth/login` `{email, password}` | 200 `{access_token}`, 401 |

### Widgets (Bearer token, tenant scoped)
| Method | Path | Result |
|--------|------|--------|
| POST | `/api/widgets` | 201 widget |
| GET | `/api/widgets` | 200 list of your widgets |
| GET / PATCH / DELETE | `/api/widgets/{id}` | 200 / 200 / 204; another tenant's widget -> 404 |
| GET | `/api/widgets/{id}/embed` | 200 `{snippet}` |

Widget body: `type` (`signup_form` / `cta_popover`), `title`, `description`, `button_text`,
`fields` (1-20 of `{name, label, type: text|email|textarea, required, max_length}`),
`display_options` (`position`: inline / bottom-right / bottom-left / center, `theme`: light / dark),
`allowed_origins` (empty = any origin), `is_active`.

### Public delivery
| Method | Path | Caching |
|--------|------|---------|
| GET | `/widget.js?id=wgt_...` | `public, max-age=300`, ETag |
| GET | `/static/widget.v{N}.js` | `public, max-age=31536000, immutable`; a release adds a new N |
| GET | `/public/widgets/{public_id}/config` | `public, max-age=60`, ETag / 304; unknown or inactive -> 404 |

### Public submission
`POST /public/submissions` with `Content-Type: application/json` and an optional `Idempotency-Key` header:

~~~json
{"widget_id": "wgt_demo000001", "fields": {"email": "ann@example.com", "name": "Ann"}, "website": ""}
~~~

| Case | Status |
|------|--------|
| stored | 201 `{id, status: "received"}` |
| same Idempotency-Key again | 200 with the original id, no new row |
| honeypot `website` filled | 200 `{id: null}`, silently dropped |
| body over `MAX_BODY_BYTES` | 413 |
| not JSON content type | 415 |
| malformed JSON, unknown / missing / invalid / too long field | 422 with details |
| unknown or inactive widget | 404 |
| Origin not in the widget's `allowed_origins` | 403 |
| rate limit hit (per IP or per widget) | 429 + `Retry-After` |
| database unavailable | 503 + `Retry-After` |

### Dashboard (Bearer token, tenant scoped)
| Method | Path |
|--------|------|
| GET | `/api/submissions?widget_id=&limit=&before=` (keyset pagination via `next_before`) |
| GET | `/api/stats/summary` (totals, last 24 h / 7 d, per widget) |
| GET | `/api/stats/timeseries?bucket=day|hour&days=&widget_id=` (UTC, zero filled) |
| GET | `/api/stats/geo?widget_id=` (country counts; `Unknown` = not enriched) |

### Development only (`APP_ENV=development`)
| Method | Path | Purpose |
|--------|------|---------|
| GET / POST | `/dev/geo` `{mode: real|mock, a_down, b_down}` | switch geo providers at runtime |
| POST | `/dev/rate-limits/reset` | clear the in-memory limiters |

### Example

~~~bash
TOKEN=$(curl -s localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"owner@demo-widgets.dev","password":"DemoOwner123!"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s localhost:8000/public/submissions -H 'Origin: http://localhost:5500' -H 'Content-Type: application/json' \
  -d '{"widget_id":"wgt_demo000001","fields":{"email":"ann@example.com"}}'
curl -s "localhost:8000/api/stats/summary" -H "Authorization: Bearer $TOKEN"
~~~

## Proving the acceptance probes

| Probe | How |
|-------|-----|
| 1 valid cross-origin submission | Submit the form on http://localhost:5500, then `GET /api/submissions` as tenant A |
| 2 malformed / oversized | Buttons on the abuse page, or `pytest -k probe2` |
| 3 burst | 15 quick POSTs from one client -> 429s; a request from another client (different `X-Forwarded-For`) and `/health` still succeed |
| 4 geo fallback | `POST /dev/geo {"mode":"mock","a_down":true}` -> submit (enriched by `mock-b`); then `{"b_down":true}` -> stored without geo |
| 5 email failure | `FORCE_EMAIL_FAILURE=true docker compose up -d worker`, submit -> 201 and stored; worker log shows two `job_retry` lines and `ALERT job_dead` |
| 6 honeypot | POST with `"website":"http://spam.test"` -> 200 `{id: null}`, nothing stored |

## Tests

~~~bash
python -m pytest                                  # host; needs Postgres from DATABASE_URL
docker compose exec -T api python -m pytest -q    # inside the stack
~~~

The suite creates and migrates a separate `<db>_test` database, uses mock geo providers and needs no
network. It covers CORS, validation, rate limiting, the honeypot, the geo fallback chain, email
failure and retries, database outage, tenant isolation, caching and stats.

## Evidence

`python -m scripts.collect_evidence` runs every requirement against the live stack and rewrites
[EVIDENCE.md](EVIDENCE.md) with real request/response output and PASS/FAIL checks. Stop the Docker
worker first (`docker compose stop worker`), because the email probe drives the worker in-process.

## Configuration

See [.env.example](.env.example). Key settings:

| Variable | Default | Meaning |
|----------|---------|---------|
| `APP_ENV` | development | `development` mounts `/dev/*`; use `production` anywhere real |
| `JWT_SECRET`, `IP_HASH_SALT` | - | secrets; never commit them |
| `RATE_LIMIT_PER_IP` / `RATE_LIMIT_PER_WIDGET` | 10/minute / 60/minute | sliding windows |
| `MAX_BODY_BYTES` | 16384 | submission body limit |
| `TRUST_PROXY_HEADERS` | true in the demo | honour `X-Forwarded-For` for the client IP |
| `GEO_PROVIDER_MODE` | real | `real` (ip-api.com -> ipapi.co) or `mock` |
| `GEO_DEV_IP_OVERRIDE` | 8.8.8.8 | development only: geolocate this IP when the client IP is private |
| `WIDGET_BUNDLE_VERSION` | 1 | selects `static/widget.v{N}.js` |
| `EMAIL_MODE` | console | `console` or `smtp` (Mailpit); the Docker worker uses smtp |
| `FORCE_EMAIL_FAILURE` | false | makes every email send fail (probe 5) |
| `JOB_MAX_ATTEMPTS` | 3 | attempts before a job is marked dead |

## Project layout

~~~text
app/            FastAPI app, services, repositories, models, schemas, providers, worker
migrations/     Alembic migrations
static/         widget bundle (widget.v1.js) and dashboard page
demo-site/      second-origin customer site and abuse page
scripts/        seed and evidence generator
tests/          pytest suite
docs/DESIGN.md  design document
~~~

## Limitations

- The rate limiter is in-memory and per process: it resets on restart and is not shared between
  instances. A multi-instance deployment needs Redis or similar.
- `TRUST_PROXY_HEADERS=true` lets any client choose its IP through `X-Forwarded-For`. It exists so the
  probes can simulate many visitors locally; in production enable it only behind a trusted proxy.
  Without it, all host clients reach the Docker API through one gateway IP and share one per-IP budget.
- The `allowed_origins` check relies on the `Origin` header. It stops other websites in browsers, not
  scripted clients, which can send any header. Widget public ids are not secrets.
- Geo enrichment runs inline with a 2 s timeout per provider, so a hanging provider adds latency.
  Lookups are not cached, and the free tiers are rate limited (ip-api.com: 45 requests/min).
- `/dev/*` endpoints are for local demos and are only mounted when `APP_ENV=development`.
- Owner auth is minimal: no refresh tokens, password reset, roles or account management. Tokens expire
  after 60 minutes, and the dashboard keeps the token in memory only.
- Confirmation emails go to the address the visitor typed, without double opt-in. There are no GDPR
  export or delete endpoints.
- Dead jobs stay in the `jobs` table; there is no endpoint to requeue them.
- The widget UI is intentionally minimal (Shadow DOM, no build step or minification).
- Everything runs on localhost; nothing is deployed.

## License

MIT - see [LICENSE](LICENSE).