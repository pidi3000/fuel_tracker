from fastapi import APIRouter

from app.api import (
    auth,
    events,
    fuel_ups,
    history,
    notifications,
    receipts,
    reference,
    settings,
    system,
    users,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(reference.router)
api_router.include_router(fuel_ups.router)
api_router.include_router(notifications.router)
api_router.include_router(receipts.router)
api_router.include_router(events.router)
api_router.include_router(history.router)
api_router.include_router(settings.router)
