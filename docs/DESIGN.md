# Design - Embeddable Widget & Lead-Capture Platform

## 1. Problem
Customers (tenants) create embeddable widgets (signup form, CTA popover), paste one
`<script>` tag into any website, and receive visitor submissions. The submission API is
public: any origin, untrusted input, uncontrolled traffic. The backend must validate,
throttle, filter spam, enrich with geo data, store, and fire a notification, degrading
gracefully when dependencies fail.

## 2. Actors and request paths
| Actor | Path | Auth | CORS | Cache |
|-------|------|------|------|-------|
| Owner | `/auth/*`, `/api/*` (widgets, submissions, stats) | JWT bearer | none | no-store |
| Customer site | `GET /widget.js`, `GET /static/widget.v{N}.js`, `GET /public/widgets/{id}/config` | none | `*` | loader 5 min, bundle 1 year immutable, config 60 s + ETag |
| Visitor | `POST /public/submissions`, `OPTIONS` preflight | none | `*` (no credentials) + optional per-widget origin allow-list | no-store |

## 3. Data model (PostgreSQL, Alembic migrations)
Enum-like columns are VARCHAR with a CHECK constraint (no native Postgres enums).

**tenants**: `id uuid pk`, `name`, `created_at`

**users**: `id uuid pk`, `tenant_id fk -> tenants`, `email unique`, `password_hash (bcrypt)`, `created_at`

**widgets**
- `id uuid pk`, `public_id unique` (e.g. `wgt_8f3k2a9x1q`, used in the snippet)
- `tenant_id fk` (indexed), `type` in (`signup_form`, `cta_popover`)
- `title`, `description`, `button_text`
- `fields jsonb`: list of `{name, label, type: text|email|textarea, required, max_length}`
- `display_options jsonb`: `{position, theme}`
- `allowed_origins text[]`: empty = any origin
- `is_active`, `created_at`, `updated_at`

**submissions**
- `id uuid pk`, `widget_id fk`, `tenant_id fk` (denormalised so every owner query filters by tenant directly)
- `data jsonb` (validated field values only), `origin`, `ip_hash` (salted SHA-256; raw IP never stored)
- `country`, `city`, `geo_provider` (nullable)
- `idempotency_key` (nullable), `created_at`
- Indexes: `(tenant_id, created_at)`, `(widget_id, created_at)`, unique `(widget_id, idempotency_key)`

**jobs** (DB-backed queue for side effects)
- `id`, `kind`, `payload jsonb`, `status` in (`pending`, `running`, `done`, `dead`)
- `attempts`, `max_attempts`, `run_after`, `last_error`, `created_at`, `updated_at`
- Index `(status, run_after)`; worker claims rows with `SELECT ... FOR UPDATE SKIP LOCKED`

## 4. Embed flow
1. Owner creates a widget, then calls `GET /api/widgets/{id}/embed`, which returns
   `<script src="{API_BASE_URL}/widget.js?id=wgt_..." async></script>`
2. `/widget.js` is a tiny loader (short cache). It reads its own `id` and injects
   `/static/widget.v{N}.js` (long cache, immutable; new release -> new N -> new URL).
3. The bundle fetches `/public/widgets/{id}/config` (small JSON, 60 s cache, ETag / 304).
4. It renders a `<div>` + `<form>` with the configured fields plus a hidden honeypot input `website`.
5. On submit it POSTs JSON to `/public/submissions` with an `Idempotency-Key` header (UUID per submit).

## 5. API contracts
### Auth
- `POST /auth/register` `{email, password, tenant_name}` -> 201 `{access_token}` | 409 email taken
- `POST /auth/login` `{email, password}` -> 200 `{access_token}` | 401

### Widgets (JWT, tenant-scoped)
- `POST /api/widgets` -> 201; `GET /api/widgets` -> 200 list
- `GET | PATCH | DELETE /api/widgets/{id}` -> 200 / 200 / 204
- Another tenant's widget -> **404** (existence is not leaked)
- `GET /api/widgets/{id}/embed` -> 200 `{snippet}`

### Public delivery
- `GET /widget.js?id=` -> JS, `Cache-Control: public, max-age=300`
- `GET /static/widget.v{N}.js` -> JS, `Cache-Control: public, max-age=31536000, immutable`
- `GET /public/widgets/{public_id}/config` -> 200 small JSON, `Cache-Control: public, max-age=60`, `ETag`; inactive/unknown -> 404

### Public submission
`POST /public/submissions`

~~~json
{ "widget_id": "wgt_8f3k2a9x1q", "fields": { "email": "a@b.com", "name": "Ann" }, "website": "" }
~~~

| Case | Response |
|------|----------|
| stored | **201** `{id, status: "received"}` |
| same Idempotency-Key replayed | **200** original body, no new row |
| honeypot filled | **200** `{status: "received"}`, silently not stored, logged |
| body > MAX_BODY_BYTES | **413** |
| not `application/json` | **415** |
| malformed JSON, schema or field validation fails, unknown field | **422** |
| unknown or inactive widget | **404** |
| origin not in widget allow-list | **403** |
| rate limit hit | **429** + `Retry-After` |

All errors use `{"error": {"code": "...", "message": "..."}}`. Bad input never produces a 500.

### Dashboard (JWT, tenant-scoped)
- `GET /api/submissions?widget_id=&limit=&before=`
- `GET /api/stats/summary`: totals and per-widget counts
- `GET /api/stats/timeseries?bucket=day|hour&widget_id=`
- `GET /api/stats/geo?widget_id=`: counts by country

### Ops
- `GET /health` -> DB connectivity

## 6. Submission pipeline (cheap checks first)
1. Body size guard (413) and content-type check (415)
2. Per-IP rate limit (429), before parsing, so floods are cheap to reject
3. Pydantic envelope validation (422)
4. Widget lookup (404) + origin allow-list (403)
5. Per-widget rate limit (429)
6. Field validation against the widget's own field definitions (422)
7. Honeypot check -> silent drop
8. Idempotency lookup -> replay
9. Geo enrichment: provider A -> provider B -> none (timeouts; errors never propagate)
10. One transaction: insert submission + insert `send_confirmation_email` job
11. 201

## 7. Resilience
- **Geo fallback chain:** providers share one interface `lookup(ip) -> Geo | None`; the chain
  catches every exception/timeout and moves on. Mock mode gives deterministic A/B up/down via env flags.
- **Side effects:** email runs in a separate worker process, never in the request. Retries with
  exponential backoff up to JOB_MAX_ATTEMPTS; final failure -> status `dead` + ERROR log (the failure alert).
  `FORCE_EMAIL_FAILURE=true` proves the submission is unaffected.
- **Rate limiting:** in-memory sliding window keyed by IP hash and widget id.

## 8. Layers

~~~text
app/api/            HTTP: routers, error handlers, auth dependency
app/services/       business logic: auth, widgets, submission pipeline, stats
app/repositories/   data access (every owner query takes tenant_id)
app/models/         SQLAlchemy models
app/schemas/        Pydantic request/response contracts
app/core/           security (bcrypt, JWT)
app/providers/      geo providers + fallback chain, email senders
app/worker.py       background job runner
migrations/         Alembic migrations
static/             widget loader + versioned bundle
demo-site/          second-origin customer test page (served on :5500)
~~~

## 9. Non-goals
- **No form-builder UI and no hosted CDN.** Widgets are created via the API; the customer site is a local HTML page on a second port.
- No visitor accounts, CAPTCHA, or GDPR export flows in the core (possible stretch goals).
- No multi-instance deployment; the in-memory rate limiter is a documented single-instance limitation.

## 10. Changes made during Phase 2
- Honeypot check moved before per-field validation, so bots get a fake success and no validation feedback.
- IP hash is HMAC-SHA256 keyed with `IP_HASH_SALT`; raw IPs are never stored or logged.
- `TRUST_PROXY_HEADERS`: X-Forwarded-For is honoured only when explicitly enabled (locally to simulate many visitors; in production only behind a trusted proxy).
- Dev-only endpoints, mounted only when `APP_ENV=development`: `GET/POST /dev/geo` switches real/mock providers and toggles mock A/B down at runtime; `POST /dev/rate-limits/reset`.
- Only allowed requests consume rate-limit budget; rejected ones do not.
- Geo enrichment runs inline with a per-provider timeout (worst case about 2x timeout of added latency when both real providers hang).
- Email is a transactional outbox: the job row is committed with the submission; the worker sends it later with exponential backoff and marks it `dead` + ERROR log after `JOB_MAX_ATTEMPTS`.
- Database outage: 3 s connect timeout, TCP keepalives and a 10 s statement timeout; the API answers 503 `service_unavailable` with `Retry-After` instead of hanging; `worker --drain` exits with code 1 after 3 consecutive DB errors.
- Dashboard pagination is keyset on `created_at` via the `before` cursor.