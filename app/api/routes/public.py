from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.errors import AppError
from app.config import get_settings
from app.core.ip import client_ip
from app.db import SessionLocal
from app.services import submission_service
from app.services.submission_service import SubmissionContext, SubmissionResult

router = APIRouter(tags=["public"])


def _too_large(limit: int) -> AppError:
    return AppError(413, "payload_too_large", f"Request body exceeds {limit} bytes")


def _run_pipeline(ctx: SubmissionContext) -> SubmissionResult:
    db = SessionLocal()
    try:
        return submission_service.process_submission(db, ctx)
    finally:
        db.close()


@router.post("/public/submissions", status_code=201, summary="Public widget submission (CORS, rate limited)")
async def create_submission(request: Request) -> JSONResponse:
    limit = get_settings().max_body_bytes

    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise AppError(400, "bad_request", "Invalid Content-Length header")
        if int(declared) > limit:
            raise _too_large(limit)

    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise AppError(415, "unsupported_media_type", "Content-Type must be application/json")

    # Also enforce the limit while streaming (chunked bodies have no Content-Length).
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise _too_large(limit)

    ctx = SubmissionContext(
        body=bytes(body),
        ip=client_ip(request),
        origin=request.headers.get("origin"),
        idempotency_key=request.headers.get("idempotency-key"),
    )
    result = await run_in_threadpool(_run_pipeline, ctx)
    return JSONResponse(status_code=result.status_code, content=result.body, headers={"Cache-Control": "no-store"})