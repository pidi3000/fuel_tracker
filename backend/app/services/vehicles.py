"""LubeLogger's vehicle list and last odometer readings, kept for when it can't be reached.

The vehicle list is kept for a minute, so a short LubeLogger outage doesn't block users.

The last odometer reading of each vehicle is remembered. LubeLogger is always asked first and the
remembered reading updated with its answer. If LubeLogger can't be reached, the remembered reading
is used, however old it is. A background job asks again when the last pull is over an hour ago, so
the reading stays current while nobody adds a fuel-up.
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

# The background job asks LubeLogger for the readings again when the last pull is this old
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
        self._last_refresh: float | None = None  # when the readings were last all pulled

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

        LubeLogger is always asked first and the saved reading updated. If it can't be reached (or
        was found offline a moment ago), the saved reading is used, however old it is. Only a
        vehicle that was never seen raises the error.
        """
        if self._client is None:
            raise LubeLoggerError(
                "LubeLogger isn't set up. Set LUBELOGGER_URL and LUBELOGGER_API_KEY."
            )
        try:
            value = await self._client.latest_odometer(vehicle_id)
        except LubeLoggerUnavailable:
            seen = self._odometers.get(vehicle_id)
            if seen is None:
                raise
            return OdometerReading(seen[0], saved=True, age_seconds=self._clock() - seen[1])
        self._odometers[vehicle_id] = (value, self._clock())
        return OdometerReading(value, saved=False)

    async def refresh_odometers(self) -> bool:
        """Ask LubeLogger for every vehicle's reading if the last pull is over an hour ago.

        Returns whether it asked. Does nothing while LubeLogger can't be reached; the saved
        readings stay as they are and it is tried again at the next call.
        """
        if self._client is None:
            return False
        now = self._clock()
        if (
            self._last_refresh is not None
            and now - self._last_refresh < self._odometer_refresh_after
        ):
            return False
        try:
            for vehicle in await self.all():
                self._odometers[vehicle.id] = (
                    await self._client.latest_odometer(vehicle.id),
                    self._clock(),
                )
        except LubeLoggerError:
            return False
        self._last_refresh = now
        return True

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
