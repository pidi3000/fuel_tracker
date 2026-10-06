"""Matching an email receipt to a fuel record that already exists in LubeLogger.

For a fuel-up that was entered in LubeLogger by hand. The receipt is attached to that record
instead of making a new one.

The date decides. A receipt is always older than the record (the receipt is made when paying, the
record is entered afterwards, and LubeLogger only knows the day), so only records on the day of
the receipt or later are offered, the closest day first. The fuel amount and the total price are
then checked against the receipt: by default only records where both fit are offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models import Receipt
from app.services.lubelogger import GasRecord, UploadedFile
from app.services.processor import TRANSACTION_MARKER
from app.services.receipts import currencies_match, units_match

# Amounts are compared to the cent; LubeLogger rounds what a person types
TOLERANCE = Decimal("0.01")
# How many days after the receipt a record may be (people don't always enter a fill-up at once)
DEFAULT_DAYS = 30
MAX_DAYS = 365


@dataclass
class Candidate:
    record: GasRecord
    days_after: int  # how many days after the receipt the record is dated
    amount_matches: bool
    price_matches: bool

    @property
    def score(self) -> int:
        return int(self.amount_matches) + int(self.price_matches)


def receipt_day(receipt: Receipt, tz: ZoneInfo) -> date | None:
    """The day the receipt was made, in the configured time zone."""
    return receipt.paid_at.astimezone(tz).date() if receipt.paid_at else None


def has_receipt(record: GasRecord) -> bool:
    """Whether a Pace Drive receipt is already recorded in this record's notes."""
    return TRANSACTION_MARKER.strip() in record.notes


def candidates(
    receipt: Receipt,
    records: list[GasRecord],
    *,
    tz: ZoneInfo,
    volume_unit: str,
    currency: str,
    days: int = DEFAULT_DAYS,
    include_other_amounts: bool = False,
) -> list[Candidate]:
    """Records the receipt may belong to, the closest in date first."""
    day = receipt_day(receipt, tz)
    if day is None:
        return []
    amount_comparable = receipt.quantity is not None and units_match(receipt.unit, volume_unit)
    price_comparable = receipt.total is not None and currencies_match(receipt.currency, currency)
    found: list[Candidate] = []
    for record in records:
        days_after = (record.date - day).days
        if days_after < 0 or days_after > days or has_receipt(record):
            continue
        candidate = Candidate(
            record=record,
            days_after=days_after,
            amount_matches=amount_comparable
            and abs(record.fuel_consumed - receipt.quantity) <= TOLERANCE,
            price_matches=price_comparable and abs(record.cost - receipt.total) <= TOLERANCE,
        )
        if include_other_amounts or (candidate.amount_matches and candidate.price_matches):
            found.append(candidate)
    # Closest day first; of the same day, the better fit; then the older record
    found.sort(key=lambda c: (c.days_after, -c.score, c.record.id))
    return found


def updated_record(
    record: GasRecord,
    receipt: Receipt,
    uploaded: UploadedFile | None,
    *,
    address_field: str,
) -> dict:
    """The record as it is sent back to LubeLogger, with the receipt added.

    What the record already holds is kept: an update replaces the whole record. The receipt adds
    its transaction ID to the notes (so it is never used twice), the PDF as an attachment and, if
    the record has none, the station address.
    """
    body = dict(record.raw)

    def put(key: str, value) -> None:
        existing = next((k for k in body if k.lower() == key.lower()), key)
        body[existing] = value

    notes = record.notes
    if receipt.transaction_id:
        line = f"{TRANSACTION_MARKER}{receipt.transaction_id}"
        notes = f"{notes}\n{line}" if notes.strip() else line
    put("notes", notes)

    if uploaded is not None:
        files = [{"name": f.name, "location": f.location} for f in record.files]
        files.append({"name": uploaded.name, "location": uploaded.location})
        put("files", files)

    if receipt.address:
        raw_fields = next((v for k, v in body.items() if k.lower() == "extrafields"), None)
        fields = [dict(item) for item in raw_fields] if isinstance(raw_fields, list) else []
        current = next((f for f in fields if f.get("name") == address_field), None)
        if current is None:
            fields.append({"name": address_field, "value": receipt.address, "fieldType": 0})
        elif not str(current.get("value") or "").strip():
            current["value"] = receipt.address
        put("extraFields", fields)
    return body
