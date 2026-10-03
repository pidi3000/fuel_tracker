"""In-process event bus: tells connected web UIs (over SSE) that something changed."""

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str  # e.g. "fuel_up", "receipt", "notification"
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def publish(self, type: str, **data: Any) -> None:
        event = Event(type, data)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A client that doesn't keep up reloads everything when it reconnects
                self._subscribers.discard(queue)

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
