import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services.receipt_parser import (
    DATE_FROM_METADATA,
    TOTAL_MISMATCH,
    ReceiptParseError,
    currencies_match,
    parse_decimal,
    parse_receipt,
    parse_text,
    pdf_creation_date,
    units_match,
)

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"
BERLIN = ZoneInfo("Europe/Berlin")
FORMAT = "%m/%d/%Y, %I:%M %p"

RECEIPT_TEXT = """Your fuel stop at
TESTOIL
Musterstrasse 12
12345 Musterstadt
Date 9/23/2026, 5:08 PM
Super 41.15 EUR
Fuel pump 6
· Quantity 23.00 L
· Price / L 1.789 EUR
Total 41.15 EUR
Net 34.58 EUR
VAT 19% A 6.57 EUR
Transaction ID: 00000000-1111-4222-8333-444444444444
"""


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.pdf"))


def test_fixtures_exist() -> None:
    assert len(fixture_names()) >= 3


@pytest.mark.parametrize("name", fixture_names())
def test_fixture_receipts_are_read_correctly(name: str) -> None:
    expected = json.loads((FIXTURES / f"{name}.json").read_text())
    receipt = parse_receipt((FIXTURES / f"{name}.pdf").read_bytes(), tz=BERLIN, date_format=FORMAT)

    assert receipt.missing == []
    assert receipt.warnings == []
    assert receipt.station == expected["station"]
    assert receipt.address == expected["address"]
    assert receipt.printed_date == expected["printed_date"]
    assert receipt.paid_at == datetime.fromisoformat(expected["paid_at"])
    assert receipt.paid_at is not None and receipt.paid_at.utcoffset().total_seconds() == 0
    assert receipt.fuel_type == expected["fuel_type"]
    assert receipt.quantity == Decimal(expected["quantity"])
    assert receipt.unit == expected["unit"]
    assert receipt.total == Decimal(expected["total"])
    assert receipt.currency == expected["currency"]
    assert receipt.transaction_id == expected["transaction_id"]


def test_pdf_creation_date_is_the_backup_for_an_unreadable_date() -> None:
    pdf = (FIXTURES / "pace_super.pdf").read_bytes()
    receipt = parse_receipt(pdf, tz=BERLIN, date_format="%d.%m.%Y %H:%M")  # wrong format
    assert receipt.printed_date == "9/23/2026, 5:08 PM"
    # The metadata says 15:08:40 UTC, which is 17:08 in Berlin
    assert receipt.paid_at == datetime(2026, 9, 23, 15, 8, 40, tzinfo=UTC)
    assert receipt.warnings == [DATE_FROM_METADATA]
    assert receipt.date_from_metadata and receipt.missing == []


def test_the_printed_date_wins_over_the_metadata() -> None:
    receipt = parse_receipt(
        (FIXTURES / "pace_super.pdf").read_bytes(), tz=BERLIN, date_format=FORMAT
    )
    assert receipt.paid_at == datetime(2026, 9, 23, 15, 8, tzinfo=UTC)  # not 15:08:40
    assert not receipt.date_from_metadata


def test_time_zone_is_applied() -> None:
    tokyo = parse_text(RECEIPT_TEXT, tz=ZoneInfo("Asia/Tokyo"), date_format=FORMAT)
    assert tokyo.paid_at == datetime(2026, 9, 23, 8, 8, tzinfo=UTC)
    winter = parse_text(
        RECEIPT_TEXT.replace("9/23/2026", "1/5/2027"), tz=BERLIN, date_format=FORMAT
    )
    assert winter.paid_at == datetime(2027, 1, 5, 16, 8, tzinfo=UTC)  # UTC+1


def test_narrow_no_break_space_before_pm() -> None:
    receipt = parse_text(RECEIPT_TEXT.replace("5:08 PM", "5:08 PM"), tz=BERLIN, date_format=FORMAT)
    assert receipt.paid_at == datetime(2026, 9, 23, 15, 8, tzinfo=UTC)


def test_noon_and_midnight() -> None:
    noon = parse_text(RECEIPT_TEXT.replace("5:08 PM", "12:05 PM"), tz=BERLIN, date_format=FORMAT)
    midnight = parse_text(
        RECEIPT_TEXT.replace("5:08 PM", "12:05 AM"), tz=BERLIN, date_format=FORMAT
    )
    assert noon.paid_at == datetime(2026, 9, 23, 10, 5, tzinfo=UTC)
    assert midnight.paid_at == datetime(2026, 9, 22, 22, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    ("line", "missing"),
    [
        ("Total 41.15 EUR", "total"),
        ("· Quantity 23.00 L", "quantity"),
        ("Transaction ID: 00000000-1111-4222-8333-444444444444", "transaction_id"),
        ("Super 41.15 EUR", "fuel_type"),
        ("Date 9/23/2026, 5:08 PM", "date"),
    ],
)
def test_missing_values_are_reported_and_nothing_is_guessed(line: str, missing: str) -> None:
    receipt = parse_text(RECEIPT_TEXT.replace(line + "\n", ""), tz=BERLIN, date_format=FORMAT)
    assert missing in receipt.missing
    assert getattr(receipt, {"date": "paid_at"}.get(missing, missing)) is None


def test_missing_quantity_also_loses_the_unit() -> None:
    receipt = parse_text(
        RECEIPT_TEXT.replace("· Quantity 23.00 L\n", ""), tz=BERLIN, date_format=FORMAT
    )
    assert set(receipt.missing) == {"quantity", "unit"}


def test_a_changed_layout_reports_everything_missing() -> None:
    receipt = parse_text("Some other receipt\nAmount due 12.00", tz=BERLIN, date_format=FORMAT)
    assert "total" in receipt.missing and "transaction_id" in receipt.missing


def test_other_units_and_currencies_are_read_as_printed() -> None:
    text = RECEIPT_TEXT.replace("23.00 L", "6.08 gal").replace("EUR", "USD")
    receipt = parse_text(text, tz=BERLIN, date_format=FORMAT)
    assert receipt.unit == "gal" and receipt.currency == "USD"
    assert not units_match(receipt.unit, "L") and units_match(receipt.unit, "gal")
    assert not currencies_match(receipt.currency, "EUR")


def test_price_that_does_not_fit_the_total_is_a_warning() -> None:
    receipt = parse_text(
        RECEIPT_TEXT.replace("1.789 EUR", "1.999 EUR"), tz=BERLIN, date_format=FORMAT
    )
    assert receipt.warnings == [TOTAL_MISMATCH] and receipt.missing == []


def test_multi_word_fuel_type_and_station() -> None:
    text = RECEIPT_TEXT.replace("Super 41.15", "Super Plus E10 41.15").replace(
        "TESTOIL", "Fuel Station Number One"
    )
    receipt = parse_text(text, tz=BERLIN, date_format=FORMAT)
    assert receipt.fuel_type == "Super Plus E10"
    assert receipt.station == "Fuel Station Number One"
    assert receipt.address == "Fuel Station Number One, Musterstrasse 12, 12345 Musterstadt"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("54.03", "54.03"),
        ("54,03", "54.03"),
        ("1,054.03", "1054.03"),
        ("1.054,03", "1054.03"),
        ("2,349", "2.349"),
        ("23", "23"),
    ],
)
def test_parse_decimal(text: str, value: str) -> None:
    assert parse_decimal(text) == Decimal(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("D:20260923150840+00'00'", datetime(2026, 9, 23, 15, 8, 40, tzinfo=UTC)),
        ("D:20260923170840+02'00'", datetime(2026, 9, 23, 15, 8, 40, tzinfo=UTC)),
        ("D:20260923100840-05'00'", datetime(2026, 9, 23, 15, 8, 40, tzinfo=UTC)),
        ("D:20260923150840Z", datetime(2026, 9, 23, 15, 8, 40, tzinfo=UTC)),
        ("D:20260923150840", datetime(2026, 9, 23, 15, 8, 40, tzinfo=UTC)),
        ("nonsense", None),
        (None, None),
    ],
)
def test_pdf_creation_date(raw: str | None, expected: datetime | None) -> None:
    assert pdf_creation_date(raw) == expected


def test_not_a_pdf() -> None:
    with pytest.raises(ReceiptParseError):
        parse_receipt(b"definitely not a pdf", tz=BERLIN, date_format=FORMAT)


def test_a_pdf_that_is_not_a_receipt() -> None:
    # A minimal PDF with other text
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF"
    )
    with pytest.raises(ReceiptParseError):
        parse_receipt(pdf, tz=BERLIN, date_format=FORMAT)
