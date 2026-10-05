import httpx
import pytest

from app.models import Notification
from app.services.updates import (
    UpdateChecker,
    announce_update,
    channel_of,
)
from tests.conftest import AppUnderTest
from tests.helpers import create_user, sign_in_admin, sign_in_as

IMAGE = "ghcr.io/owner/fuel_tracker"
REVISION = "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0"


class FakeRegistry:
    """Answers like ghcr.io: a token, tags, manifests and config blobs behind a redirect."""

    def __init__(self, tags=None, revision=REVISION, index=False) -> None:
        self.tags = tags if tags is not None else ["test", "0.1.0", "0.2.0", "0.2", "latest"]
        self.revision = revision
        self.index = index
        self.denied = False
        self.down = False
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("no network", request=request)
        path = request.url.path
        if request.url.host == "blobs.example":  # where the blob redirect leads
            assert "authorization" not in request.headers, "the token must not leave the registry"
            return httpx.Response(
                200,
                json={"config": {"Labels": {"org.opencontainers.image.revision": self.revision}}},
            )
        if self.denied:
            return httpx.Response(403, json={"errors": [{"code": "DENIED"}]})
        if path == "/token":
            assert request.url.params["scope"] == "repository:owner/fuel_tracker:pull"
            return httpx.Response(200, json={"token": "t0ken"})
        assert request.headers["authorization"] == "Bearer t0ken"
        if path == "/v2/owner/fuel_tracker/tags/list":
            return httpx.Response(200, json={"name": "owner/fuel_tracker", "tags": self.tags})
        if path == "/v2/owner/fuel_tracker/manifests/test" and self.index:
            return httpx.Response(
                200,
                json={
                    "manifests": [
                        {"digest": "sha256:att", "platform": {"os": "unknown"}},
                        {"digest": "sha256:img", "platform": {"os": "linux"}},
                    ]
                },
            )
        if path in (
            "/v2/owner/fuel_tracker/manifests/test",
            "/v2/owner/fuel_tracker/manifests/sha256:img",
        ):
            return httpx.Response(200, json={"config": {"digest": "sha256:cfg"}})
        if path == "/v2/owner/fuel_tracker/blobs/sha256:cfg":
            return httpx.Response(307, headers={"location": "https://blobs.example/cfg"})
        return httpx.Response(404)


def checker(version: str, registry: FakeRegistry) -> UpdateChecker:
    return UpdateChecker(IMAGE, version, transport=httpx.MockTransport(registry.handler))


@pytest.mark.parametrize(
    ("version", "channel"),
    [
        ("0.2.0", "release"),
        ("1.10.3", "release"),
        ("0.1.0-test+a1b2c3d", "test"),
        ("0.0.0-test+local", "dev"),
        ("0.2.0-dev", "dev"),
        ("unknown", "dev"),
    ],
)
def test_the_channel_follows_the_version(version: str, channel: str) -> None:
    assert channel_of(version) == channel


async def test_a_release_image_only_looks_at_release_tags() -> None:
    registry = FakeRegistry(
        tags=["test", "0.1.0", "0.1", "0", "latest", "0.2.0", "0.10.0", "1.0.0-rc1"]
    )
    status = await checker("0.2.0", registry).check()
    assert (status.channel, status.latest, status.available) == ("release", "0.10.0", True)
    assert status.error is None and status.checked_at is not None
    # The test image isn't looked at
    assert not any("manifests" in r.url.path for r in registry.requests)


async def test_the_newest_release_is_not_an_update() -> None:
    status = await checker("0.2.0", FakeRegistry(tags=["test", "0.1.0", "0.2.0"])).check()
    assert (status.latest, status.available) == ("0.2.0", False)
    ahead = await checker("0.3.0", FakeRegistry(tags=["0.2.0"])).check()
    assert ahead.available is False


async def test_a_new_test_image_is_found_by_its_commit() -> None:
    registry = FakeRegistry()
    newer = await checker("0.2.0-test+0000000", registry).check()
    assert (newer.channel, newer.latest, newer.available) == ("test", "test+a1b2c3d", True)
    same = await checker("0.2.0-test+a1b2c3d", registry).check()
    assert (same.latest, same.available) == ("test+a1b2c3d", False)
    # Release tags aren't looked at
    assert not any("tags/list" in r.url.path for r in registry.requests)


async def test_the_test_image_may_be_an_index() -> None:
    status = await checker("0.2.0-test+0000000", FakeRegistry(index=True)).check()
    assert status.available is True and status.error is None


async def test_builds_that_cant_be_compared_are_not_checked() -> None:
    registry = FakeRegistry()
    status = await checker("0.0.0-test+local", registry).check()
    assert (status.channel, status.available, status.error) == ("dev", False, None)
    assert registry.requests == []


async def test_registry_problems_are_reported() -> None:
    registry = FakeRegistry()
    registry.denied = True
    status = await checker("0.2.0", registry).check()
    assert status.available is False and "Is the package public?" in status.error
    registry.denied = False
    registry.down = True
    status = await checker("0.2.0", registry).check()
    assert "can't be reached" in status.error
    registry.down = False
    assert (
        "no release images"
        in (await checker("0.2.0", FakeRegistry(tags=["test"])).check()).error.lower()
    )
    assert (
        "which commit"
        in (await checker("0.2.0-test+abc1234", FakeRegistry(revision="")).check()).error
    )


async def test_a_failed_check_keeps_the_last_answer() -> None:
    registry = FakeRegistry()
    update = checker("0.1.0", registry)
    await update.check()
    registry.down = True
    status = await update.check()
    assert status.error and status.latest == "0.2.0" and status.available is True


def test_the_image_name_needs_a_registry() -> None:
    with pytest.raises(ValueError):
        UpdateChecker("fuel_tracker", "0.2.0")


async def test_the_admins_hear_about_a_version_once(api: AppUnderTest) -> None:
    update = checker("0.1.0", FakeRegistry())
    status = await update.check()
    async with api.services.sessionmaker() as session:
        assert await announce_update(session, api.services.events, status) is True
        await session.commit()
    async with api.services.sessionmaker() as session:
        assert await announce_update(session, api.services.events, status) is False
        titles = [n.title for n in (await session.execute(Notification.__table__.select())).all()]
    assert titles == ["Update available: 0.2.0"]
    # Nothing to announce when there is no update
    current = await checker("0.2.0", FakeRegistry()).check()
    async with api.services.sessionmaker() as session:
        assert await announce_update(session, api.services.events, current) is False


async def test_update_endpoints_are_for_admins(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.services.updates = checker("0.1.0", FakeRegistry())

    before = (await api.client.get("/api/update")).json()
    assert before["enabled"] is True and before["channel"] == "release"
    assert before["current"] == "0.1.0" and before["latest"] is None

    after = (await api.client.post("/api/update/check")).json()
    assert after["latest"] == "0.2.0" and after["available"] is True and after["checked_at"]
    titles = [n["title"] for n in (await api.client.get("/api/notifications")).json()]
    assert "Update available: 0.2.0" in titles

    await create_user(api, "bob", [1])
    await sign_in_as(api, "bob")
    assert (await api.client.get("/api/update")).status_code == 403
    assert (await api.client.post("/api/update/check")).status_code == 403


async def test_the_checker_can_be_turned_off(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.services.updates = None
    data = (await api.client.get("/api/update")).json()
    assert data["enabled"] is False and data["available"] is False
    assert (await api.client.post("/api/update/check")).json()["enabled"] is False
