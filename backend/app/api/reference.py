"""Data the forms need: vehicles, fuel types and units."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import CurrentUser, ServicesDep
from app.services.lubelogger import LubeLoggerError, LubeLoggerUnavailable

router = APIRouter(tags=["reference"])


class VehicleOut(BaseModel):
    id: int
    name: str
    identifier: str


class OdometerOut(BaseModel):
    odometer: int | None
    # True when LubeLogger can't be reached and this is the reading saved earlier (under 1 h old)
    saved: bool = False
    saved_minutes_ago: int | None = None


class FuelTypesOut(BaseModel):
    fuel_types: list[str]
    volume_unit: str
    currency: str
    tz: str
    review_before_send: bool


@router.get("/vehicles")
async def list_vehicles(user: CurrentUser, services: ServicesDep) -> list[VehicleOut]:
    """The vehicles from LubeLogger that the user may log fuel-ups for."""
    try:
        vehicles = await services.vehicles.all()
    except LubeLoggerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except LubeLoggerError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [
        VehicleOut(id=v.id, name=v.name, identifier=v.identifier)
        for v in vehicles
        if user.can_access_vehicle(v.id)
    ]


@router.get("/vehicles/{vehicle_id}/odometer")
async def last_odometer(vehicle_id: int, user: CurrentUser, services: ServicesDep) -> OdometerOut:
    """The last odometer reading in LubeLogger (a hint for the form)."""
    if not user.can_access_vehicle(vehicle_id) or services.lubelogger is None:
        raise HTTPException(404, "Unknown vehicle.")
    try:
        reading = await services.vehicles.latest_odometer(vehicle_id)
        return OdometerOut(
            odometer=reading.value,
            saved=reading.saved,
            saved_minutes_ago=round(reading.age_seconds / 60) if reading.saved else None,
        )
    except LubeLoggerUnavailable:
        return OdometerOut(odometer=None)
    except LubeLoggerError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/fuel-types")
async def fuel_types(_: CurrentUser, services: ServicesDep) -> FuelTypesOut:
    runtime = services.runtime
    return FuelTypesOut(
        fuel_types=runtime.fuel_types,
        volume_unit=runtime.volume_unit,
        currency=runtime.currency,
        tz=str(runtime.tz),
        review_before_send=runtime.review_before_send,
    )
