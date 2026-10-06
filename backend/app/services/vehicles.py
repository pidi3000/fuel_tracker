"""LubeLogger's vehicle list and last odometer readings, kept for when it can't be reached.

The vehicle list is kept for a minute, so a short LubeLogger outage doesn't block users.

The last odometer reading of each vehicle is remembered. A reading less than an hour old is used as
it is. An older one is asked for again, so a reading changed in LubeLogger in the meantime is picked
up. If LubeLogger can't be reached then, the last reading seen is used, however old it is.
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

# A saved odometer reading is used as it is for this long; after that LubeLogger is asked again
ODOMETER_REFRESH_SECONDS = 3600


@dataclass(frozen=True)
class OdometerReading:
    value: int
    # LubeLogger couldn't be asked, so this is the last reading seen (possibly a long time ago)
    saved: bool
    age_seconds: float = 0.0  # how long ago the reading was seen in LubeLogger


def age_text(seconds: float) -> str:
    """ "5 min", "3 h" or "2 days": how long ago something was."""
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} min"
    hours = round(minutes / 60)
    return f"{hours} h" if hours < 48 else f"{round(hours / 24)} days"


class VehicleDirectory:
    def __init__(
        self,
        client: LubeLoggerClient | None,
        ttl: float = 60,
        clock: Callable[[], float] = time.monotonic,
        odometer_refresh_after: float = ODOMETER_REFRESH_SECONDS,
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock
        self._odometer_refresh_after = odometer_refresh_after
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

        A reading seen less than an hour ago is used without asking. Otherwise LubeLogger is asked,
        and if it can't be reached the last reading seen is used, however old it is. Only a vehicle
        that was never seen raises the error.
        """
        if self._client is None:
            raise LubeLoggerError(
                "LubeLogger isn't set up. Set LUBELOGGER_URL and LUBELOGGER_API_KEY."
            )
        seen = self._odometers.get(vehicle_id)
        if seen is not None and (age := self._clock() - seen[1]) < self._odometer_refresh_after:
            return OdometerReading(seen[0], saved=False, age_seconds=age)
        try:
            value = await self._client.latest_odometer(vehicle_id)
        except LubeLoggerUnavailable:
            if seen is None:
                raise
            return OdometerReading(seen[0], saved=True, age_seconds=self._clock() - seen[1])
        self._odometers[vehicle_id] = (value, self._clock())
        return OdometerReading(value, saved=False)

    def note_odometer(self, vehicle_id: int, value: int) -> None:
        """A record with this reading was just written to LubeLogger: the saved one can't be lower.

        The saved reading keeps its age; only LubeLogger answering renews it.
        """
        seen = self._odometers.get(vehicle_id)
        if seen is not None and value > seen[0]:
            self._odometers[vehicle_id] = (value, seen[1])

    async def get(self, vehicle_id: int) -> Vehicle | None:
        return next((v for v in await self.all() if v.id == vehicle_id), None)

    def invalidate(self) -> None:
        self._fetched_at = 0.0
