"""Fuel-ups: create, list, edit, approve and retry."""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Self

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.deps import CurrentUser, ServicesDep, SessionDep
from app.models import FuelUp, PaymentSource, Status
from app.services import fuel_ups as service
from app.services import receipts as receipt_service
from app.services.fuel_ups import FuelUpError
from app.services.runtime_settings import RuntimeSettings

router = APIRouter(prefix="/fuel-ups", tags=["fuel-ups"])

Odometer = Annotated[int, Field(ge=0, le=9_999_999)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Quantity = Annotated[Decimal, Field(ge=0, le=9999)]
Price = Annotated[Decimal, Field(ge=0, le=99999)]


def _round(value: Decimal | None, places: str) -> Decimal | None:
    return None if value is None else value.quantize(Decimal(places), ROUND_HALF_UP)


class FuelUpCreate(BaseModel):
    """What the web UI and the Apple Shortcut send."""

    vehicle_id: int
    odometer: Odometer
    # Defaults to now. Without a time zone the time is read in the configured time zone
    fuel_up_time: datetime | None = None
    is_fill_to_full: bool = True
    missed_fuel_up: bool = False
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    payment_source: PaymentSource = PaymentSource.MANUAL
    # Only for payment_source "manual"
    fuel_type: str | None = Field(default=None, max_length=100)
    quantity: Quantity | None = None
    total_price: Price | None = None

    @model_validator(mode="after")
    def location_is_complete(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send both latitude and longitude, or neither.")
        return self

    @field_validator("quantity")
    @classmethod
    def round_quantity(cls, value: Decimal | None) -> Decimal | None:
        return _round(value, "0.001")

    @field_validator("total_price")
    @classmethod
    def round_price(cls, value: Decimal | None) -> Decimal | None:
        return _round(value, "0.01")


class FuelUpUpdate(BaseModel):
    """Only the fields that are sent are changed."""

    vehicle_id: int | None = None
    odometer: Odometer | None = None
    fuel_up_time: datetime | None = None
    is_fill_to_full: bool | None = None
    missed_fuel_up: bool | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    payment_source: PaymentSource | None = None
    fuel_type: str | None = Field(default=None, max_length=100)
    quantity: Quantity | None = None
    total_price: Price | None = None

    @field_validator("quantity")
    @classmethod
    def round_quantity(cls, value: Decimal | None) -> Decimal | None:
        return _round(value, "0.001")

    @field_validator("total_price")
    @classmethod
    def round_price(cls, value: Decimal | None) -> Decimal | None:
        return _round(value, "0.01")


class FuelUpOut(BaseModel):
    id: int
    vehicle_id: int
    vehicle_name: str
    odometer: int
    fuel_up_time: datetime
    is_fill_to_full: bool
    missed_fuel_up: bool
    latitude: float | None
    longitude: float | None
    payment_source: PaymentSource
    fuel_type: str | None
    quantity: Decimal | None
    total_price: Decimal | None
    volume_unit: str
    currency: str
    address: str | None
    # "pending", "needs_attention", "done" or "failed"
    status: str
    # True while the fuel-up is being written to LubeLogger (status is "pending" then)
    sending: bool
    attention: str | None
    attention_message: str | None
    warnings: list[str]
    error_message: str | None
    lubelogger_record_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    editable: bool
    receipt_id: int | None = None
    # Waiting for the receipt, until this time (then it fails)
    waiting_for_receipt: bool = False
    receipt_deadline: datetime | None = None

    @classmethod
    def from_model(cls, fuel_up: FuelUp, runtime: RuntimeSettings) -> "FuelUpOut":
        sending = fuel_up.status == Status.SENDING
        waiting = (
            fuel_up.status == Status.PENDING
            and fuel_up.payment_source == PaymentSource.EMAIL_RECEIPT
            and fuel_up.receipt_id is None
        )
        deadline = (
            fuel_up.pending_since + timedelta(minutes=runtime.receipt_timeout_minutes)
            if waiting and fuel_up.pending_since
            else None
        )
        return cls(
            id=fuel_up.id,
            vehicle_id=fuel_up.vehicle_id,
            vehicle_name=fuel_up.vehicle_name,
            odometer=fuel_up.odometer,
            fuel_up_time=fuel_up.fuel_up_time,
            is_fill_to_full=fuel_up.is_fill_to_full,
            missed_fuel_up=fuel_up.missed_fuel_up,
            latitude=fuel_up.latitude,
            longitude=fuel_up.longitude,
            payment_source=PaymentSource(fuel_up.payment_source),
            fuel_type=fuel_up.fuel_type,
            quantity=fuel_up.quantity,
            total_price=fuel_up.total_price,
            volume_unit=runtime.volume_unit,
            currency=runtime.currency,
            address=fuel_up.address,
            status=Status.PENDING if sending else fuel_up.status,
            sending=sending,
            attention=fuel_up.attention,
            attention_message=fuel_up.attention_message,
            warnings=list(fuel_up.warnings or []),
            error_message=fuel_up.error_message,
            lubelogger_record_id=fuel_up.lubelogger_record_id,
            created_by=fuel_up.created_by_name,
            created_at=fuel_up.created_at,
            updated_at=fuel_up.updated_at,
            editable=fuel_up.editable,
            receipt_id=fuel_up.receipt_id,
            waiting_for_receipt=waiting,
            receipt_deadline=deadline,
        )


def http_error(exc: FuelUpError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_fuel_up(
    body: FuelUpCreate, user: CurrentUser, session: SessionDep, services: ServicesDep
) -> FuelUpOut:
    """Create a fuel-up. It is written to LubeLogger in the background."""
    fields = {
        "vehicle_id": body.vehicle_id,
        "odometer": body.odometer,
        "fuel_up_time": body.fuel_up_time,
        "is_fill_to_full": body.is_fill_to_full,
        "missed_fuel_up": body.missed_fuel_up,
        "latitude": body.latitude,
        "longitude": body.longitude,
    }
    try:
        if body.payment_source == PaymentSource.MANUAL:
            fuel_up = await service.create_manual(
                session,
                services.context,
                user,
                **fields,
                fuel_type=body.fuel_type,
                quantity=body.quantity,
                total_price=body.total_price,
            )
        else:
            # The payment data comes from the receipt, so any sent here is ignored
            fuel_up = await service.create_waiting_for_receipt(
                session, services.context, user, **fields
            )
            await receipt_service.try_match_fuel_up(session, services.receipts, fuel_up)
    except FuelUpError as exc:
        await session.rollback()
        raise http_error(exc) from exc
    await session.commit()
    services.processor.wake()
    if services.mail_watcher is not None:
        services.mail_watcher.check_now()
    return FuelUpOut.from_model(fuel_up, services.runtime)


@router.get("")
async def list_fuel_ups(
    user: CurrentUser, session: SessionDep, services: ServicesDep
) -> list[FuelUpOut]:
    fuel_ups = await service.list_visible(session, user)
    return [FuelUpOut.from_model(f, services.runtime) for f in fuel_ups]


@router.get("/{fuel_up_id}")
async def get_fuel_up(
    fuel_up_id: int, user: CurrentUser, session: SessionDep, services: ServicesDep
) -> FuelUpOut:
    try:
        fuel_up = await service.get_visible(session, user, fuel_up_id)
    except FuelUpError as exc:
        raise http_error(exc) from exc
    return FuelUpOut.from_model(fuel_up, services.runtime)


@router.patch("/{fuel_up_id}")
async def update_fuel_up(
    fuel_up_id: int,
    body: FuelUpUpdate,
    user: CurrentUser,
    session: SessionDep,
    services: ServicesDep,
) -> FuelUpOut:
    try:
        fuel_up = await service.get_visible(session, user, fuel_up_id)
        changes = body.model_dump(exclude_unset=True)
        # Fields that can't be emptied: leaving them out is the way to keep them
        for field in (
            "vehicle_id",
            "odometer",
            "payment_source",
            "is_fill_to_full",
            "missed_fuel_up",
        ):
            if field in changes and changes[field] is None:
                del changes[field]
        await service.update(session, services.context, user, fuel_up, changes)
        if "fuel_up_time" in changes:
            # A receipt that fits the new time may already be here
            await receipt_service.try_match_fuel_up(session, services.receipts, fuel_up)
    except FuelUpError as exc:
        await session.rollback()
        raise http_error(exc) from exc
    await session.commit()
    services.processor.wake()
    return FuelUpOut.from_model(fuel_up, services.runtime)


@router.post("/{fuel_up_id}/approve")
async def approve_fuel_up(
    fuel_up_id: int, user: CurrentUser, session: SessionDep, services: ServicesDep
) -> FuelUpOut:
    """Send a fuel-up that waits for review to LubeLogger."""
    try:
        fuel_up = await service.get_visible(session, user, fuel_up_id)
        service.approve(services.context, fuel_up)
    except FuelUpError as exc:
        raise http_error(exc) from exc
    await session.commit()
    services.processor.wake()
    return FuelUpOut.from_model(fuel_up, services.runtime)


@router.post("/{fuel_up_id}/retry")
async def retry_fuel_up(
    fuel_up_id: int, user: CurrentUser, session: SessionDep, services: ServicesDep
) -> FuelUpOut:
    """Try a failed fuel-up again."""
    try:
        fuel_up = await service.get_visible(session, user, fuel_up_id)
        service.retry(services.context, fuel_up)
        await receipt_service.try_match_fuel_up(session, services.receipts, fuel_up)
    except FuelUpError as exc:
        raise http_error(exc) from exc
    await session.commit()
    services.processor.wake()
    if services.mail_watcher is not None:
        services.mail_watcher.check_now()
    return FuelUpOut.from_model(fuel_up, services.runtime)
