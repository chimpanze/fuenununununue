from __future__ import annotations

from src.core.time_utils import utc_now, ensure_aware_utc
import esper
import logging

from src.models import ShipBuildQueue, Fleet
from src.core.sync import complete_next_ship_build, upsert_fleet

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

            current_build = ship_queue.items[0]
            completion_time = ensure_aware_utc(current_build.get("completion_time"))
            if not completion_time:
                # Malformed item; drop it to avoid blocking the queue
                ship_queue.items.pop(0)
                continue
            current_build["completion_time"] = completion_time

            if current_time >= completion_time:
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

                # Persist completion to DB best-effort
                try:
                    complete_next_ship_build(self.world, ent)
                except Exception:
                    pass

                # Persist updated fleet counts best-effort
                try:
                    upsert_fleet(self.world, ent)
                except Exception:
                    pass

                try:
                    logger.info(
                        "ship_build_complete",
                        extra={
                            "action_type": "ship_build_complete",
                            "entity": ent,
                            "ship_type": ship_type,
                            "count": count,
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                except Exception:
                    pass
