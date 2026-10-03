"""First-run setup, login and logout, the current user and personal API tokens."""

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import SESSION_COOKIE, CurrentUser, SessionDep, SettingsDep
from app.core.security import verify_password
from app.models import Role, User
from app.services import auth as auth_service
from app.services.ratelimit import FailureLimiter

router = APIRouter(tags=["auth"])

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{3,50}$")
MIN_PASSWORD_LENGTH = 8

Username = Annotated[str, Field(min_length=3, max_length=50)]
Password = Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)]


def validate_username(value: str) -> str:
    value = value.strip()
    if not USERNAME_PATTERN.match(value):
        raise ValueError("Use 3-50 letters, digits and . _ - @")
    return value


def validate_email(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if len(value) > 254 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        raise ValueError("Not a valid email address")
    return value


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    role: Role
    is_active: bool
    vehicle_ids: list[int]
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            role=Role(user.role),
            is_active=user.is_active,
            vehicle_ids=user.vehicle_ids,
            created_at=user.created_at,
        )


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupIn(BaseModel):
    username: Username
    password: Password
    email: str | None = None

    _username = field_validator("username")(validate_username)
    _email = field_validator("email")(validate_email)


class LoginIn(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: Password


class TokenCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Give the token a name")
        return value


class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None


class TokenCreated(TokenOut):
    token: str


def set_session_cookie(request: Request, response: Response, token: str, days: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=days * 86400,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def get_limiter(request: Request) -> FailureLimiter:
    return request.app.state.login_limiter


@router.get("/setup")
async def setup_status(session: SessionDep) -> SetupStatus:
    return SetupStatus(needs_setup=await auth_service.count_users(session) == 0)


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    body: SetupIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    """Create the first user, who becomes the admin. Only works while no user exists."""
    if await auth_service.count_users(session) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup is already done.")
    user = await auth_service.create_user(
        session, username=body.username, password=body.password, role=Role.ADMIN, email=body.email
    )
    token = await auth_service.create_session(session, user, settings.session_days)
    await session.commit()
    set_session_cookie(request, response, token, settings.session_days)
    return UserOut.from_user(user)


@router.post("/auth/login")
async def login(
    body: LoginIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    limiter = get_limiter(request)
    keys = [f"ip:{client_key(request)}", f"user:{body.username.lower()}"]
    if wait := max(limiter.retry_after(key) for key in keys):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed attempts. Try again later.",
            headers={"Retry-After": str(wait)},
        )
    user = await auth_service.authenticate(session, body.username, body.password)
    if user is None:
        for key in keys:
            limiter.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password.")
    limiter.reset(keys[1])
    token = await auth_service.create_session(session, user, settings.session_days)
    await auth_service.delete_expired_sessions(session)
    await session.commit()
    set_session_cookie(request, response, token, settings.session_days)
    return UserOut.from_user(user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: SessionDep) -> None:
    if cookie := request.cookies.get(SESSION_COOKIE):
        await auth_service.delete_session(session, cookie)
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.from_user(user)


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeIn, request: Request, user: CurrentUser, session: SessionDep
) -> None:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is wrong.")
    # Sign out all other browsers, keep this one
    await auth_service.set_password(
        session, user, body.new_password, keep_token=request.cookies.get(SESSION_COOKIE)
    )
    await session.commit()


@router.get("/tokens")
async def list_tokens(user: CurrentUser, session: SessionDep) -> list[TokenOut]:
    tokens = await auth_service.list_api_tokens(session, user)
    return [TokenOut.model_validate(t, from_attributes=True) for t in tokens]


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_token(body: TokenCreateIn, user: CurrentUser, session: SessionDep) -> TokenCreated:
    """Create a token for the Shortcut or other clients. The token is only shown here, once."""
    record, token = await auth_service.create_api_token(session, user, body.name)
    await session.commit()
    return TokenCreated(
        id=record.id,
        name=record.name,
        created_at=record.created_at,
        last_used_at=None,
        token=token,
    )


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_token(token_id: int, user: CurrentUser, session: SessionDep) -> None:
    if not await auth_service.delete_api_token(session, user, token_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such token.")
    await session.commit()
