from __future__ import annotations

from src.core.time_utils import utc_now, ensure_aware_utc
import logging
import esper

from src.models import Fleet, Position, FleetMovement
from src.core.sync import upsert_fleet

logger = logging.getLogger(__name__)


class FleetMovementSystem(esper.Processor):
    """ECS processor that finalizes fleet movements upon arrival.

    This system checks fleets that have an active FleetMovement component and,
    when the arrival time is reached or passed, updates the entity's Position
    to the target coordinates and removes the FleetMovement component.

    Additionally, it emits mission-specific events. For mission 'espionage',
    a scouting report is generated about the target coordinates if occupied.

    For mission 'colonize', colonization completes only after an additional
    colonization duration. The colony ship is consumed on successful completion.
    """

    def process(self) -> None:
        now = utc_now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)

        for ent, (fleet, movement) in getter(Fleet, FleetMovement):
            # Normalize potentially naive timestamps to aware UTC
            try:
                movement.arrival_time = ensure_aware_utc(getattr(movement, "arrival_time", None))
                movement.departure_time = ensure_aware_utc(getattr(movement, "departure_time", None))
            except Exception:
                pass
            mission = str(getattr(movement, "mission", "")).lower()

            # If not yet arrived to current phase ETA, skip
            if now < movement.arrival_time:
                continue

            # Handle colonization as a two-phase mission
            if mission == "colonize" and not bool(getattr(movement, "recalled", False)):
                # First arrival: start colonization timer if not started
                if not hasattr(movement, "colonizing_until"):
                    # Require at least one colony ship to begin colonization
                    try:
                        has_colony_ship = int(getattr(fleet, "colony_ship", 0)) > 0
                    except Exception:
                        has_colony_ship = False

                    if not has_colony_ship:
                        # Abort mission if no colony ship available
                        try:
                            self.world.remove_component(ent, FleetMovement)
                        except Exception:
                            pass
                        # Delete persisted mission best-effort
                        try:
                            from src.core.sync import delete_fleet_mission as _del_mission
                            _del_mission(self.world, ent)
                        except Exception:
                            pass
                        try:
                            logger.info("colonize_aborted_no_ship", extra={
                                "action_type": "colonize_aborted_no_ship",
                                "entity": ent,
                                "owner_id": getattr(movement, "owner_id", None),
                                "target": {
                                    "g": int(movement.target.galaxy),
                                    "s": int(movement.target.system),
                                    "p": int(movement.target.planet),
                                },
                                "timestamp": now.isoformat(),
                            })
                        except Exception:
                            pass
                        continue

                    # Start colonization countdown
                    try:
                        from datetime import timedelta as _td
                        from src.core.config import COLONIZATION_TIME_SECONDS
                        delta = _td(seconds=int(COLONIZATION_TIME_SECONDS))
                        base = movement.arrival_time if hasattr(movement, "arrival_time") else now
                        movement.colonizing_until = base + delta
                        movement.arrival_time = movement.colonizing_until  # reuse arrival_time as phase ETA
                        # If colonization time already elapsed (e.g., long delay), finalize immediately
                        if now >= movement.colonizing_until:
                            target_g = int(movement.target.galaxy)
                            target_s = int(movement.target.system)
                            target_p = int(movement.target.planet)
                            ok = True
                            try:
                                from src.models import Player as _P
                                from src.core.sync import create_colony as _create_colony
                                try:
                                    p = self.world.component_for_entity(ent, _P)
                                    username = getattr(p, "name", "Player")
                                except Exception:
                                    username = "Player"
                                owner_id = int(getattr(movement, "owner_id", 0) or 0)
                                ok = _create_colony(owner_id, username, target_g, target_s, target_p, "Colony")
                            except Exception:
                                ok = True
                            if ok:
                                try:
                                    c = int(getattr(fleet, "colony_ship", 0))
                                    setattr(fleet, "colony_ship", max(0, c - 1))
                                except Exception:
                                    pass
                                # Persist updated fleet counts best-effort
                                try:
                                    upsert_fleet(self.world, ent)
                                except Exception:
                                    pass
                            try:
                                self.world.remove_component(ent, FleetMovement)
                            except Exception:
                                pass
                            # Delete persisted mission best-effort
                            try:
                                from src.core.sync import delete_fleet_mission as _del_mission
                                _del_mission(self.world, ent)
                            except Exception:
                                pass
                            try:
                                logger.info(
                                    "colonize_complete",
                                    extra={
                                        "action_type": "colonize_complete",
                                        "entity": ent,
                                        "owner_id": getattr(movement, "owner_id", None),
                                        "success": bool(ok),
                                        "target": {"g": target_g, "s": target_s, "p": target_p},
                                        "timestamp": now.isoformat(),
                                    },
                                )
                            except Exception:
                                pass
                            continue
                        # Keep fleet position unchanged during colonization phase
                    except Exception:
                        # If we cannot start timer, abort safely
                        try:
                            self.world.remove_component(ent, FleetMovement)
                        except Exception:
                            pass
                    continue
                else:
                    # Colonization countdown completed; attempt to create colony
                    target_g = int(movement.target.galaxy)
                    target_s = int(movement.target.system)
                    target_p = int(movement.target.planet)

                    # Best-effort: create colony (DB-backed when available)
                    ok = True
                    try:
                        from src.models import Player as _P
                        from src.core.sync import create_colony as _create_colony
                        try:
                            p = self.world.component_for_entity(ent, _P)
                            username = getattr(p, "name", "Player")
                        except Exception:
                            username = "Player"
                        owner_id = int(getattr(movement, "owner_id", 0) or 0)
                        ok = _create_colony(owner_id, username, target_g, target_s, target_p, "Colony")
                    except Exception:
                        ok = True  # allow ECS-only success path

                    # Consume colony ship on success
                    if ok:
                        try:
                            c = int(getattr(fleet, "colony_ship", 0))
                            setattr(fleet, "colony_ship", max(0, c - 1))
                        except Exception:
                            pass
                        # Persist updated fleet counts best-effort
                        try:
                            upsert_fleet(self.world, ent)
                        except Exception:
                            pass

                    # Remove movement regardless of success to end mission
                    try:
                        self.world.remove_component(ent, FleetMovement)
                    except Exception:
                        pass
                    # Delete persisted mission best-effort
                    try:
                        from src.core.sync import delete_fleet_mission as _del_mission
                        _del_mission(self.world, ent)
                    except Exception:
                        pass

                    # Log completion
                    try:
                        logger.info(
                            "colonize_complete",
                            extra={
                                "action_type": "colonize_complete",
                                "entity": ent,
                                "owner_id": getattr(movement, "owner_id", None),
                                "success": bool(ok),
                                "target": {"g": target_g, "s": target_s, "p": target_p},
                                "timestamp": now.isoformat(),
                            },
                        )
                    except Exception:
                        pass
                    continue

            # Non-colonize missions: proceed with position update and optional espionage
            # Ensure a Position exists for the entity; create if missing
            try:
                pos = self.world.component_for_entity(ent, Position)
            except Exception:
                pos = Position()
                try:
                    self.world.add_component(ent, pos)
                except Exception:
                    # If add fails, continue to next entity to avoid breaking loop
                    continue

            # Update coordinates to the target
            pos.galaxy = int(movement.target.galaxy)
            pos.system = int(movement.target.system)
            pos.planet = int(movement.target.planet)

            # Mission-specific handling: espionage report on arrival
            try:
                if mission == "espionage":
                    # Late import to avoid heavy top-level deps in minimal environments
                    from src.models import Player as _P, Position as _Pos, Resources as _R, Buildings as _B, Fleet as _F, Planet as _Pl

                    target_g = int(movement.target.galaxy)
                    target_s = int(movement.target.system)
                    target_p = int(movement.target.planet)

                    defender_id = None
                    snapshot = {}
                    try:
                        for dent, (dplayer, dpos) in self.world.get_components(_P, _Pos):
                            if int(dpos.galaxy) == target_g and int(dpos.system) == target_s and int(dpos.planet) == target_p:
                                candidate_uid = int(getattr(dplayer, "user_id", 0) or 0)
                                # Skip self to avoid self-espionage when multiple players share coords in tests
                                if candidate_uid == int(getattr(movement, "owner_id", 0) or 0):
                                    continue
                                defender_id = candidate_uid
                                # Collect optional components
                                res = None
                                bld = None
                                dflt = None
                                pl = None
                                try:
                                    res = self.world.component_for_entity(dent, _R)
                                except Exception:
                                    pass
                                try:
                                    bld = self.world.component_for_entity(dent, _B)
                                except Exception:
                                    pass
                                try:
                                    dflt = self.world.component_for_entity(dent, _F)
                                except Exception:
                                    pass
                                try:
                                    pl = self.world.component_for_entity(dent, _Pl)
                                except Exception:
                                    pass

                                snapshot = {
                                    "planet": {
                                        "name": getattr(pl, "name", None) if pl else None,
                                        "temperature": getattr(pl, "temperature", None) if pl else None,
                                        "size": getattr(pl, "size", None) if pl else None,
                                    },
                                    "resources": {
                                        "metal": int(getattr(res, "metal", 0)) if res else None,
                                        "crystal": int(getattr(res, "crystal", 0)) if res else None,
                                        "deuterium": int(getattr(res, "deuterium", 0)) if res else None,
                                    },
                                    "buildings": {
                                        "metal_mine": int(getattr(bld, "metal_mine", 0)) if bld else None,
                                        "crystal_mine": int(getattr(bld, "crystal_mine", 0)) if bld else None,
                                        "deuterium_synthesizer": int(getattr(bld, "deuterium_synthesizer", 0)) if bld else None,
                                        "solar_plant": int(getattr(bld, "solar_plant", 0)) if bld else None,
                                        "robot_factory": int(getattr(bld, "robot_factory", 0)) if bld else None,
                                        "shipyard": int(getattr(bld, "shipyard", 0)) if bld else None,
                                    },
                                    "fleet": {
                                        "light_fighter": int(getattr(dflt, "light_fighter", 0)) if dflt else None,
                                        "heavy_fighter": int(getattr(dflt, "heavy_fighter", 0)) if dflt else None,
                                        "cruiser": int(getattr(dflt, "cruiser", 0)) if dflt else None,
                                        "battleship": int(getattr(dflt, "battleship", 0)) if dflt else None,
                                        "bomber": int(getattr(dflt, "bomber", 0)) if dflt else None,
                                        "colony_ship": int(getattr(dflt, "colony_ship", 0)) if dflt else None,
                                    },
                                }
                                break
                    except Exception:
                        defender_id = None
                        snapshot = {}

                    # Emit report to world handler if available
                    try:
                        handler = getattr(self.world, "handle_espionage_report", None)
                        if callable(handler):
                            handler({
                                "attacker_user_id": int(getattr(movement, "owner_id", 0) or 0),
                                "defender_user_id": defender_id,
                                "location": {"galaxy": target_g, "system": target_s, "planet": target_p},
                                "snapshot": snapshot,
                                "entity_id": ent,
                            })
                    except Exception:
                        pass
            except Exception:
                # Do not break processing due to espionage handling errors
                pass

            # Remove movement component to mark completion
            try:
                self.world.remove_component(ent, FleetMovement)
            except Exception:
                # Non-fatal; continue processing
                pass
            # Delete persisted mission best-effort
            try:
                from src.core.sync import delete_fleet_mission as _del_mission
                _del_mission(self.world, ent)
            except Exception:
                pass

            # Log the completion event
            try:
                logger.info(
                    "fleet_movement_complete",
                    extra={
                        "action_type": "fleet_movement_complete",
                        "entity": ent,
                        "owner_id": getattr(movement, "owner_id", None),
                        "mission": getattr(movement, "mission", None),
                        "timestamp": now.isoformat(),
                        "target": {
                            "g": pos.galaxy,
                            "s": pos.system,
                            "p": pos.planet,
                        },
                    },
                )
            except Exception:
                pass
