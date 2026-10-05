import asyncio
import json

from app.api.events import event_stream
from app.services.events import EventBus
from tests.conftest import AppUnderTest
from tests.helpers import create_fuel_up, create_user, get_fuel_up, sign_in_admin, sign_in_as
from tests.test_receipts import receipt_fuel_up


async def next_chunk(stream, seconds: float = 2) -> str:
    async with asyncio.timeout(seconds):
        return await anext(stream)


async def test_event_stream_sends_events_and_heartbeats() -> None:
    bus = EventBus()
    stream = event_stream(bus, heartbeat=0.05)
    assert await next_chunk(stream) == "retry: 3000\n\n"
    assert (await next_chunk(stream)).startswith("event: hello")
    assert bus.subscriber_count == 1

    bus.publish("fuel_up", id=7)
    chunk = await next_chunk(stream)
    assert chunk == 'event: fuel_up\ndata: {"id": 7}\n\n'
    assert json.loads(chunk.split("data: ")[1]) == {"id": 7}

    assert await next_chunk(stream) == ": ping\n\n"  # nothing happened for a while
    await stream.aclose()
    assert bus.subscriber_count == 0


async def test_events_reach_every_subscriber() -> None:
    bus = EventBus()
    one, two = event_stream(bus), event_stream(bus)
    for stream in (one, two):
        await next_chunk(stream)
        await next_chunk(stream)
    bus.publish("notification", id=1)
    assert "notification" in await next_chunk(one)
    assert "notification" in await next_chunk(two)
    await one.aclose()
    await two.aclose()


async def test_a_slow_client_is_dropped_instead_of_blocking() -> None:
    bus = EventBus()
    stream = event_stream(bus)
    await next_chunk(stream)
    await next_chunk(stream)
    for i in range(500):
        bus.publish("fuel_up", id=i)
    assert bus.subscriber_count == 0
    await stream.aclose()


async def test_events_endpoint_needs_login(api: AppUnderTest) -> None:
    assert (await api.client.get("/api/events")).status_code == 401


async def test_changes_publish_events(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    with api.services.events.subscribe() as queue:
        created = await create_fuel_up(api)
        assert (await asyncio.wait_for(queue.get(), 1)).data == {"id": created["id"]}
        await api.services.processor.process_due()
        event = await asyncio.wait_for(queue.get(), 1)
        assert event.type == "fuel_up" and event.data == {"id": created["id"]}

        api.fake.fail_add = [400]
        second = await create_fuel_up(api, odometer=20000)
        await api.services.processor.process_due()
        types = []
        while not queue.empty():
            types.append(queue.get_nowait().type)
        assert "notification" in types and second["id"]


async def test_history_comes_from_lubelogger(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.fake.add_record(
        1,
        date="2026-09-01",
        odometer=1000,
        fuelConsumed=30,
        cost=50.5,
        notes="old",
        files=[{"name": "r.pdf", "location": "/documents/x.pdf", "isPending": False}],
    )
    api.fake.add_record(
        1,
        date="2026-09-20",
        odometer=1500,
        fuelConsumed=31.25,
        cost=60,
        extraFields=[
            {"name": "GPS Location", "value": "52.2,8.8", "isRequired": False, "fieldType": 5},
            {"name": "Address", "value": "TESTOIL, Main St 1", "isRequired": False, "fieldType": 0},
        ],
    )
    api.fake.add_record(2, date="2026-09-10", odometer=300, fuelConsumed=20, cost=30)

    history = (await api.client.get("/api/history")).json()
    assert history["total"] == 3
    assert [i["odometer"] for i in history["items"]] == [1500, 300, 1000]  # newest first
    newest = history["items"][0]
    assert newest["vehicle_name"] == "2020 VW Golf"
    assert newest["gps"] == "52.2,8.8" and newest["address"] == "TESTOIL, Main St 1"
    assert newest["fuel_consumed"] == "31.25" and newest["cost"] == "60"
    assert history["items"][2]["files"] == [{"name": "r.pdf", "location": "/documents/x.pdf"}]

    api.fake.uploads["/documents/x.pdf"] = b"%PDF-1.4 receipt"
    shown = await api.client.get(f"/api/history/1/{history['items'][2]['id']}/files/0")
    assert shown.status_code == 200 and shown.content == b"%PDF-1.4 receipt"
    assert shown.headers["content-type"] == "application/pdf"
    assert shown.headers["content-disposition"].startswith("inline")
    assert (await api.client.get(f"/api/history/1/{newest['id']}/files/0")).status_code == 404
    assert (await api.client.get("/api/history/1/9999/files/0")).status_code == 404

    one = (await api.client.get("/api/history?vehicle_id=2")).json()
    assert [i["vehicle_id"] for i in one["items"]] == [2]
    page = (await api.client.get("/api/history?limit=1&offset=1")).json()
    assert page["total"] == 3 and [i["odometer"] for i in page["items"]] == [300]
    assert (await api.client.get("/api/history?vehicle_id=99")).status_code == 404
    assert (await api.client.get("/api/history?limit=0")).status_code == 422


async def test_history_respects_vehicle_access(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await create_user(api, "bob", [1])
    api.fake.add_record(1, date="2026-09-01", odometer=1000, fuelConsumed=30, cost=50)
    api.fake.add_record(2, date="2026-09-02", odometer=2000, fuelConsumed=30, cost=50)
    await sign_in_as(api, "bob")
    history = (await api.client.get("/api/history")).json()
    assert [i["vehicle_id"] for i in history["items"]] == [1]
    assert (await api.client.get("/api/history?vehicle_id=2")).status_code == 404
    assert (await api.client.get("/api/history/2/1/files/0")).status_code == 404


async def test_history_when_lubelogger_is_down(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.client.get("/api/vehicles")  # warms the vehicle cache
    api.fake.down = True
    response = await api.client.get("/api/history")
    assert response.status_code == 503 and "can't be reached" in response.json()["detail"]


async def test_switching_a_waiting_fuel_up_to_manual(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    edited = await api.client.patch(
        f"/api/fuel-ups/{created['id']}",
        json={
            "payment_source": "manual",
            "fuel_type": "Diesel",
            "quantity": "30",
            "total_price": "45.5",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["waiting_for_receipt"] is False and edited.json()["sending"] is True
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    assert api.fake.records[0]["notes"].startswith("Fuel type: Diesel\nPayment: Manual")


async def test_saving_an_unchanged_form_changes_nothing(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    before = await get_fuel_up(api, created["id"])
    # What the web UI sends when a waiting fuel-up is saved without changes
    same = await api.client.patch(
        f"/api/fuel-ups/{created['id']}",
        json={
            "vehicle_id": 1,
            "odometer": 12345,
            "fuel_up_time": before["fuel_up_time"],
            "is_fill_to_full": True,
            "missed_fuel_up": False,
            "payment_source": "email_receipt",
        },
    )
    assert same.status_code == 200, same.text
    after = same.json()
    assert after["receipt_deadline"] == before["receipt_deadline"]  # the wait wasn't restarted
    assert after["waiting_for_receipt"] is True


async def test_an_unchanged_odometer_is_not_checked_again(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.services.runtime.set("review_before_send", True)
    created = await create_fuel_up(api, odometer=15000)
    api.fake.other_odometer[1] = 16000  # LubeLogger moved on
    same = await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"odometer": 15000})
    assert same.status_code == 200
    changed = await api.client.patch(f"/api/fuel-ups/{created['id']}", json={"odometer": 15001})
    assert changed.status_code == 422
