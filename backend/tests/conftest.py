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
from tests.fake_email import EMAIL_URL, PUBLIC_URL, FakeEmailSender
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
    # No pause after a failure here: most tests switch the fake on and off between two calls
    return LubeLoggerClient(
        "http://lubelogger.test",
        pause_after_failure=0,
        transport=httpx.MockTransport(fake.handler),
    )


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


@pytest.fixture
def sender() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
async def mail_api(
    settings: Settings, fake_lubelogger: FakeLubeLogger, sender: FakeEmailSender
) -> AsyncIterator[AppUnderTest]:
    """The running app with email set up, sending into `sender`."""
    settings = settings.model_copy(
        update={"apprise_email_url": EMAIL_URL, "public_url": PUBLIC_URL}
    )
    app = create_app(settings, lubelogger=lubelogger_client(fake_lubelogger), email_sender=sender)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield AppUnderTest(app, client, app.state.services, fake_lubelogger)
