"""Looks for a newer Fuel Tracker image in the container registry.

A release image (version `1.2.3`) only looks for a newer release: the highest `X.Y.Z` tag. A test
image (version `1.2.3-test+a1b2c3d`) only looks for a newer test image: the `test` tag is compared
by the commit its image was built from (the `org.opencontainers.image.revision` label). Builds
from source and local images have nothing to compare with and are not checked.

Only registries that hand out pull tokens like ghcr.io does are supported.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.models import Notification, NotificationKind
from app.services import notifications
from app.services.events import EventBus

logger = logging.getLogger(__name__)

REVISION_LABEL = "org.opencontainers.image.revision"
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

CHANNEL_RELEASE = "release"
CHANNEL_TEST = "test"
CHANNEL_DEV = "dev"


class UpdateCheckError(Exception):
    """The registry couldn't be asked, or its answer wasn't what was expected."""


@dataclass(frozen=True)
class UpdateStatus:
    channel: str
    current: str
    latest: str | None = None
    available: bool = False
    checked_at: datetime | None = None
    error: str | None = None


def channel_of(version: str) -> str:
    """Which kind of build this is: a release, a test image, or one that can't be checked."""
    if "-test+" in version:
        return CHANNEL_DEV if version.endswith("+local") else CHANNEL_TEST
    if version == "unknown" or version.endswith("-dev"):
        return CHANNEL_DEV
    return CHANNEL_RELEASE


def release_tuple(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


class UpdateChecker:
    def __init__(
        self,
        image: str,
        version: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        host, _, self._repo = image.strip().partition("/")
        if not host or not self._repo:
            raise ValueError(f"'{image}' isn't an image name like ghcr.io/owner/name")
        self._host = host
        self._transport = transport
        self.status = UpdateStatus(channel=channel_of(version), current=version)

    async def check(self) -> UpdateStatus:
        """Ask the registry and remember the answer. Errors are kept in the status."""
        channel = self.status.channel
        if channel == CHANNEL_DEV:
            self.status = replace(self.status, checked_at=utcnow(), error=None)
            return self.status
        try:
            async with httpx.AsyncClient(
                base_url=f"https://{self._host}",
                timeout=20,
                follow_redirects=True,
                transport=self._transport,
            ) as http:
                token = await self._token(http)
                http.headers["Authorization"] = f"Bearer {token}"
                if channel == CHANNEL_TEST:
                    latest, available = await self._check_test(http)
                else:
                    latest, available = await self._check_release(http)
        except UpdateCheckError as exc:
            self.status = replace(self.status, checked_at=utcnow(), error=str(exc))
        except httpx.HTTPError as exc:
            message = f"{self._host} can't be reached ({type(exc).__name__})."
            self.status = replace(self.status, checked_at=utcnow(), error=message)
        else:
            self.status = replace(
                self.status, latest=latest, available=available, checked_at=utcnow(), error=None
            )
        return self.status

    async def _get(self, http: httpx.AsyncClient, path: str, **kwargs) -> httpx.Response:
        response = await http.get(path, **kwargs)
        if response.status_code in (401, 403, 404):
            raise UpdateCheckError(
                f"{self._host} refused access to {self._repo} (HTTP {response.status_code}). "
                "Is the package public?"
            )
        if response.status_code >= 400:
            raise UpdateCheckError(f"{self._host} answered with HTTP {response.status_code}.")
        return response

    async def _token(self, http: httpx.AsyncClient) -> str:
        response = await self._get(
            http,
            "/token",
            params={"service": self._host, "scope": f"repository:{self._repo}:pull"},
        )
        token = response.json().get("token")
        if not token:
            raise UpdateCheckError(f"{self._host} didn't give an access token.")
        return token

    async def _check_release(self, http: httpx.AsyncClient) -> tuple[str, bool]:
        response = await self._get(http, f"/v2/{self._repo}/tags/list", params={"n": 1000})
        versions = [
            (release_tuple(tag), tag)
            for tag in response.json().get("tags") or []
            if re.fullmatch(r"\d+\.\d+\.\d+", tag)
        ]
        if not versions:
            raise UpdateCheckError("No release images were found.")
        newest, latest = max(versions)
        current = release_tuple(self.status.current)
        return latest, current is not None and newest is not None and newest > current

    async def _check_test(self, http: httpx.AsyncClient) -> tuple[str, bool]:
        manifest = (
            await self._get(
                http, f"/v2/{self._repo}/manifests/test", headers={"Accept": MANIFEST_TYPES}
            )
        ).json()
        if "manifests" in manifest:  # an index: take the image of the first platform
            entry = next(
                (m for m in manifest["manifests"] if m.get("platform", {}).get("os") == "linux"),
                None,
            )
            if entry is None:
                raise UpdateCheckError("The test image has no Linux build.")
            manifest = (
                await self._get(
                    http,
                    f"/v2/{self._repo}/manifests/{entry['digest']}",
                    headers={"Accept": MANIFEST_TYPES},
                )
            ).json()
        config = (
            await self._get(http, f"/v2/{self._repo}/blobs/{manifest['config']['digest']}")
        ).json()
        revision = ((config.get("config") or {}).get("Labels") or {}).get(REVISION_LABEL)
        if not revision:
            raise UpdateCheckError("The test image doesn't say which commit it was built from.")
        latest = f"test+{revision[:7]}"
        running = self.status.current.partition("+")[2]
        return latest, not (running and revision.startswith(running))


async def announce_update(
    session: AsyncSession, events: EventBus | None, status: UpdateStatus
) -> bool:
    """Tell the admins about a newer image, once per version. Returns True if it did."""
    if not status.available or not status.latest:
        return False
    title = f"Update available: {status.latest}"
    existing = await session.execute(
        select(Notification.id).where(Notification.title == title).limit(1)
    )
    if existing.first() is not None:
        return False
    if status.channel == CHANNEL_TEST:
        message = f"A newer test image is available. This one is {status.current}."
    else:
        message = (
            f"A newer release image is available. You are running {status.current}. "
            "To update, pull the new image and restart the container."
        )
    await notifications.notify(
        session,
        events,
        kind=NotificationKind.UPDATE_AVAILABLE,
        level="info",
        title=title,
        message=message,
    )
    return True
