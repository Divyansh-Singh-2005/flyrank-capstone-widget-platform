import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import AppError
from app.core.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID


def _unauthorized() -> AppError:
    return AppError(401, "unauthorized", "Missing or invalid bearer token", {"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        payload = decode_access_token(credentials.credentials)
        return CurrentUser(user_id=uuid.UUID(payload["sub"]), tenant_id=uuid.UUID(payload["tid"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _unauthorized()