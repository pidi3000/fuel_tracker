"""LubeLogger that doesn't answer must not hold everything up."""

from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

from app.core.config import Settings
from app.main import create_app
from app.services.lubelogger import LubeLoggerClient, LubeLoggerRejected, LubeLoggerUnavailable
from app.services.vehicles import OdometerReading, VehicleDirectory, age_text
from tests.conftest import AppUnderTest
from tests.fake_lubelogger import FakeLubeLogger
from tests.helpers import fuel_up_body, sign_in_admin


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Server:
    """What LubeLogger does next: not answer (a connect timeout), fail with 500 or work."""

    def __init__(self) -> None:
        self.mode = "down"
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "down":
            raise httpx.ConnectTimeout("no answer", request=request)
        if self.mode == "error":
            return httpx.Response(500, text="boom")
        if request.url.path.startswith("/documents/"):
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(200, json=[])


def make(server: Server, clock: Clock, **kwargs) -> LubeLoggerClient:
    return LubeLoggerClient(
        "http://lubelogger.test",
        clock=clock,
        transport=httpx.MockTransport(server.handler),
        **kwargs,
    )


def test_connecting_gets_a_few_seconds_and_an_answer_longer() -> None:
    timeout = make(Server(), Clock())._http.timeout
    assert timeout.connect == 3 and timeout.read == 20


async def test_after_a_failure_lubelogger_is_left_alone_for_a_while() -> None:
    server, clock = Server(), Clock()
    client = make(server, clock)
    with pytest.raises(LubeLoggerUnavailable, match="ConnectTimeout"):
        await client.vehicles()
    assert server.calls == 1

    # The next calls fail at once, without asking again
    for call in (client.vehicles(), client.latest_odometer(1), client.download("/documents/a.pdf")):
        with pytest.raises(LubeLoggerUnavailable, match="isn't tried again for 30 s"):
            await call
    assert server.calls == 1

    clock.now += 10
    with pytest.raises(LubeLoggerUnavailable, match="for 20 s"):
        await client.vehicles()

    # After the pause it is tried again, and a failure starts a new pause
    clock.now += 21
    with pytest.raises(LubeLoggerUnavailable, match="ConnectTimeout"):
        await client.vehicles()
    assert server.calls == 2
    with pytest.raises(LubeLoggerUnavailable, match="isn't tried again"):
        await client.vehicles()
    assert server.calls == 2


async def test_when_it_answers_again_everything_works_at_once() -> None:
    server, clock = Server(), Clock()
    client = make(server, clock)
    with pytest.raises(LubeLoggerUnavailable):
        await client.vehicles()
    clock.now += 31
    server.mode = "ok"
    assert await client.vehicles() == []
    assert (await client.download("/documents/a.pdf"))[0] == b"%PDF"
    assert await client.vehicles() == []  # no pause is left over


async def test_an_answer_with_an_error_does_not_start_a_pause() -> None:
    server, clock = Server(), Clock()
    server.mode = "error"
    client = make(server, clock)
    for _ in range(3):
        with pytest.raises(LubeLoggerUnavailable, match="had an error"):
            await client.vehicles()
    assert server.calls == 3  # it is up, only unhappy: every call is tried


async def test_checking_the_connection_ignores_the_pause() -> None:
    server, clock = Server(), Clock()
    client = make(server, clock)
    with pytest.raises(LubeLoggerUnavailable):
        await client.vehicles()
    server.mode = "ok"
    await client.check()  # asks although the pause isn't over, and ends it
    assert server.calls == 2
    assert await client.vehicles() == []


async def test_the_pause_can_be_switched_off() -> None:
    server, clock = Server(), Clock()
    client = make(server, clock, pause_after_failure=0)
    for _ in range(3):
        with pytest.raises(LubeLoggerUnavailable, match="ConnectTimeout"):
            await client.vehicles()
    assert server.calls == 3


async def test_saving_a_fuel_up_asks_a_dead_lubelogger_only_once(
    settings: Settings, fake_lubelogger: FakeLubeLogger
) -> None:
    clock = Clock()
    client = LubeLoggerClient(
        "http://lubelogger.test",
        clock=clock,
        transport=httpx.MockTransport(fake_lubelogger.handler),
    )
    app = create_app(settings, lubelogger=client)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as api,
    ):
        await sign_in_admin(SimpleNamespace(client=api))  # all it needs is the client
        assert (await api.get("/api/vehicles")).status_code == 200  # the list is saved

        app.state.services.vehicles.invalidate()  # as if the 60 s had passed
        fake_lubelogger.down = True
        fake_lubelogger.requests.clear()
        response = await api.post("/api/fuel-ups", json=fuel_up_body())

        assert response.status_code == 201, response.text
        # The vehicle list was asked for once; the odometer check then failed at once
        assert len(fake_lubelogger.requests) == 1
        assert any("wasn't checked yet" in w for w in response.json()["warnings"])


# --- the saved odometer reading ---

HOUR = 3600


class Fixed:
    """A LubeLogger client whose last odometer reading and availability can be set."""

    def __init__(self) -> None:
        self.reading = 1000
        self.down = False
        self.calls = 0

    async def latest_odometer(self, vehicle_id: int) -> int:
        self.calls += 1
        if self.down:
            raise LubeLoggerUnavailable("LubeLogger can't be reached (test).")
        return self.reading


def directory(client: Fixed, clock: Clock) -> VehicleDirectory:
    return VehicleDirectory(client, clock=clock)  # type: ignore[arg-type]


async def test_a_reading_under_an_hour_old_is_used_without_asking() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    assert await vehicles.latest_odometer(1) == OdometerReading(1000, saved=False)
    client.reading = 1500  # changed in LubeLogger a moment later: not looked at yet
    clock.now += HOUR - 1
    reading = await vehicles.latest_odometer(1)
    assert reading.value == 1000 and not reading.saved
    assert client.calls == 1


async def test_after_an_hour_lubelogger_is_asked_again_and_a_new_reading_is_picked_up() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    await vehicles.latest_odometer(1)
    client.reading = 1500
    clock.now += HOUR
    assert (await vehicles.latest_odometer(1)).value == 1500
    assert client.calls == 2
    # ... and that reading is then good for another hour
    clock.now += HOUR - 1
    assert (await vehicles.latest_odometer(1)).value == 1500
    assert client.calls == 2


async def test_when_lubelogger_is_down_the_last_reading_seen_is_used_however_old() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    await vehicles.latest_odometer(1)
    client.down = True
    for waited in (HOUR, 5 * HOUR, 40 * 24 * HOUR):
        clock.now = 1000 + waited
        reading = await vehicles.latest_odometer(1)
        assert reading.value == 1000 and reading.saved and round(reading.age_seconds) == waited


async def test_within_the_hour_a_down_lubelogger_isnt_even_asked() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    await vehicles.latest_odometer(1)
    client.down = True
    clock.now += 20 * 60
    reading = await vehicles.latest_odometer(1)
    assert reading.value == 1000 and not reading.saved and client.calls == 1


async def test_a_vehicle_that_was_never_seen_has_no_reading_when_lubelogger_is_down() -> None:
    client, clock = Fixed(), Clock()
    client.down = True
    with pytest.raises(LubeLoggerUnavailable):
        await directory(client, clock).latest_odometer(1)

    class Refused(Fixed):
        async def latest_odometer(self, vehicle_id: int) -> int:
            raise LubeLoggerRejected("no")

    vehicles = directory(Refused(), clock)
    vehicles._odometers[1] = (900, clock.now - 2 * HOUR)  # a saved reading doesn't hide a refusal
    with pytest.raises(LubeLoggerRejected):
        await vehicles.latest_odometer(1)


async def test_the_reading_is_kept_per_vehicle_and_a_written_record_raises_it() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    await vehicles.latest_odometer(1)
    clock.now += 30 * 60
    vehicles.note_odometer(2, 5000)  # never seen: nothing is invented
    vehicles.note_odometer(1, 900)  # lower: ignored
    vehicles.note_odometer(1, 1200)  # a record with this reading was just written
    assert (await vehicles.latest_odometer(1)).value == 1200
    client.down = True
    with pytest.raises(LubeLoggerUnavailable):
        await vehicles.latest_odometer(2)
    # The raised reading keeps its age: after the hour LubeLogger is asked, then the copy is used
    clock.now += 31 * 60
    reading = await vehicles.latest_odometer(1)
    assert reading.value == 1200 and reading.saved and round(reading.age_seconds) == 3660


def test_ages_are_told_in_the_unit_that_fits() -> None:
    assert [age_text(s) for s in (20 * 60, 89 * 60, 3 * HOUR, 47 * HOUR, 5 * 24 * HOUR)] == [
        "20 min",
        "89 min",
        "3 h",
        "47 h",
        "5 days",
    ]


async def test_the_hint_and_the_check_follow_the_one_hour_rule(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    clock = Clock()
    api.services.vehicles._clock = clock
    api.fake.add_record(1, date="2026-09-01", odometer=20000, fuelConsumed=30, cost=50)
    assert (await api.client.get("/api/vehicles/1/odometer")).json()["odometer"] == 20000
    assert (await api.client.get("/api/vehicles")).status_code == 200  # the vehicle list is saved

    # Changed in LubeLogger, but the reading is only 30 minutes old: it isn't asked for yet
    api.fake.add_record(1, date="2026-10-01", odometer=21000, fuelConsumed=30, cost=50)
    clock.now += 30 * 60
    api.fake.requests.clear()
    assert (await api.client.get("/api/vehicles/1/odometer")).json() == {
        "odometer": 20000,
        "saved": False,
        "saved_age": None,
    }
    assert api.fake.requests == []

    # After an hour it is asked, and the new reading is picked up
    clock.now += 31 * 60
    assert (await api.client.get("/api/vehicles/1/odometer")).json()["odometer"] == 21000

    # LubeLogger is down for a long time: the last reading seen is still used
    api.fake.down = True
    clock.now += 3 * 3600
    hint = (await api.client.get("/api/vehicles/1/odometer")).json()
    assert hint == {"odometer": 21000, "saved": True, "saved_age": "3 h"}
    low = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=20500))
    assert low.status_code == 422 and "last reading seen (21000)" in low.text
    ok = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=21500))
    assert ok.status_code == 201
    assert any("last one seen (3 h ago)" in w for w in ok.json()["warnings"])


async def test_a_sent_fuel_up_raises_the_saved_reading(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    api.fake.add_record(1, date="2026-09-01", odometer=20000, fuelConsumed=30, cost=50)
    await api.client.get("/api/vehicles/1/odometer")  # seen: 20000
    created = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=20500))
    assert created.status_code == 201
    await api.services.processor.process_due()  # written to LubeLogger
    api.fake.requests.clear()
    # Within the hour the saved reading is used, and it already knows about the new record
    assert (await api.client.get("/api/vehicles/1/odometer")).json()["odometer"] == 20500
    assert api.fake.requests == []
    low = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=20300))
    assert low.status_code == 422
