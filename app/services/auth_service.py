from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Tenant, User
from app.repositories import users as users_repo
from app.schemas.auth import LoginRequest, RegisterRequest


def _email_taken() -> AppError:
    return AppError(409, "email_taken", "An account with this email already exists")


def register(db: Session, data: RegisterRequest) -> str:
    email = data.email.lower()
    if users_repo.get_by_email(db, email) is not None:
        raise _email_taken()

    tenant = Tenant(name=data.tenant_name)
    db.add(tenant)
    db.flush()
    user = User(tenant_id=tenant.id, email=email, password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _email_taken()
    return create_access_token(user.id, tenant.id)


def login(db: Session, data: LoginRequest) -> str:
    user = users_repo.get_by_email(db, data.email.lower())
    if user is None or not verify_password(data.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Invalid email or password")
    return create_access_token(user.id, user.tenant_id)