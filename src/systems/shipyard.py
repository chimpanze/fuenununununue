from __future__ import annotations

from src.core.time_utils import utc_now, ensure_aware_utc
import esper
import logging

from src.models import ShipBuildQueue, Fleet
from src.core.sync import complete_next_ship_build, upsert_fleet
from src.core.metrics import metrics
from src.core.notifications import create_notification

logger = logging.getLogger(__name__)


class ShipyardSystem(esper.Processor):
    """ECS processor that completes pending ship constructions once their timers elapse.

    Mirrors the behavior of BuildingConstructionSystem but operates on fleets.
    Each queue item must contain:
      - 'type': ship type (e.g., 'light_fighter')
      - 'count': number of ships to add (defaults to 1)
      - 'completion_time': datetime when ships finish
    Optional:
      - 'cost': resource cost (for visibility; deduction occurs at enqueue time elsewhere)
    """

    def process(self) -> None:
        current_time = utc_now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)

        for ent, (ship_queue, fleet) in getter(ShipBuildQueue, Fleet):
            if not ship_queue.items:
                continue

            completed_batch = []

            # Process all items that are due at this tick
            while ship_queue.items:
                current_build = ship_queue.items[0]
                completion_time = ensure_aware_utc(current_build.get("completion_time"))
                if not completion_time:
                    # Malformed item; drop it to avoid blocking the queue
                    ship_queue.items.pop(0)
                    continue
                current_build["completion_time"] = completion_time

                if current_time < completion_time:
                    break

                ship_type = current_build.get("type")
                count = int(current_build.get("count", 1))

                if ship_type and hasattr(fleet, ship_type):
                    try:
                        current = int(getattr(fleet, ship_type))
                        setattr(fleet, ship_type, current + max(0, count))
                    except Exception:
                        # Keep processing even if attribute issues occur
                        pass

                # Remove completed item from queue
                ship_queue.items.pop(0)
                completed_batch.append({
                    "type": ship_type,
                    "count": count,
                    "queued_at": current_build.get("queued_at"),
                    "completion_time": completion_time,
                })

                # Persist completion to DB best-effort (per item)
                try:
                    complete_next_ship_build(self.world, ent)
                except Exception:
                    pass

            if completed_batch:
                # Persist updated fleet counts best-effort
                try:
                    upsert_fleet(self.world, ent)
                except Exception:
                    pass

                # Attempt to send a single WS notification with batched items
                try:
                    from src.models import Player as _P
                    for p_ent, (player,) in getter(_P):
                        if p_ent == ent:
                            user_id = getattr(player, "user_id", None)
                            break
                    else:
                        user_id = None
                    if user_id is not None:
                        try:
                            from src.api.ws import send_to_user
                            send_to_user(int(user_id), {
                                "type": "ship_build_complete_batch",
                                "entity": ent,
                                "items": completed_batch,
                                "timestamp": utc_now().isoformat(),
                            })
                        except Exception:
                            pass
                        # Offline notification (best-effort)
                        try:
                            create_notification(int(user_id), "ship_build_complete", {
                                "items": completed_batch,
                            }, priority="info")
                        except Exception:
                            pass
                except Exception:
                    pass

                # Record actual durations for each completed item, if queued_at present
                try:
                    for item in completed_batch:
                        q_at = item.get("queued_at")
                        if q_at is not None:
                            q_at = ensure_aware_utc(q_at)
                            duration_s = max(0.0, (current_time - q_at).total_seconds())
                            metrics.record_timer("queue.ship.actual_s", float(duration_s))
                        metrics.increment_event("queue.ship.completed", int(item.get("count", 1)))
                except Exception:
                    pass

                try:
                    # Also log one combined event
                    total_count = sum(int(it.get("count", 0)) for it in completed_batch)
                    types = [it.get("type") for it in completed_batch]
                    logger.info(
                        "ship_build_complete_batch",
                        extra={
                            "action_type": "ship_build_complete_batch",
                            "entity": ent,
                            "types": ",".join([t for t in types if t]),
                            "total_count": total_count,
                            "items": str(completed_batch),
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                except Exception:
                    pass
