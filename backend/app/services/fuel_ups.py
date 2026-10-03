"""Creating, editing and moving fuel-ups through their statuses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.models import Attention, FuelUp, PaymentSource, Status, User
from app.services.events import EventBus
from app.services.lubelogger import LubeLoggerClient, LubeLoggerError, LubeLoggerUnavailable
from app.services.runtime_settings import RuntimeSettings
from app.services.vehicles import VehicleDirectory

# Statuses of fuel-ups that are not in LubeLogger yet
OPEN_STATUSES = (Status.PENDING, Status.NEEDS_ATTENTION, Status.SENDING, Status.FAILED)


class FuelUpError(Exception):
    """A rule was broken. `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def invalid(message: str) -> FuelUpError:
    return FuelUpError(422, message)


@dataclass
class Context:
    """What the fuel-up logic needs from the app."""

    runtime: RuntimeSettings
    events: EventBus
    lubelogger: LubeLoggerClient | None
    vehicles: VehicleDirectory


def as_utc(value: datetime | None, runtime: RuntimeSettings) -> datetime:
    """A datetime without a time zone is read as local time in the configured time zone."""
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=runtime.tz)
    return value.astimezone(UTC)


def clean_payment(
    ctx: Context,
    fuel_type: str | None,
    quantity: Decimal | None,
    total_price: Decimal | None,
    *,
    from_receipt: bool = False,
) -> None:
    """Checks the payment values; raises if one is missing or wrong.

    The fuel type of a receipt is taken as printed, so it needn't be in the configured list.
    """
    if not fuel_type:
        raise invalid("Choose a fuel type.")
    if not from_receipt and fuel_type not in ctx.runtime.fuel_types:
        raise invalid(
            f"Unknown fuel type '{fuel_type}'. Choose one of: {', '.join(ctx.runtime.fuel_types)}."
        )
    if quantity is None or quantity <= 0:
        raise invalid(f"Enter the fuel amount in {ctx.runtime.volume_unit}.")
    if total_price is None or total_price < 0:
        raise invalid(f"Enter the total price in {ctx.runtime.currency}.")
    if quantity > 9999:
        raise invalid("That fuel amount is too large.")
    if total_price > 99999:
        raise invalid("That price is too large.")


def check_fuel_up_payment(ctx: Context, fuel_up: FuelUp) -> None:
    clean_payment(
        ctx,
        fuel_up.fuel_type,
        fuel_up.quantity,
        fuel_up.total_price,
        from_receipt=fuel_up.payment_source == PaymentSource.EMAIL_RECEIPT,
    )


async def highest_open_odometer(
    session: AsyncSession, vehicle_id: int, exclude_id: int | None = None
) -> tuple[int, int] | None:
    """The highest odometer among fuel-ups not yet in LubeLogger, as (odometer, fuel-up id)."""
    query = (
        select(FuelUp.odometer, FuelUp.id)
        .where(FuelUp.vehicle_id == vehicle_id, FuelUp.status.in_(OPEN_STATUSES))
        .order_by(FuelUp.odometer.desc())
        .limit(1)
    )
    if exclude_id is not None:
        query = query.where(FuelUp.id != exclude_id)
    row = (await session.execute(query)).first()
    return (row[0], row[1]) if row else None


async def check_odometer(
    session: AsyncSession,
    ctx: Context,
    vehicle_id: int,
    odometer: int,
    exclude_id: int | None = None,
) -> list[str]:
    """Rejects an odometer reading lower than the last one. Returns warnings."""
    warnings: list[str] = []
    if (open_floor := await highest_open_odometer(session, vehicle_id, exclude_id)) and (
        odometer < open_floor[0]
    ):
        raise invalid(
            f"The odometer reading {odometer} is lower than {open_floor[0]} "
            f"of fuel-up #{open_floor[1]}, which is not in LubeLogger yet."
        )
    if ctx.lubelogger is None:
        return warnings
    try:
        latest = await ctx.lubelogger.latest_odometer(vehicle_id)
    except LubeLoggerUnavailable:
        # Checked again when the fuel-up is written to LubeLogger
        return ["LubeLogger wasn't reachable, so the odometer reading wasn't checked yet."]
    except LubeLoggerError as exc:
        raise FuelUpError(502, str(exc)) from exc
    if odometer < latest:
        raise invalid(f"The odometer reading {odometer} is lower than the last reading ({latest}).")
    return warnings


async def vehicle_for(ctx: Context, user: User, vehicle_id: int):
    """The LubeLogger vehicle, if the user may log fuel-ups for it."""
    try:
        vehicle = await ctx.vehicles.get(vehicle_id)
    except LubeLoggerError as exc:
        raise FuelUpError(503, str(exc)) from exc
    if vehicle is None or not user.can_access_vehicle(vehicle_id):
        raise invalid("Unknown vehicle.")
    return vehicle


def touch(ctx: Context, fuel_up: FuelUp) -> None:
    fuel_up.updated_at = utcnow()
    ctx.events.publish("fuel_up", id=fuel_up.id)


def start_sending(ctx: Context, fuel_up: FuelUp) -> None:
    fuel_up.status = Status.SENDING
    fuel_up.attention = None
    fuel_up.attention_message = None
    fuel_up.error_message = None
    fuel_up.send_attempts = 0
    fuel_up.next_attempt_at = utcnow()
    touch(ctx, fuel_up)


def ask_for_review(ctx: Context, fuel_up: FuelUp) -> None:
    fuel_up.status = Status.NEEDS_ATTENTION
    fuel_up.attention = Attention.REVIEW
    fuel_up.attention_message = "Check the fuel-up, then approve it to send it to LubeLogger."
    fuel_up.next_attempt_at = None
    touch(ctx, fuel_up)


def advance_when_complete(ctx: Context, fuel_up: FuelUp) -> None:
    """The payment data is complete: hold it for review or send it."""
    if ctx.runtime.review_before_send:
        ask_for_review(ctx, fuel_up)
    else:
        start_sending(ctx, fuel_up)


async def _create(
    session: AsyncSession,
    ctx: Context,
    user: User,
    *,
    vehicle_id: int,
    odometer: int,
    fuel_up_time: datetime | None,
    is_fill_to_full: bool,
    missed_fuel_up: bool,
    latitude: float | None,
    longitude: float | None,
    payment_source: PaymentSource,
    fuel_type: str | None = None,
    quantity: Decimal | None = None,
    total_price: Decimal | None = None,
) -> FuelUp:
    vehicle = await vehicle_for(ctx, user, vehicle_id)
    if payment_source == PaymentSource.MANUAL:
        clean_payment(ctx, fuel_type, quantity, total_price)
    warnings = await check_odometer(session, ctx, vehicle_id, odometer)

    fuel_up = FuelUp(
        created_by_id=user.id,
        created_by_name=user.username,
        vehicle_id=vehicle_id,
        vehicle_name=vehicle.name,
        odometer=odometer,
        fuel_up_time=as_utc(fuel_up_time, ctx.runtime),
        is_fill_to_full=is_fill_to_full,
        missed_fuel_up=missed_fuel_up,
        latitude=latitude,
        longitude=longitude,
        payment_source=payment_source,
        fuel_type=fuel_type,
        quantity=quantity,
        total_price=total_price,
        warnings=warnings,
    )
    session.add(fuel_up)
    await session.flush()
    return fuel_up


async def create_manual(session: AsyncSession, ctx: Context, user: User, **fields) -> FuelUp:
    """A fuel-up with payment data entered by hand: held for review or sent."""
    fuel_up = await _create(session, ctx, user, payment_source=PaymentSource.MANUAL, **fields)
    advance_when_complete(ctx, fuel_up)
    return fuel_up


async def create_waiting_for_receipt(
    session: AsyncSession, ctx: Context, user: User, **fields
) -> FuelUp:
    """A fuel-up whose payment data comes from the email receipt, which is awaited."""
    fuel_up = await _create(
        session, ctx, user, payment_source=PaymentSource.EMAIL_RECEIPT, **fields
    )
    wait_for_receipt(ctx, fuel_up)
    return fuel_up


def wait_for_receipt(ctx: Context, fuel_up: FuelUp) -> None:
    fuel_up.status = Status.PENDING
    fuel_up.attention = None
    fuel_up.attention_message = None
    fuel_up.error_message = None
    fuel_up.next_attempt_at = None
    fuel_up.pending_since = utcnow()
    touch(ctx, fuel_up)


async def get_visible(session: AsyncSession, user: User, fuel_up_id: int) -> FuelUp:
    fuel_up = await session.get(FuelUp, fuel_up_id)
    if fuel_up is None or not user.can_access_vehicle(fuel_up.vehicle_id):
        raise FuelUpError(404, "No such fuel-up.")
    return fuel_up


async def list_visible(session: AsyncSession, user: User) -> list[FuelUp]:
    query = select(FuelUp).order_by(FuelUp.fuel_up_time.desc(), FuelUp.id.desc())
    if not user.is_admin:
        query = query.where(FuelUp.vehicle_id.in_(user.vehicle_ids or [-1]))
    return list((await session.execute(query)).scalars())


async def update(
    session: AsyncSession, ctx: Context, user: User, fuel_up: FuelUp, changes: dict
) -> FuelUp:
    """Applies edits (`changes` holds only the fields that were sent)."""
    if not fuel_up.editable:
        raise FuelUpError(409, "This fuel-up can't be edited any more.")

    if "vehicle_id" in changes and changes["vehicle_id"] != fuel_up.vehicle_id:
        vehicle = await vehicle_for(ctx, user, changes["vehicle_id"])
        fuel_up.vehicle_id = vehicle.id
        fuel_up.vehicle_name = vehicle.name
        changes.setdefault("odometer", fuel_up.odometer)
    if "odometer" in changes:
        await check_odometer(session, ctx, fuel_up.vehicle_id, changes["odometer"], fuel_up.id)
        fuel_up.odometer = changes["odometer"]
    if "fuel_up_time" in changes:
        fuel_up.fuel_up_time = as_utc(changes["fuel_up_time"], ctx.runtime)
        if fuel_up.status == Status.PENDING:
            # A receipt that fits the new time may already be here; the matcher looks again
            fuel_up.pending_since = utcnow()
    for field in ("is_fill_to_full", "missed_fuel_up"):
        if field in changes:
            setattr(fuel_up, field, changes[field])
    if "latitude" in changes or "longitude" in changes:
        fuel_up.latitude = changes.get("latitude", fuel_up.latitude)
        fuel_up.longitude = changes.get("longitude", fuel_up.longitude)

    payment_fields = {"fuel_type", "quantity", "total_price", "payment_source"}
    if payment_fields & changes.keys():
        if fuel_up.status == Status.PENDING:
            raise FuelUpError(409, "The payment data comes from the receipt. Wait for it.")
        if changes.get("payment_source") == PaymentSource.MANUAL:
            fuel_up.payment_source = PaymentSource.MANUAL
        elif changes.get("payment_source") == PaymentSource.EMAIL_RECEIPT and (
            fuel_up.payment_source != PaymentSource.EMAIL_RECEIPT
        ):
            raise FuelUpError(409, "A fuel-up can't be switched to a receipt.")
        for field in ("fuel_type", "quantity", "total_price"):
            if field in changes:
                setattr(fuel_up, field, changes[field])
        check_fuel_up_payment(ctx, fuel_up)
    touch(ctx, fuel_up)
    return fuel_up


def approve(ctx: Context, fuel_up: FuelUp) -> FuelUp:
    if fuel_up.status != Status.NEEDS_ATTENTION:
        raise FuelUpError(409, "Only fuel-ups that need attention can be approved.")
    check_fuel_up_payment(ctx, fuel_up)
    start_sending(ctx, fuel_up)
    return fuel_up


def retry(ctx: Context, fuel_up: FuelUp) -> FuelUp:
    """Try a failed fuel-up again: look for the receipt again, or send it again."""
    if fuel_up.status != Status.FAILED:
        raise FuelUpError(409, "Only failed fuel-ups can be retried.")
    if fuel_up.payment_source == PaymentSource.EMAIL_RECEIPT and fuel_up.receipt_id is None:
        wait_for_receipt(ctx, fuel_up)
        return fuel_up
    check_fuel_up_payment(ctx, fuel_up)
    start_sending(ctx, fuel_up)
    return fuel_up


async def count_open(session: AsyncSession) -> int:
    query = select(func.count()).select_from(FuelUp).where(FuelUp.status.in_(OPEN_STATUSES))
    return (await session.execute(query)).scalar_one()
