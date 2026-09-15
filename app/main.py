import logging

from fastapi import FastAPI, Request

from app.api.errors import register_error_handlers
from app.api.routes import auth, health, widgets

PRIVATE_PREFIXES = ("/api/", "/auth/")


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Widget & Lead-Capture Platform", version="0.1.0")
    register_error_handlers(app)

    @app.middleware("http")
    async def no_store_for_private_routes(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(PRIVATE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(widgets.router)
    return app


app = create_app()