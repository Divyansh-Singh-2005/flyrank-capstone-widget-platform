from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.core.rate_limit import reset_all_limiters
from app.providers import geo

# Mounted only when APP_ENV=development (see app/main.py).
router = APIRouter(prefix="/dev", tags=["dev (development only)"])


class GeoStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["real", "mock"] | None = None
    a_down: bool | None = None
    b_down: bool | None = None


@router.get("/geo")
def get_geo_state() -> dict:
    return geo.get_geo_state()


@router.post("/geo")
def set_geo_state(update: GeoStateUpdate) -> dict:
    return geo.update_geo_state(**update.model_dump())


@router.post("/rate-limits/reset")
def reset_rate_limits() -> dict:
    reset_all_limiters()
    return {"status": "reset"}