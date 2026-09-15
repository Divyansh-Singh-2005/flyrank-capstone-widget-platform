import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.api.errors import register_error_handlers
from app.api.routes import auth, dev, health, public, submissions, widgets
from app.config import get_settings

logger = logging.getLogger("app")

PRIVATE_PREFIXES = ("/api/", "/auth/")
PUBLIC_PREFIXES = ("/public/", "/widget.js", "/static/")
PREFLIGHT_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
    "Access-Control-Max-Age": "600",
}


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Widget & Lead-Capture Platform", version="0.2.0")
    register_error_handlers(app)

    @app.middleware("http")
    async def cors_and_cache_policy(request: Request, call_next):
        path = request.url.path
        is_public = path.startswith(PUBLIC_PREFIXES)

        # Public paths: any origin, no credentials. Private paths: no CORS at all.
        if is_public and request.method == "OPTIONS" and "access-control-request-method" in request.headers:
            return Response(status_code=204, headers=PREFLIGHT_HEADERS)

        response = await call_next(request)
        if is_public:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Expose-Headers"] = "Retry-After"
        elif path.startswith(PRIVATE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(widgets.router)
    app.include_router(submissions.router)
    app.include_router(public.router)
    if settings.app_env == "development":
        app.include_router(dev.router)
        logger.warning("APP_ENV=development: /dev endpoints are enabled")
    return app


app = create_app()