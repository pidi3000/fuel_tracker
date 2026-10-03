from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport

from app.core.config import Settings
from app.main import create_app
from app.services.container import Services
from app.services.lubelogger import LubeLoggerClient
from tests.fake_lubelogger import FakeLubeLogger


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>Fuel Tracker</title>")
    return Settings(
        data_dir=tmp_path / "data",
        static_dir=static_dir,
        # Tests run the background work themselves, when they want it
        background_workers=False,
        lubelogger_url="http://lubelogger.test",
        review_before_send=False,
    )


@pytest.fixture
def fake_lubelogger() -> FakeLubeLogger:
    return FakeLubeLogger()


def lubelogger_client(fake: FakeLubeLogger) -> LubeLoggerClient:
    return LubeLoggerClient("http://lubelogger.test", transport=httpx.MockTransport(fake.handler))


@pytest.fixture
def client(settings: Settings, fake_lubelogger: FakeLubeLogger) -> Iterator[TestClient]:
    app = create_app(settings, lubelogger=lubelogger_client(fake_lubelogger))
    with TestClient(app) as client:
        yield client


@dataclass
class AppUnderTest:
    app: FastAPI
    client: httpx.AsyncClient
    services: Services
    fake: FakeLubeLogger


@pytest.fixture
async def api(settings: Settings, fake_lubelogger: FakeLubeLogger) -> AsyncIterator[AppUnderTest]:
    """The running app with an async HTTP client, for tests that also drive background work."""
    app = create_app(settings, lubelogger=lubelogger_client(fake_lubelogger))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield AppUnderTest(app, client, app.state.services, fake_lubelogger)
