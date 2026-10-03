from fastapi.testclient import TestClient

from app.core.security import TOKEN_PREFIX
from app.services.ratelimit import FailureLimiter

ADMIN = {"username": "alice", "password": "correct horse"}


def setup_admin(client: TestClient, **extra) -> dict:
    response = client.post("/api/setup", json={**ADMIN, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_setup_status_and_first_user_becomes_admin(client: TestClient) -> None:
    assert client.get("/api/setup").json() == {"needs_setup": True}

    user = setup_admin(client, email="alice@example.com")
    assert user["role"] == "admin"
    assert user["email"] == "alice@example.com"
    assert "password" not in user and "password_hash" not in user

    assert client.get("/api/setup").json() == {"needs_setup": False}
    # Setup signs the new admin in
    assert client.get("/api/auth/me").json()["username"] == "alice"


def test_setup_only_works_once(client: TestClient) -> None:
    setup_admin(client)
    response = client.post("/api/setup", json={"username": "mallory", "password": "password123"})
    assert response.status_code == 409


def test_setup_validates_input(client: TestClient) -> None:
    for body in (
        {"username": "al", "password": "long enough"},
        {"username": "has space", "password": "long enough"},
        {"username": "alice", "password": "short"},
        {"username": "alice", "password": "long enough", "email": "not-an-email"},
    ):
        assert client.post("/api/setup", json=body).status_code == 422, body
    assert client.get("/api/setup").json() == {"needs_setup": True}


def test_login_logout(client: TestClient) -> None:
    setup_admin(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    assert client.post("/api/auth/login", json={**ADMIN, "password": "wrong"}).status_code == 401
    assert (
        client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code
        == 401
    )

    response = client.post(
        "/api/auth/login", json={"username": "ALICE", "password": "correct horse"}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/auth/me").json()["username"] == "alice"

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_secure_cookie_over_https(client: TestClient) -> None:
    setup_admin(client)
    client.post("/api/auth/logout")
    response = client.post("https://testserver/api/auth/login", json=ADMIN)
    assert "Secure" in response.headers["set-cookie"]


def test_login_is_rate_limited(client: TestClient) -> None:
    setup_admin(client)
    client.post("/api/auth/logout")
    client.app.state.login_limiter = FailureLimiter(limit=3, window=60)
    for _ in range(3):
        assert client.post("/api/auth/login", json={**ADMIN, "password": "nope"}).status_code == 401
    blocked = client.post("/api/auth/login", json=ADMIN)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_inactive_user_cannot_log_in(client: TestClient) -> None:
    setup_admin(client)
    bob = client.post("/api/users", json={"username": "bob", "password": "bobs password"}).json()
    client.patch(f"/api/users/{bob['id']}", json={"is_active": False})
    client.post("/api/auth/logout")
    response = client.post("/api/auth/login", json={"username": "bob", "password": "bobs password"})
    assert response.status_code == 401


def test_change_password_keeps_this_browser_only(client: TestClient) -> None:

    setup_admin(client)
    other = TestClient(client.app)
    other.post("/api/auth/login", json=ADMIN)
    assert other.get("/api/auth/me").status_code == 200

    wrong = client.post(
        "/api/auth/password", json={"current_password": "nope", "new_password": "a new password"}
    )
    assert wrong.status_code == 400
    ok = client.post(
        "/api/auth/password",
        json={"current_password": "correct horse", "new_password": "a new password"},
    )
    assert ok.status_code == 204

    assert client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json=ADMIN).status_code == 401
    assert (
        client.post("/api/auth/login", json={**ADMIN, "password": "a new password"}).status_code
        == 200
    )


def test_api_tokens(client: TestClient) -> None:
    setup_admin(client)
    created = client.post("/api/tokens", json={"name": "Shortcut"})
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith(TOKEN_PREFIX)

    # The token is shown once; listing doesn't include it
    listed = client.get("/api/tokens").json()
    assert [t["name"] for t in listed] == ["Shortcut"]
    assert "token" not in listed[0]

    # Use it without a session cookie
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=bearer).json()["username"] == "alice"
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401

    client.post("/api/auth/login", json=ADMIN)
    assert client.get("/api/tokens").json()[0]["last_used_at"] is not None
    token_id = listed[0]["id"]
    assert client.delete(f"/api/tokens/{token_id}").status_code == 204
    assert client.delete(f"/api/tokens/{token_id}").status_code == 404
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me", headers=bearer).status_code == 401


def test_tokens_belong_to_their_owner(client: TestClient) -> None:
    setup_admin(client)
    client.post("/api/users", json={"username": "bob", "password": "bobs password"})
    token_id = client.post("/api/tokens", json={"name": "mine"}).json()["id"]

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "bob", "password": "bobs password"})
    assert client.get("/api/tokens").json() == []
    assert client.delete(f"/api/tokens/{token_id}").status_code == 404


def test_failure_limiter_window() -> None:
    now = [0.0]
    limiter = FailureLimiter(limit=2, window=10, clock=lambda: now[0])
    limiter.record_failure("k")
    assert limiter.retry_after("k") == 0
    limiter.record_failure("k")
    assert limiter.retry_after("k") > 0
    now[0] = 11
    assert limiter.retry_after("k") == 0
