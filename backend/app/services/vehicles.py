"""LubeLogger's vehicle list, cached briefly so a short LubeLogger outage doesn't block users."""

import time
from collections.abc import Callable

from app.services.lubelogger import (
    LubeLoggerClient,
    LubeLoggerError,
    LubeLoggerUnavailable,
    Vehicle,
)


class VehicleDirectory:
    def __init__(
        self,
        client: LubeLoggerClient | None,
        ttl: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock
        self._cache: list[Vehicle] | None = None
        self._fetched_at = 0.0

    async def all(self) -> list[Vehicle]:
        """All vehicles. Falls back to the last known list if LubeLogger can't be reached."""
        if self._client is None:
            raise LubeLoggerError(
                "LubeLogger isn't set up. Set LUBELOGGER_URL and LUBELOGGER_API_KEY."
            )
        if self._cache is not None and self._clock() - self._fetched_at < self._ttl:
            return self._cache
        try:
            self._cache = await self._client.vehicles()
            self._fetched_at = self._clock()
        except LubeLoggerUnavailable:
            if self._cache is None:
                raise
        return self._cache

    async def get(self, vehicle_id: int) -> Vehicle | None:
        return next((v for v in await self.all() if v.id == vehicle_id), None)

    def invalidate(self) -> None:
        self._fetched_at = 0.0
