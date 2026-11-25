"""
Tests for the FastAPI app. TestClient runs the app in-process (no server),
and using it as a context manager also runs the lifespan startup/shutdown.
"""
from fastapi.testclient import TestClient

from config.settings import settings
from src.rtfm_agent.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
    }


def test_root_points_to_docs() -> None:
    with TestClient(app) as client:
        body = client.get("/").json()

    assert body["docs"] == "/docs"
    assert body["health"] == "/health"


def test_openapi_docs_available() -> None:
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
