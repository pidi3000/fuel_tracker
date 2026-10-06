"""LubeLogger that doesn't answer must not hold everything up."""

from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

from app.core.config import Settings
from app.main import create_app
from app.services.lubelogger import LubeLoggerClient, LubeLoggerRejected, LubeLoggerUnavailable
from app.services.vehicles import OdometerReading, VehicleDirectory
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


async def test_the_odometer_is_always_asked_for_so_a_new_reading_is_picked_up() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    assert (await vehicles.latest_odometer(1)) == OdometerReading(1000, saved=False)
    # Nothing is added for a while, but the reading changes in LubeLogger
    clock.now += 5 * 3600
    client.reading = 1500
    assert (await vehicles.latest_odometer(1)).value == 1500
    assert client.calls == 2


async def test_the_saved_reading_is_used_while_lubelogger_is_down_for_up_to_an_hour() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    await vehicles.latest_odometer(1)
    client.down = True
    clock.now += 20 * 60
    reading = await vehicles.latest_odometer(1)
    assert reading.value == 1000 and reading.saved and round(reading.age_seconds) == 1200
    clock.now += 40 * 60  # exactly an hour old
    assert (await vehicles.latest_odometer(1)).saved
    clock.now += 1  # older than an hour: no longer used
    with pytest.raises(LubeLoggerUnavailable):
        await vehicles.latest_odometer(1)


async def test_the_saved_reading_is_per_vehicle_and_replaced_by_a_new_one() -> None:
    client, clock = Fixed(), Clock()
    vehicles = directory(client, clock)
    await vehicles.latest_odometer(1)
    client.down = True
    with pytest.raises(LubeLoggerUnavailable):  # vehicle 2 was never seen
        await vehicles.latest_odometer(2)
    client.down = False
    client.reading = 2000
    await vehicles.latest_odometer(1)
    clock.now += 50 * 60
    client.down = True
    assert (await vehicles.latest_odometer(1)).value == 2000  # the newest copy, and its age


async def test_nothing_is_saved_without_a_reading_and_errors_other_than_down_pass() -> None:
    client, clock = Fixed(), Clock()
    client.down = True
    with pytest.raises(LubeLoggerUnavailable):
        await directory(client, clock).latest_odometer(1)

    class Refused(Fixed):
        async def latest_odometer(self, vehicle_id: int) -> int:
            raise LubeLoggerRejected("no")

    vehicles = directory(Refused(), clock)
    vehicles._odometers[1] = (900, clock.now)  # a saved reading doesn't hide a refusal
    with pytest.raises(LubeLoggerRejected):
        await vehicles.latest_odometer(1)


async def test_the_hint_and_the_check_use_the_saved_reading(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    clock = Clock()
    api.services.vehicles._clock = clock
    api.fake.add_record(1, date="2026-09-01", odometer=20000, fuelConsumed=30, cost=50)
    assert (await api.client.get("/api/vehicles/1/odometer")).json()["odometer"] == 20000
    assert (await api.client.get("/api/vehicles")).status_code == 200  # the vehicle list is saved

    api.fake.down = True
    clock.now += 15 * 60
    hint = (await api.client.get("/api/vehicles/1/odometer")).json()
    assert hint == {"odometer": 20000, "saved": True, "saved_minutes_ago": 15}

    # A lower reading is refused although LubeLogger can't be asked
    low = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=19000))
    assert low.status_code == 422 and "last reading seen (20000)" in low.text
    # A higher one is accepted, with a note that it was checked against the saved reading
    ok = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=20500))
    assert ok.status_code == 201
    assert any("last one seen (15 min ago)" in w for w in ok.json()["warnings"])

    # After an hour the saved reading is not used any more
    clock.now += 50 * 60
    assert (await api.client.get("/api/vehicles/1/odometer")).json()["odometer"] is None
    unchecked = await api.client.post("/api/fuel-ups", json=fuel_up_body(odometer=20600))
    assert unchecked.status_code == 201
    assert any("wasn't checked yet" in w for w in unchecked.json()["warnings"])

    # When LubeLogger is back, its own reading counts again at once
    api.fake.down = False
    api.fake.add_record(1, date="2026-10-01", odometer=21000, fuelConsumed=30, cost=50)
    assert (await api.client.get("/api/vehicles/1/odometer")).json() == {
        "odometer": 21000,
        "saved": False,
        "saved_minutes_ago": None,
    }
