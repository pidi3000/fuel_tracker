from fastapi import APIRouter

from app.api import auth, fuel_ups, notifications, reference, system, users

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(reference.router)
api_router.include_router(fuel_ups.router)
api_router.include_router(notifications.router)
