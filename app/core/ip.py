import hashlib
import hmac
import ipaddress

from starlette.requests import Request

from app.config import get_settings


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip(request: Request) -> str:
    """Client IP. X-Forwarded-For is only honoured when TRUST_PROXY_HEADERS is enabled."""
    if get_settings().trust_proxy_headers:
        candidate = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if candidate and _is_ip(candidate):
            return candidate
    if request.client and request.client.host:
        return request.client.host
    return "0.0.0.0"


def hash_ip(ip: str) -> str:
    """Keyed hash so raw visitor IPs are never stored."""
    salt = get_settings().ip_hash_salt.encode("utf-8")
    return hmac.new(salt, ip.encode("utf-8"), hashlib.sha256).hexdigest()


def is_public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False