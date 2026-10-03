"""Settings an admin can override in the web UI.

The environment variables are the defaults. An override is stored in the database and wins until
it is reset. Everything else (secrets, connection details) is environment-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import SettingOverride


class SettingError(ValueError):
    """The value isn't valid for the setting; the message is safe to show."""


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    description: str
    kind: str  # "int", "bool", "text" or "list"
    minimum: int | None = None
    maximum: int | None = None
    check: Callable[[Any], None] | None = None


def _check_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise SettingError("Unknown time zone. Use a name like Europe/Berlin.") from exc


def _check_date_format(value: str) -> None:
    sample = datetime(2026, 9, 23, 17, 8)
    try:
        round_trip = datetime.strptime(sample.strftime(value), value)
    except ValueError:
        round_trip = None
    if round_trip != sample:
        raise SettingError(
            "This isn't a usable date format. It must contain the date and the time, "
            "written with Python format codes, e.g. %m/%d/%Y, %I:%M %p."
        )


SPECS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in [
        SettingSpec(
            "review_before_send",
            "Review before sending to LubeLogger",
            "Hold every fuel-up for review and send it only after it is approved.",
            "bool",
        ),
        SettingSpec(
            "match_window_minutes",
            "Receipt matching window (minutes)",
            "A receipt belongs to a fuel-up when their times are at most this far apart.",
            "int",
            1,
            24 * 60,
        ),
        SettingSpec(
            "receipt_timeout_minutes",
            "Receipt wait time (minutes)",
            "A fuel-up that waited this long for its receipt is marked as failed.",
            "int",
            1,
            7 * 24 * 60,
        ),
        SettingSpec(
            "tz",
            "Time zone",
            "Used to read the date on receipts and for the date in LubeLogger.",
            "text",
            check=_check_timezone,
        ),
        SettingSpec(
            "pace_date_format",
            "Pace Drive receipt date format",
            "How the date on a receipt is written, as Python format codes.",
            "text",
            check=_check_date_format,
        ),
        SettingSpec(
            "fuel_types",
            "Fuel types",
            "The choices for manual fuel-ups. Use the names Pace Drive prints on its receipts.",
            "list",
        ),
        SettingSpec(
            "volume_unit",
            "Fuel unit",
            "The unit LubeLogger uses for fuel amounts. Receipts in another unit need attention.",
            "text",
        ),
        SettingSpec(
            "currency",
            "Currency",
            "The currency of prices. Receipts in another currency need attention.",
            "text",
        ),
        SettingSpec(
            "done_retention_days",
            "Keep finished fuel-ups (days)",
            "How long a finished fuel-up stays visible here before it is deleted. "
            "The record stays in LubeLogger.",
            "int",
            0,
            365,
        ),
    ]
}


def normalize(key: str, raw: Any) -> Any:
    """Check a value for a setting and return it in its stored form."""
    spec = SPECS.get(key)
    if spec is None:
        raise SettingError("This setting can't be changed here.")
    if spec.kind == "bool":
        if not isinstance(raw, bool):
            raise SettingError("Must be true or false.")
        return raw
    if spec.kind == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise SettingError("Must be a whole number.")
        if spec.minimum is not None and raw < spec.minimum:
            raise SettingError(f"Must be at least {spec.minimum}.")
        if spec.maximum is not None and raw > spec.maximum:
            raise SettingError(f"Must be at most {spec.maximum}.")
        return raw
    if spec.kind == "list":
        items = raw.split(",") if isinstance(raw, str) else raw
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise SettingError("Must be a list of names.")
        cleaned = [i.strip() for i in items if i.strip()]
        if not cleaned:
            raise SettingError("Needs at least one entry.")
        if len({i.lower() for i in cleaned}) != len(cleaned):
            raise SettingError("Entries must be different.")
        return cleaned
    if not isinstance(raw, str) or not raw.strip():
        raise SettingError("Must not be empty.")
    value = raw.strip()
    if spec.check:
        spec.check(value)
    return value


class RuntimeSettings:
    """The effective value of each overridable setting: the override, else the environment."""

    def __init__(
        self, env: Settings, sessionmaker: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        self._env = env
        self._sessionmaker = sessionmaker
        self._overrides: dict[str, Any] = {}

    async def load(self) -> None:
        if self._sessionmaker is None:
            return
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(SettingOverride))).scalars()
            self._overrides = {row.key: row.value for row in rows if row.key in SPECS}

    def get(self, key: str) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return getattr(self._env, key)

    def is_overridden(self, key: str) -> bool:
        return key in self._overrides

    async def set(self, key: str, raw: Any) -> Any:
        value = normalize(key, raw)
        assert self._sessionmaker is not None
        async with self._sessionmaker() as session:
            row = await session.get(SettingOverride, key)
            if row is None:
                session.add(SettingOverride(key=key, value=value))
            else:
                row.value = value
            await session.commit()
        self._overrides[key] = value
        return value

    async def reset(self, key: str) -> None:
        if key not in SPECS:
            raise SettingError("This setting can't be changed here.")
        assert self._sessionmaker is not None
        async with self._sessionmaker() as session:
            row = await session.get(SettingOverride, key)
            if row is not None:
                await session.delete(row)
                await session.commit()
        self._overrides.pop(key, None)

    def describe(self) -> list[dict[str, Any]]:
        """Every overridable setting with its effective value, for the settings page."""
        return [
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "kind": spec.kind,
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "value": self.get(spec.key),
                "default": getattr(self._env, spec.key),
                "overridden": self.is_overridden(spec.key),
            }
            for spec in SPECS.values()
        ]

    # --- typed accessors ---

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.get("tz"))

    @property
    def fuel_types(self) -> list[str]:
        return list(self.get("fuel_types"))

    @property
    def volume_unit(self) -> str:
        return self.get("volume_unit")

    @property
    def currency(self) -> str:
        return self.get("currency")

    @property
    def review_before_send(self) -> bool:
        return self.get("review_before_send")

    @property
    def done_retention_days(self) -> int:
        return self.get("done_retention_days")

    @property
    def match_window_minutes(self) -> int:
        return self.get("match_window_minutes")

    @property
    def receipt_timeout_minutes(self) -> int:
        return self.get("receipt_timeout_minutes")

    @property
    def pace_date_format(self) -> str:
        return self.get("pace_date_format")
