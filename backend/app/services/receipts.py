"""Receipts from email: store them, match them to fuel-ups and apply their values."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.models import Attention, FuelUp, PaymentSource, Receipt, ReceiptState, Status
from app.services import fuel_ups as fuel_up_service
from app.services import notifications
from app.services.events import EventBus
from app.services.receipt_parser import (
    DATE_FROM_METADATA,
    ParsedReceipt,
    ReceiptParseError,
    currencies_match,
    parse_receipt,
    units_match,
)
from app.services.runtime_settings import RuntimeSettings

logger = logging.getLogger(__name__)

RECEIPT_DIR = "receipts"

MISSING_LABELS = {
    "station": "station",
    "date": "date",
    "fuel_type": "fuel type",
    "quantity": "fuel amount",
    "unit": "unit",
    "total": "total price",
    "currency": "currency",
    "transaction_id": "transaction ID",
}


@dataclass
class ReceiptContext:
    fuel: fuel_up_service.Context
    data_dir: Path

    @property
    def runtime(self) -> RuntimeSettings:
        return self.fuel.runtime

    @property
    def events(self) -> EventBus:
        return self.fuel.events

    @property
    def receipt_dir(self) -> Path:
        return self.data_dir / RECEIPT_DIR


def message_id_of(message_id: str | None, raw: bytes) -> str:
    """The email's Message-ID; mails without one get a hash of their content."""
    if message_id and message_id.strip():
        return message_id.strip()[:300]
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def pdf_path(ctx: ReceiptContext, receipt: Receipt) -> Path | None:
    return ctx.receipt_dir / receipt.pdf_file if receipt.pdf_file else None


async def find_by_message_id(session: AsyncSession, message_id: str) -> Receipt | None:
    result = await session.execute(select(Receipt).where(Receipt.message_id == message_id))
    return result.scalar_one_or_none()


async def find_by_transaction(session: AsyncSession, transaction_id: str) -> Receipt | None:
    result = await session.execute(
        select(Receipt).where(Receipt.transaction_id == transaction_id).limit(1)
    )
    return result.scalar_one_or_none()


async def store_receipt(
    session: AsyncSession,
    ctx: ReceiptContext,
    *,
    message_id: str,
    subject: str,
    pdf: bytes,
    pdf_name: str,
) -> Receipt | None:
    """Read a receipt PDF and store it. Returns None if it is a duplicate."""
    parsed: ParsedReceipt | None = None
    parse_error: str | None = None
    try:
        parsed = parse_receipt(pdf, tz=ctx.runtime.tz, date_format=ctx.runtime.pace_date_format)
    except ReceiptParseError as exc:
        parse_error = str(exc)

    if (
        parsed
        and parsed.transaction_id
        and await find_by_transaction(session, parsed.transaction_id)
    ):
        logger.info("Receipt %s was already received, skipping", parsed.transaction_id)
        return None

    receipt = Receipt(
        message_id=message_id,
        mail_subject=subject[:500],
        pdf_name=pdf_name[:200] or "receipt.pdf",
        parse_error=parse_error,
    )
    if parsed:
        receipt.station = parsed.station
        receipt.address = parsed.address
        receipt.paid_at = parsed.paid_at
        receipt.printed_date = parsed.printed_date
        receipt.fuel_type = parsed.fuel_type
        receipt.quantity = parsed.quantity
        receipt.unit = parsed.unit
        receipt.total = parsed.total
        receipt.currency = parsed.currency
        receipt.transaction_id = parsed.transaction_id
        receipt.missing = parsed.missing
        receipt.warnings = parsed.warnings
    session.add(receipt)
    await session.flush()

    ctx.receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt.pdf_file = f"{receipt.id}.pdf"
    (ctx.receipt_dir / receipt.pdf_file).write_bytes(pdf)
    return receipt


# --- matching ---


def _closest(candidates: list, moment: datetime, key) -> object | None:
    return min(candidates, key=lambda c: abs(key(c) - moment), default=None)


async def find_fuel_up_for(
    session: AsyncSession, ctx: ReceiptContext, receipt: Receipt
) -> FuelUp | None:
    """The waiting fuel-up that fits the receipt's time best, if any."""
    if receipt.paid_at is None:
        return None
    window = timedelta(minutes=ctx.runtime.match_window_minutes)
    result = await session.execute(
        select(FuelUp).where(
            FuelUp.status == Status.PENDING,
            FuelUp.payment_source == PaymentSource.EMAIL_RECEIPT,
            FuelUp.receipt_id.is_(None),
            FuelUp.fuel_up_time >= receipt.paid_at - window,
            FuelUp.fuel_up_time <= receipt.paid_at + window,
        )
    )
    return _closest(list(result.scalars()), receipt.paid_at, lambda f: f.fuel_up_time)


async def find_receipt_for(
    session: AsyncSession, ctx: ReceiptContext, fuel_up: FuelUp
) -> Receipt | None:
    """The unmatched receipt that fits the fuel-up's time best, if any."""
    window = timedelta(minutes=ctx.runtime.match_window_minutes)
    result = await session.execute(
        select(Receipt).where(
            Receipt.state == ReceiptState.UNMATCHED,
            Receipt.paid_at.is_not(None),
            Receipt.paid_at >= fuel_up.fuel_up_time - window,
            Receipt.paid_at <= fuel_up.fuel_up_time + window,
        )
    )
    return _closest(list(result.scalars()), fuel_up.fuel_up_time, lambda r: r.paid_at)


async def try_match_receipt(
    session: AsyncSession, ctx: ReceiptContext, receipt: Receipt
) -> FuelUp | None:
    fuel_up = await find_fuel_up_for(session, ctx, receipt)
    if fuel_up is not None:
        await apply_receipt(session, ctx, fuel_up, receipt)
    return fuel_up


async def try_match_fuel_up(
    session: AsyncSession, ctx: ReceiptContext, fuel_up: FuelUp
) -> Receipt | None:
    if fuel_up.status != Status.PENDING or fuel_up.receipt_id is not None:
        return None
    receipt = await find_receipt_for(session, ctx, fuel_up)
    if receipt is not None:
        await apply_receipt(session, ctx, fuel_up, receipt)
    return receipt


async def apply_receipt(
    session: AsyncSession,
    ctx: ReceiptContext,
    fuel_up: FuelUp,
    receipt: Receipt,
) -> None:
    """Link the receipt to the fuel-up, copy its values and move the fuel-up on."""
    fctx = ctx.fuel
    receipt.state = ReceiptState.MATCHED
    fuel_up.receipt_id = receipt.id
    fuel_up.payment_source = PaymentSource.EMAIL_RECEIPT
    fuel_up.pending_since = None
    fuel_up.fuel_type = receipt.fuel_type
    fuel_up.quantity = receipt.quantity
    fuel_up.total_price = receipt.total
    fuel_up.address = receipt.address
    fuel_up.warnings = [w for w in (fuel_up.warnings or []) if not w.startswith("receipt:")]
    ctx.events.publish("receipt", id=receipt.id)

    problems = []
    if receipt.missing:
        names = ", ".join(MISSING_LABELS.get(m, m) for m in receipt.missing)
        problems.append(
            (Attention.UNREADABLE, f"Couldn't read these values from the receipt: {names}.")
        )
    else:
        if not units_match(receipt.unit, ctx.runtime.volume_unit):
            problems.append(
                (
                    Attention.UNIT_MISMATCH,
                    f"The receipt uses the unit {receipt.unit}, but LubeLogger uses "
                    f"{ctx.runtime.volume_unit}. Convert the fuel amount, then approve.",
                )
            )
        if not currencies_match(receipt.currency, ctx.runtime.currency):
            problems.append(
                (
                    Attention.UNIT_MISMATCH,
                    f"The receipt uses the currency {receipt.currency}, but LubeLogger uses "
                    f"{ctx.runtime.currency}. Convert the price, then approve.",
                )
            )

    if DATE_FROM_METADATA in (receipt.warnings or []):
        fuel_up.warnings = [*fuel_up.warnings, "receipt: The date was taken from the PDF file."]
        await notifications.notify(
            session,
            ctx.events,
            level="warning",
            title=f"Receipt date for fuel-up #{fuel_up.id} came from the PDF",
            message=(
                "The date printed on the receipt couldn't be read, so the time the PDF was "
                "created was used instead. Check the date format in the settings."
            ),
            user_id=fuel_up.created_by_id,
            fuel_up_id=fuel_up.id,
        )

    if problems:
        fuel_up.status = Status.NEEDS_ATTENTION
        fuel_up.attention = problems[0][0]
        fuel_up.attention_message = " ".join(message for _, message in problems)
        fuel_up.next_attempt_at = None
        await notifications.notify(
            session,
            ctx.events,
            level="warning",
            title=f"Fuel-up #{fuel_up.id} needs attention",
            message=fuel_up.attention_message,
            user_id=fuel_up.created_by_id,
            fuel_up_id=fuel_up.id,
        )
        fuel_up_service.touch(fctx, fuel_up)
        return

    if ctx.runtime.review_before_send:
        fuel_up_service.ask_for_review(fctx, fuel_up)
        # The user isn't looking at the screen: say that the receipt has arrived
        await notifications.notify(
            session,
            ctx.events,
            level="info",
            title=f"Receipt for fuel-up #{fuel_up.id} arrived",
            message="Check the fuel-up and approve it to send it to LubeLogger.",
            user_id=fuel_up.created_by_id,
            fuel_up_id=fuel_up.id,
        )
    else:
        fuel_up_service.start_sending(fctx, fuel_up)


async def release_receipt(session: AsyncSession, fuel_up: FuelUp) -> None:
    """Take the receipt back from a fuel-up (it matches again when the fuel-up waits)."""
    if fuel_up.receipt_id is None:
        return
    receipt = await session.get(Receipt, fuel_up.receipt_id)
    if receipt is not None and receipt.state == ReceiptState.MATCHED:
        receipt.state = ReceiptState.UNMATCHED
    fuel_up.receipt_id = None


# --- periodic work ---


async def expire_waiting_fuel_ups(
    session: AsyncSession, ctx: ReceiptContext, now: datetime | None = None
) -> int:
    """Fail the fuel-ups that waited too long for their receipt."""
    now = now or utcnow()
    cutoff = now - timedelta(minutes=ctx.runtime.receipt_timeout_minutes)
    result = await session.execute(
        select(FuelUp).where(
            FuelUp.status == Status.PENDING,
            FuelUp.payment_source == PaymentSource.EMAIL_RECEIPT,
            FuelUp.receipt_id.is_(None),
            FuelUp.pending_since.is_not(None),
            FuelUp.pending_since <= cutoff,
        )
    )
    expired = list(result.scalars())
    minutes = ctx.runtime.receipt_timeout_minutes
    for fuel_up in expired:
        fuel_up.status = Status.FAILED
        fuel_up.error_message = (
            f"No receipt arrived within {minutes} minutes. Retry the search, "
            "or enter the payment data manually."
        )
        fuel_up.updated_at = now
        await notifications.notify(
            session,
            ctx.events,
            level="error",
            title=f"No receipt for fuel-up #{fuel_up.id}",
            message=f"{fuel_up.vehicle_name}, {fuel_up.odometer}: {fuel_up.error_message}",
            user_id=fuel_up.created_by_id,
            fuel_up_id=fuel_up.id,
        )
        ctx.events.publish("fuel_up", id=fuel_up.id)
    return len(expired)


async def rematch_waiting(session: AsyncSession, ctx: ReceiptContext) -> int:
    """Match waiting fuel-ups to receipts that are already here (e.g. after an edit)."""
    result = await session.execute(
        select(FuelUp).where(
            FuelUp.status == Status.PENDING,
            FuelUp.payment_source == PaymentSource.EMAIL_RECEIPT,
            FuelUp.receipt_id.is_(None),
        )
    )
    matched = 0
    for fuel_up in result.scalars():
        if await try_match_fuel_up(session, ctx, fuel_up):
            matched += 1
    return matched


async def notify_lonely_receipts(
    session: AsyncSession, ctx: ReceiptContext, now: datetime | None = None
) -> int:
    """Tell the admins about receipts that still have no fuel-up after the matching window."""
    now = now or utcnow()
    cutoff = now - timedelta(minutes=ctx.runtime.match_window_minutes)
    result = await session.execute(
        select(Receipt).where(
            Receipt.state == ReceiptState.UNMATCHED,
            Receipt.notified.is_(False),
            Receipt.created_at <= cutoff,
        )
    )
    receipts = list(result.scalars())
    for receipt in receipts:
        receipt.notified = True
        if receipt.parse_error:
            continue  # the admins were told when it arrived
        where = receipt.station or "a station"
        await notifications.notify(
            session,
            ctx.events,
            level="info",
            title="Receipt without a fuel-up",
            message=(
                f"A receipt from {where} ({receipt.printed_date or 'unknown date'}) has no "
                "fuel-up. Complete it with the vehicle and odometer reading, or ignore it."
            ),
        )
    return len(receipts)
