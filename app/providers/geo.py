import logging
import threading
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import get_settings

logger = logging.getLogger("app.geo")
USER_AGENT = "flyrank-widget-platform/0.1"


class GeoProviderError(Exception):
    pass


@dataclass(frozen=True)
class Geo:
    country: str | None
    city: str | None
    provider: str


def _clip(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


class GeoProvider(Protocol):
    name: str

    def lookup(self, ip: str) -> Geo: ...


class IpApiProvider:
    """Provider A: ip-api.com (free, no key, 45 req/min)."""

    name = "ip-api"

    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def lookup(self, ip: str) -> Geo:
        response = httpx.get(
            f"{self.base_url}/{ip}",
            params={"fields": "status,message,country,city"},
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise GeoProviderError(f"lookup failed: {data.get('message', 'unknown')}")
        return Geo(_clip(data.get("country"), 80), _clip(data.get("city"), 120), self.name)


class IpapiCoProvider:
    """Provider B: ipapi.co (free tier, no key)."""

    name = "ipapi.co"

    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def lookup(self, ip: str) -> Geo:
        response = httpx.get(
            f"{self.base_url}/{ip}/json/",
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise GeoProviderError(f"lookup failed: {data.get('reason', 'unknown')}")
        return Geo(_clip(data.get("country_name"), 80), _clip(data.get("city"), 120), self.name)


class MockGeoProvider:
    """Deterministic provider for proving the fallback chain."""

    def __init__(self, name: str, down: bool, country: str, city: str):
        self.name = name
        self.down = down
        self.country = country
        self.city = city

    def lookup(self, ip: str) -> Geo:
        if self.down:
            raise GeoProviderError(f"{self.name} is down (mock toggle)")
        return Geo(self.country, self.city, self.name)


class GeoChain:
    """Try providers in order; any failure falls through; all down -> None."""

    def __init__(self, providers: list[GeoProvider]):
        self.providers = list(providers)

    def lookup(self, ip: str) -> Geo | None:
        for provider in self.providers:
            try:
                result = provider.lookup(ip)
            except Exception as exc:  # timeouts, HTTP errors, bad JSON: all degrade
                # Only log our own messages; httpx messages contain the URL (and the IP).
                detail = str(exc) if isinstance(exc, GeoProviderError) else type(exc).__name__
                logger.warning("geo_provider_failed provider=%s error=%s", provider.name, detail)
                continue
            logger.info("geo_provider_ok provider=%s", provider.name)
            return result
        logger.warning("geo_all_providers_failed; storing submission without geo")
        return None


# Runtime state: starts from .env, can be switched via the dev-only /dev/geo endpoint.
_state_lock = threading.Lock()
_state: dict | None = None


def get_geo_state() -> dict:
    global _state
    with _state_lock:
        if _state is None:
            settings = get_settings()
            _state = {
                "mode": settings.geo_provider_mode,
                "a_down": settings.mock_geo_a_down,
                "b_down": settings.mock_geo_b_down,
            }
        return dict(_state)


def update_geo_state(**changes: object) -> dict:
    get_geo_state()
    with _state_lock:
        for key, value in changes.items():
            if value is not None:
                _state[key] = value
        return dict(_state)


def build_chain(state: dict | None = None) -> GeoChain:
    state = state or get_geo_state()
    if state["mode"] == "mock":
        return GeoChain(
            [
                MockGeoProvider("mock-a", bool(state["a_down"]), "Mockland", "Alpha City"),
                MockGeoProvider("mock-b", bool(state["b_down"]), "Mockland", "Beta City"),
            ]
        )
    settings = get_settings()
    return GeoChain(
        [
            IpApiProvider(settings.geo_provider_a_url, settings.geo_timeout_seconds),
            IpapiCoProvider(settings.geo_provider_b_url, settings.geo_timeout_seconds),
        ]
    )