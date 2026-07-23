"""Async SSE activity log bus for real-time UI progress."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Ring buffer so late subscribers still see recent activity
_HISTORY_SIZE = 80
_history: Deque[dict] = deque(maxlen=_HISTORY_SIZE)
_subscribers: Set[asyncio.Queue] = set()
_sub_lock = asyncio.Lock()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def subscribe() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    async with _sub_lock:
        _subscribers.add(queue)
        for event in list(_history):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
    return queue


async def unsubscribe(queue: asyncio.Queue) -> None:
    async with _sub_lock:
        _subscribers.discard(queue)


async def broadcast_log(
    stage: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> dict:
    """Publish a structured activity event to all SSE subscribers."""
    event = {
        "stage": stage,
        "message": message,
        "data": data or {},
        "ts": _utc_iso(),
    }
    _history.append(event)
    logger.info("[%s] %s", stage, message)

    async with _sub_lock:
        dead: List[asyncio.Queue] = []
        for queue in _subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest then retry once so slow clients don't stall pipelines
                try:
                    _ = queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    dead.append(queue)
        for queue in dead:
            _subscribers.discard(queue)
    return event


def format_sse(event: dict) -> str:
    payload = json.dumps(event, ensure_ascii=False)
    return f"event: {event.get('stage', 'message')}\ndata: {payload}\n\n"


async def sse_event_stream(queue: asyncio.Queue) -> AsyncIterator[str]:
    """Yield SSE frames; send heartbeats so proxies keep the connection alive."""
    try:
        yield format_sse(
            {
                "stage": "CONNECTED",
                "message": "Live activity stream connected.",
                "data": {},
                "ts": _utc_iso(),
            }
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
                yield format_sse(event)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        raise
    finally:
        await unsubscribe(queue)
