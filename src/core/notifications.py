from __future__ import annotations

"""Notification storage service (DB-backed with in-memory fallback).

This module provides a minimal API to create and retrieve notifications for
players. It always records notifications in an in-memory ring buffer to support
lightweight unit tests and disabled-DB environments. When the async database
layer is available, it also persists notifications to the SQL database.

Design notes:
- Synchronous wrapper create_notification() schedules an async insert if an
  event loop is already running, otherwise runs it with asyncio.run().
- Errors in the DB path are swallowed after logging; in-memory storage is the
  source of truth for tests in environments without DB deps.
- Payloads must be JSON-serializable.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Optional DB imports guarded for environments without SQLAlchemy/greenlet
try:
    from sqlalchemy.exc import SQLAlchemyError  # type: ignore
    from src.core.database import SessionLocal, is_db_enabled  # type: ignore
    from src.models.database import Notification as ORMNotification  # type: ignore
    _DB_AVAILABLE = is_db_enabled()
except Exception:  # pragma: no cover
    _DB_AVAILABLE = False
    SessionLocal = None  # type: ignore
    ORMNotification = None  # type: ignore

# In-memory store: ring buffer per user
_MAX_PER_USER = 100
_inmem: Dict[int, List[Dict[str, Any]]] = {}


async def _insert_notification_async(user_id: int, ntype: str, payload: Dict[str, Any], priority: str, created_at: datetime) -> None:
    if not _DB_AVAILABLE:
        return
    try:
        async with SessionLocal() as session:  # type: ignore[misc]
            n = ORMNotification(  # type: ignore[call-arg]
                user_id=int(user_id),
                type=str(ntype),
                payload=dict(payload or {}),
                priority=str(priority or "normal"),
                created_at=created_at,
                read_at=None,
            )
            session.add(n)
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover - env dependent
        try:
            logger.warning("notification_db_insert_failed user_id=%s type=%s err=%s", user_id, ntype, exc)
        except Exception:
            pass
    except Exception:  # pragma: no cover
        try:
            logger.debug("notification_db_insert_unknown_error user_id=%s type=%s", user_id, ntype)
        except Exception:
            pass


def _append_in_memory(user_id: int, record: Dict[str, Any]) -> None:
    bucket = _inmem.setdefault(int(user_id), [])
    bucket.append(record)
    # Trim to max size
    if len(bucket) > _MAX_PER_USER:
        del bucket[0 : len(bucket) - _MAX_PER_USER]


def create_notification(user_id: int, ntype: str, payload: Optional[Dict[str, Any]] = None, priority: str = "normal") -> Dict[str, Any]:
    """Create and store a notification for a user.

    Returns the in-memory record for convenience/testing.
    """
    created_at = datetime.now(timezone.utc)
    rec = {
        "id": None,  # populated by DB if needed; in-memory remains None
        "user_id": int(user_id),
        "type": str(ntype),
        "payload": dict(payload or {}),
        "priority": str(priority or "normal"),
        "created_at": created_at.isoformat(),
        "read_at": None,
    }
    # Always append to in-memory for tests and offline retrieval fallback
    try:
        _append_in_memory(user_id, rec)
    except Exception:
        pass

    # Best-effort DB persistence
    if _DB_AVAILABLE:
        try:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_insert_notification_async(user_id, ntype, payload or {}, priority, created_at))
            except RuntimeError:
                asyncio.run(_insert_notification_async(user_id, ntype, payload or {}, priority, created_at))
        except Exception:  # pragma: no cover
            try:
                logger.debug("notification_schedule_failed user_id=%s type=%s", user_id, ntype)
            except Exception:
                pass

    return rec


def get_in_memory_notifications(user_id: int, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    data = list(_inmem.get(int(user_id), []))
    if offset < 0:
        offset = 0
    if limit <= 0:
        return []
    return data[offset : offset + limit]


def clear_in_memory_notifications(user_id: Optional[int] = None) -> None:
    if user_id is None:
        _inmem.clear()
    else:
        _inmem.pop(int(user_id), None)


__all__ = [
    "create_notification",
    "get_in_memory_notifications",
    "clear_in_memory_notifications",
]
