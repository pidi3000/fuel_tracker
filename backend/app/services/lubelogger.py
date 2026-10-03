"""Client for the LubeLogger API.

Every request sends `culture-invariant: true`, so LubeLogger answers with ISO dates and plain
JSON numbers whatever its server language is. Numbers are also *sent* as JSON numbers: LubeLogger
turns them into text with its own culture and parses them back the same way, which is not the
case for numbers sent as strings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# LubeLogger's extra field types
FIELD_TYPE_TEXT = 0
FIELD_TYPE_LOCATION = 5


class LubeLoggerError(Exception):
    """Something went wrong talking to LubeLogger; the message is safe to show to users."""


class LubeLoggerUnavailable(LubeLoggerError):
    """LubeLogger can't be reached or has a server error. Trying again later may help."""


class LubeLoggerRejected(LubeLoggerError):
    """LubeLogger answered, but refused the request. Trying again won't help."""


@dataclass(frozen=True)
class Vehicle:
    id: int
    name: str  # e.g. "2020 VW Golf"
    identifier: str  # license plate or the configured identifier


@dataclass(frozen=True)
class UploadedFile:
    name: str
    location: str


@dataclass(frozen=True)
class ExtraField:
    name: str
    value: str
    field_type: int = FIELD_TYPE_TEXT


@dataclass
class GasRecord:
    """A fuel record as stored in LubeLogger."""

    id: int
    vehicle_id: int
    date: date
    odometer: int
    fuel_consumed: Decimal
    cost: Decimal
    is_fill_to_full: bool = True
    missed_fuel_up: bool = False
    notes: str = ""
    extra_fields: list[ExtraField] = field(default_factory=list)
    files: list[UploadedFile] = field(default_factory=list)


@dataclass
class NewGasRecord:
    date: date
    odometer: int
    fuel_consumed: Decimal
    cost: Decimal
    is_fill_to_full: bool = True
    missed_fuel_up: bool = False
    notes: str = ""
    extra_fields: list[ExtraField] = field(default_factory=list)
    files: list[UploadedFile] = field(default_factory=list)


def _get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Case-insensitive lookup, as LubeLogger's property naming differs between setups."""
    lowered = key.lower()
    for name, value in data.items():
        if name.lower() == lowered:
            return value
    return default


def _number(value: Any) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(0)


def _json_number(value: Decimal | int) -> float | int:
    """JSON numbers go through float; any realistic fuel amount or price survives that."""
    return value if isinstance(value, int) else float(value)


def _parse_record(raw: dict[str, Any]) -> GasRecord:
    return GasRecord(
        id=int(_get(raw, "id", 0)),
        vehicle_id=int(_get(raw, "vehicleId", 0)),
        date=date.fromisoformat(str(_get(raw, "date"))[:10]),
        odometer=int(_number(_get(raw, "odometer"))),
        fuel_consumed=_number(_get(raw, "fuelConsumed")),
        cost=_number(_get(raw, "cost")),
        is_fill_to_full=str(_get(raw, "isFillToFull", True)).lower() == "true",
        missed_fuel_up=str(_get(raw, "missedFuelUp", False)).lower() == "true",
        notes=_get(raw, "notes") or "",
        extra_fields=[
            ExtraField(
                name=str(_get(item, "name", "")),
                value=str(_get(item, "value", "")),
                field_type=int(_get(item, "fieldType", 0) or 0),
            )
            for item in (_get(raw, "extraFields") or [])
        ],
        files=[
            UploadedFile(name=str(_get(item, "name", "")), location=str(_get(item, "location", "")))
            for item in (_get(raw, "files") or [])
        ],
    )


def _message(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict) and (text := _get(data, "message")):
            return str(text)
    except ValueError:
        pass
    return f"HTTP {response.status_code}"


class LubeLoggerClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        timeout: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"culture-invariant": "true", "Accept": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise LubeLoggerUnavailable(
                f"LubeLogger can't be reached ({type(exc).__name__})."
            ) from exc
        if response.status_code >= 500:
            raise LubeLoggerUnavailable(f"LubeLogger had an error: {_message(response)}.")
        if response.status_code in (401, 403):
            raise LubeLoggerRejected(
                f"LubeLogger refused the API key (HTTP {response.status_code}). "
                "Check LUBELOGGER_API_KEY and its permissions."
            )
        if response.status_code >= 400:
            raise LubeLoggerRejected(f"LubeLogger rejected the request: {_message(response)}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise LubeLoggerUnavailable(
                "LubeLogger sent a response that isn't JSON. Is LUBELOGGER_URL correct?"
            ) from exc
        if isinstance(data, dict) and _get(data, "success") is False:
            raise LubeLoggerRejected(f"LubeLogger rejected the request: {_get(data, 'message')}")
        return data

    async def check(self) -> None:
        """Raises if LubeLogger can't be used (unreachable, wrong URL or API key)."""
        await self._request("GET", "/api/vehicles")

    async def vehicles(self) -> list[Vehicle]:
        result = []
        for raw in await self._request("GET", "/api/vehicles"):
            identifier = str(_get(raw, "licensePlate", "") or "")
            id_field = str(_get(raw, "vehicleIdentifier", "LicensePlate"))
            if id_field != "LicensePlate":
                for extra in _get(raw, "extraFields") or []:
                    if _get(extra, "name") == id_field:
                        identifier = str(_get(extra, "value", "") or "")
            name = " ".join(
                str(part)
                for part in (_get(raw, "year"), _get(raw, "make"), _get(raw, "model"))
                if part
            )
            result.append(Vehicle(id=int(_get(raw, "id")), name=name, identifier=identifier))
        return result

    async def latest_odometer(self, vehicle_id: int) -> int:
        data = await self._request(
            "GET", "/api/vehicle/odometerrecords/latest", params={"vehicleId": vehicle_id}
        )
        return int(data)

    async def gas_records(self, vehicle_id: int) -> list[GasRecord]:
        data = await self._request(
            "GET", "/api/vehicle/gasrecords", params={"vehicleId": vehicle_id}
        )
        return [_parse_record(raw) for raw in data]

    async def all_gas_records(self) -> list[GasRecord]:
        data = await self._request("GET", "/api/vehicle/gasrecords/all")
        return [_parse_record(raw) for raw in data]

    async def extra_field_names(self, record_type: str = "GasRecord") -> set[str]:
        """Names of the extra fields defined for a record type."""
        data = await self._request("GET", "/api/extrafields")
        for entry in data:
            if _get(entry, "recordType") == record_type:
                return {str(_get(f, "name")) for f in _get(entry, "extraFields") or []}
        return set()

    async def upload_document(
        self, filename: str, content: bytes, content_type: str = "application/pdf"
    ) -> UploadedFile:
        data = await self._request(
            "POST", "/api/documents/upload", files={"documents": (filename, content, content_type)}
        )
        if not isinstance(data, list) or not data:
            raise LubeLoggerRejected("LubeLogger didn't accept the file.")
        return UploadedFile(
            name=str(_get(data[0], "name", filename)), location=str(_get(data[0], "location"))
        )

    async def add_gas_record(self, vehicle_id: int, record: NewGasRecord) -> int:
        body = {
            "date": record.date.isoformat(),
            "odometer": record.odometer,
            "fuelConsumed": _json_number(record.fuel_consumed),
            "cost": _json_number(record.cost),
            "isFillToFull": record.is_fill_to_full,
            "missedFuelUp": record.missed_fuel_up,
            "notes": record.notes,
            "extraFields": [
                {"name": f.name, "value": f.value, "isRequired": False, "fieldType": f.field_type}
                for f in record.extra_fields
            ],
            "files": [{"name": f.name, "location": f.location} for f in record.files],
        }
        data = await self._request(
            "POST", "/api/vehicle/gasrecords/add", params={"vehicleId": vehicle_id}, json=body
        )
        extra = _get(data, "additionalData") or {}
        return int(_get(extra, "recordId", 0) or 0)
