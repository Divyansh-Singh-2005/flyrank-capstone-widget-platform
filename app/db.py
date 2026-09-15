from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Fail fast when the database is unreachable instead of hanging requests for minutes.
CONNECT_ARGS = {
    "connect_timeout": 3,
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 3,
    "keepalives_count": 3,
    "options": "-c statement_timeout=10000",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_timeout=5,
    connect_args=CONNECT_ARGS,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()