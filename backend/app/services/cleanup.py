"""Housekeeping: Fuel Tracker only keeps what is still being worked on.

A finished fuel-up stays visible for a grace period (`done_retention_days`), then it is deleted
together with its receipt and the PDF. The record itself stays in LubeLogger.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.models import FuelUp, Notification, Receipt, ReceiptState, Status
from app.services import auth as auth_service
from app.services.events import EventBus
from app.services.runtime_settings import RuntimeSettings

logger = logging.getLogger(__name__)

# Read notifications are only kept for this long
NOTIFICATION_DAYS = 30


def _remove_pdf(receipt_dir: Path, receipt: Receipt) -> None:
    if receipt.pdf_file:
        (receipt_dir / receipt.pdf_file).unlink(missing_ok=True)


async def clean_up(
    session: AsyncSession,
    runtime: RuntimeSettings,
    events: EventBus | None,
    receipt_dir: Path,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete what has outlived its use. Returns how many of each kind were deleted."""
    now = now or utcnow()
    retention = timedelta(days=runtime.done_retention_days)
    removed = {"fuel_ups": 0, "receipts": 0, "notifications": 0}

    # Finished fuel-ups and their receipts
    result = await session.execute(
        select(FuelUp).where(FuelUp.status == Status.DONE, FuelUp.completed_at <= now - retention)
    )
    for fuel_up in result.scalars():
        if fuel_up.receipt_id is not None and (
            receipt := await session.get(Receipt, fuel_up.receipt_id)
        ):
            _remove_pdf(receipt_dir, receipt)
            fuel_up.receipt_id = None
            await session.flush()
            await session.delete(receipt)
            removed["receipts"] += 1
        await session.delete(fuel_up)
        removed["fuel_ups"] += 1
        if events is not None:
            events.publish("fuel_up", id=fuel_up.id)

    # Receipts the user decided not to use. Receipts without a fuel-up wait for a decision.
    result = await session.execute(
        select(Receipt).where(
            Receipt.state == ReceiptState.IGNORED, Receipt.created_at <= now - retention
        )
    )
    for receipt in result.scalars():
        _remove_pdf(receipt_dir, receipt)
        await session.delete(receipt)
        removed["receipts"] += 1

    cutoff = now - timedelta(days=NOTIFICATION_DAYS)
    deleted = await session.execute(
        delete(Notification).where(
            Notification.is_read.is_(True), Notification.created_at <= cutoff
        )
    )
    removed["notifications"] = deleted.rowcount or 0

    await auth_service.delete_expired_sessions(session)
    if any(removed.values()):
        logger.info("Cleaned up: %s", removed)
    return removed
