import asyncio

import pytest

from tests.conftest import AppUnderTest
from tests.helpers import (
    create_fuel_up,
    create_user,
    fuel_up_body,
    get_fuel_up,
    sign_in_admin,
    sign_in_as,
)


async def test_manual_fuel_up_is_written_to_lubelogger(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(
        api, latitude=52.2063, longitude=8.8024, is_fill_to_full=False, missed_fuel_up=True
    )
    assert created["status"] == "pending" and created["sending"] is True
    assert created["quantity"] == "23.000" and created["total_price"] == "54.03"
    assert created["created_by"] == "alice"
    assert api.fake.records == []  # the sending happens in the background

    assert await api.services.processor.process_due() == 1

    done = await get_fuel_up(api, created["id"])
    assert done["status"] == "done" and done["lubelogger_record_id"] == 1
    (record,) = api.fake.records
    assert record["vehicleId"] == 1
    assert record["date"] == "2026-09-23"
    assert record["odometer"] == 12345
    assert record["fuelConsumed"] == 23.0 and record["cost"] == 54.03
    assert record["isFillToFull"] is False and record["missedFuelUp"] is True
    assert record["notes"] == "Fuel type: Super\nPayment: Manual\nCreated by: alice"
    assert record["extraFields"] == [
        {"name": "GPS Location", "value": "52.206300,8.802400", "isRequired": False, "fieldType": 5}
    ]
    assert record["files"] == []


async def test_date_in_lubelogger_uses_the_configured_time_zone(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    # 23:30 UTC on Sept 23 is already Sept 24 in Berlin (UTC+2)
    await create_fuel_up(api, fuel_up_time="2026-09-23T23:30:00Z")
    await api.services.processor.process_due()
    assert api.fake.records[0]["date"] == "2026-09-24"


async def test_time_without_zone_is_read_in_the_configured_zone(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api, fuel_up_time="2026-09-23T17:08:00")
    assert created["fuel_up_time"].startswith("2026-09-23T15:08:00")  # Berlin is UTC+2


async def test_time_defaults_to_now(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    body = fuel_up_body()
    del body["fuel_up_time"]
    response = await api.client.post("/api/fuel-ups", json=body)
    assert response.status_code == 201


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"vehicle_id": 99}, "Unknown vehicle"),
        ({"fuel_type": None}, "Choose a fuel type"),
        ({"fuel_type": "Kerosene"}, "Unknown fuel type"),
        ({"quantity": None}, "fuel amount"),
        ({"quantity": "0"}, "fuel amount"),
        ({"total_price": None}, "total price"),
    ],
)
async def test_validation(api: AppUnderTest, overrides: dict, message: str) -> None:
    await sign_in_admin(api)
    response = await api.client.post("/api/fuel-ups", json=fuel_up_body(**overrides))
    assert response.status_code == 422
    assert message in str(response.json()["detail"])


async def test_request_validation(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    for overrides in ({"odometer": -5}, {"latitude": 52.0}, {"longitude": 8.0, "latitude": 95}):
        response = await api.client.post("/api/fuel-ups", json=fuel_up_body(**overrides))
        assert response.status_code == 422, overrides


async def test_odometer_must_not_be_lower_than_lubelogger(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.fake.add_record(1, date="2026-09-01", odometer=20000, fuelConsumed=40, cost=70)
    response = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=19999))
    assert response.status_code == 422
    assert "19999" in response.json()["detail"] and "20000" in response.json()["detail"]
    # Equal is fine
    assert (
        await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=20000))
    ).status_code == 201


async def test_odometer_must_not_be_lower_than_a_fuel_up_still_in_progress(
    api: AppUnderTest,
) -> None:
    await sign_in_admin(api)
    first = await create_fuel_up(api, odometer=15000)
    response = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=14000))
    assert response.status_code == 422
    assert f"#{first['id']}" in response.json()["detail"]
    # Another vehicle is unaffected
    assert (
        await api.client.post("/api/fuel-ups", json=fuel_up_body(vehicle_id=2, odometer=100))
    ).status_code == 201


async def test_creating_works_while_lubelogger_is_down_using_the_known_vehicles(
    api: AppUnderTest,
) -> None:
    await sign_in_admin(api)
    assert (await api.client.get("/api/vehicles")).status_code == 200  # fills the cache
    api.fake.down = True
    created = await create_fuel_up(api)
    assert "wasn't checked" in created["warnings"][0]
    # ... and is sent once LubeLogger is back
    api.fake.down = False
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"


async def test_creating_fails_clearly_when_lubelogger_was_never_reachable(
    api: AppUnderTest,
) -> None:
    await sign_in_admin(api)
    api.fake.down = True
    response = await api.client.post("/api/fuel-ups", json=fuel_up_body())
    assert response.status_code == 503
    assert "can't be reached" in response.json()["detail"]


async def test_review_before_send(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.services.runtime.set("review_before_send", True)
    created = await create_fuel_up(api)
    assert created["status"] == "needs_attention" and created["attention"] == "review"
    assert "Review before sending" in created["attention_message"]
    assert await api.services.processor.process_due() == 0
    assert api.fake.records == []

    # Correct a mistake during the review, then approve
    edited = await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"total_price": "50.10"})
    assert edited.status_code == 200 and edited.json()["total_price"] == "50.10"
    approved = await api.client.post(f"/api/fuel-ups/{created['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "pending"
    await api.services.processor.process_due()

    assert api.fake.records[0]["cost"] == 50.1
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"


async def test_approve_only_when_needing_attention(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api)
    response = await api.client.post(f"/api/fuel-ups/{created['id']}/approve")
    assert response.status_code == 409


async def test_lubelogger_unreachable_is_retried_then_fails(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.services.processor._retry_delays = (0, 0)
    created = await create_fuel_up(api)
    api.fake.down = True

    await api.services.processor.process_due()
    state = await get_fuel_up(api, created["id"])
    assert state["status"] == "pending" and "Trying again" in state["error_message"]
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "pending"
    await api.services.processor.process_due()  # the third failed try: out of retries

    failed = await get_fuel_up(api, created["id"])
    assert failed["status"] == "failed"
    assert "unreachable after 3 tries" in failed["error_message"]
    notifications = (await api.client.get("/api/notifications")).json()
    assert notifications[0]["level"] == "error" and notifications[0]["fuel_up_id"] == created["id"]

    # Back up: retry works
    api.fake.down = False
    retried = await api.client.post(f"/api/fuel-ups/{created['id']}/retry")
    assert retried.json()["status"] == "pending"
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    assert len(api.fake.records) == 1


async def test_retry_waits_between_tries(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api)
    api.fake.down = True
    await api.services.processor.process_due()
    # The next try is in a minute, so nothing is due now
    assert await api.services.processor.process_due() == 0
    assert (await get_fuel_up(api, created["id"]))["status"] == "pending"


async def test_rejection_fails_at_once(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api)
    api.fake.fail_add = [400]
    await api.services.processor.process_due()
    failed = await get_fuel_up(api, created["id"])
    assert failed["status"] == "failed" and "rejected" in failed["error_message"].lower()


async def test_wrong_api_key_fails_with_a_hint(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api)
    api.fake.api_key = "secret"
    await api.services.processor.process_due()
    failed = await get_fuel_up(api, created["id"])
    assert failed["status"] == "failed" and "LUBELOGGER_API_KEY" in failed["error_message"]


async def test_a_record_created_by_an_earlier_try_is_not_duplicated(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.services.processor._retry_delays = (0,)
    created = await create_fuel_up(api)
    api.fake.add_then_lose_response = 1
    await api.services.processor.process_due()  # created, but the answer got lost
    assert len(api.fake.records) == 1
    await api.services.processor.process_due()  # the second try finds it
    assert len(api.fake.records) == 1
    done = await get_fuel_up(api, created["id"])
    assert done["status"] == "done" and done["lubelogger_record_id"] == 1


async def test_odometer_is_checked_again_when_sending(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api, odometer=15000)
    # Someone logs a higher reading in LubeLogger in the meantime
    api.fake.other_odometer[1] = 16000
    await api.services.processor.process_due()
    failed = await get_fuel_up(api, created["id"])
    assert failed["status"] == "failed" and "16000" in failed["error_message"]

    # Fix the reading and retry
    assert (
        await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"odometer": 16050})
    ).status_code == 200
    await api.client.post(f"/api/fuel-ups/{created['id']}/retry")
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    assert api.fake.records[0]["odometer"] == 16050


async def test_edit_rules(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api)
    await api.services.processor.process_due()
    # Done fuel-ups are final
    response = await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"odometer": 12400})
    assert response.status_code == 409

    await api.services.runtime.set("review_before_send", True)
    other = await create_fuel_up(api, odometer=13000)
    bad = await api.client.patch(f"/api/fuel-ups/{other['id']}", json={"odometer": 100})
    assert bad.status_code == 422
    bad = await api.client.patch(f"/api/fuel-ups/{other['id']}", json={"fuel_type": "Kerosene"})
    assert bad.status_code == 422
    # A rejected edit changes nothing
    assert (await get_fuel_up(api, other["id"]))["odometer"] == 13000
    moved = await api.client.patch(f"/api/fuel-ups/{other['id']}", json={"vehicle_id": 2})
    assert moved.status_code == 200 and moved.json()["vehicle_name"] == "2018 Opel Corsa"


async def test_users_only_see_their_vehicles(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await create_user(api, "bob", [1])
    mine = await create_fuel_up(api, vehicle_id=1)
    theirs = await create_fuel_up(api, vehicle_id=2)

    await sign_in_as(api, "bob")
    vehicles = (await api.client.get("/api/vehicles")).json()
    assert [v["id"] for v in vehicles] == [1]
    assert vehicles[0] == {"id": 1, "name": "2020 VW Golf", "identifier": "TEST-1"}
    listed = (await api.client.get("/api/fuel-ups")).json()
    assert [f["id"] for f in listed] == [mine["id"]]
    assert (await api.client.get(f"/api/fuel-ups/{theirs['id']}")).status_code == 404
    assert (await api.client.post(f"/api/fuel-ups/{theirs['id']}/retry")).status_code == 404
    response = await api.client.post("/api/fuel-ups", json=fuel_up_body(vehicle_id=2))
    assert response.status_code == 422

    await sign_in_as(api, "alice")
    assert len((await api.client.get("/api/fuel-ups")).json()) == 2


async def test_vehicle_identifier_can_be_an_extra_field(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    vehicles = (await api.client.get("/api/vehicles")).json()
    assert vehicles[1]["identifier"] == "Red one"


async def test_odometer_hint_and_reference_data(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.fake.add_record(1, date="2026-09-01", odometer=20000, fuelConsumed=40, cost=70)
    assert (await api.client.get("/api/vehicles/1/odometer")).json() == {
        "odometer": 20000,
        "saved": False,
        "saved_minutes_ago": None,
    }
    api.fake.down = True
    assert (await api.client.get("/api/vehicles/1/odometer")).json() == {
        "odometer": 20000,  # the reading seen a moment ago, as LubeLogger can't be reached
        "saved": True,
        "saved_minutes_ago": 0,
    }

    reference = (await api.client.get("/api/fuel-types")).json()
    assert reference["fuel_types"] == ["Diesel", "Super", "Super Plus", "Super E10"]
    assert reference["volume_unit"] == "L" and reference["currency"] == "EUR"


async def test_requires_login(api: AppUnderTest) -> None:
    for path in ("/api/fuel-ups", "/api/vehicles", "/api/fuel-types", "/api/notifications"):
        assert (await api.client.get(path)).status_code == 401, path
    assert (await api.client.post("/api/fuel-ups", json=fuel_up_body())).status_code == 401


async def test_shortcut_creates_a_fuel_up_with_an_api_token(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    token = (await api.client.post("/api/tokens", json={"name": "Shortcut"})).json()["token"]
    await api.client.post("/api/auth/logout")

    response = await api.client.post(
        "/api/fuel-ups",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "vehicle_id": 1,
            "odometer": 12345,
            "latitude": 52.2,
            "longitude": 8.8,
            "fuel_type": "Diesel",
            "quantity": 41.5,
            "total_price": 72.3,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["quantity"] == "41.500"


async def test_notifications_are_private_and_can_be_marked_read(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await create_user(api, "bob", [1])
    created = await create_fuel_up(api)
    api.fake.fail_add = [400]
    await api.services.processor.process_due()

    unread = (await api.client.get("/api/notifications?unread=true")).json()
    assert len(unread) == 1 and unread[0]["fuel_up_id"] == created["id"]
    await api.client.post("/api/notifications/read", json={"ids": [unread[0]["id"]]})
    assert (await api.client.get("/api/notifications?unread=true")).json() == []
    assert len((await api.client.get("/api/notifications")).json()) == 1

    await sign_in_as(api, "bob")
    assert (await api.client.get("/api/notifications")).json() == []


async def test_status_endpoint(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    assert (await api.client.get("/api/status")).json()["lubelogger"]["state"] == "ok"
    api.fake.extra_fields = {"GasRecord": ["Address"]}
    status = (await api.client.get("/api/status")).json()["lubelogger"]
    assert status["state"] == "error" and "GPS Location" in status["message"]
    api.fake.down = True
    assert (await api.client.get("/api/status")).json()["lubelogger"]["state"] == "error"


# --- deleting ---


async def waiting_for_receipt(api: AppUnderTest, **overrides) -> dict:
    """A fuel-up that waits for its receipt email (so it isn't being sent)."""
    created = await create_fuel_up(
        api,
        payment_source="email_receipt",
        fuel_type=None,
        quantity=None,
        total_price=None,
        **overrides,
    )
    assert created["waiting_for_receipt"] is True
    return created


async def test_a_fuel_up_that_waits_can_be_deleted(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await waiting_for_receipt(api)
    assert created["deletable"] is True
    assert (await api.client.delete(f"/api/fuel-ups/{created['id']}")).status_code == 204
    assert (await api.client.get(f"/api/fuel-ups/{created['id']}")).status_code == 404
    assert (await api.client.get("/api/fuel-ups")).json() == []
    assert (await api.client.delete(f"/api/fuel-ups/{created['id']}")).status_code == 404


async def test_a_fuel_up_waiting_for_review_or_failed_can_be_deleted(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.services.runtime.set("review_before_send", True)
    review = await create_fuel_up(api)
    assert review["status"] == "needs_attention" and review["deletable"] is True
    assert (await api.client.delete(f"/api/fuel-ups/{review['id']}")).status_code == 204

    await api.services.runtime.set("review_before_send", False)
    api.fake.fail_add = [400]
    failed = await create_fuel_up(api, odometer=20000)
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, failed["id"]))["status"] == "failed"
    assert (await get_fuel_up(api, failed["id"]))["deletable"] is True
    assert (await api.client.delete(f"/api/fuel-ups/{failed['id']}")).status_code == 204
    assert api.fake.records == []  # nothing was ever written to LubeLogger


async def test_a_fuel_up_in_lubelogger_cannot_be_deleted(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api)
    await api.services.processor.process_due()
    done = await get_fuel_up(api, created["id"])
    assert done["status"] == "done" and done["deletable"] is False
    response = await api.client.delete(f"/api/fuel-ups/{created['id']}")
    assert response.status_code == 409 and "in LubeLogger" in response.json()["detail"]
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"


async def test_a_fuel_up_that_is_being_sent_cannot_be_deleted(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await create_fuel_up(api)  # sending, until the processor has run
    assert created["sending"] is True and created["deletable"] is False
    response = await api.client.delete(f"/api/fuel-ups/{created['id']}")
    assert response.status_code == 409 and "being sent" in response.json()["detail"]


async def test_deleting_lets_the_receipt_go_and_keeps_the_messages(api: AppUnderTest) -> None:
    from sqlalchemy import select

    from app.models import Notification, Receipt, ReceiptState
    from tests.fake_mailbox import FakeMailbox, build_email, receipt_pdf
    from tests.test_receipts import deliver, receipt_fuel_up

    await sign_in_admin(api)
    await api.services.runtime.set("review_before_send", True)
    created = await receipt_fuel_up(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    taken = await get_fuel_up(api, created["id"])
    assert taken["receipt_id"] is not None and taken["status"] == "needs_attention"

    assert (await api.client.delete(f"/api/fuel-ups/{created['id']}")).status_code == 204
    async with api.services.sessionmaker() as session:
        (receipt,) = (await session.execute(select(Receipt))).scalars()
        notes = list((await session.execute(select(Notification))).scalars())
    # The receipt is without a fuel-up again, and can be used for another one
    assert receipt.state == ReceiptState.UNMATCHED
    assert [r["id"] for r in (await api.client.get("/api/receipts")).json()] == [receipt.id]
    # The message about the fuel-up stays, without a link to it
    assert notes and all(n.fuel_up_id is None for n in notes)


async def test_deleting_needs_access_to_the_vehicle(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await waiting_for_receipt(api)
    await create_user(api, "bob", [2])
    await sign_in_as(api, "bob")
    assert (await api.client.delete(f"/api/fuel-ups/{created['id']}")).status_code == 404
    await sign_in_as(api, "alice")
    assert (await api.client.delete(f"/api/fuel-ups/{created['id']}")).status_code == 204


async def test_deleting_publishes_an_event(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await waiting_for_receipt(api)
    with api.services.events.subscribe() as queue:
        await api.client.delete(f"/api/fuel-ups/{created['id']}")
        event = await asyncio.wait_for(queue.get(), 1)
    assert event.type == "fuel_up" and event.data == {"id": created["id"]}
