"""Background work: writes fuel-ups to LubeLogger and keeps the other statuses moving."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.types import utcnow
from app.models import FuelUp, NotificationKind, PaymentSource, Receipt, Status
from app.services import notifications
from app.services.events import EventBus
from app.services.lubelogger import (
    FIELD_TYPE_LOCATION,
    ExtraField,
    LubeLoggerClient,
    LubeLoggerError,
    LubeLoggerRejected,
    LubeLoggerUnavailable,
    NewGasRecord,
    UploadedFile,
)
from app.services.runtime_settings import RuntimeSettings
from app.services.vehicles import VehicleDirectory

logger = logging.getLogger(__name__)

# Waiting times before trying again when LubeLogger can't be reached; after the last one it fails
RETRY_DELAYS = (60, 300, 900)

PAYMENT_LABELS = {
    PaymentSource.MANUAL: "Manual",
    PaymentSource.EMAIL_RECEIPT: "Pace Drive email receipt",
}


class SendRefused(LubeLoggerError):
    """The fuel-up can't be written as it is (e.g. the odometer reading is too low)."""


# The line in the notes of a LubeLogger record that says which receipt it was made from
TRANSACTION_MARKER = "PaceDrive Transaction ID: "


def notes_text(
    fuel_type: str | None,
    payment_source: PaymentSource,
    created_by: str,
    transaction_id: str | None = None,
) -> str:
    """The notes Fuel Tracker adds to a LubeLogger record."""
    lines = []
    if fuel_type:
        lines.append(f"Fuel type: {fuel_type}")
    lines.append(f"Payment: {PAYMENT_LABELS[payment_source]}")
    lines.append(f"Created by: {created_by}")
    if transaction_id:
        lines.append(f"{TRANSACTION_MARKER}{transaction_id}")
    return "\n".join(lines)


def build_notes(fuel_up: FuelUp, transaction_id: str | None = None) -> str:
    return notes_text(
        fuel_up.fuel_type,
        PaymentSource(fuel_up.payment_source),
        fuel_up.created_by_name,
        transaction_id,
    )


class Processor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        runtime: RuntimeSettings,
        events: EventBus,
        lubelogger: LubeLoggerClient | None,
        *,
        vehicles: VehicleDirectory | None = None,
        gps_field: str,
        address_field: str,
        receipt_dir: Path,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        interval: float = 15,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._runtime = runtime
        self._events = events
        self._lubelogger = lubelogger
        self._vehicles = vehicles
        self._gps_field = gps_field
        self._address_field = address_field
        self._receipt_dir = receipt_dir
        self._retry_delays = retry_delays
        self._interval = interval
        self._clock = clock
        self._wake = asyncio.Event()
        # Other parts (receipts, cleanup) add their periodic work here
        self.periodic_jobs: list[Callable[[], object]] = []

    def wake(self) -> None:
        """Look for work right now instead of at the next interval."""
        self._wake.set()

    async def run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            self._wake.clear()
            await self.tick()

    async def tick(self) -> None:
        try:
            await self.process_due()
        except Exception:
            logger.exception("Sending fuel-ups failed")
        for job in self.periodic_jobs:
            try:
                result = job()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Background job %s failed", getattr(job, "__name__", job))

    async def process_due(self) -> int:
        """Send every fuel-up that is due. Returns how many were handled."""
        async with self._sessionmaker() as session:
            query = select(FuelUp.id).where(
                FuelUp.status == Status.SENDING,
                (FuelUp.next_attempt_at.is_(None)) | (FuelUp.next_attempt_at <= self._clock()),
            )
            ids = list((await session.execute(query)).scalars())
        for fuel_up_id in ids:
            await self.send(fuel_up_id)
        return len(ids)

    async def send(self, fuel_up_id: int) -> None:
        async with self._sessionmaker() as session:
            fuel_up = await session.get(FuelUp, fuel_up_id)
            if fuel_up is None or fuel_up.status != Status.SENDING:
                return
            try:
                fuel_up.lubelogger_record_id = await self._write(session, fuel_up)
            except LubeLoggerUnavailable as exc:
                await self._postpone_or_fail(session, fuel_up, exc)
            except LubeLoggerError as exc:
                await self._fail(session, fuel_up, str(exc))
            else:
                fuel_up.status = Status.DONE
                fuel_up.completed_at = self._clock()
                fuel_up.error_message = None
                fuel_up.next_attempt_at = None
                fuel_up.updated_at = self._clock()
            await session.commit()
            self._events.publish("fuel_up", id=fuel_up.id)

    async def _postpone_or_fail(
        self, session: AsyncSession, fuel_up: FuelUp, exc: LubeLoggerUnavailable
    ) -> None:
        fuel_up.send_attempts += 1
        if fuel_up.send_attempts > len(self._retry_delays):
            await self._fail(
                session,
                fuel_up,
                f"{exc} It stayed unreachable after {fuel_up.send_attempts} tries.",
            )
            return
        delay = self._retry_delays[fuel_up.send_attempts - 1]
        fuel_up.next_attempt_at = self._clock() + timedelta(seconds=delay)
        fuel_up.error_message = f"{exc} Trying again in {int(delay // 60) or 1} min."
        fuel_up.updated_at = self._clock()

    async def _fail(self, session: AsyncSession, fuel_up: FuelUp, message: str) -> None:
        fuel_up.status = Status.FAILED
        fuel_up.error_message = message
        fuel_up.next_attempt_at = None
        fuel_up.updated_at = self._clock()
        await notifications.notify(
            session,
            self._events,
            kind=NotificationKind.FUEL_UP_FAILED,
            level="error",
            title=f"Fuel-up #{fuel_up.id} failed",
            message=f"{fuel_up.vehicle_name}, {fuel_up.odometer}: {message}",
            user_id=fuel_up.created_by_id,
            fuel_up_id=fuel_up.id,
        )

    async def _write(self, session: AsyncSession, fuel_up: FuelUp) -> int:
        """Create the record in LubeLogger. Returns its id."""
        if fuel_up.lubelogger_record_id:
            return fuel_up.lubelogger_record_id
        client = self._lubelogger
        if client is None:
            raise LubeLoggerRejected("LubeLogger isn't set up. Set LUBELOGGER_URL.")
        if fuel_up.quantity is None or fuel_up.total_price is None or not fuel_up.fuel_type:
            raise SendRefused("The payment data is incomplete.")

        latest = await client.latest_odometer(fuel_up.vehicle_id)
        if fuel_up.odometer < latest:
            raise SendRefused(
                f"The odometer reading {fuel_up.odometer} is lower than the last reading "
                f"in LubeLogger ({latest}). Correct it and try again."
            )

        record_date = fuel_up.fuel_up_time.astimezone(self._runtime.tz).date()

        # A previous try may have created the record without us hearing back
        for existing in await client.gas_records(fuel_up.vehicle_id):
            if (
                existing.odometer == fuel_up.odometer
                and existing.date == record_date
                and existing.fuel_consumed == fuel_up.quantity
                and existing.cost == fuel_up.total_price
            ):
                return existing.id

        transaction_id, uploaded = await self._receipt_parts(session, client, fuel_up)

        extra_fields = []
        if fuel_up.latitude is not None and fuel_up.longitude is not None:
            extra_fields.append(
                ExtraField(
                    self._gps_field,
                    f"{fuel_up.latitude:.6f},{fuel_up.longitude:.6f}",
                    FIELD_TYPE_LOCATION,
                )
            )
        if fuel_up.address:
            extra_fields.append(ExtraField(self._address_field, fuel_up.address))

        record = NewGasRecord(
            date=record_date,
            odometer=fuel_up.odometer,
            fuel_consumed=fuel_up.quantity,
            cost=fuel_up.total_price,
            is_fill_to_full=fuel_up.is_fill_to_full,
            missed_fuel_up=fuel_up.missed_fuel_up,
            notes=build_notes(fuel_up, transaction_id),
            extra_fields=extra_fields,
            files=[uploaded] if uploaded else [],
        )
        record_id = await client.add_gas_record(fuel_up.vehicle_id, record)
        if self._vehicles is not None:
            self._vehicles.note_odometer(fuel_up.vehicle_id, fuel_up.odometer)
        return record_id

    async def _receipt_parts(
        self, session: AsyncSession, client: LubeLoggerClient, fuel_up: FuelUp
    ) -> tuple[str | None, UploadedFile | None]:
        """For a fuel-up with a receipt: its transaction ID and the PDF uploaded to LubeLogger."""
        if fuel_up.receipt_id is None:
            return None, None
        receipt = await session.get(Receipt, fuel_up.receipt_id)
        if receipt is None:
            return None, None

        if receipt.transaction_id:
            marker = f"{TRANSACTION_MARKER}{receipt.transaction_id}"
            for record in await client.all_gas_records():
                if marker in record.notes:
                    raise SendRefused(
                        f"This receipt (transaction {receipt.transaction_id}) is already in "
                        f"LubeLogger, as fuel record #{record.id}."
                    )

        if fuel_up.lubelogger_file:
            return receipt.transaction_id, UploadedFile(**fuel_up.lubelogger_file)
        path = self._receipt_dir / receipt.pdf_file if receipt.pdf_file else None
        if path is None or not path.is_file():
            return receipt.transaction_id, None
        uploaded = await client.upload_document(receipt.pdf_name, path.read_bytes())
        # Kept right away: a later failure must not upload the file a second time
        fuel_up.lubelogger_file = {"name": uploaded.name, "location": uploaded.location}
        await session.commit()
        return receipt.transaction_id, uploaded
