import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import DecimalText, UTCDateTime, utcnow


class ReceiptState(enum.StrEnum):
    UNMATCHED = "unmatched"  # no fuel-up yet
    MATCHED = "matched"
    IGNORED = "ignored"


class Receipt(Base):
    """A receipt that arrived by email, with the values read from its PDF."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The email's Message-ID, so the same mail is never stored twice
    message_id: Mapped[str] = mapped_column(String(300), unique=True)
    mail_subject: Mapped[str] = mapped_column(String(500), default="")
    state: Mapped[str] = mapped_column(String(20), default=ReceiptState.UNMATCHED, index=True)

    # The PDF, relative to the receipts directory
    pdf_file: Mapped[str | None] = mapped_column(String(200))
    pdf_name: Mapped[str] = mapped_column(String(200), default="receipt.pdf")

    station: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    printed_date: Mapped[str | None] = mapped_column(String(100))
    fuel_type: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[Decimal | None] = mapped_column(DecimalText)
    unit: Mapped[str | None] = mapped_column(String(20))
    total: Mapped[Decimal | None] = mapped_column(DecimalText)
    currency: Mapped[str | None] = mapped_column(String(10))
    transaction_id: Mapped[str | None] = mapped_column(String(100), index=True)

    # Values that couldn't be read, and things worth knowing
    missing: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Set when the PDF couldn't be read at all
    parse_error: Mapped[str | None] = mapped_column(Text)

    # Whether the email has been moved out of the inbox. It stays there until the receipt is
    # linked to a fuel-up (or ignored).
    email_moved: Mapped[bool] = mapped_column(Boolean, default=False)
    # Whether the user was told that this receipt has no fuel-up
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
