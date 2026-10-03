"""Runs the client against a real LubeLogger. Skipped unless LUBELOGGER_TEST_URL is set.

Use a throwaway LubeLogger (it gets a test vehicle and fuel records), never your own one:

    LUBELOGGER_TEST_URL=http://localhost:18080 uv run pytest tests/test_lubelogger_integration.py
"""

import os
from datetime import date
from decimal import Decimal

import pytest

from app.services.lubelogger import (
    FIELD_TYPE_LOCATION,
    ExtraField,
    LubeLoggerClient,
    NewGasRecord,
)

URL = os.environ.get("LUBELOGGER_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="LUBELOGGER_TEST_URL is not set")


async def test_round_trip_against_a_real_lubelogger() -> None:
    client = LubeLoggerClient(URL or "", os.environ.get("LUBELOGGER_TEST_API_KEY", ""))
    try:
        await client.check()
        vehicles = await client.vehicles()
        assert vehicles, "create a vehicle in the test LubeLogger first"
        vehicle = vehicles[0]

        before = await client.latest_odometer(vehicle.id)
        uploaded = await client.upload_document("receipt.pdf", b"%PDF-1.4 test")
        odometer = before + 111
        record_id = await client.add_gas_record(
            vehicle.id,
            NewGasRecord(
                date=date(2026, 9, 23),
                odometer=odometer,
                fuel_consumed=Decimal("23.45"),
                cost=Decimal("54.03"),
                is_fill_to_full=False,
                notes="Fuel type: Super\nPayment: Manual",
                extra_fields=[
                    ExtraField("GPS Location", "52.206300,8.802400", FIELD_TYPE_LOCATION),
                    ExtraField("Address", "TESTOIL, Musterstrasse 1"),
                ],
                files=[uploaded],
            ),
        )
        assert record_id > 0
        assert await client.latest_odometer(vehicle.id) == odometer

        (record,) = [r for r in await client.gas_records(vehicle.id) if r.id == record_id]
        assert record.date == date(2026, 9, 23)
        assert record.fuel_consumed == Decimal("23.45") and record.cost == Decimal("54.03")
        assert record.is_fill_to_full is False
        assert record.notes == "Fuel type: Super\nPayment: Manual"
        assert {f.name: f.value for f in record.extra_fields}[
            "Address"
        ] == "TESTOIL, Musterstrasse 1"
        assert [f.name for f in record.files] == ["receipt.pdf"]
        assert any(r.id == record_id for r in await client.all_gas_records())
    finally:
        await client.aclose()
