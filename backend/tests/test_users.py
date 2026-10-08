from fastapi.testclient import TestClient

from tests.test_auth import setup_admin


def make_user(client: TestClient, username: str = "bob", **extra) -> dict:
    response = client.post(
        "/api/users", json={"username": username, "password": "bobs password", **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def login_as(client: TestClient, username: str, password: str = "bobs password") -> None:
    client.post("/api/auth/logout")
    assert (
        client.post(
            "/api/auth/login", json={"username": username, "password": password}
        ).status_code
        == 200
    )


def test_requires_login_and_admin(client: TestClient) -> None:
    assert client.get("/api/users").status_code == 401
    setup_admin(client)
    make_user(client)
    login_as(client, "bob")
    assert client.get("/api/users").status_code == 403
    assert (
        client.post("/api/users", json={"username": "eve", "password": "evil password"}).status_code
        == 403
    )


def test_create_list_and_vehicle_access(client: TestClient) -> None:
    setup_admin(client)
    bob = make_user(client, vehicle_ids=[3, 1, 3])
    assert bob["role"] == "user" and bob["vehicle_ids"] == [1, 3]
    assert bob["email"] is None  # users set their own address

    assert [u["username"] for u in client.get("/api/users").json()] == ["alice", "bob"]

    updated = client.patch(f"/api/users/{bob['id']}", json={"vehicle_ids": [2, 3]}).json()
    assert updated["vehicle_ids"] == [2, 3]
    assert updated["role"] == "user"  # untouched fields stay

    cleared = client.patch(f"/api/users/{bob['id']}", json={"vehicle_ids": []}).json()
    assert cleared["vehicle_ids"] == []


def test_usernames_are_unique_ignoring_case(client: TestClient) -> None:
    setup_admin(client)
    make_user(client, "bob")
    response = client.post("/api/users", json={"username": "BOB", "password": "another password"})
    assert response.status_code == 409


def test_cannot_remove_the_last_admin(client: TestClient) -> None:
    admin = setup_admin(client)
    # Not yourself
    assert client.patch(f"/api/users/{admin['id']}", json={"role": "user"}).status_code == 409
    assert client.patch(f"/api/users/{admin['id']}", json={"is_active": False}).status_code == 409
    assert client.delete(f"/api/users/{admin['id']}").status_code == 409

    # A second admin can demote or remove the first, but not the last one standing
    carol = make_user(client, "carol", role="admin")
    login_as(client, "carol")
    assert client.patch(f"/api/users/{admin['id']}", json={"role": "user"}).status_code == 200
    login_as(client, "alice", "correct horse")
    assert client.patch(f"/api/users/{carol['id']}", json={"role": "user"}).status_code == 403


def test_last_admin_guard_in_service(client: TestClient) -> None:
    admin = setup_admin(client)
    other = make_user(client, "dave", role="admin")
    login_as(client, "dave")
    # Deactivating the only other admin (alice) is fine while dave stays active
    assert client.patch(f"/api/users/{admin['id']}", json={"is_active": False}).status_code == 200
    # ... but dave cannot demote himself, being the last admin
    assert client.patch(f"/api/users/{other['id']}", json={"role": "user"}).status_code == 409


def test_admin_resets_password_and_signs_user_out(client: TestClient) -> None:
    setup_admin(client)
    bob = make_user(client)
    bob_client = TestClient(client.app)
    bob_client.post("/api/auth/login", json={"username": "bob", "password": "bobs password"})
    assert bob_client.get("/api/auth/me").status_code == 200

    client.patch(f"/api/users/{bob['id']}", json={"password": "reset password"})
    assert bob_client.get("/api/auth/me").status_code == 401
    login_as(client, "bob", "reset password")


def test_delete_user(client: TestClient) -> None:
    setup_admin(client)
    bob = make_user(client)
    assert client.delete(f"/api/users/{bob['id']}").status_code == 204
    assert client.get(f"/api/users/{bob['id']}").status_code == 404
    assert client.delete("/api/users/999").status_code == 404
    assert [u["username"] for u in client.get("/api/users").json()] == ["alice"]
