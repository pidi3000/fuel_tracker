import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select

from app.core.database import MIGRATIONS_DIR
from app.core.types import utcnow
from app.models import EmailState, Notification, NotificationKind
from app.services import notifications
from app.services import receipts as receipt_service
from app.services.email_notifications import EmailNotifier, problem_from_log, recipient_url
from tests.conftest import AppUnderTest
from tests.fake_email import EMAIL_URL, PUBLIC_URL, FakeEmailSender
from tests.helpers import (
    add_notification,
    create_user,
    email_state,
    sign_in_admin,
    sign_in_as,
)
from tests.test_receipts import insert_receipt, receipt_fuel_up

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
    assert email.body == f"Take a look.\n\nOpen it in Fuel Tracker:\n{PUBLIC_URL}/notifications"
    assert email.level == "error"
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

    assert sender.sent[0].body.startswith("Heads up\n\nOpen it in Fuel Tracker:")


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
        old.execute(
            "INSERT INTO users (username, password_hash, role, is_active, created_at) "
            "VALUES ('alice', 'x', 'admin', 1, '2026-10-01 10:00:00')"
        )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with closing(sqlite3.connect(database)) as upgraded:
        row = upgraded.execute(
            "SELECT email_state, email_attempts, kind, receipt_id FROM notifications"
        ).fetchone()
        wants = upgraded.execute("SELECT email_disabled_kinds FROM users").fetchone()
    assert row == ("skipped", 0, "other", None)
    assert wants == ("[]",)  # a user wants everything until they say otherwise


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
        await notifications.notify(
            session,
            None,
            kind=NotificationKind.FUEL_UP_FAILED,
            level="error",
            title="Never happened",
        )
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
    assert status["state"] == "error" and "Account page" in status["message"]


async def test_status_is_not_ok_before_an_email_went_out(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    # An address and a valid URL don't show that the mail server's login works
    status = (await mail_api.client.get("/api/status")).json()["email"]
    assert status["state"] == "untested" and "test email" in status["message"]

    await mail_api.client.post("/api/notifications/email/test")

    assert (await mail_api.client.get("/api/status")).json()["email"] == {
        "state": "ok",
        "message": "",
    }


async def test_status_is_ok_after_a_notification_went_out(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await add_notification(mail_api)

    await mail_api.services.email.deliver_pending()

    assert (await mail_api.client.get("/api/status")).json()["email"]["state"] == "ok"


async def test_status_goes_back_to_ok_after_a_failure(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await mail_api.client.post("/api/notifications/email/test")
    sender.error = "Connection error"
    await mail_api.client.post("/api/notifications/email/test")
    assert (await mail_api.client.get("/api/status")).json()["email"]["state"] == "error"

    sender.error = None
    await mail_api.client.post("/api/notifications/email/test")

    assert (await mail_api.client.get("/api/status")).json()["email"]["state"] == "ok"
