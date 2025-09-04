from __future__ import annotations

from typing import TypedDict, Optional, Dict, Any, List
from datetime import datetime
import logging

from src.core.database import is_db_enabled
from src.core.metrics import metrics

logger = logging.getLogger(__name__)


class TradeEventPayload(TypedDict, total=False):
    # Standardized event shape
    type: str  # "offer_created" | "trade_completed"
    offer_id: int
    seller_user_id: int
    buyer_user_id: Optional[int]
    offered_resource: str
    offered_amount: int
    requested_resource: str
    requested_amount: int
    status: str  # "open" | "completed"
    timestamp: str  # ISO8601


def _emit_ws_to_participants(payload: Dict[str, Any]) -> None:
    """Best-effort WebSocket emission to seller/buyer.
    Adds type="trade_event" for WS channel.
    """
    try:
        from src.api.ws import send_to_user as _ws_send  # lazy import
        seller_id = payload.get("seller_user_id")
        buyer_id = payload.get("buyer_user_id")
        event = dict(payload)
        event["type"] = "trade_event"
        if seller_id:
            _ws_send(int(seller_id), event)
        if buyer_id:
            _ws_send(int(buyer_id), event)
    except Exception:
        # Never raise from background contexts
        try:
            logger.debug("trade_ws_emit_failed")
        except Exception:
            pass


async def record_trade_event(event: TradeEventPayload, session=None) -> Dict[str, Any]:
    """Record a trade event in DB when enabled; otherwise append to in-memory.

    Returns the recorded event dict with id/timestamp populated when possible.
    """
    if is_db_enabled() and session is not None:
        try:
            from src.models.database import TradeEvent as ORMTradeEvent  # type: ignore
            # Construct ORM row
            row = ORMTradeEvent(
                type=str(event.get("type")),
                offer_id=int(event.get("offer_id")),
                seller_user_id=int(event.get("seller_user_id")),
                buyer_user_id=int(event.get("buyer_user_id")) if event.get("buyer_user_id") is not None else None,
                offered_resource=str(event.get("offered_resource")),
                offered_amount=int(event.get("offered_amount")),
                requested_resource=str(event.get("requested_resource")),
                requested_amount=int(event.get("requested_amount")),
                status=str(event.get("status")),
            )
            session.add(row)
            await session.commit()
            payload: Dict[str, Any] = {
                "id": int(row.id),
                "type": row.type,
                "offer_id": int(row.offer_id),
                "seller_user_id": int(row.seller_user_id) if row.seller_user_id is not None else None,
                "buyer_user_id": int(row.buyer_user_id) if row.buyer_user_id is not None else None,
                "offered_resource": row.offered_resource,
                "offered_amount": int(row.offered_amount),
                "requested_resource": row.requested_resource,
                "requested_amount": int(row.requested_amount),
                "status": row.status,
                "timestamp": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
            }
            # Emit WS (best-effort)
            _emit_ws_to_participants(payload)
            try:
                logger.info(
                    "trade_event_recorded_db",
                    extra={
                        "action_type": payload.get("type"),
                        "event_id": payload.get("id"),
                        "offer_id": payload.get("offer_id"),
                        "seller_user_id": payload.get("seller_user_id"),
                        "buyer_user_id": payload.get("buyer_user_id"),
                        "timestamp": payload.get("timestamp"),
                    },
                )
            except Exception:
                pass
            metrics.increment_event("db.trade_event_recorded")
            return payload
        except Exception:
            # Fall through to in-memory as a safety net
            try:
                logger.warning("trade_event_db_failed_fallback_inmem", exc_info=True)
            except Exception:
                pass
    # In-memory path
    return record_trade_event_sync(event)


def record_trade_event_sync(event: TradeEventPayload, gw=None) -> Dict[str, Any]:
    """Synchronous in-memory event recording. Used by GameWorld and as DB fallback.

    If gw is None, attempt to use the shared singleton from src.core.state.
    """
    try:
        if gw is None:
            from src.core.state import game_world as gw  # type: ignore
    except Exception:
        gw = None
    payload = dict(event or {})
    # Assign monotonically increasing id
    try:
        eid = int(gw._next_trade_event_id)  # type: ignore[attr-defined]
        gw._next_trade_event_id += 1  # type: ignore[attr-defined]
    except Exception:
        eid = 1
        try:
            if gw is not None:
                gw._next_trade_event_id = 2  # type: ignore[attr-defined]
        except Exception:
            pass
    payload["id"] = eid
    if "timestamp" not in payload:
        payload["timestamp"] = datetime.now().isoformat()
    try:
        gw._trade_history.append(payload)  # type: ignore[attr-defined]
    except Exception:
        # If no gw provided/available, just return the payload
        pass
    try:
        logger.info(
            "trade_event_recorded_inmem",
            extra={
                "action_type": payload.get("type"),
                "event_id": eid,
                "offer_id": payload.get("offer_id"),
                "seller_user_id": payload.get("seller_user_id"),
                "buyer_user_id": payload.get("buyer_user_id"),
                "timestamp": payload.get("timestamp"),
            },
        )
    except Exception:
        pass
    # Emit WS (best-effort)
    _emit_ws_to_participants(payload)
    try:
        metrics.increment_event("inmem.trade_event_recorded")
    except Exception:
        pass
    return payload


async def list_trade_history(user_id: int, limit: int = 50, offset: int = 0, session=None, gw=None) -> List[Dict[str, Any]]:
    """Unified trade history listing.

    Prefers DB when available and session is provided; otherwise uses in-memory history from gw (or the shared singleton).
    Returns newest-first.
    """
    if is_db_enabled() and session is not None:
        try:
            from sqlalchemy import select, or_  # type: ignore
            from src.models.database import TradeEvent as ORMTradeEvent  # type: ignore
            stmt = (
                select(ORMTradeEvent)
                .where(or_(ORMTradeEvent.seller_user_id == int(user_id), ORMTradeEvent.buyer_user_id == int(user_id)))
                .order_by(ORMTradeEvent.created_at.desc())
                .offset(int(offset))
                .limit(int(limit))
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": int(e.id),
                    "type": e.type,
                    "offer_id": int(e.offer_id),
                    "seller_user_id": int(e.seller_user_id) if e.seller_user_id is not None else None,
                    "buyer_user_id": int(e.buyer_user_id) if e.buyer_user_id is not None else None,
                    "offered_resource": e.offered_resource,
                    "offered_amount": int(e.offered_amount),
                    "requested_resource": e.requested_resource,
                    "requested_amount": int(e.requested_amount),
                    "status": e.status,
                    "timestamp": e.created_at.isoformat() if getattr(e, "created_at", None) else None,
                }
                for e in rows
            ]
        except Exception:
            # Fallback to in-memory
            pass
    # In-memory path
    return list_trade_history_in_memory(user_id, limit=limit, offset=offset, gw=gw)


def list_trade_history_in_memory(user_id: int, limit: int = 50, offset: int = 0, gw=None) -> List[Dict[str, Any]]:
    try:
        if gw is None:
            from src.core.state import game_world as gw  # type: ignore
    except Exception:
        gw = None
    try:
        uid = int(user_id)
    except Exception:
        return []
    try:
        history = list(getattr(gw, "_trade_history", []))
    except Exception:
        history = []
    relevant = [e for e in reversed(history) if e.get("seller_user_id") == uid or e.get("buyer_user_id") == uid]
    start = max(0, int(offset))
    end = max(start, start + int(limit))
    return [dict(e) for e in relevant[start:end]]


__all__ = [
    "TradeEventPayload",
    "record_trade_event",
    "record_trade_event_sync",
    "list_trade_history",
    "list_trade_history_in_memory",
]
