from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.types import utcnow
from app.models import FuelUp, Notification, Receipt, ReceiptState
from app.services.cleanup import clean_up
from tests.conftest import AppUnderTest
from tests.fake_mailbox import FakeMailbox, build_email, receipt_pdf
from tests.helpers import create_fuel_up, create_user, get_fuel_up, sign_in_admin, sign_in_as
from tests.test_receipts import deliver, insert_receipt, receipt_fuel_up

# --- settings ---


async def test_settings_are_for_admins_only(api: AppUnderTest) -> None:
    assert (await api.client.get("/api/settings")).status_code == 401
    await sign_in_admin(api)
    await create_user(api, "bob", [1])
    await sign_in_as(api, "bob")
    assert (await api.client.get("/api/settings")).status_code == 403
    assert (await api.client.put("/api/settings/tz", json={"value": "UTC"})).status_code == 403
    assert (await api.client.delete("/api/settings/tz")).status_code == 403


async def test_settings_list_shows_values_and_connection_details(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    data = (await api.client.get("/api/settings")).json()
    by_key = {s["key"]: s for s in data["settings"]}
    assert by_key["receipt_timeout_minutes"]["value"] == 60
    assert by_key["receipt_timeout_minutes"]["overridden"] is False
    assert by_key["fuel_types"]["value"] == ["Diesel", "Super", "Super Plus", "Super E10"]
    assert by_key["review_before_send"]["kind"] == "bool"

    env = data["environment"]
    assert env["lubelogger_url"] == "http://lubelogger.test"
    assert env["lubelogger_api_key_set"] is False
    assert env["imap_host"] == "" and env["receipt_sender"] == "no-reply@connectedfueling.com"
    # Secrets never leave the server
    assert "imap_password" not in env and "lubelogger_api_key" not in env


async def test_changing_and_resetting_a_setting(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    changed = await api.client.put("/api/settings/receipt_timeout_minutes", json={"value": 90})
    assert changed.status_code == 200
    assert changed.json()["value"] == 90 and changed.json()["overridden"] is True
    assert changed.json()["default"] == 60
    assert api.services.runtime.receipt_timeout_minutes == 90

    reset = await api.client.delete("/api/settings/receipt_timeout_minutes")
    assert reset.json()["value"] == 60 and reset.json()["overridden"] is False
    assert api.services.runtime.receipt_timeout_minutes == 60


async def test_invalid_values_are_rejected_with_a_reason(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    bad = await api.client.put("/api/settings/tz", json={"value": "Mars/Olympus"})
    assert bad.status_code == 422 and "time zone" in bad.json()["detail"]
    bad = await api.client.put("/api/settings/match_window_minutes", json={"value": 0})
    assert bad.status_code == 422 and "at least 1" in bad.json()["detail"]
    assert (await api.client.put("/api/settings/nope", json={"value": 1})).status_code == 404
    assert (await api.client.delete("/api/settings/nope")).status_code == 404
    # Secrets can't be changed this way
    assert (
        await api.client.put("/api/settings/imap_password", json={"value": "x"})
    ).status_code == 404
    assert api.services.runtime.is_overridden("tz") is False


async def test_settings_take_effect_at_once(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.client.put("/api/settings/fuel_types", json={"value": "Petrol, Diesel"})
    assert (await api.client.get("/api/fuel-types")).json()["fuel_types"] == ["Petrol", "Diesel"]
    response = await api.client.post(
        "/api/fuel-ups",
        json={
            "vehicle_id": 1,
            "odometer": 1,
            "fuel_type": "Super",
            "quantity": "1",
            "total_price": "1",
        },
    )
    assert response.status_code == 422

    await api.client.put("/api/settings/review_before_send", json={"value": True})
    created = await create_fuel_up(api, fuel_type="Petrol")
    assert created["status"] == "needs_attention"

    await api.client.put("/api/settings/volume_unit", json={"value": "gal"})
    assert (await api.client.get("/api/fuel-types")).json()["volume_unit"] == "gal"


async def test_the_receipt_wait_time_setting_is_used(api: AppUnderTest) -> None:
    from app.services import receipts as receipt_service

    await sign_in_admin(api)
    await api.client.put("/api/settings/receipt_timeout_minutes", json={"value": 5})
    created = await receipt_fuel_up(api)
    async with api.services.sessionmaker() as session:
        later = utcnow() + timedelta(minutes=6)
        assert (
            await receipt_service.expire_waiting_fuel_ups(session, api.services.receipts, later)
            == 1
        )
        await session.commit()
    assert "5 minutes" in (await get_fuel_up(api, created["id"]))["error_message"]


async def test_the_date_format_setting_is_used_for_receipts(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.client.put("/api/settings/pace_date_format", json={"value": "%d.%m.%Y, %H:%M"})
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    async with api.services.sessionmaker() as session:
        (receipt,) = (await session.execute(select(Receipt))).scalars()
    # The English date can't be read with the German format: the PDF's date is used
    assert "date_from_pdf_metadata" in receipt.warnings
    assert receipt.paid_at == datetime(2026, 9, 23, 15, 8, 40, tzinfo=UTC)


# --- cleanup ---


async def finish(api: AppUnderTest, **overrides) -> int:
    created = await create_fuel_up(api, **overrides)
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    return created["id"]


async def run_cleanup(api: AppUnderTest, now: datetime | None = None) -> dict:
    async with api.services.sessionmaker() as session:
        removed = await clean_up(
            session,
            api.services.runtime,
            api.services.events,
            api.services.receipts.receipt_dir,
            now,
        )
        await session.commit()
    return removed


async def test_finished_fuel_ups_are_deleted_after_the_grace_period(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    fuel_up_id = await finish(api)
    assert (await run_cleanup(api))["fuel_ups"] == 0
    assert (await run_cleanup(api, utcnow() + timedelta(days=6)))["fuel_ups"] == 0
    assert (await run_cleanup(api, utcnow() + timedelta(days=8)))["fuel_ups"] == 1
    assert (await api.client.get(f"/api/fuel-ups/{fuel_up_id}")).status_code == 404
    assert len(api.fake.records) == 1  # the record stays in LubeLogger
    # ... and shows in the history
    assert (await api.client.get("/api/history")).json()["total"] == 1


async def test_a_grace_period_of_zero_deletes_at_once(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.client.put("/api/settings/done_retention_days", json={"value": 0})
    await finish(api)
    assert (await run_cleanup(api, utcnow() + timedelta(seconds=1)))["fuel_ups"] == 1


async def test_unfinished_fuel_ups_are_never_deleted(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.services.runtime.set("review_before_send", True)
    waiting = await create_fuel_up(api, odometer=100)
    api.fake.fail_add = [400]
    await api.services.runtime.set("review_before_send", False)
    failed = await create_fuel_up(api, odometer=200)
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, failed["id"]))["status"] == "failed"

    removed = await run_cleanup(api, utcnow() + timedelta(days=400))
    assert removed["fuel_ups"] == 0
    assert (await get_fuel_up(api, waiting["id"]))["status"] == "needs_attention"


async def test_the_receipt_and_its_pdf_go_with_the_fuel_up(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    created = await receipt_fuel_up(api)
    await deliver(api, FakeMailbox(), build_email(pdf=receipt_pdf()))
    await api.services.processor.process_due()
    assert (await get_fuel_up(api, created["id"]))["status"] == "done"
    pdfs = list(api.services.receipts.receipt_dir.glob("*.pdf"))
    assert len(pdfs) == 1

    removed = await run_cleanup(api, utcnow() + timedelta(days=8))
    assert removed == {"fuel_ups": 1, "receipts": 1, "notifications": 0}
    assert list(api.services.receipts.receipt_dir.glob("*.pdf")) == []
    async with api.services.sessionmaker() as session:
        assert (await session.execute(select(Receipt))).scalars().all() == []
        assert (await session.execute(select(FuelUp))).scalars().all() == []


async def test_ignored_receipts_are_cleaned_but_unmatched_ones_wait(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    ignored = await insert_receipt(api, state=ReceiptState.IGNORED, transaction_id="a")
    waiting = await insert_receipt(api, state=ReceiptState.UNMATCHED, transaction_id="b")
    removed = await run_cleanup(api, utcnow() + timedelta(days=30))
    assert removed["receipts"] == 1
    async with api.services.sessionmaker() as session:
        assert await session.get(Receipt, ignored) is None
        assert await session.get(Receipt, waiting) is not None


async def test_old_read_notifications_are_cleaned(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    async with api.services.sessionmaker() as session:
        session.add_all(
            [
                Notification(level="info", title="read", is_read=True),
                Notification(level="info", title="unread", is_read=False),
            ]
        )
        await session.commit()
    assert (await run_cleanup(api, utcnow() + timedelta(days=10)))["notifications"] == 0
    assert (await run_cleanup(api, utcnow() + timedelta(days=31)))["notifications"] == 1
    async with api.services.sessionmaker() as session:
        titles = [n.title for n in (await session.execute(select(Notification))).scalars()]
    assert titles == ["unread"]


async def test_the_background_loop_runs_the_cleanup(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    await api.client.put("/api/settings/done_retention_days", json={"value": 0})
    fuel_up_id = await finish(api)
    async with api.services.sessionmaker() as session:
        fuel_up = await session.get(FuelUp, fuel_up_id)
        fuel_up.completed_at = utcnow() - timedelta(minutes=1)
        await session.commit()
    # The periodic jobs registered by the app include the cleanup
    await api.services.processor.tick()
    assert (await api.client.get(f"/api/fuel-ups/{fuel_up_id}")).status_code == 404
