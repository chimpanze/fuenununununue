from __future__ import annotations

"""WebSocket helpers for cross-thread, real-time event delivery.

This module provides a thin, thread-safe bridge for sending messages to
connected WebSocket clients from the background game loop thread and
synchronous ECS systems.

Design:
- FastAPI runs on an asyncio event loop. We capture that loop at app startup
  via set_loop() and store it here.
- Producers (systems, GameWorld) call send_to_user(user_id, payload) from any
  thread. We schedule the actual coroutine onto the captured loop using
  asyncio.run_coroutine_threadsafe.
- We lazily import ws_manager from src.api.routes at call time to avoid
  circular imports at module import time.

Payload contract:
- Each message is a JSON-serializable dict and SHOULD include a 'type' key.
- Examples: {"type": "resource_update", ...}, {"type": "building_complete", ...}
"""

from typing import Optional, Dict, Any
import asyncio
import logging

logger = logging.getLogger(__name__)

# Captured asyncio loop used by FastAPI app
_loop: Optional[asyncio.AbstractEventLoop] = None

def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Record the running asyncio loop for thread-safe scheduling."""
    global _loop
    _loop = loop
    try:
        logger.info("ws_loop_set")
    except Exception:
        pass

async def _send_to_user_async(user_id: int, message: Dict[str, Any]) -> None:
    # Lazy import to avoid cycles
    try:
        from src.api.routes import ws_manager  # type: ignore
    except Exception:
        return
    try:
        await ws_manager.send_to_user(int(user_id), dict(message))
    except Exception:
        # Avoid raising from background contexts
        try:
            logger.exception("ws_send_failed user_id=%s", user_id)
        except Exception:
            pass

def send_to_user(user_id: int, message: Dict[str, Any]) -> None:
    """Thread-safe fire-and-forget send to a specific user.

    Safe to call from any thread. If the event loop is not available yet,
    the message is dropped silently (best-effort semantics).
    """
    if _loop is None:
        return
    try:
        # Schedule task creation on the target loop thread to avoid creating
        # coroutine objects in this thread (which could lead to 'never awaited'
        # warnings if scheduling fails).
        uid = int(user_id)
        payload = dict(message)

        def _schedule() -> None:
            try:
                asyncio.create_task(_send_to_user_async(uid, payload))
            except Exception:
                try:
                    logger.exception("ws_create_task_failed user_id=%s", uid)
                except Exception:
                    pass

        _loop.call_soon_threadsafe(_schedule)
    except Exception:
        # Do not propagate errors to producers
        try:
            logger.debug("ws_schedule_failed user_id=%s", user_id)
        except Exception:
            pass

__all__ = ["set_loop", "send_to_user"]
