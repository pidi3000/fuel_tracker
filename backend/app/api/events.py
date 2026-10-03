"""Live updates for the web UI over Server-Sent Events (SSE)."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, ServicesDep
from app.services.events import EventBus

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 15


async def event_stream(bus: EventBus, heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    """SSE text for each event. Events only carry ids; the client loads the data through the API,
    which checks what the user may see."""
    with bus.subscribe() as queue:
        # Tells the browser to reconnect quickly, and lets it know the stream is open
        yield "retry: 3000\n\n"
        yield "event: hello\ndata: {}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                yield ": ping\n\n"  # keeps proxies from closing an idle connection
                continue
            yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"


@router.get("/events")
async def events(request: Request, _: CurrentUser, services: ServicesDep) -> StreamingResponse:
    return StreamingResponse(
        event_stream(services.events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx and similar proxies not to hold the stream back
            "X-Accel-Buffering": "no",
        },
    )
