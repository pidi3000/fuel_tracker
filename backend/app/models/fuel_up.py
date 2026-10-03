import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import DecimalText, UTCDateTime, utcnow


class Status(enum.StrEnum):
    PENDING = "pending"  # waiting for the receipt
    NEEDS_ATTENTION = "needs_attention"  # waiting for review, or something needs a look
    SENDING = "sending"  # being written to LubeLogger (shown as Pending in the UI)
    DONE = "done"
    FAILED = "failed"


class PaymentSource(enum.StrEnum):
    MANUAL = "manual"
    EMAIL_RECEIPT = "email_receipt"


class Attention(enum.StrEnum):
    """Why a fuel-up is in Needs attention."""

    REVIEW = "review"  # the "review before sending" setting is on
    UNIT_MISMATCH = "unit_mismatch"
    UNREADABLE = "unreadable"  # a value couldn't be read from the receipt
    DATE_FALLBACK = "date_fallback"  # the receipt date came from the PDF metadata


class FuelUp(Base):
    __tablename__ = "fuel_ups"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Kept as text too, since the notes in LubeLogger name the user and users can be deleted
    created_by_name: Mapped[str] = mapped_column(String(50))

    vehicle_id: Mapped[int] = mapped_column(Integer, index=True)
    vehicle_name: Mapped[str] = mapped_column(String(200), default="")
    odometer: Mapped[int] = mapped_column(Integer)
    fuel_up_time: Mapped[datetime] = mapped_column(UTCDateTime)
    is_fill_to_full: Mapped[bool] = mapped_column(Boolean, default=True)
    missed_fuel_up: Mapped[bool] = mapped_column(Boolean, default=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    payment_source: Mapped[str] = mapped_column(String(20), default=PaymentSource.MANUAL)
    fuel_type: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[Decimal | None] = mapped_column(DecimalText)
    total_price: Mapped[Decimal | None] = mapped_column(DecimalText)
    address: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default=Status.PENDING, index=True)
    attention: Mapped[str | None] = mapped_column(String(30))
    attention_message: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)

    # When the wait for a receipt started (the wait time counts from here)
    pending_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    lubelogger_record_id: Mapped[int | None] = mapped_column(Integer)
    # The receipt PDF once it is uploaded to LubeLogger ({"name": ..., "location": ...}), so a
    # second try doesn't upload it again
    lubelogger_file: Mapped[dict | None] = mapped_column(JSON)
    receipt_id: Mapped[int | None] = mapped_column(
        ForeignKey("receipts.id", ondelete="SET NULL"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def editable(self) -> bool:
        return self.status in (Status.PENDING, Status.NEEDS_ATTENTION, Status.FAILED)


class Notification(Base):
    """A message shown in the web UI. `user_id` is empty for messages meant for admins."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(10), default="info")  # info, warning or error
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="")
    fuel_up_id: Mapped[int | None] = mapped_column(Integer)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SettingOverride(Base):
    """A setting changed in the web UI; it wins over the environment variable."""

    __tablename__ = "settings_overrides"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
