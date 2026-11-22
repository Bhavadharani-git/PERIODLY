import os
from pathlib import Path

# Keep the smoke test isolated from a developer's or school's real local DB.
TEST_DB = Path(__file__).resolve().parent / "periodly_test.db"
os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"

from fastapi.testclient import TestClient
from app.main import app


def test_health_checks_database():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == "connected"


def test_frontend_is_served_from_same_origin():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "PERIODLY" in response.text


def test_api_docs_are_available_in_non_production_mode():
    response = TestClient(app).get("/docs")
    assert response.status_code == 200


def test_production_api_requires_admin_key(monkeypatch):
    import app.main as main_module

    secret = "test-only-secret-that-is-at-least-32-chars"
    monkeypatch.setattr(main_module, "IS_PRODUCTION", True)
    monkeypatch.setattr(main_module, "PERIODLY_API_KEY", secret)
    client = TestClient(main_module.app)

    denied = client.get("/api/schools")
    assert denied.status_code == 401

    allowed = client.get("/api/schools", headers={"X-API-Key": secret})
    assert allowed.status_code == 200

    # The lightweight health endpoint remains available to hosting health checks.
    health = client.get("/api/health")
    assert health.status_code == 200
