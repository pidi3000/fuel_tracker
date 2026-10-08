"""User management (admins only)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.auth import Password, Username, UserOut, validate_username
from app.api.deps import AdminUser, SessionDep
from app.models import Role, User
from app.services import auth as auth_service

router = APIRouter(prefix="/users", tags=["users"])

VehicleIds = Annotated[list[int], Field(max_length=200)]


class UserCreateIn(BaseModel):
    username: Username
    password: Password
    role: Role = Role.USER
    vehicle_ids: VehicleIds = []

    _username = field_validator("username")(validate_username)


class UserUpdateIn(BaseModel):
    """Only the fields that are sent are changed. (Users set their own email address.)"""

    role: Role | None = None
    is_active: bool | None = None
    password: Password | None = None
    vehicle_ids: VehicleIds | None = None


async def _get_user(session: SessionDep, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user.")
    return user


@router.get("")
async def list_users(_: AdminUser, session: SessionDep) -> list[UserOut]:
    result = await session.execute(select(User).order_by(User.username))
    return [UserOut.from_user(u) for u in result.scalars()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreateIn, _: AdminUser, session: SessionDep) -> UserOut:
    user = await auth_service.create_user(
        session,
        username=body.username,
        password=body.password,
        role=body.role,
        vehicle_ids=body.vehicle_ids,
    )
    await session.commit()
    return UserOut.from_user(user)


@router.get("/{user_id}")
async def get_user(user_id: int, _: AdminUser, session: SessionDep) -> UserOut:
    return UserOut.from_user(await _get_user(session, user_id))


@router.patch("/{user_id}")
async def update_user(
    user_id: int, body: UserUpdateIn, admin: AdminUser, session: SessionDep
) -> UserOut:
    user = await _get_user(session, user_id)
    if user.id == admin.id and (body.is_active is False or body.role == Role.USER):
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove your own admin access.")
    await auth_service.update_user(
        session,
        user,
        role=body.role,
        is_active=body.is_active,
        password=body.password,
        vehicle_ids=body.vehicle_ids,
    )
    await session.commit()
    return UserOut.from_user(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, admin: AdminUser, session: SessionDep) -> None:
    user = await _get_user(session, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't delete yourself.")
    await auth_service.delete_user(session, user)
    await session.commit()
