from __future__ import annotations

from datetime import datetime
import esper
import logging

from src.models import ResearchQueue, Research

logger = logging.getLogger(__name__)


class ResearchSystem(esper.Processor):
    """ECS processor that completes pending research once their timers elapse.

    This system inspects each entity with a ResearchQueue and Research components.
    When the head-of-queue item has a completion_time in the past, it increments
    the corresponding research level and removes the item from the queue.
    """

    def process(self) -> None:
        current_time = datetime.now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)
        for ent, (rq, research) in getter(ResearchQueue, Research):
            if not rq.items:
                continue

            current_item = rq.items[0]
            if current_time >= current_item.get("completion_time", current_time):
                research_type = current_item.get("type")
                if not research_type or not hasattr(research, research_type):
                    # Invalid queue item; drop it to prevent blocking
                    rq.items.pop(0)
                    try:
                        logger.info(
                            "research_item_invalid",
                            extra={
                                "action_type": "research_item_invalid",
                                "entity": ent,
                                "item": str(current_item),
                                "timestamp": datetime.now().isoformat(),
                            },
                        )
                    except Exception:
                        pass
                    continue

                # Apply completion: increment the research level
                current_level = getattr(research, research_type)
                new_level = int(current_level) + 1
                setattr(research, research_type, new_level)

                # Remove completed item from queue
                rq.items.pop(0)

                # Emit real-time research completion to owning user (best-effort)
                try:
                    from src.models import Player as _P
                    from src.api.ws import send_to_user as _ws_send
                    player = self.world.component_for_entity(ent, _P)
                    user_id = int(getattr(player, 'user_id', 0))
                    if user_id:
                        _ws_send(user_id, {
                            "type": "research_complete",
                            "research_type": research_type,
                            "new_level": int(new_level),
                            "ts": datetime.now().isoformat(),
                        })
                except Exception:
                    pass

                # Persist offline notification store with info priority (best-effort)
                try:
                    from src.models import Player as _P2
                    from src.core.notifications import create_notification as _notify
                    player2 = self.world.component_for_entity(ent, _P2)
                    uid2 = int(getattr(player2, 'user_id', 0))
                    if uid2:
                        _notify(uid2, "research_complete", {
                            "research_type": research_type,
                            "new_level": int(new_level),
                        }, priority="info")
                except Exception:
                    pass

                try:
                    logger.info(
                        "research_complete",
                        extra={
                            "action_type": "research_complete",
                            "entity": ent,
                            "research_type": research_type,
                            "new_level": int(new_level),
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
