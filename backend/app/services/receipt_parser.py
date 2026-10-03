"""Reads the values from a Pace Drive receipt (a PDF with real text, sent by email).

The values are found by the labels printed next to them, for example `Quantity 23.00 L` or
`Transaction ID: ...`. The labels are the English ones of the Pace Drive app. If Pace changes the
layout, values go missing: they are reported in `ParsedReceipt.missing` and nothing is guessed.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import pdfplumber

# pdfminer complains about harmless font details in every Pace PDF
logging.getLogger("pdfminer").setLevel(logging.ERROR)


class ReceiptParseError(Exception):
    """The file isn't a readable Pace Drive receipt at all."""


@dataclass
class ParsedReceipt:
    station: str | None = None
    address: str | None = None  # station name and postal address on one line
    paid_at: datetime | None = None  # timezone-aware, in UTC
    printed_date: str | None = None
    fuel_type: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    total: Decimal | None = None
    currency: str | None = None
    price_per_unit: Decimal | None = None
    transaction_id: str | None = None
    # Values that couldn't be read
    missing: list[str] = field(default_factory=list)
    # Things worth knowing that don't stop the fuel-up
    warnings: list[str] = field(default_factory=list)

    @property
    def date_from_metadata(self) -> bool:
        return DATE_FROM_METADATA in self.warnings


DATE_FROM_METADATA = "date_from_pdf_metadata"
TOTAL_MISMATCH = "total_does_not_match_price"

_DECIMAL = r"-?\d[\d.,]*"
_FUEL_LINE = re.compile(rf"^(?P<type>.+?)\s+(?P<amount>{_DECIMAL})\s+(?P<currency>[A-Z]{{3}})$")
_QUANTITY = re.compile(rf"^\W*Quantity\s+(?P<value>{_DECIMAL})\s+(?P<unit>\S+)$", re.IGNORECASE)
_PRICE = re.compile(rf"^\W*Price\s*/\s*(?P<unit>\S+)\s+(?P<value>{_DECIMAL})\s+[A-Z]{{3}}$")
_TOTAL = re.compile(rf"^Total\s+(?P<value>{_DECIMAL})\s+(?P<currency>[A-Z]{{3}})$")
_DATE = re.compile(r"^Date\s+(?P<value>.+)$")
_TRANSACTION = re.compile(r"^Transaction ID:?\s*(?P<value>\S+)", re.IGNORECASE)
_PDF_DATE = re.compile(
    r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:([+-Z])(\d{2})?'?(\d{2})?)?"
)

# Different spellings of the same unit
_UNIT_ALIASES = {
    "l": "l",
    "lt": "l",
    "ltr": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "gal": "gal",
    "gallon": "gal",
    "gallons": "gal",
    "kwh": "kwh",
    "kg": "kg",
}


def normalize_unit(unit: str) -> str:
    return _UNIT_ALIASES.get(unit.strip().lower().rstrip("."), unit.strip().lower())


def units_match(receipt_unit: str | None, configured_unit: str) -> bool:
    return receipt_unit is not None and normalize_unit(receipt_unit) == normalize_unit(
        configured_unit
    )


def currencies_match(receipt_currency: str | None, configured_currency: str) -> bool:
    return (
        receipt_currency is not None
        and receipt_currency.strip().upper() == configured_currency.strip().upper()
    )


def parse_decimal(text: str) -> Decimal:
    """Reads 54.03, 54,03, 1,054.03 and 1.054,03."""
    text = text.strip().replace(" ", "")
    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal_separator == "," else ","
        text = text.replace(thousands, "").replace(decimal_separator, ".")
    elif "," in text:
        # Only a comma: a decimal comma (receipts in other languages)
        text = text.replace(",", ".")
    return Decimal(text)


def _normalize_lines(text: str) -> list[str]:
    # Newer browsers print a narrow no-break space before AM/PM
    text = text.replace(" ", " ").replace(" ", " ")
    return [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip()]


def pdf_creation_date(raw: str | None) -> datetime | None:
    """Reads the creation date from the PDF metadata, e.g. `D:20260923150840+00'00'`."""
    if not raw or not (match := _PDF_DATE.match(raw)):
        return None
    year, month, day, hour, minute, second, sign, tz_hour, tz_minute = match.groups()
    try:
        moment = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))
    except ValueError:
        return None
    offset_minutes = 0
    if sign in ("+", "-"):
        offset_minutes = int(tz_hour or 0) * 60 + int(tz_minute or 0)
        if sign == "-":
            offset_minutes = -offset_minutes
    return (moment - timedelta(minutes=offset_minutes)).replace(tzinfo=UTC)


def parse_text(
    text: str,
    *,
    tz: ZoneInfo,
    date_format: str,
    metadata_date: datetime | None = None,
) -> ParsedReceipt:
    """Read the values from the text of a receipt."""
    lines = _normalize_lines(text)
    receipt = ParsedReceipt()

    # Station and address: the lines between "Your fuel stop at" and "Date"
    for i, line in enumerate(lines):
        if line.lower().startswith("your fuel stop at"):
            block = []
            for following in lines[i + 1 :]:
                if _DATE.match(following):
                    break
                block.append(following)
            if block:
                receipt.station = block[0]
                receipt.address = ", ".join(block)
            break

    for i, line in enumerate(lines):
        if match := _DATE.match(line):
            receipt.printed_date = match["value"]
        elif line.startswith("Fuel pump") and i > 0 and (fuel := _FUEL_LINE.match(lines[i - 1])):
            receipt.fuel_type = fuel["type"]
            receipt.currency = fuel["currency"]
        elif match := _QUANTITY.match(line):
            receipt.quantity = _safe_decimal(match["value"])
            receipt.unit = match["unit"]
        elif match := _PRICE.match(line):
            receipt.price_per_unit = _safe_decimal(match["value"])
        elif match := _TOTAL.match(line):
            receipt.total = _safe_decimal(match["value"])
            receipt.currency = match["currency"]
        elif match := _TRANSACTION.match(line):
            receipt.transaction_id = match["value"]

    if receipt.printed_date:
        try:
            local = datetime.strptime(receipt.printed_date, date_format).replace(tzinfo=tz)
            receipt.paid_at = local.astimezone(UTC)
        except ValueError:
            pass
    if receipt.paid_at is None and metadata_date is not None:
        receipt.paid_at = metadata_date
        receipt.warnings.append(DATE_FROM_METADATA)

    required = {
        "station": receipt.station,
        "date": receipt.paid_at,
        "fuel_type": receipt.fuel_type,
        "quantity": receipt.quantity,
        "unit": receipt.unit,
        "total": receipt.total,
        "currency": receipt.currency,
        "transaction_id": receipt.transaction_id,
    }
    receipt.missing = [name for name, value in required.items() if value in (None, "")]

    if (
        receipt.quantity is not None
        and receipt.price_per_unit is not None
        and receipt.total is not None
        and abs(receipt.quantity * receipt.price_per_unit - receipt.total) > Decimal("0.05")
    ):
        receipt.warnings.append(TOTAL_MISMATCH)
    return receipt


def _safe_decimal(text: str) -> Decimal | None:
    try:
        return parse_decimal(text)
    except InvalidOperation:
        return None


def parse_receipt(pdf: bytes, *, tz: ZoneInfo, date_format: str) -> ParsedReceipt:
    """Read a receipt PDF. Raises ReceiptParseError if the file has no readable receipt."""
    try:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            text = "\n".join(page.extract_text() or "" for page in document.pages)
            metadata_date = pdf_creation_date(str(document.metadata.get("CreationDate") or ""))
    except Exception as exc:
        raise ReceiptParseError("The file is not a readable PDF.") from exc
    if "fuel stop at" not in text.lower() and "transaction id" not in text.lower():
        raise ReceiptParseError("The PDF doesn't look like a Pace Drive receipt.")
    return parse_text(text, tz=tz, date_format=date_format, metadata_date=metadata_date)
