"""Environment-driven runtime configuration for PERIODLY."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./periodly.db").strip()
PERIODLY_API_KEY = os.getenv("PERIODLY_API_KEY", "").strip()

def _csv_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]

if IS_PRODUCTION:
    if (not PERIODLY_API_KEY or len(PERIODLY_API_KEY) < 32
            or PERIODLY_API_KEY.upper().startswith(("REPLACE", "CHANGE_ME", "YOUR_"))):
        raise RuntimeError(
            "Production requires PERIODLY_API_KEY with at least 32 characters. "
            'Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\"'
        )
    if not DATABASE_URL.startswith("postgresql+psycopg://"):
        raise RuntimeError(
            "Production requires a persistent PostgreSQL DATABASE_URL; SQLite is only for local development."
        )
    _db_url = urlsplit(DATABASE_URL)
    _db_password = _db_url.password or ""
    if (not _db_url.hostname or not _db_url.path.strip("/") or len(_db_password) < 24
            or _db_password.upper().startswith(("REPLACE", "CHANGE_ME", "YOUR_"))):
        raise RuntimeError(
            "Production DATABASE_URL must include a database name and a non-placeholder password "
            "of at least 24 characters. Use a unique random secret."
        )
    if PERIODLY_API_KEY == _db_password:
        raise RuntimeError("PERIODLY_API_KEY and the PostgreSQL password must be different secrets.")
    CORS_ORIGINS = _csv_env("CORS_ORIGINS")
    if "*" in CORS_ORIGINS:
        raise RuntimeError("Wildcard CORS origins are not allowed in production.")
    for origin in CORS_ORIGINS:
        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path not in ("", "/")
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise RuntimeError(
                "Each production CORS origin must be an exact HTTPS origin, such as https://school.example.com. "
                "Leave CORS_ORIGINS empty for same-origin hosting."
            )
else:
    CORS_ORIGINS = _csv_env(
        "CORS_ORIGINS",
        "null,http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000",
    )
