from __future__ import annotations

from datetime import datetime
import esper
import logging

from src.models import BuildQueue, Resources, Buildings, Player
from src.core.sync import sync_building_level
from src.api.ws import send_to_user
from src.core.notifications import create_notification

logger = logging.getLogger(__name__)


class BuildingConstructionSystem(esper.Processor):
    """ECS processor that completes pending building constructions once their timers elapse."""

    def process(self) -> None:
        """Run one tick of the building construction system."""
        current_time = datetime.now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)
        for ent, (build_queue, resources, buildings) in getter(
            BuildQueue, Resources, Buildings
        ):
            if not build_queue.items:
                continue

            current_build = build_queue.items[0]

            # Check if construction is complete
            if current_time >= current_build['completion_time']:
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

                # Emit real-time building completion to owning user (best-effort)
                try:
                    from src.models import Player as _P
                    player = self.world.component_for_entity(ent, _P)
                    user_id = int(getattr(player, 'user_id', 0))
                    if user_id:
                        send_to_user(user_id, {
                            "type": "building_complete",
                            "building_type": building_type,
                            "new_level": int(new_level),
                            "ts": datetime.now().isoformat(),
                        })
                except Exception:
                    pass

                # Persist offline notification store (best-effort)
                try:
                    from src.models import Player as _P2
                    player2 = self.world.component_for_entity(ent, _P2)
                    uid2 = int(getattr(player2, 'user_id', 0))
                    if uid2:
                        create_notification(uid2, "building_complete", {
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
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
