from datetime import UTC, datetime

from app.models import Notification, NotificationKind, User
from app.services import notifications
from tests.conftest import AppUnderTest

PASSWORD = "correct horse"


async def sign_in_admin(
    api: AppUnderTest, username: str = "alice", email: str | None = None
) -> None:
    response = await api.client.post(
        "/api/setup", json={"username": username, "password": PASSWORD, "email": email}
    )
    assert response.status_code == 201, response.text


async def create_user(
    api: AppUnderTest,
    username: str,
    vehicle_ids: list[int],
    *,
    admin: bool = False,
    email: str | None = None,
) -> int:
    response = await api.client.post(
        "/api/users",
        json={
            "username": username,
            "password": PASSWORD,
            "vehicle_ids": vehicle_ids,
            "role": "admin" if admin else "user",
        },
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    if email:
        await set_email(api, user_id, email)
    return user_id


async def set_email(api: AppUnderTest, user_id: int, email: str | None) -> None:
    """Give a user an email address (users do it themselves in the app; admins can't)."""
    async with api.services.sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.email = email
        await session.commit()


async def sign_in_as(api: AppUnderTest, username: str) -> None:
    await api.client.post("/api/auth/logout")
    response = await api.client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def fuel_up_body(**overrides) -> dict:
    body = {
        "vehicle_id": 1,
        "odometer": 12345,
        "fuel_up_time": datetime(2026, 9, 23, 15, 8, tzinfo=UTC).isoformat(),
        "fuel_type": "Super",
        "quantity": "23.00",
        "total_price": "54.03",
    }
    body.update(overrides)
    return body


async def create_fuel_up(api: AppUnderTest, **overrides) -> dict:
    response = await api.client.post("/api/fuel-ups", json=fuel_up_body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def get_fuel_up(api: AppUnderTest, fuel_up_id: int) -> dict:
    response = await api.client.get(f"/api/fuel-ups/{fuel_up_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def add_notification(api: AppUnderTest, **fields) -> int:
    values = {
        "kind": NotificationKind.FUEL_UP_FAILED,
        "level": "warning",
        "title": "Something happened",
        "message": "Take a look.",
    }
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
