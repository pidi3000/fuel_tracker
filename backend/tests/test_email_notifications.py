import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport
from sqlalchemy import create_engine, select

from app.core.config import Settings
from app.core.database import MIGRATIONS_DIR
from app.core.types import utcnow
from app.main import create_app
from app.models import EmailState, Notification
from app.services import notifications
from app.services import receipts as receipt_service
from app.services.email_notifications import EmailNotifier, problem_from_log, recipient_url
from tests.conftest import AppUnderTest, lubelogger_client
from tests.fake_email import FakeEmailSender
from tests.fake_lubelogger import FakeLubeLogger
from tests.helpers import create_user, sign_in_admin, sign_in_as
from tests.test_receipts import insert_receipt, receipt_fuel_up

EMAIL_URL = "mailtos://fuel:secret@smtp.example.org:587?from=fuel@example.org&name=Fuel%20Tracker"


@pytest.fixture
def sender() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
async def mail_api(
    settings: Settings, fake_lubelogger: FakeLubeLogger, sender: FakeEmailSender
) -> AsyncIterator[AppUnderTest]:
    """The running app with email set up, sending into `sender`."""
    settings = settings.model_copy(update={"apprise_email_url": EMAIL_URL})
    app = create_app(settings, lubelogger=lubelogger_client(fake_lubelogger), email_sender=sender)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield AppUnderTest(app, client, app.state.services, fake_lubelogger)


async def add_notification(api: AppUnderTest, **fields) -> int:
    values = {"level": "warning", "title": "Something happened", "message": "Take a look."}
    values.update(fields)
    async with api.services.sessionmaker() as session:
        notification = await notifications.notify(session, None, **values)
        await session.commit()
        return notification.id


async def email_state(api: AppUnderTest, notification_id: int) -> str:
    async with api.services.sessionmaker() as session:
        notification = await session.get(Notification, notification_id)
        assert notification is not None
        return notification.email_state


# --- the address ---


def test_the_address_is_added_to_the_url() -> None:
    url = recipient_url(EMAIL_URL, "bob+fuel@example.org")
    assert url.startswith("mailtos://fuel:secret@smtp.example.org:587?")
    assert "to=bob%2Bfuel%40example.org" in url
    # The other parameters stay, a "to" that was in the URL is replaced
    assert "name=Fuel%20Tracker" in url and "from=fuel%40example.org" in url
    again = recipient_url(url, "carol@example.org")
    assert again.count("to=") == 1 and "carol%40example.org" in again


def test_the_reason_comes_from_apprises_log() -> None:
    log = (
        "DEBUG|Connecting to remote SMTP server...\n"
        'WARNING|Connection error while submitting email to "smtp.example.org"\n'
        "DEBUG|Socket Exception: (535, b'5.7.8 Authentication credentials invalid')\n"
    )
    assert problem_from_log(log) == (
        'Connection error while submitting email to "smtp.example.org": '
        "(535, b'5.7.8 Authentication credentials invalid')"
    )
    assert problem_from_log("WARNING|Something odd") == "Something odd"
    assert "did not accept" in problem_from_log("")


# --- who gets it ---


async def test_a_notification_goes_to_its_user(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    bob = await create_user(mail_api, "bob", [1], email="bob@example.org")
    note = await add_notification(mail_api, user_id=bob, title="Fuel-up #3 failed", level="error")

    assert await mail_api.services.email.deliver_pending() == 1

    assert sender.addresses == ["bob@example.org"]
    email = sender.sent[0]
    assert email.title == "Fuel Tracker: Fuel-up #3 failed"
    assert email.body == "Take a look." and email.level == "error"
    assert await email_state(mail_api, note) == EmailState.SENT
    # Once sent, it is not sent again
    assert await mail_api.services.email.deliver_pending() == 0
    assert len(sender.sent) == 1


async def test_a_user_without_an_address_leaves_it_to_the_admins(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "boss", [], admin=True, email="boss@example.org")
    await create_user(mail_api, "nomail", [], admin=True)  # an admin without an address
    bob = await create_user(mail_api, "bob", [1])
    await add_notification(mail_api, user_id=bob)

    await mail_api.services.email.deliver_pending()

    assert sorted(sender.addresses) == ["alice@example.org", "boss@example.org"]


async def test_a_notification_for_nobody_in_particular_goes_to_the_admins(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "bob", [1], email="bob@example.org")
    await add_notification(mail_api, title="Receipt without a fuel-up")

    await mail_api.services.email.deliver_pending()

    assert sender.addresses == ["alice@example.org"]


async def test_an_inactive_user_does_not_get_emails(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    bob = await create_user(mail_api, "bob", [1], email="bob@example.org")
    await mail_api.client.patch(f"/api/users/{bob}", json={"is_active": False})
    await add_notification(mail_api, user_id=bob)

    await mail_api.services.email.deliver_pending()

    assert sender.addresses == ["alice@example.org"]


async def test_the_same_address_twice_gets_one_email(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="Shared@example.org")
    await create_user(mail_api, "boss", [], admin=True, email="shared@example.org")
    await add_notification(mail_api)

    await mail_api.services.email.deliver_pending()

    assert len(sender.sent) == 1


async def test_nobody_to_send_to_is_not_an_error(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api)  # no address
    note = await add_notification(mail_api)

    assert await mail_api.services.email.deliver_pending() == 1

    assert sender.sent == []
    assert await email_state(mail_api, note) == EmailState.SKIPPED


async def test_a_message_without_text_uses_its_title(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await add_notification(mail_api, title="Heads up", message="")

    await mail_api.services.email.deliver_pending()

    assert sender.sent[0].body == "Heads up"


# --- when it is not set up ---


async def test_nothing_is_sent_when_email_is_not_set_up(api: AppUnderTest) -> None:
    await sign_in_admin(api, email="alice@example.org")
    note = await add_notification(api)
    sender = FakeEmailSender()
    notifier = EmailNotifier(api.services.sessionmaker, "", sender=sender)

    await notifier.deliver_pending()

    assert sender.sent == []
    # ... and the message is not kept for the day it is set up
    assert await email_state(api, note) == EmailState.SKIPPED
    later = EmailNotifier(api.services.sessionmaker, EMAIL_URL, sender=sender)
    await later.deliver_pending()
    assert sender.sent == []


async def test_a_url_that_is_not_an_email_url_is_refused(api: AppUnderTest) -> None:
    sender = FakeEmailSender()
    notifier = EmailNotifier(api.services.sessionmaker, "ntfy://topic", sender=sender)
    await sign_in_admin(api, email="alice@example.org")
    note = await add_notification(api)

    assert not notifier.ready
    status = await notifier.status()
    assert status.state == "error" and "mailto" in status.message
    await notifier.deliver_pending()
    assert sender.sent == [] and await email_state(api, note) == EmailState.SKIPPED


async def test_notifications_from_before_the_upgrade_are_not_sent(tmp_path: Path) -> None:
    database = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{database}")
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0006")
    with closing(sqlite3.connect(database)) as old, old:
        old.execute(
            "INSERT INTO notifications (level, title, message, is_read, created_at) "
            "VALUES ('info', 'Old news', '', 0, '2026-10-01 10:00:00')"
        )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with closing(sqlite3.connect(database)) as upgraded:
        row = upgraded.execute("SELECT email_state, email_attempts FROM notifications").fetchone()
    assert row == ("skipped", 0)


# --- when sending fails ---


class Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now


async def test_a_failed_email_is_tried_again_and_then_given_up(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    note = await add_notification(mail_api)
    clock = Clock()
    notifier = EmailNotifier(
        mail_api.services.sessionmaker,
        EMAIL_URL,
        sender=sender,
        retry_delays=(60, 300),
        clock=clock,
    )
    sender.error = "Connection error while submitting email"

    assert await notifier.deliver_pending() == 0  # tried, and it will be tried again
    status = await notifier.status()
    assert status.state == "error" and "Connection error" in status.message
    assert await email_state(mail_api, note) == EmailState.PENDING

    # Not before the waiting time is over
    clock.now += timedelta(seconds=30)
    await notifier.deliver_pending()
    clock.now += timedelta(seconds=31)
    await notifier.deliver_pending()
    clock.now += timedelta(seconds=301)
    assert await notifier.deliver_pending() == 1  # the third failure is the last

    assert await email_state(mail_api, note) == EmailState.FAILED
    sender.error = None
    clock.now += timedelta(hours=1)
    await notifier.deliver_pending()
    assert sender.sent == []  # given up for good


async def test_the_email_goes_out_when_the_mail_server_comes_back(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    note = await add_notification(mail_api)
    clock = Clock()
    notifier = EmailNotifier(
        mail_api.services.sessionmaker, EMAIL_URL, sender=sender, retry_delays=(60,), clock=clock
    )
    sender.error = "Connection error"
    await notifier.deliver_pending()

    sender.error = None
    clock.now += timedelta(seconds=61)
    assert await notifier.deliver_pending() == 1

    assert len(sender.sent) == 1 and await email_state(mail_api, note) == EmailState.SENT
    assert (await notifier.status()).state == "ok"


async def test_a_retry_does_not_send_again_to_who_already_got_it(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "boss", [], admin=True, email="boss@example.org")
    await add_notification(mail_api)
    clock = Clock()
    notifier = EmailNotifier(
        mail_api.services.sessionmaker, EMAIL_URL, sender=sender, retry_delays=(60,), clock=clock
    )
    sender.fail_for = {"boss@example.org"}
    await notifier.deliver_pending()
    assert sender.addresses == ["alice@example.org"]

    sender.fail_for = set()
    clock.now += timedelta(seconds=61)
    await notifier.deliver_pending()

    assert sorted(sender.addresses) == ["alice@example.org", "boss@example.org"]


# --- through the app ---


async def test_a_fuel_up_that_gets_no_receipt_is_emailed_to_its_creator(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "bob", [1], email="bob@example.org")
    await sign_in_as(mail_api, "bob")
    created = await receipt_fuel_up(mail_api)
    async with mail_api.services.sessionmaker() as session:
        await receipt_service.expire_waiting_fuel_ups(
            session, mail_api.services.receipts, utcnow() + timedelta(hours=2)
        )
        await session.commit()

    await mail_api.services.email.deliver_pending()

    assert sender.addresses == ["bob@example.org"]
    assert sender.sent[0].title == f"Fuel Tracker: No receipt for fuel-up #{created['id']}"
    assert sender.sent[0].level == "error"


async def test_a_receipt_without_a_fuel_up_is_emailed_to_the_admins(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "bob", [1], email="bob@example.org")
    await insert_receipt(mail_api)
    async with mail_api.services.sessionmaker() as session:
        await receipt_service.notify_lonely_receipts(
            session, mail_api.services.receipts, utcnow() + timedelta(hours=1)
        )
        await session.commit()

    await mail_api.services.email.deliver_pending()

    assert sender.addresses == ["alice@example.org"]
    assert sender.sent[0].title == "Fuel Tracker: Receipt without a fuel-up"


async def test_a_rolled_back_notification_is_not_sent(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    async with mail_api.services.sessionmaker() as session:
        await notifications.notify(session, None, level="error", title="Never happened")
        await session.rollback()

    await mail_api.services.email.deliver_pending()

    assert sender.sent == []
    async with mail_api.services.sessionmaker() as session:
        assert (await session.execute(select(Notification))).first() is None


# --- the settings page ---


async def test_status_says_when_email_is_not_set_up(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    status = (await api.client.get("/api/status")).json()["email"]
    assert status["state"] == "not_configured" and "APPRISE_EMAIL_URL" in status["message"]


async def test_status_says_whether_there_is_someone_to_send_to(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api)
    # Set up, but nobody has an address to send the admin messages to
    status = (await mail_api.client.get("/api/status")).json()["email"]
    assert status["state"] == "error" and "email address" in status["message"]
    await mail_api.client.patch("/api/users/1", json={"email": "alice@example.org"})
    assert (await mail_api.client.get("/api/status")).json()["email"] == {
        "state": "ok",
        "message": "",
    }


async def test_a_test_email_goes_to_the_signed_in_admin(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api)
    body = (await mail_api.client.post("/api/status/email-test")).json()
    assert body["state"] == "error" and "no email address" in body["message"]
    assert sender.sent == []

    await mail_api.client.patch("/api/users/1", json={"email": "alice@example.org"})
    body = (await mail_api.client.post("/api/status/email-test")).json()
    assert body["state"] == "ok" and "alice@example.org" in body["message"]
    assert sender.addresses == ["alice@example.org"]

    sender.error = "Authentication failed"
    body = (await mail_api.client.post("/api/status/email-test")).json()
    assert body == {"state": "error", "message": "Authentication failed"}
    status = (await mail_api.client.get("/api/status")).json()["email"]
    assert status["state"] == "error" and "Authentication failed" in status["message"]


async def test_the_test_email_is_for_admins_only(mail_api: AppUnderTest) -> None:
    assert (await mail_api.client.post("/api/status/email-test")).status_code == 401
    await sign_in_admin(mail_api)
    await create_user(mail_api, "bob", [1], email="bob@example.org")
    await sign_in_as(mail_api, "bob")
    assert (await mail_api.client.post("/api/status/email-test")).status_code == 403
