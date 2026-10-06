"""Receipts that arrived by email, especially those without a fuel-up."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, ServicesDep, SessionDep
from app.api.fuel_ups import FuelUpOut, Latitude, Longitude, Odometer, http_error
from app.core.types import utcnow
from app.models import FuelUp, Receipt, ReceiptState
from app.services import fuel_ups as fuel_up_service
from app.services import receipts as receipt_service
from app.services import record_matching
from app.services.fuel_ups import FuelUpError
from app.services.lubelogger import LubeLoggerError, LubeLoggerUnavailable
from app.services.processor import TRANSACTION_MARKER

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
    # Set when the receipt was attached to a fuel record that already existed in LubeLogger
    linked_vehicle_id: int | None = None
    linked_record_id: int | None = None


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


class CandidateOut(BaseModel):
    vehicle_id: int
    vehicle_name: str
    record_id: int
    date: date
    odometer: int
    fuel_consumed: str
    cost: str
    notes: str
    has_files: bool
    days_after: int  # how many days after the receipt the record is dated
    amount_matches: bool
    price_matches: bool


class LinkIn(BaseModel):
    vehicle_id: int
    record_id: int


def _lubelogger_error(exc: LubeLoggerError) -> HTTPException:
    if isinstance(exc, LubeLoggerUnavailable):
        return HTTPException(503, str(exc))
    return HTTPException(502, str(exc))


async def _open_receipt(session: SessionDep, user: CurrentUser, receipt_id: int) -> Receipt:
    receipt = await _visible(session, user, receipt_id)
    if receipt.state != ReceiptState.UNMATCHED:
        raise HTTPException(409, "This receipt is already used or ignored.")
    if receipt.paid_at is None:
        raise HTTPException(422, "The time of this receipt couldn't be read, so it can't be used.")
    return receipt


@router.get("/{receipt_id}/record-candidates")
async def record_candidates(
    receipt_id: int,
    user: CurrentUser,
    session: SessionDep,
    services: ServicesDep,
    days: Annotated[int, Query(ge=0, le=record_matching.MAX_DAYS)] = record_matching.DEFAULT_DAYS,
    include_other_amounts: bool = False,
) -> list[CandidateOut]:
    """Fuel records in LubeLogger this receipt may belong to, the closest in date first.

    Only records dated on the day of the receipt or later are offered, and by default only those
    with the same fuel amount and total price.
    """
    receipt = await _open_receipt(session, user, receipt_id)
    if services.lubelogger is None:
        raise HTTPException(503, "LubeLogger isn't set up. Set LUBELOGGER_URL.")
    try:
        vehicles = {v.id: v for v in await services.vehicles.all() if user.can_access_vehicle(v.id)}
        records = [
            r for r in await services.lubelogger.all_gas_records() if r.vehicle_id in vehicles
        ]
    except LubeLoggerError as exc:
        raise _lubelogger_error(exc) from exc
    runtime = services.runtime
    found = record_matching.candidates(
        receipt,
        records,
        tz=runtime.tz,
        volume_unit=runtime.volume_unit,
        currency=runtime.currency,
        days=days,
        include_other_amounts=include_other_amounts,
    )
    return [
        CandidateOut(
            vehicle_id=c.record.vehicle_id,
            vehicle_name=vehicles[c.record.vehicle_id].name,
            record_id=c.record.id,
            date=c.record.date,
            odometer=c.record.odometer,
            fuel_consumed=format(c.record.fuel_consumed, "f"),
            cost=format(c.record.cost, "f"),
            notes=c.record.notes,
            has_files=bool(c.record.files),
            days_after=c.days_after,
            amount_matches=c.amount_matches,
            price_matches=c.price_matches,
        )
        for c in found
    ]


@router.post("/{receipt_id}/link-record")
async def link_record(
    receipt_id: int,
    body: LinkIn,
    user: CurrentUser,
    session: SessionDep,
    services: ServicesDep,
) -> ReceiptOut:
    """Attach the receipt to a fuel record that already exists in LubeLogger.

    The record gets the receipt PDF, the transaction ID in its notes and, if it has none, the
    station address. Everything else in it is kept.
    """
    receipt = await _open_receipt(session, user, receipt_id)
    client = services.lubelogger
    if client is None:
        raise HTTPException(503, "LubeLogger isn't set up. Set LUBELOGGER_URL.")
    if not user.can_access_vehicle(body.vehicle_id):
        raise HTTPException(404, "Unknown vehicle.")
    try:
        everything = await client.all_gas_records()
        record = next(
            (r for r in everything if r.vehicle_id == body.vehicle_id and r.id == body.record_id),
            None,
        )
        if record is None:
            raise HTTPException(404, "That fuel record doesn't exist.")
        if record_matching.has_receipt(record):
            raise HTTPException(409, "That fuel record already has a Pace Drive receipt.")
        if receipt.transaction_id:
            marker = f"{TRANSACTION_MARKER}{receipt.transaction_id}"
            used = next((r for r in everything if marker in r.notes), None)
            if used is not None:
                raise HTTPException(
                    409,
                    f"This receipt is already in LubeLogger, as fuel record #{used.id}.",
                )
        uploaded = None
        path = receipt_service.pdf_path(services.receipts, receipt)
        if path is not None and path.is_file():
            uploaded = await client.upload_document(receipt.pdf_name, path.read_bytes())
        update = record_matching.updated_record(
            record,
            receipt,
            uploaded,
            address_field=services.settings.lubelogger_field_address,
        )
        await client.update_gas_record(update)
    except LubeLoggerError as exc:
        raise _lubelogger_error(exc) from exc

    receipt.state = ReceiptState.MATCHED
    receipt.linked_vehicle_id = record.vehicle_id
    receipt.linked_record_id = record.id
    receipt.linked_at = utcnow()
    await session.commit()
    services.events.publish("receipt", id=receipt.id)
    return await _out(session, receipt)
