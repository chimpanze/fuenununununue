from __future__ import annotations

from src.core.time_utils import utc_now, ensure_aware_utc
import esper
import logging

from src.models import ResearchQueue, Research, Player
from src.core.sync import complete_next_research

logger = logging.getLogger(__name__)


class ResearchSystem(esper.Processor):
    """ECS processor that completes pending research once their timers elapse.

    This system inspects each entity with a ResearchQueue and Research components.
    When the head-of-queue item has a completion_time in the past, it increments
    the corresponding research level and removes the item from the queue.
    """

    def process(self) -> None:
        current_time = utc_now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)
        for ent, (rq, research) in getter(ResearchQueue, Research):
            if not rq.items:
                continue

            current_item = rq.items[0]
            ct = ensure_aware_utc(current_item.get("completion_time"))
            if not ct:
                # Invalid item; drop it
                rq.items.pop(0)
                continue
            current_item["completion_time"] = ct
            if current_time >= ct:
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
                                "timestamp": utc_now().isoformat(),
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

                # Persist completion in DB (best-effort)
                try:
                    complete_next_research(self.world, ent)
                except Exception:
                    pass

                # Best-effort: fetch player once and reuse for WS + notification
                try:
                    player = self.world.component_for_entity(ent, Player)
                    user_id = int(getattr(player, 'user_id', 0))
                except Exception:
                    user_id = 0

                # Emit real-time research completion to owning user (best-effort)
                if user_id:
                    try:
                        from src.api.ws import send_to_user as _ws_send
                        _ws_send(user_id, {
                            "type": "research_complete",
                            "research_type": research_type,
                            "new_level": int(new_level),
                            "ts": current_time.isoformat(),
                        })
                    except Exception:
                        pass

                # Persist offline notification store with info priority (best-effort)
                if user_id:
                    try:
                        from src.core.notifications import create_notification as _notify
                        _notify(user_id, "research_complete", {
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
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                except Exception:
                    pass
