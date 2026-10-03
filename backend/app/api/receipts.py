"""Receipts that arrived by email, especially those without a fuel-up."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, ServicesDep, SessionDep
from app.api.fuel_ups import FuelUpOut, Latitude, Longitude, Odometer, http_error
from app.models import FuelUp, Receipt, ReceiptState
from app.services import fuel_ups as fuel_up_service
from app.services import receipts as receipt_service
from app.services.fuel_ups import FuelUpError

router = APIRouter(prefix="/receipts", tags=["receipts"])


class ReceiptOut(BaseModel):
    id: int
    state: str
    mail_subject: str
    station: str | None
    address: str | None
    paid_at: datetime | None
    printed_date: str | None
    fuel_type: str | None
    quantity: Decimal | None
    unit: str | None
    total: Decimal | None
    currency: str | None
    transaction_id: str | None
    missing: list[str]
    warnings: list[str]
    parse_error: str | None
    created_at: datetime
    fuel_up_id: int | None = None
    has_pdf: bool = False


class CompleteIn(BaseModel):
    """The values only the user knows; the rest comes from the receipt."""

    vehicle_id: int
    odometer: Odometer
    is_fill_to_full: bool = True
    missed_fuel_up: bool = False
    latitude: Latitude | None = None
    longitude: Longitude | None = None


async def _out(session: SessionDep, receipt: Receipt) -> ReceiptOut:
    fuel_up_id = (
        await session.execute(select(FuelUp.id).where(FuelUp.receipt_id == receipt.id))
    ).scalar_one_or_none()
    data = ReceiptOut.model_validate(receipt, from_attributes=True)
    data.fuel_up_id = fuel_up_id
    data.has_pdf = receipt.pdf_file is not None
    return data


async def _visible(session: SessionDep, user: CurrentUser, receipt_id: int) -> Receipt:
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "No such receipt.")
    if receipt.state == ReceiptState.MATCHED:
        fuel_up = (
            await session.execute(select(FuelUp).where(FuelUp.receipt_id == receipt.id))
        ).scalar_one_or_none()
        if fuel_up is not None and not user.can_access_vehicle(fuel_up.vehicle_id):
            raise HTTPException(404, "No such receipt.")
    return receipt


@router.get("")
async def list_receipts(
    user: CurrentUser,
    session: SessionDep,
    state: Annotated[ReceiptState, Field()] = ReceiptState.UNMATCHED,
) -> list[ReceiptOut]:
    """Receipts in one state, newest first. By default those still without a fuel-up."""
    query = select(Receipt).where(Receipt.state == state).order_by(Receipt.created_at.desc())
    receipts = list((await session.execute(query)).scalars())
    return [await _out(session, r) for r in receipts]


@router.get("/{receipt_id}")
async def get_receipt(receipt_id: int, user: CurrentUser, session: SessionDep) -> ReceiptOut:
    return await _out(session, await _visible(session, user, receipt_id))


@router.get("/{receipt_id}/pdf")
async def get_receipt_pdf(
    receipt_id: int, user: CurrentUser, session: SessionDep, services: ServicesDep
) -> FileResponse:
    receipt = await _visible(session, user, receipt_id)
    path = services.receipts.receipt_dir / receipt.pdf_file if receipt.pdf_file else None
    if path is None or not path.is_file():
        raise HTTPException(404, "The PDF is not available.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=receipt.pdf_name,
        content_disposition_type="inline",
    )


@router.post("/{receipt_id}/complete", status_code=201)
async def complete_receipt(
    receipt_id: int,
    body: CompleteIn,
    user: CurrentUser,
    session: SessionDep,
    services: ServicesDep,
) -> FuelUpOut:
    """Make a fuel-up from a receipt that has none."""
    receipt = await _visible(session, user, receipt_id)
    if receipt.state != ReceiptState.UNMATCHED:
        raise HTTPException(409, "This receipt is already used or ignored.")
    if receipt.paid_at is None:
        raise HTTPException(422, "The time of this receipt couldn't be read, so it can't be used.")
    try:
        fuel_up = await fuel_up_service.create_waiting_for_receipt(
            session,
            services.context,
            user,
            vehicle_id=body.vehicle_id,
            odometer=body.odometer,
            fuel_up_time=receipt.paid_at,
            is_fill_to_full=body.is_fill_to_full,
            missed_fuel_up=body.missed_fuel_up,
            latitude=body.latitude,
            longitude=body.longitude,
        )
        await receipt_service.apply_receipt(session, services.receipts, fuel_up, receipt)
    except FuelUpError as exc:
        await session.rollback()
        raise http_error(exc) from exc
    await session.commit()
    services.processor.wake()
    return FuelUpOut.from_model(fuel_up, services.runtime)


@router.post("/{receipt_id}/ignore", status_code=204)
async def ignore_receipt(
    receipt_id: int, user: CurrentUser, session: SessionDep, services: ServicesDep
) -> None:
    """For receipts that need no fuel-up, e.g. a vehicle that isn't tracked."""
    receipt = await _visible(session, user, receipt_id)
    if receipt.state != ReceiptState.UNMATCHED:
        raise HTTPException(409, "This receipt is already used or ignored.")
    receipt.state = ReceiptState.IGNORED
    await session.commit()
    services.events.publish("receipt", id=receipt.id)
