from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_base_url: str = "http://localhost:8000"
    widget_bundle_version: int = 1

    database_url: str

    jwt_secret: str
    jwt_expire_minutes: int = 60

    ip_hash_salt: str
    trust_proxy_headers: bool = False

    rate_limit_per_ip: str = "10/minute"
    rate_limit_per_widget: str = "60/minute"
    max_body_bytes: int = 16384

    geo_provider_mode: str = "real"
    geo_provider_a_url: str = "http://ip-api.com/json"
    geo_provider_b_url: str = "https://ipapi.co"
    geo_timeout_seconds: float = 2.0
    mock_geo_a_down: bool = False
    mock_geo_b_down: bool = False
    geo_dev_ip_override: str = ""

    email_mode: str = "console"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    force_email_failure: bool = False
    job_max_attempts: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()