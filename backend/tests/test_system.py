from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.version import get_version


def test_health_reports_database_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_version_endpoint(client: TestClient) -> None:
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {"version": get_version()}


def test_database_file_created_by_migrations(client: TestClient, settings: Settings) -> None:
    assert (settings.data_dir / "fuel_tracker.db").is_file()


def test_unknown_ui_path_serves_index(client: TestClient) -> None:
    response = client.get("/fuel-ups/123")
    assert response.status_code == 200
    assert "Fuel Tracker" in response.text


def test_unknown_api_path_is_404(client: TestClient) -> None:
    assert client.get("/api/does-not-exist").status_code == 404
