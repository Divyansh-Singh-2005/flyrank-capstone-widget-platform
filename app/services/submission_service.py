import json
import logging
import math
import re
import uuid
from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.config import get_settings
from app.core.ip import hash_ip, is_public_ip
from app.core.rate_limit import ip_limiter, widget_limiter
from app.models import Submission
from app.providers import geo
from app.repositories import jobs as jobs_repo
from app.repositories import submissions as submissions_repo
from app.repositories import widgets as widgets_repo
from app.schemas.submission import SubmissionEnvelope

logger = logging.getLogger("app.submissions")

IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9_-]{8,100}")
EMAIL_JOB_KIND = "send_confirmation_email"


@dataclass(frozen=True)
class SubmissionContext:
    body: bytes
    ip: str
    origin: str | None
    idempotency_key: str | None


@dataclass(frozen=True)
class SubmissionResult:
    status_code: int
    body: dict


def _rate_limited(scope: str, retry_after: float) -> AppError:
    seconds = max(1, math.ceil(retry_after))
    return AppError(
        429,
        "rate_limited",
        f"Too many submissions ({scope} limit). Retry later.",
        headers={"Retry-After": str(seconds)},
    )


def _parse_envelope(body: bytes) -> SubmissionEnvelope:
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise AppError(422, "invalid_json", "Request body is not valid JSON")
    try:
        return SubmissionEnvelope.model_validate(payload)
    except ValidationError as exc:
        details = [
            {"loc": [str(p) for p in err.get("loc", ())], "msg": err.get("msg", ""), "type": err.get("type", "")}
            for err in exc.errors()
        ]
        raise AppError(422, "validation_error", "Submission envelope is invalid", details=details)


def _check_idempotency_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise AppError(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key must be 8-100 characters: letters, digits, '-' or '_'",
        )
    return key


def _has_bad_chars(value: str, allow_newlines: bool) -> bool:
    for ch in value:
        code = ord(ch)
        if ch == "\t":
            continue
        if ch in "\r\n":
            if allow_newlines:
                continue
            return True
        # control chars (NUL breaks Postgres JSONB) and lone surrogates (break UTF-8 encoding)
        if code < 32 or code == 127 or 0xD800 <= code <= 0xDFFF:
            return True
    return False


def _validate_fields(field_defs: list[dict], values: dict[str, str]) -> dict[str, str]:
    defs = {spec["name"]: spec for spec in field_defs}
    errors: list[dict] = []

    def fail(name: str, message: str, kind: str) -> None:
        errors.append({"loc": ["fields", name], "msg": message, "type": kind})

    for name in sorted(set(values) - set(defs)):
        fail(name, "Unknown field", "extra_forbidden")

    cleaned: dict[str, str] = {}
    for name, spec in defs.items():
        value = values.get(name, "").strip()
        if not value:
            if spec.get("required"):
                fail(name, "Field is required", "missing")
            continue
        field_type = spec.get("type", "text")
        max_length = spec.get("max_length", 255)
        if _has_bad_chars(value, allow_newlines=field_type == "textarea"):
            fail(name, "Field contains invalid characters", "invalid_characters")
            continue
        if len(value) > max_length:
            fail(name, f"Field exceeds {max_length} characters", "too_long")
            continue
        if field_type == "email":
            try:
                value = validate_email(value, check_deliverability=False).normalized
            except EmailNotValidError:
                fail(name, "Invalid email address", "invalid_email")
                continue
        cleaned[name] = value

    if errors:
        raise AppError(422, "validation_error", "Submission fields are invalid", details=errors)
    return cleaned


def _enrich(ip: str) -> geo.Geo | None:
    """Never raises: geo is optional data."""
    settings = get_settings()
    try:
        state = geo.get_geo_state()
        lookup_ip = ip
        if state["mode"] != "mock" and not is_public_ip(ip):
            if settings.app_env == "development" and settings.geo_dev_ip_override:
                lookup_ip = settings.geo_dev_ip_override
            else:
                return None
        return geo.build_chain(state).lookup(lookup_ip)
    except Exception:
        logger.exception("geo_enrichment_crashed; continuing without geo")
        return None


def _replay(existing: Submission) -> SubmissionResult:
    return SubmissionResult(200, {"id": str(existing.id), "status": "received"})


def process_submission(db: Session, ctx: SubmissionContext) -> SubmissionResult:
    settings = get_settings()
    ip_hash = hash_ip(ctx.ip)

    # Cheap rejection first: per-IP limit before parsing anything.
    retry = ip_limiter().hit(ip_hash)
    if retry is not None:
        logger.warning("rate_limited scope=ip ip_hash=%s", ip_hash[:12])
        raise _rate_limited("per-IP", retry)

    envelope = _parse_envelope(ctx.body)
    idempotency_key = _check_idempotency_key(ctx.idempotency_key)

    widget = widgets_repo.get_by_public_id(db, envelope.widget_id)
    if widget is None or not widget.is_active:
        raise AppError(404, "widget_not_found", "Widget not found")

    origin = (ctx.origin or "").strip().lower()
    if widget.allowed_origins and origin not in widget.allowed_origins:
        raise AppError(403, "origin_not_allowed", "This origin is not allowed to submit to this widget")

    retry = widget_limiter().hit(widget.public_id)
    if retry is not None:
        logger.warning("rate_limited scope=widget widget=%s", widget.public_id)
        raise _rate_limited("per-widget", retry)

    # Honeypot before field validation: bots get a fake success and no validation feedback.
    if envelope.website.strip():
        logger.warning("honeypot_triggered widget=%s ip_hash=%s", widget.public_id, ip_hash[:12])
        return SubmissionResult(200, {"id": None, "status": "received"})

    data = _validate_fields(widget.fields, envelope.fields)

    if idempotency_key:
        existing = submissions_repo.get_by_idempotency_key(db, widget.id, idempotency_key)
        if existing is not None:
            return _replay(existing)

    geo_result = _enrich(ctx.ip)

    submission = Submission(
        id=uuid.uuid4(),
        widget_id=widget.id,
        tenant_id=widget.tenant_id,
        data=data,
        origin=origin[:255] or None,
        ip_hash=ip_hash,
        country=geo_result.country if geo_result else None,
        city=geo_result.city if geo_result else None,
        geo_provider=geo_result.provider if geo_result else None,
        idempotency_key=idempotency_key,
    )
    db.add(submission)
    # Transactional outbox: the email job is committed atomically with the row,
    # but sent later by the worker, so email failures can never touch this response.
    jobs_repo.enqueue(db, EMAIL_JOB_KIND, {"submission_id": str(submission.id)}, settings.job_max_attempts)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing = submissions_repo.get_by_idempotency_key(db, widget.id, idempotency_key)
            if existing is not None:
                return _replay(existing)
        raise

    logger.info(
        "submission_stored id=%s widget=%s geo_provider=%s",
        submission.id,
        widget.public_id,
        submission.geo_provider,
    )
    return SubmissionResult(201, {"id": str(submission.id), "status": "received"})