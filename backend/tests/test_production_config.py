import os
import subprocess
import sys


def run_config(env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)
    env.pop("PERIODLY_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
    )


def test_production_rejects_missing_admin_key():
    result = run_config({
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://periodly:very-long-random-database-password-123456@localhost/periodly",
    })
    assert result.returncode != 0
    assert "PERIODLY_API_KEY" in result.stderr


def test_production_rejects_sqlite():
    env = os.environ.copy()
    env.update({
        "APP_ENV": "production",
        "DATABASE_URL": "sqlite:///periodly.db",
        "PERIODLY_API_KEY": "unit-test-secret-which-is-longer-than-32-chars",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "PostgreSQL" in result.stderr


def test_production_rejects_wildcard_cors():
    env = os.environ.copy()
    env.update({
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://periodly:very-long-random-database-password-123456@localhost/periodly",
        "PERIODLY_API_KEY": "unit-test-secret-which-is-longer-than-32-chars",
        "CORS_ORIGINS": "*",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Wildcard CORS" in result.stderr


def test_production_rejects_non_origin_https_cors_value():
    env = os.environ.copy()
    env.update({
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://periodly:very-long-random-database-password-123456@localhost/periodly",
        "PERIODLY_API_KEY": "unit-test-secret-which-is-longer-than-32-chars",
        "CORS_ORIGINS": "https://school.example.com/path",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "exact HTTPS origin" in result.stderr


def test_production_rejects_reused_database_and_api_secret():
    shared_secret = "shared-secret-that-is-long-enough-for-production-123456"
    env = os.environ.copy()
    env.update({
        "APP_ENV": "production",
        "DATABASE_URL": f"postgresql+psycopg://periodly:{shared_secret}@localhost/periodly",
        "PERIODLY_API_KEY": shared_secret,
    })
    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must be different secrets" in result.stderr
