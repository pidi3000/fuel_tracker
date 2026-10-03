"""Past fuel records, loaded live from LubeLogger."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, ServicesDep
from app.services.lubelogger import GasRecord, LubeLoggerError, LubeLoggerUnavailable

router = APIRouter(tags=["history"])


class FileOut(BaseModel):
    name: str
    location: str


class HistoryRecord(BaseModel):
    id: int
    vehicle_id: int
    vehicle_name: str
    date: date
    odometer: int
    fuel_consumed: str
    cost: str
    is_fill_to_full: bool
    missed_fuel_up: bool
    notes: str
    gps: str | None
    address: str | None
    files: list[FileOut]


class HistoryOut(BaseModel):
    items: list[HistoryRecord]
    total: int


def _extra(record: GasRecord, name: str) -> str | None:
    return next((f.value for f in record.extra_fields if f.name == name and f.value), None)


@router.get("/history")
async def history(
    user: CurrentUser,
    services: ServicesDep,
    vehicle_id: int | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> HistoryOut:
    """Fuel records in LubeLogger for the vehicles the user may see, newest first."""
    try:
        vehicles = {v.id: v for v in await services.vehicles.all() if user.can_access_vehicle(v.id)}
        if vehicle_id is not None and vehicle_id not in vehicles:
            raise HTTPException(404, "Unknown vehicle.")
        wanted = [vehicle_id] if vehicle_id is not None else list(vehicles)
        records: list[GasRecord] = []
        if services.lubelogger is not None:
            for vid in wanted:
                records.extend(await services.lubelogger.gas_records(vid))
    except LubeLoggerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except LubeLoggerError as exc:
        raise HTTPException(502, str(exc)) from exc

    records.sort(key=lambda r: (r.date, r.odometer, r.id), reverse=True)
    settings = services.settings
    items = [
        HistoryRecord(
            id=r.id,
            vehicle_id=r.vehicle_id,
            vehicle_name=vehicles[r.vehicle_id].name,
            date=r.date,
            odometer=r.odometer,
            fuel_consumed=format(r.fuel_consumed, "f"),
            cost=format(r.cost, "f"),
            is_fill_to_full=r.is_fill_to_full,
            missed_fuel_up=r.missed_fuel_up,
            notes=r.notes,
            gps=_extra(r, settings.lubelogger_field_gps),
            address=_extra(r, settings.lubelogger_field_address),
            files=[FileOut(name=f.name, location=f.location) for f in r.files],
        )
        for r in records[offset : offset + limit]
        if r.vehicle_id in vehicles
    ]
    return HistoryOut(items=items, total=len(records))
