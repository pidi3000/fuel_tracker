from datetime import UTC, datetime

from tests.conftest import AppUnderTest

PASSWORD = "correct horse"


async def sign_in_admin(api: AppUnderTest, username: str = "alice") -> None:
    response = await api.client.post(
        "/api/setup", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


async def create_user(
    api: AppUnderTest, username: str, vehicle_ids: list[int], *, admin: bool = False
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
    return response.json()["id"]


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
