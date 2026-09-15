"""Test setup: a throwaway <db>_test database migrated with Alembic, mock geo, no network."""

import os
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("IP_HASH_SALT", "test-only-ip-hash-salt")

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.config import Settings  # noqa: E402  (importing config does not create the DB engine)

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = make_url(Settings().database_url)
TEST_DB = f"{BASE_URL.database or 'widget_platform'}_test"

os.environ.update(
    {
        "DATABASE_URL": BASE_URL.set(database=TEST_DB).render_as_string(hide_password=False),
        "APP_ENV": "development",
        "API_BASE_URL": "http://localhost:8000",
        "WIDGET_BUNDLE_VERSION": "1",
        "TRUST_PROXY_HEADERS": "true",
        "RATE_LIMIT_PER_IP": "10/minute",
        "RATE_LIMIT_PER_WIDGET": "60/minute",
        "MAX_BODY_BYTES": "16384",
        "GEO_PROVIDER_MODE": "mock",
        "MOCK_GEO_A_DOWN": "false",
        "MOCK_GEO_B_DOWN": "false",
        "GEO_DEV_IP_OVERRIDE": "",
        "EMAIL_MODE": "console",
        "FORCE_EMAIL_FAILURE": "false",
        "JOB_MAX_ATTEMPTS": "3",
    }
)

from tests.helpers import create_widget, register  # noqa: E402


def _recreate_test_database() -> None:
    admin = create_engine(
        BASE_URL.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def database():
    try:
        _recreate_test_database()
    except OperationalError as exc:
        pytest.exit(
            f"PostgreSQL not reachable at {BASE_URL.host}:{BASE_URL.port} ({type(exc.orig).__name__}). "
            "Start it with: docker compose up -d db",
            returncode=3,
        )
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    yield
    from app.db import engine

    engine.dispose()


@pytest.fixture(autouse=True)
def clean_state(database):
    from app.config import get_settings
    from app.core.rate_limit import reset_all_limiters
    from app.db import engine
    from app.providers import geo

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE submissions, jobs, widgets, users, tenants RESTART IDENTITY CASCADE"))
    reset_all_limiters()
    geo.update_geo_state(mode="mock", a_down=False, b_down=False)
    settings = get_settings()
    settings.force_email_failure = False
    yield
    settings.force_email_failure = False


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def owner(client) -> dict:
    return register(client, "owner")


@pytest.fixture
def widget(client, owner) -> dict:
    return create_widget(client, owner)