from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import Receipt, ReceiptState
from app.services.lubelogger import GasRecord
from app.services.receipts import pending_email_moves
from app.services.record_matching import candidates, updated_record
from tests.conftest import AppUnderTest
from tests.helpers import create_user, sign_in_admin, sign_in_as
from tests.test_receipts import insert_receipt
from tests.test_settings_cleanup import run_cleanup

BERLIN = ZoneInfo("Europe/Berlin")
TRANSACTION_ID = "00000000-1111-4222-8333-444444444444"
MARKER = f"PaceDrive Transaction ID: {TRANSACTION_ID}"


def record(record_id: int, day: str, *, quantity="23.00", cost="41.15", notes="") -> GasRecord:
    return GasRecord(
        id=record_id,
        vehicle_id=1,
        date=date.fromisoformat(day),
        odometer=1000 + record_id,
        fuel_consumed=Decimal(quantity),
        cost=Decimal(cost),
        notes=notes,
    )


def fixture_receipt() -> Receipt:
    # The receipt of the fixture: 2026-09-23, 17:08 in Berlin, 23.00 L, 41.15 EUR
    return Receipt(
        message_id="<x>",
        paid_at=datetime(2026, 9, 23, 15, 8, tzinfo=UTC),
        quantity=Decimal("23.00"),
        unit="L",
        total=Decimal("41.15"),
        currency="EUR",
    )


def find(records: list[GasRecord], **kwargs):
    defaults = {"tz": BERLIN, "volume_unit": "L", "currency": "EUR"}
    return candidates(fixture_receipt(), records, **{**defaults, **kwargs})


def test_records_are_offered_by_date_and_never_before_the_receipt() -> None:
    records = [
        record(1, "2026-09-22"),  # the day before: the receipt is always older than the record
        record(2, "2026-09-25"),
        record(3, "2026-09-23"),  # the day of the receipt
        record(4, "2026-12-01", quantity="23", cost="41.15"),  # far too late
    ]
    assert [(c.record.id, c.days_after) for c in find(records)] == [(3, 0), (2, 2)]


def test_the_amount_and_the_price_have_to_fit() -> None:
    records = [
        record(1, "2026-09-23", quantity="22.5"),  # another amount
        record(2, "2026-09-24", cost="40.00"),  # another price
        record(3, "2026-09-25", quantity="23.004", cost="41.154"),  # rounding is fine
    ]
    assert [c.record.id for c in find(records)] == [3]
    # On request the others are shown too, still the closest day first, and say what differs
    everything = find(records, include_other_amounts=True)
    assert [c.record.id for c in everything] == [1, 2, 3]
    assert [(c.amount_matches, c.price_matches) for c in everything] == [
        (False, True),
        (True, False),
        (True, True),
    ]


def test_on_the_same_day_the_better_fit_comes_first() -> None:
    records = [record(1, "2026-09-24", quantity="20"), record(2, "2026-09-24")]
    assert [c.record.id for c in find(records, include_other_amounts=True)] == [2, 1]


def test_the_receipts_own_day_is_the_one_in_the_configured_time_zone() -> None:
    receipt = fixture_receipt()
    receipt.paid_at = datetime(2026, 9, 23, 22, 30, tzinfo=UTC)  # already the 24th in Berlin
    records = [record(1, "2026-09-23"), record(2, "2026-09-24")]
    found = candidates(receipt, records, tz=BERLIN, volume_unit="L", currency="EUR")
    assert [(c.record.id, c.days_after) for c in found] == [(2, 0)]


def test_records_that_have_a_receipt_are_not_offered() -> None:
    records = [record(1, "2026-09-24", notes="Fuel type: Super\nPaceDrive Transaction ID: abc")]
    assert find(records, include_other_amounts=True) == []


def test_other_units_or_currencies_never_match_on_the_amount() -> None:
    receipt = fixture_receipt()
    receipt.unit = "gal"
    records = [record(1, "2026-09-24")]
    assert candidates(receipt, records, tz=BERLIN, volume_unit="L", currency="EUR") == []
    shown = candidates(
        receipt, records, tz=BERLIN, volume_unit="L", currency="EUR", include_other_amounts=True
    )
    assert [(c.amount_matches, c.price_matches) for c in shown] == [(False, True)]


def test_updating_keeps_what_the_record_holds() -> None:
    existing = record(1, "2026-09-24", notes="Shell, paid cash")
    existing.raw = {
        "id": 1,
        "vehicleId": 1,
        "date": "2026-09-24",
        "odometer": 1001,
        "fuelConsumed": 23,
        "cost": 41.15,
        "isFillToFull": True,
        "missedFuelUp": False,
        "tags": "trip work",
        "startingSoc": 0,
        "endingSoc": 0,
        "notes": "Shell, paid cash",
        "extraFields": [
            {"name": "GPS Location", "value": "52.1,8.8", "fieldType": 5, "isRequired": False}
        ],
        "files": [{"name": "old.jpg", "location": "/documents/old.jpg"}],
    }
    existing.files = []  # (parsed separately in real use)
    receipt = fixture_receipt()
    receipt.transaction_id = TRANSACTION_ID
    receipt.address = "TESTOIL, Musterstrasse 12, 12345 Musterstadt"
    receipt.fuel_type = "Super"
    from app.services.lubelogger import UploadedFile

    body = updated_record(
        existing,
        receipt,
        UploadedFile("receipt.pdf", "/documents/r.pdf"),
        address_field="Address",
        linked_by="pidi",
    )
    assert body["notes"] == (
        "Shell, paid cash\n\n\nFuel type: Super\n"
        f"Payment: Pace Drive email receipt\nCreated by: pidi\n{MARKER}"
    )
    assert body["tags"] == "trip work" and body["id"] == 1 and body["startingSoc"] == 0
    assert body["files"][-1] == {"name": "receipt.pdf", "location": "/documents/r.pdf"}
    names = {f["name"]: f for f in body["extraFields"]}
    assert names["GPS Location"]["value"] == "52.1,8.8"  # untouched
    assert names["Address"]["value"] == "TESTOIL, Musterstrasse 12, 12345 Musterstadt"


# --- through the API ---


async def seed(api: AppUnderTest) -> int:
    """A receipt without a fuel-up, and fuel records entered by hand."""
    receipt_id = await insert_receipt(api, transaction_id=TRANSACTION_ID, pdf_file="1.pdf")
    api.services.receipts.receipt_dir.mkdir(parents=True, exist_ok=True)
    (api.services.receipts.receipt_dir / "1.pdf").write_bytes(b"%PDF-1.4 receipt")
    api.fake.add_record(1, date="2026-09-21", odometer=900, fuelConsumed=23, cost=41.15)  # before
    api.fake.add_record(
        1,
        date="2026-09-25",
        odometer=1000,
        fuelConsumed=23,
        cost=41.15,
        notes="paid cash",
        tags="work",
        extraFields=[{"name": "Address", "value": "", "fieldType": 0, "isRequired": False}],
    )
    api.fake.add_record(2, date="2026-09-24", odometer=500, fuelConsumed=23, cost=41.15)
    api.fake.add_record(1, date="2026-09-26", odometer=1100, fuelConsumed=30, cost=55)
    return receipt_id


async def test_the_candidates_are_listed_closest_first(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    receipt_id = await seed(api)
    found = (await api.client.get(f"/api/receipts/{receipt_id}/record-candidates")).json()
    assert [(c["record_id"], c["days_after"]) for c in found] == [(3, 1), (2, 2)]
    assert found[0]["vehicle_name"] == "2018 Opel Corsa" and found[0]["odometer"] == 500
    assert found[1]["notes"] == "paid cash"
    assert all(c["amount_matches"] and c["price_matches"] for c in found)

    everything = (
        await api.client.get(
            f"/api/receipts/{receipt_id}/record-candidates?include_other_amounts=true"
        )
    ).json()
    assert [c["record_id"] for c in everything] == [3, 2, 4]
    assert (await api.client.get(f"/api/receipts/{receipt_id}/record-candidates?days=1")).json()[0][
        "record_id"
    ] == 3


async def test_only_records_of_the_users_vehicles_are_offered(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    receipt_id = await seed(api)
    await create_user(api, "bob", [1])
    await sign_in_as(api, "bob")
    found = (await api.client.get(f"/api/receipts/{receipt_id}/record-candidates")).json()
    assert [c["record_id"] for c in found] == [2]
    link = {"vehicle_id": 2, "record_id": 3}
    assert (
        await api.client.post(f"/api/receipts/{receipt_id}/link-record", json=link)
    ).status_code == 404


async def test_linking_attaches_the_receipt_to_the_record(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    receipt_id = await seed(api)
    response = await api.client.post(
        f"/api/receipts/{receipt_id}/link-record", json={"vehicle_id": 1, "record_id": 2}
    )
    assert response.status_code == 200, response.text
    out = response.json()
    assert (
        out["state"] == "matched" and out["linked_record_id"] == 2 and out["linked_vehicle_id"] == 1
    )

    updated = next(r for r in api.fake.records if r["id"] == 2)
    assert updated["notes"].startswith("paid cash\n\n\nFuel type: ")
    assert updated["notes"].endswith(
        f"Payment: Pace Drive email receipt\nCreated by: alice\n{MARKER}"
    )
    assert [f["name"] for f in updated["files"]] == ["receipt.pdf"]
    assert updated["files"][0]["location"] in api.fake.uploads
    assert updated["extraFields"][0]["value"].startswith("TESTOIL")
    assert updated["tags"] == "work" and updated["odometer"] == 1000  # the rest is kept
    assert updated["date"] == "2026-09-25" and updated["cost"] == 41.15

    # The email can leave the inbox now
    async with api.services.sessionmaker() as session:
        assert await pending_email_moves(session) == {receipt_id}
    # It isn't offered again, and can't be used a second time
    assert (
        await api.client.get(f"/api/receipts/{receipt_id}/record-candidates")
    ).status_code == 409
    again = await api.client.post(
        f"/api/receipts/{receipt_id}/link-record", json={"vehicle_id": 2, "record_id": 3}
    )
    assert again.status_code == 409


async def test_a_receipt_that_is_in_lubelogger_already_is_not_linked_again(
    api: AppUnderTest,
) -> None:
    await sign_in_admin(api)
    receipt_id = await seed(api)
    api.fake.add_record(
        1, date="2026-09-23", odometer=950, fuelConsumed=23, cost=41.15, notes=MARKER
    )
    response = await api.client.post(
        f"/api/receipts/{receipt_id}/link-record", json={"vehicle_id": 1, "record_id": 2}
    )
    assert response.status_code == 409 and "already in LubeLogger" in response.json()["detail"]
    # A record that has a receipt of its own can't take another
    record_with_receipt = len(api.fake.records)
    other = await insert_receipt(api, transaction_id="another")
    response = await api.client.post(
        f"/api/receipts/{other}/link-record",
        json={"vehicle_id": 1, "record_id": record_with_receipt},
    )
    assert response.status_code == 409 and "already has" in response.json()["detail"]


async def test_a_failing_update_leaves_the_receipt_alone(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    receipt_id = await seed(api)
    api.fake.fail_update = [400]
    link = {"vehicle_id": 1, "record_id": 2}
    assert (
        await api.client.post(f"/api/receipts/{receipt_id}/link-record", json=link)
    ).status_code == 502
    api.fake.down = True
    assert (
        await api.client.post(f"/api/receipts/{receipt_id}/link-record", json=link)
    ).status_code == 503
    api.fake.down = False
    async with api.services.sessionmaker() as session:
        receipt = (await session.execute(select(Receipt))).scalar_one()
    assert receipt.state == ReceiptState.UNMATCHED and receipt.linked_record_id is None
    assert (
        await api.client.post(f"/api/receipts/{receipt_id}/link-record", json=link)
    ).status_code == 200


async def test_receipts_that_cant_be_used_are_refused(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    unreadable = await insert_receipt(api, paid_at=None, parse_error="no")
    assert (
        await api.client.get(f"/api/receipts/{unreadable}/record-candidates")
    ).status_code == 422
    ignored = await insert_receipt(api, state=ReceiptState.IGNORED)
    assert (await api.client.get(f"/api/receipts/{ignored}/record-candidates")).status_code == 409


async def test_linked_receipts_are_cleaned_up_after_the_grace_period(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    receipt_id = await seed(api)
    await api.client.post(
        f"/api/receipts/{receipt_id}/link-record", json={"vehicle_id": 1, "record_id": 2}
    )
    assert (await run_cleanup(api))["receipts"] == 0  # just linked
    later = datetime.now(UTC) + timedelta(days=8)
    assert (await run_cleanup(api, later))["receipts"] == 1
    assert not (api.services.receipts.receipt_dir / "1.pdf").exists()
