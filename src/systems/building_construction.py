from __future__ import annotations

from datetime import datetime
from src.core.time_utils import utc_now, ensure_aware_utc
import esper
import logging

from src.models import BuildQueue, Resources, Buildings, Player
from src.core.sync import sync_building_level, complete_next_build_queue
from src.api.ws import send_to_user
from src.core.notifications import create_notification

logger = logging.getLogger(__name__)


class BuildingConstructionSystem(esper.Processor):
    """ECS processor that completes pending building constructions once their timers elapse."""

    def process(self) -> None:
        """Run one tick of the building construction system."""
        current_time = utc_now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)
        for ent, (build_queue, resources, buildings) in getter(
            BuildQueue, Resources, Buildings
        ):
            if not build_queue.items:
                continue

            current_build = build_queue.items[0]

            # Normalize and validate completion_time
            ct = ensure_aware_utc(current_build.get('completion_time'))
            if not ct:
                # Malformed item; drop it to avoid blocking the queue
                build_queue.items.pop(0)
                continue
            current_build['completion_time'] = ct

            # Check if construction is complete
            if current_time >= ct:
                building_type = current_build['type']

                # Complete the construction
                if hasattr(buildings, building_type):
                    current_level = getattr(buildings, building_type)
                    new_level = current_level + 1
                    setattr(buildings, building_type, new_level)
                    # Persist building level best-effort
                    try:
                        sync_building_level(self.world, ent, building_type, new_level)
                    except Exception:
                        pass

                # Remove completed item from queue
                build_queue.items.pop(0)

                # Persist completion in DB (best-effort)
                try:
                    complete_next_build_queue(self.world, ent)
                except Exception:
                    pass

                # Best-effort: fetch player once and reuse for WS + notification
                try:
                    player = self.world.component_for_entity(ent, Player)
                    user_id = int(getattr(player, 'user_id', 0))
                except Exception:
                    user_id = 0

                # Emit real-time building completion to owning user (best-effort)
                if user_id:
                    try:
                        send_to_user(user_id, {
                            "type": "building_complete",
                            "building_type": building_type,
                            "new_level": int(new_level),
                            "ts": current_time.isoformat(),
                        })
                    except Exception:
                        pass

                # Persist offline notification store (best-effort)
                if user_id:
                    try:
                        create_notification(user_id, "building_complete", {
                            "building_type": building_type,
                            "new_level": int(new_level),
                        }, priority="normal")
                    except Exception:
                        pass

                try:
                    logger.info(
                        "build_complete",
                        extra={
                            "action_type": "build_complete",
                            "entity": ent,
                            "building_type": building_type,
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                except Exception:
                    pass
