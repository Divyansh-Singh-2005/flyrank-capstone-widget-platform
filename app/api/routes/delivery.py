import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.config import get_settings
from app.db import get_db
from app.repositories import widgets as widgets_repo
from app.schemas.submission import WIDGET_PUBLIC_ID_PATTERN

router = APIRouter(tags=["delivery"])

STATIC_DIR = Path(__file__).resolve().parents[3] / "static"
WIDGET_ID_RE = re.compile(WIDGET_PUBLIC_ID_PATTERN)
JS_MEDIA_TYPE = "application/javascript; charset=utf-8"
LOADER_CACHE = "public, max-age=300"
BUNDLE_CACHE = "public, max-age=31536000, immutable"
CONFIG_CACHE = "public, max-age=60"
HONEYPOT_FIELD = "website"

LOADER_TEMPLATE = """/* widget loader: tiny and short-cached; pulls the immutable versioned bundle */
(function () {
  var s = document.currentScript;
  if (!s || !s.src) { return; }
  var src = new URL(s.src);
  var id = src.searchParams.get("id");
  if (!id) { return; }
  var queue = window.__flyrankWidgets = window.__flyrankWidgets || [];
  queue.push({ id: id, base: src.origin, script: s });
  if (window.__flyrankProcess) { window.__flyrankProcess(); return; }
  if (window.__flyrankBundleRequested) { return; }
  window.__flyrankBundleRequested = true;
  var bundle = document.createElement("script");
  bundle.src = src.origin + "/static/widget.v__VERSION__.js";
  bundle.async = true;
  document.head.appendChild(bundle);
})();
"""


def _etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def _etag_matches(if_none_match: str, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    candidates = [part.strip().removeprefix("W/") for part in if_none_match.split(",")]
    return etag in candidates


def cached_response(request: Request, body: bytes, media_type: str, cache_control: str) -> Response:
    etag = _etag(body)
    headers = {"Cache-Control": cache_control, "ETag": etag}
    if _etag_matches(request.headers.get("if-none-match", ""), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type=media_type, headers=headers)


@router.get("/widget.js", summary="Embed loader (5 min cache)")
def widget_loader(
    request: Request,
    widget_id: str = Query(alias="id", pattern=WIDGET_PUBLIC_ID_PATTERN),
) -> Response:
    version = get_settings().widget_bundle_version
    body = LOADER_TEMPLATE.replace("__VERSION__", str(version)).encode("utf-8")
    return cached_response(request, body, JS_MEDIA_TYPE, LOADER_CACHE)


@router.get("/static/widget.v{version}.js", summary="Versioned widget bundle (immutable)")
def widget_bundle(version: str, request: Request) -> Response:
    if not (version.isascii() and version.isdigit() and len(version) <= 6 and int(version) >= 1):
        raise AppError(404, "not_found", "Unknown widget bundle version")
    path = STATIC_DIR / f"widget.v{int(version)}.js"
    if not path.is_file():
        raise AppError(404, "not_found", "Unknown widget bundle version")
    return cached_response(request, path.read_bytes(), JS_MEDIA_TYPE, BUNDLE_CACHE)


@router.get("/public/widgets/{public_id}/config", summary="Public widget config (60 s cache + ETag)")
def widget_config(public_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    widget = widgets_repo.get_by_public_id(db, public_id) if WIDGET_ID_RE.fullmatch(public_id) else None
    if widget is None or not widget.is_active:
        raise AppError(404, "widget_not_found", "Widget not found")
    base = get_settings().api_base_url.rstrip("/")
    payload = {
        "public_id": widget.public_id,
        "type": widget.type,
        "title": widget.title,
        "description": widget.description,
        "button_text": widget.button_text,
        "fields": widget.fields,
        "display_options": widget.display_options,
        "honeypot_field": HONEYPOT_FIELD,
        "submit_url": f"{base}/public/submissions",
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return cached_response(request, body, "application/json", CONFIG_CACHE)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page() -> HTMLResponse:
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )