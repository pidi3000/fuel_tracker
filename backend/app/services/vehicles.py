"""LubeLogger's vehicle list and last odometer readings, with a saved copy for when it is down.

The vehicle list is kept for a minute, so a short LubeLogger outage doesn't block users. The last
odometer reading of each vehicle is always asked for first, so a reading changed in LubeLogger is
picked up at once; the copy saved from the last time is only used when LubeLogger can't be
reached, and only if it is not older than an hour.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.services.lubelogger import (
    LubeLoggerClient,
    LubeLoggerError,
    LubeLoggerUnavailable,
    Vehicle,
)

# How long the saved odometer reading of a vehicle may be used when LubeLogger can't be reached
ODOMETER_BACKUP_SECONDS = 3600


@dataclass(frozen=True)
class OdometerReading:
    value: int
    saved: bool  # the copy saved earlier, because LubeLogger can't be reached
    age_seconds: float = 0.0  # how old the saved copy is


class VehicleDirectory:
    def __init__(
        self,
        client: LubeLoggerClient | None,
        ttl: float = 60,
        clock: Callable[[], float] = time.monotonic,
        odometer_max_age: float = ODOMETER_BACKUP_SECONDS,
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock
        self._odometer_max_age = odometer_max_age
        self._cache: list[Vehicle] | None = None
        self._fetched_at = 0.0
        self._odometers: dict[int, tuple[int, float]] = {}  # vehicle id: (reading, when seen)

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

    async def latest_odometer(self, vehicle_id: int) -> OdometerReading:
        """The last odometer reading of a vehicle in LubeLogger.

        LubeLogger is asked every time. If it can't be reached, the reading seen last is used when
        it is not older than an hour; otherwise the error is raised.
        """
        if self._client is None:
            raise LubeLoggerError(
                "LubeLogger isn't set up. Set LUBELOGGER_URL and LUBELOGGER_API_KEY."
            )
        try:
            value = await self._client.latest_odometer(vehicle_id)
        except LubeLoggerUnavailable:
            saved = self._odometers.get(vehicle_id)
            if saved is not None and (age := self._clock() - saved[1]) <= self._odometer_max_age:
                return OdometerReading(saved[0], saved=True, age_seconds=age)
            raise
        self._odometers[vehicle_id] = (value, self._clock())
        return OdometerReading(value, saved=False)

    async def get(self, vehicle_id: int) -> Vehicle | None:
        return next((v for v in await self.all() if v.id == vehicle_id), None)

    def invalidate(self) -> None:
        self._fetched_at = 0.0
