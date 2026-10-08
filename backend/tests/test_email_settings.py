"""What each user gets by email, their own address, and the link in the emails."""

import pytest

from app.models import NotificationKind, User
from app.services.email_notifications import EmailNotifier
from tests.conftest import AppUnderTest
from tests.fake_email import EMAIL_URL, PUBLIC_URL, FakeEmailSender
from tests.helpers import (
    add_notification,
    create_user,
    sign_in_admin,
    sign_in_as,
)

USER_KINDS = {"fuel_up_failed", "needs_attention", "review_ready"}
ADMIN_KINDS = USER_KINDS | {"receipt_without_fuel_up", "receipt_email_problem", "update_available"}


async def save(api: AppUnderTest, email: str | None, kinds: list[str]):
    return await api.client.put("/api/notifications/email", json={"email": email, "kinds": kinds})


async def kinds_of(api: AppUnderTest) -> dict[str, bool]:
    body = (await api.client.get("/api/notifications/email")).json()
    return {item["kind"]: item["enabled"] for item in body["kinds"]}


# --- each user's own settings ---


async def test_a_user_sees_the_kinds_that_can_reach_them(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api)
    assert set(await kinds_of(mail_api)) == ADMIN_KINDS
    await create_user(mail_api, "bob", [1])
    await sign_in_as(mail_api, "bob")

    body = (await mail_api.client.get("/api/notifications/email")).json()

    assert body["available"] is True and body["email"] is None
    assert {item["kind"] for item in body["kinds"]} == USER_KINDS
    assert all(item["enabled"] and item["label"] and item["description"] for item in body["kinds"])


async def test_email_is_not_available_when_the_server_cannot_send(api: AppUnderTest) -> None:
    await sign_in_admin(api)
    assert (await api.client.get("/api/notifications/email")).json()["available"] is False


async def test_a_user_saves_their_own_address_and_choices(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api)
    await create_user(mail_api, "bob", [1])
    await sign_in_as(mail_api, "bob")

    response = await save(mail_api, " bob@example.org ", ["fuel_up_failed", "review_ready"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "bob@example.org"
    assert {item["kind"]: item["enabled"] for item in body["kinds"]} == {
        "fuel_up_failed": True,
        "needs_attention": False,
        "review_ready": True,
    }
    assert (await mail_api.client.get("/api/auth/me")).json()["email"] == "bob@example.org"
    assert await kinds_of(mail_api) == {
        "fuel_up_failed": True,
        "needs_attention": False,
        "review_ready": True,
    }
    # The address can be removed again
    assert (await save(mail_api, None, [])).json()["email"] is None


async def test_bad_input_is_refused(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api)
    await create_user(mail_api, "bob", [1])
    await sign_in_as(mail_api, "bob")
    await save(mail_api, "bob@example.org", ["fuel_up_failed"])

    assert (await save(mail_api, "not-an-address", ["fuel_up_failed"])).status_code == 422
    assert (await save(mail_api, None, ["nonsense"])).status_code == 422
    # A kind that is for admins only
    assert (await save(mail_api, None, ["update_available"])).status_code == 422
    # Nothing of it was saved
    assert (await mail_api.client.get("/api/auth/me")).json()["email"] == "bob@example.org"


async def test_the_settings_need_a_login(mail_api: AppUnderTest) -> None:
    assert (await mail_api.client.get("/api/notifications/email")).status_code == 401
    assert (await save(mail_api, None, [])).status_code == 401
    assert (await mail_api.client.post("/api/notifications/email/test")).status_code == 401


async def test_admins_cannot_set_the_address_of_other_users(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api)
    created = await mail_api.client.post(
        "/api/users",
        json={"username": "bob", "password": "long enough", "email": "bob@example.org"},
    )
    assert created.status_code == 201 and created.json()["email"] is None

    changed = await mail_api.client.patch(
        f"/api/users/{created.json()['id']}", json={"email": "bob@example.org"}
    )
    assert changed.status_code == 200 and changed.json()["email"] is None


# --- the test email ---


async def test_a_test_email_goes_to_the_users_own_address(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "bob", [1])
    await sign_in_as(mail_api, "bob")
    body = (await mail_api.client.post("/api/notifications/email/test")).json()
    assert body["state"] == "error" and "Account page" in body["message"]
    assert sender.sent == []

    await save(mail_api, "bob@example.org", ["fuel_up_failed"])
    body = (await mail_api.client.post("/api/notifications/email/test")).json()
    assert body["state"] == "ok" and "bob@example.org" in body["message"]
    assert sender.addresses == ["bob@example.org"]

    sender.error = "Authentication failed"
    body = (await mail_api.client.post("/api/notifications/email/test")).json()
    assert body == {"state": "error", "message": "Authentication failed"}
    await sign_in_as(mail_api, "alice")
    status = (await mail_api.client.get("/api/status")).json()["email"]
    assert status["state"] == "error" and "Authentication failed" in status["message"]


async def test_no_test_email_without_a_mail_server(api: AppUnderTest) -> None:
    await sign_in_admin(api, email="alice@example.org")
    body = (await api.client.post("/api/notifications/email/test")).json()
    assert body["state"] == "error" and "not set up" in body["message"]


# --- who wants what ---


async def test_a_user_who_switched_a_kind_off_gets_no_email_for_it(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    bob = await create_user(mail_api, "bob", [1])
    await sign_in_as(mail_api, "bob")
    await save(mail_api, "bob@example.org", ["fuel_up_failed", "review_ready"])
    await add_notification(mail_api, user_id=bob, kind=NotificationKind.NEEDS_ATTENTION)
    await add_notification(mail_api, user_id=bob, kind=NotificationKind.REVIEW_READY)

    await mail_api.services.email.deliver_pending()

    # Only the one bob wants, and the admin doesn't get the other one instead
    assert sender.addresses == ["bob@example.org"]


async def test_a_user_without_an_address_leaves_it_to_the_admins_who_want_it(
    mail_api: AppUnderTest, sender
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "boss", [], admin=True, email="boss@example.org")
    bob = await create_user(mail_api, "bob", [1])
    # Alice doesn't want failed fuel-ups by email, the boss does
    await save(mail_api, "alice@example.org", sorted(ADMIN_KINDS - {"fuel_up_failed"}))
    await add_notification(mail_api, user_id=bob, kind=NotificationKind.FUEL_UP_FAILED)

    await mail_api.services.email.deliver_pending()

    assert sender.addresses == ["boss@example.org"]


async def test_admins_choose_for_the_messages_to_the_admins(mail_api: AppUnderTest, sender) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await create_user(mail_api, "boss", [], admin=True, email="boss@example.org")
    await save(mail_api, "alice@example.org", sorted(ADMIN_KINDS - {"update_available"}))
    await add_notification(mail_api, kind=NotificationKind.UPDATE_AVAILABLE, level="info")
    await add_notification(mail_api, kind=NotificationKind.RECEIPT_WITHOUT_FUEL_UP)

    await mail_api.services.email.deliver_pending()

    assert sorted((email.to, email.title) for email in sender.sent) == [
        ("alice@example.org", "Fuel Tracker: Something happened"),
        ("boss@example.org", "Fuel Tracker: Something happened"),
        ("boss@example.org", "Fuel Tracker: Something happened"),
    ]


async def test_switching_off_what_an_admin_no_longer_sees_is_kept(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await save(mail_api, "alice@example.org", sorted(ADMIN_KINDS - {"update_available"}))
    async with mail_api.services.sessionmaker() as session:
        alice = await session.get(User, 1)
        assert alice is not None
        assert alice.email_disabled_kinds == ["update_available"]
        alice.role = "user"
        await session.commit()

    await save(mail_api, "alice@example.org", ["fuel_up_failed"])

    async with mail_api.services.sessionmaker() as session:
        alice = await session.get(User, 1)
        assert alice is not None
        assert alice.email_disabled_kinds == ["needs_attention", "review_ready", "update_available"]


# --- the link ---


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"fuel_up_id": 5}, "/fuel-ups/5"),
        ({"receipt_id": 7, "kind": NotificationKind.RECEIPT_EMAIL_PROBLEM}, "/receipts/7"),
        ({"fuel_up_id": 5, "receipt_id": 7}, "/fuel-ups/5"),
        ({"kind": NotificationKind.UPDATE_AVAILABLE}, "/admin/settings"),
        ({"kind": NotificationKind.RECEIPT_EMAIL_PROBLEM}, "/notifications"),
    ],
)
async def test_the_email_links_to_what_it_is_about(
    mail_api: AppUnderTest, sender, fields: dict, path: str
) -> None:
    await sign_in_admin(mail_api, email="alice@example.org")
    await add_notification(mail_api, **fields)

    await mail_api.services.email.deliver_pending()

    assert sender.sent[0].body.endswith(f"\n\nOpen it in Fuel Tracker:\n{PUBLIC_URL}{path}")


@pytest.mark.parametrize(
    ("public_url", "link", "message"),
    [
        ("https://fuel.example.org/", "https://fuel.example.org/fuel-ups/5", ""),
        ("http://192.168.1.5:8000", "http://192.168.1.5:8000/fuel-ups/5", ""),
        ("", None, "PUBLIC_URL is not set"),
        ("fuel.example.org", None, "isn't a web address"),
    ],
)
async def test_the_link_comes_from_public_url(
    api: AppUnderTest, public_url: str, link: str | None, message: str
) -> None:
    await sign_in_admin(api, email="alice@example.org")
    sender = FakeEmailSender()
    notifier = EmailNotifier(
        api.services.sessionmaker, EMAIL_URL, public_url=public_url, sender=sender
    )
    await add_notification(api, fuel_up_id=5)

    await notifier.deliver_pending()

    body = sender.sent[0].body
    if link:
        assert body.endswith(f"\n\nOpen it in Fuel Tracker:\n{link}")
    else:
        assert body == "Take a look."
    status = await notifier.status()
    assert status.state == "ok" and message in status.message


async def test_settings_show_the_public_address(mail_api: AppUnderTest) -> None:
    await sign_in_admin(mail_api)
    env = (await mail_api.client.get("/api/settings")).json()["environment"]
    assert env["public_url"] == PUBLIC_URL
