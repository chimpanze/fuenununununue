from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from queue import Queue
from typing import Dict, Optional
import logging
import esper
from dataclasses import fields

from src.core.sync import (
    sync_planet_resources,
    sync_building_level,
    spend_resources_atomic,
    load_player_into_world,
    load_all_players_into_world,
    cleanup_inactive_players,
)

from src.models import (
    Player,
    Position,
    Resources,
    ResourceProduction,
    Buildings,
    BuildQueue,
    ShipBuildQueue,
    Fleet,
    Research,
    ResearchQueue,
    Planet,
)
from src.systems import (
    ResourceProductionSystem,
    BuildingConstructionSystem,
    PlayerActivitySystem,
    ResearchSystem,
    ShipyardSystem,
    FleetMovementSystem,
    BattleSystem,
)

logger = logging.getLogger(__name__)
from src.core.metrics import metrics


class GameWorld:
    def __init__(self) -> None:
        # Initialize an Esper World instance
        self.world = esper.World() if hasattr(esper, "World") else esper
        self.running = False
        self.game_thread: Optional[threading.Thread] = None
        self.command_queue: Queue = Queue()

        # Persistence cadence trackers
        self._last_save_ts: float = 0.0
        self._last_cleanup_day: Optional[int] = None

        # Register systems
        self.world.add_processor(ResourceProductionSystem())
        self.world.add_processor(BuildingConstructionSystem())
        self.world.add_processor(PlayerActivitySystem())
        self.world.add_processor(ResearchSystem())
        self.world.add_processor(ShipyardSystem())
        self.world.add_processor(FleetMovementSystem())
        self.world.add_processor(BattleSystem())

        # In-memory battle report store (used when DB is not integrated for reports)
        self._battle_reports: list[dict] = []
        self._next_battle_report_id: int = 1
        # In-memory espionage report store
        self._espionage_reports: list[dict] = []
        self._next_espionage_report_id: int = 1

        # In-memory marketplace offers store
        self._market_offers: list[dict] = []
        self._next_offer_id: int = 1

        # In-memory trade history (events) store
        self._trade_history: list[dict] = []
        self._next_trade_event_id: int = 1

        # Expose handlers so systems can push reports
        setattr(self.world, "handle_battle_report", self.handle_battle_report)
        setattr(self.world, "handle_espionage_report", self.handle_espionage_report)

        # No default test data; players are created via registration or tests
        # Initialize galaxy (config-driven; idempotent)
        try:
            from src.systems.planet_creation import initialize_galaxy
            initialize_galaxy()
        except Exception:
            pass


    def start_game_loop(self) -> None:
        """Start the game loop in a separate thread."""
        if not self.running:
            self.running = True
            self.game_thread = threading.Thread(target=self._game_loop, daemon=True)
            self.game_thread.start()
            logger.info("Game loop started")

    def stop_game_loop(self) -> None:
        """Stop the game loop."""
        self.running = False
        if self.game_thread:
            self.game_thread.join()
            logger.info("Game loop stopped")

    def _game_loop(self) -> None:
        """Main game loop - processes all systems every tick."""
        from src.core.config import TICK_RATE

        tick_rate = TICK_RATE  # ticks per second
        while self.running:
            start_time = time.time()

            # Process queued commands
            self._process_commands()

            # Process all ECS systems
            self.world.process()

            # Periodic persistence (every ~60s)
            try:
                import time as _t
                if _t.time() - self._last_save_ts >= 60.0:
                    self.save_player_data()
                    self._last_save_ts = _t.time()
            except Exception:
                pass

            # Daily cleanup job (once per day)
            try:
                from datetime import datetime as _dt, timezone as _tz
                day = _dt.now(_tz.utc).timetuple().tm_yday
                if self._last_cleanup_day != day:
                    cleanup_inactive_players(days=30)
                    self._last_cleanup_day = day
            except Exception:
                pass

            # Maintain tick rate
            elapsed = time.time() - start_time
            try:
                metrics.record_tick(elapsed)
            except Exception:
                pass
            sleep_time = max(0, tick_rate - elapsed)
            time.sleep(sleep_time)

    def _process_commands(self) -> None:
        """Process commands from the HTTP API."""
        while not self.command_queue.empty():
            try:
                command = self.command_queue.get_nowait()
                self._execute_command(command)
            except Exception as e:
                logger.error(f"Error processing command: {e}")

    def _execute_command(self, command: Dict) -> None:
        """Execute a command from the API."""
        cmd_type = command.get('type')
        user_id = command.get('user_id')

        try:
            logger.info(
                "execute_command",
                extra={
                    "action_type": cmd_type,
                    "user_id": user_id,
                    "timestamp": datetime.now().isoformat(),
                },
            )
        except Exception:
            pass

        if cmd_type == 'build_building':
            self._handle_build_building(user_id, command.get('building_type'))
        elif cmd_type == 'demolish_building':
            self._handle_demolish_building(user_id, command.get('building_type'))
        elif cmd_type == 'cancel_build_queue':
            self._handle_cancel_build_queue(user_id, command.get('index'))
        elif cmd_type == 'update_player_activity':
            self._handle_update_activity(user_id)
        elif cmd_type == 'start_research':
            self._handle_start_research(user_id, command.get('research_type'))
        elif cmd_type == 'build_ships':
            self._handle_build_ships(user_id, command.get('ship_type'), int(command.get('quantity', 1)))
        elif cmd_type == 'colonize':
            self._handle_colonize(
                user_id,
                int(command.get('galaxy', 1) or 1),
                int(command.get('system', 1) or 1),
                int(command.get('position', 1) or 1),
                command.get('planet_name') or "Colony",
            )
        elif cmd_type == 'fleet_dispatch':
            self._handle_fleet_dispatch(
                user_id,
                int(command.get('galaxy', 1) or 1),
                int(command.get('system', 1) or 1),
                int(command.get('position', 1) or 1),
                command.get('mission') or 'transfer',
                command.get('speed'),
                command.get('ships'),
            )
        elif cmd_type == 'fleet_recall':
            try:
                fleet_id = int(command.get('fleet_id')) if command.get('fleet_id') is not None else None
            except Exception:
                fleet_id = None
            self._handle_fleet_recall(user_id, fleet_id)
        elif cmd_type == 'trade_create_offer':
            self._handle_trade_create_offer(
                user_id,
                command.get('offered_resource'),
                int(command.get('offered_amount', 0) or 0),
                command.get('requested_resource'),
                int(command.get('requested_amount', 0) or 0),
            )
        elif cmd_type == 'trade_accept_offer':
            try:
                offer_id = int(command.get('offer_id'))
            except Exception:
                offer_id = -1
            self._handle_trade_accept_offer(user_id, offer_id)

    def _handle_demolish_building(self, user_id: int, building_type: str) -> None:
        """Handle building demolition with prerequisite safety and partial refund."""
        from src.core.config import PREREQUISITES
        for ent, (player, resources, buildings, build_queue) in self.world.get_components(
            Player, Resources, Buildings, BuildQueue
        ):
            if player.user_id != user_id:
                continue

            if not hasattr(buildings, building_type):
                return
            current_level = getattr(buildings, building_type)
            if current_level <= 0:
                return

            # Prevent breaking other buildings' prerequisites
            reverse_reqs = []
            for target_bld, reqs in PREREQUISITES.items():
                min_lvl = reqs.get(building_type)
                if min_lvl is None:
                    continue
                target_level = getattr(buildings, target_bld, 0) if hasattr(buildings, target_bld) else 0
                if target_level > 0 and (current_level - 1) < min_lvl:
                    reverse_reqs.append((target_bld, min_lvl, target_level))
            if reverse_reqs:
                try:
                    logger.info(
                        "demolish_blocked_prereq",
                        extra={
                            "action_type": "demolish_blocked_prereq",
                            "user_id": user_id,
                            "building_type": building_type,
                            "blocked": str(reverse_reqs),
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
                return

            # Apply demolition
            new_level = current_level - 1
            setattr(buildings, building_type, new_level)

            # Refund 30% of the last level's cost (cost at new_level)
            refund_base = self._calculate_building_cost(building_type, new_level)
            resources.metal += int(refund_base['metal'] * 0.3)
            resources.crystal += int(refund_base['crystal'] * 0.3)
            resources.deuterium += int(refund_base['deuterium'] * 0.3)

            # Persist building change best-effort
            try:
                sync_building_level(self.world, ent, building_type, new_level)
            except Exception:
                pass

            logger.info(f"Demolished {building_type} to level {new_level} for user {user_id}")
            return

    def _handle_cancel_build_queue(self, user_id: int, index: int | None) -> None:
        """Cancel a pending build queue item and partially refund resources."""
        if index is None:
            return
        for ent, (player, resources, buildings, build_queue) in self.world.get_components(
            Player, Resources, Buildings, BuildQueue
        ):
            if player.user_id != user_id:
                continue
            if index < 0 or index >= len(build_queue.items):
                return
            item = build_queue.items.pop(index)
            cost = item.get('cost', {'metal': 0, 'crystal': 0, 'deuterium': 0})
            resources.metal += int(cost.get('metal', 0) * 0.5)
            resources.crystal += int(cost.get('crystal', 0) * 0.5)
            resources.deuterium += int(cost.get('deuterium', 0) * 0.5)
            logger.info(f"Cancelled build queue index {index} for user {user_id}")
            return

    def _handle_build_building(self, user_id: int, building_type: str) -> None:
        """Handle building construction command."""
        from src.core.config import PREREQUISITES
        for ent, (player, resources, buildings, build_queue) in self.world.get_components(
            Player, Resources, Buildings, BuildQueue
        ):
            if player.user_id != user_id:
                continue

            # Validate prerequisites if any
            reqs = PREREQUISITES.get(building_type, {})
            unmet = []
            for req_bld, min_lvl in reqs.items():
                cur_lvl = getattr(buildings, req_bld, 0) if hasattr(buildings, req_bld) else 0
                if cur_lvl < min_lvl:
                    unmet.append((req_bld, min_lvl, cur_lvl))
            if unmet:
                try:
                    logger.info(
                        "build_prereq_unmet",
                        extra={
                            "action_type": "build_prereq_unmet",
                            "user_id": user_id,
                            "building_type": building_type,
                            "unmet": str(unmet),
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
                return

            # Calculate cost and build time based on current level
            current_level = getattr(buildings, building_type, 0) if hasattr(buildings, building_type) else 0
            cost = self._calculate_building_cost(building_type, current_level)
            build_time = self._calculate_build_time(building_type, current_level)
            # Apply research-based build time reduction (hyperspace)
            try:
                from src.models import Research as _R
                from src.core.config import BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL, MIN_BUILD_TIME_FACTOR
                r = self.world.component_for_entity(ent, _R)
                hyper_lvl = int(getattr(r, 'hyperspace', 0)) if r is not None else 0
                reduction = max(MIN_BUILD_TIME_FACTOR, 1.0 - BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL * hyper_lvl)
                build_time = int(max(1, build_time * reduction))
            except Exception:
                pass

            # Check if player has enough resources
            if (
                resources.metal >= cost['metal'] and
                resources.crystal >= cost['crystal'] and
                resources.deuterium >= cost['deuterium']
            ):
                # Deduct resources in ECS
                resources.metal -= cost['metal']
                resources.crystal -= cost['crystal']
                resources.deuterium -= cost['deuterium']
                # Persist resource spend atomically (best-effort)
                try:
                    spend_resources_atomic(self.world, ent, cost)
                except Exception:
                    pass

                # Add to build queue
                completion_time = datetime.now() + timedelta(seconds=build_time)
                build_queue.items.append({
                    'type': building_type,
                    'completion_time': completion_time,
                    'cost': cost,
                })

                logger.info(f"Started building {building_type} for user {user_id}")
                return

        logger.warning(f"Could not build {building_type} for user {user_id}")

    def _handle_update_activity(self, user_id: int) -> None:
        """Update player's last activity time."""
        for ent, (player,) in self.world.get_components(Player):
            if player.user_id == user_id:
                player.last_active = datetime.now()
                break

    def _handle_start_research(self, user_id: int, research_type: str) -> None:
        """Handle research start command: deduct resources and enqueue research."""
        if not research_type:
            return
        for ent, (player, resources, research, research_queue) in self.world.get_components(
            Player, Resources, Research, ResearchQueue
        ):
            if player.user_id != user_id:
                continue
            # Validate research type
            if not hasattr(research, research_type):
                return
            # Validate research prerequisites
            try:
                from src.core.config import RESEARCH_PREREQUISITES
                reqs = RESEARCH_PREREQUISITES.get(research_type, {})
            except Exception:
                reqs = {}
            unmet = []
            for dep, min_lvl in reqs.items():
                dep_cur = getattr(research, dep, 0) if hasattr(research, dep) else 0
                if dep_cur < min_lvl:
                    unmet.append((dep, min_lvl, dep_cur))
            if unmet:
                try:
                    logger.info(
                        "research_prereq_unmet",
                        extra={
                            "action_type": "research_prereq_unmet",
                            "user_id": user_id,
                            "research_type": research_type,
                            "unmet": str(unmet),
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
                return
            current_level = getattr(research, research_type, 0)
            cost = self._calculate_research_cost(research_type, current_level)
            duration = self._calculate_research_time(research_type, current_level)
            # Check resources
            if (
                resources.metal >= cost['metal'] and
                resources.crystal >= cost['crystal'] and
                resources.deuterium >= cost['deuterium']
            ):
                # Deduct in ECS; DB sync for research spend not implemented yet
                resources.metal -= cost['metal']
                resources.crystal -= cost['crystal']
                resources.deuterium -= cost['deuterium']
                completion_time = datetime.now() + timedelta(seconds=duration)
                research_queue.items.append({
                    'type': research_type,
                    'completion_time': completion_time,
                    'cost': cost,
                })
                logger.info(f"Started research {research_type} for user {user_id}")
                return
        logger.warning(f"Could not start research {research_type} for user {user_id}")

    def _handle_build_ships(self, user_id: int, ship_type: str, quantity: int) -> None:
        """Handle ship building: validate input, spend resources, and enqueue ship construction.

        Ships are produced at the Shipyard; requires at least level 1.
        Costs and times are per unit and scale linearly with quantity.
        Also enforces a maximum total fleet size based on Computer Technology.
        """
        if not ship_type or quantity is None:
            return
        try:
            quantity = max(1, int(quantity))
        except Exception:
            quantity = 1
        for ent, (player, resources, buildings, fleet) in self.world.get_components(
            Player, Resources, Buildings, Fleet
        ):
            if player.user_id != user_id:
                continue
            # Validate ship type exists on Fleet component
            if not hasattr(fleet, ship_type):
                return
            # Require shipyard
            shipyard_level = int(getattr(buildings, 'shipyard', 0)) if hasattr(buildings, 'shipyard') else 0
            if shipyard_level <= 0:
                try:
                    logger.info(
                        "ship_build_prereq_unmet",
                        extra={
                            "action_type": "ship_build_prereq_unmet",
                            "user_id": user_id,
                            "ship_type": ship_type,
                            "reason": "shipyard_level_0",
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
                return
            # Fleet size validation based on Computer Technology
            try:
                from src.core.config import BASE_MAX_FLEET_SIZE, FLEET_SIZE_PER_COMPUTER_LEVEL
            except Exception:
                BASE_MAX_FLEET_SIZE, FLEET_SIZE_PER_COMPUTER_LEVEL = 50, 10
            # Compute current total fleet size
            try:
                total_current = 0
                for f in fields(Fleet):
                    total_current += int(getattr(fleet, f.name, 0))
                # Include queued ships (all types)
                try:
                    sbq = self.world.component_for_entity(ent, ShipBuildQueue)
                except Exception:
                    sbq = None
                if sbq and getattr(sbq, 'items', None):
                    for item in sbq.items:
                        try:
                            total_current += int(item.get('count', 0))
                        except Exception:
                            pass
                # Get computer tech level (default 0)
                try:
                    from src.models import Research as _R
                    r = self.world.component_for_entity(ent, _R)
                    comp_lvl = int(getattr(r, 'computer', 0)) if r is not None else 0
                except Exception:
                    comp_lvl = 0
                max_allowed = int(BASE_MAX_FLEET_SIZE) + int(FLEET_SIZE_PER_COMPUTER_LEVEL) * max(0, comp_lvl)
                if total_current + quantity > max_allowed:
                    try:
                        logger.info(
                            "fleet_size_limit_reject",
                            extra={
                                "action_type": "fleet_size_limit_reject",
                                "user_id": user_id,
                                "ship_type": ship_type,
                                "request_quantity": quantity,
                                "current_total": total_current,
                                "max_allowed": max_allowed,
                                "timestamp": datetime.now().isoformat(),
                            },
                        )
                    except Exception:
                        pass
                    return
            except Exception:
                # If any unexpected error in validation, fail safe by rejecting
                return
            # Costs and time
            try:
                from src.core.config import BASE_SHIP_COSTS, BASE_SHIP_TIMES, BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL, MIN_BUILD_TIME_FACTOR
            except Exception:
                return
            per_cost = BASE_SHIP_COSTS.get(ship_type, {'metal': 0, 'crystal': 0, 'deuterium': 0})
            per_time = int(BASE_SHIP_TIMES.get(ship_type, 60))
            total_cost = {
                'metal': int(per_cost.get('metal', 0)) * quantity,
                'crystal': int(per_cost.get('crystal', 0)) * quantity,
                'deuterium': int(per_cost.get('deuterium', 0)) * quantity,
            }
            duration = per_time * quantity
            # Apply hyperspace reduction to duration
            try:
                from src.models import Research as _R
                r = self.world.component_for_entity(ent, _R)
                hyper_lvl = int(getattr(r, 'hyperspace', 0)) if r is not None else 0
                reduction = max(MIN_BUILD_TIME_FACTOR, 1.0 - BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL * hyper_lvl)
                duration = int(max(1, duration * reduction))
            except Exception:
                pass
            # Check resources
            if (
                resources.metal >= total_cost['metal'] and
                resources.crystal >= total_cost['crystal'] and
                resources.deuterium >= total_cost['deuterium']
            ):
                # Deduct and persist best-effort
                resources.metal -= total_cost['metal']
                resources.crystal -= total_cost['crystal']
                resources.deuterium -= total_cost['deuterium']
                try:
                    spend_resources_atomic(self.world, ent, total_cost)
                except Exception:
                    pass
                # Ensure ShipBuildQueue component exists
                try:
                    ship_queue = self.world.component_for_entity(ent, ShipBuildQueue)
                except Exception:
                    ship_queue = None
                if ship_queue is None:
                    ship_queue = ShipBuildQueue()
                    try:
                        self.world.add_component(ent, ship_queue)
                    except Exception:
                        pass
                # Queue the construction
                completion_time = datetime.now() + timedelta(seconds=duration)
                ship_queue.items.append({
                    'type': ship_type,
                    'count': quantity,
                    'completion_time': completion_time,
                    'cost': total_cost,
                })
                try:
                    logger.info(
                        "ship_build_started",
                        extra={
                            "action_type": "ship_build_started",
                            "user_id": user_id,
                            "ship_type": ship_type,
                            "count": quantity,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
                return
        logger.warning(f"Could not build ships {ship_type} x{quantity} for user {user_id}")

    def _handle_colonize(self, user_id: int, galaxy: int, system: int, position: int, planet_name: str) -> None:
        """Handle planet colonization using a colony ship.

        Validates that the player has at least one colony ship stationed and that
        the target coordinates are valid and unoccupied (DB-backed check when available).
        On success, decrements the colony ship count and persists the new planet best-effort.
        """
        # Basic coordinate validation
        try:
            if galaxy <= 0 or system <= 0 or position <= 0:
                return
        except Exception:
            return
        # Find player entity and fleet
        ent_match = None
        player_comp = None
        for ent, (player, fleet) in self.world.get_components(Player, Fleet):
            if player.user_id == user_id:
                ent_match = ent
                player_comp = player
                # Validate colony ship availability
                try:
                    cships = int(getattr(fleet, 'colony_ship', 0))
                except Exception:
                    cships = 0
                if cships <= 0:
                    return
                # Attempt to persist colony creation
                try:
                    from src.core.sync import create_colony
                    ok = create_colony(user_id, player.name, galaxy, system, position, planet_name)
                except Exception:
                    ok = True  # allow ECS-only success if persistence path fails
                if not ok:
                    return
                # Decrement colony ship in ECS
                try:
                    setattr(fleet, 'colony_ship', max(0, cships - 1))
                except Exception:
                    pass
                # Optionally log
                try:
                    logger.info(
                        "colonization_success",
                        extra={
                            "action_type": "colonize",
                            "user_id": user_id,
                            "galaxy": galaxy,
                            "system": system,
                            "position": position,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
                return
        # If no entity found, nothing to do
        return

    def _handle_fleet_dispatch(self, user_id: int, galaxy: int, system: int, planet_pos: int, mission: str, speed: Optional[float], ships: Optional[Dict]) -> None:
        """Handle fleet dispatch command.

        Minimal initial implementation: validates coordinates, then attaches a
        FleetMovement component to the player's entity so that the FleetMovementSystem
        will process arrival when due. Travel time calculation and ship composition
        handling are deferred to future tasks.
        """
        # Basic validation
        try:
            if galaxy <= 0 or system <= 0 or planet_pos <= 0:
                return
            mission = mission or 'transfer'
        except Exception:
            return
        # Find player entity with position and fleet
        for ent, (player, pos, fleet) in self.world.get_components(Player, Position, Fleet):
            if player.user_id != user_id:
                continue
            # Build movement component
            try:
                from src.models import FleetMovement as _FM
                now = datetime.now()
                origin = Position(galaxy=pos.galaxy, system=pos.system, planet=pos.planet)
                target = Position(galaxy=galaxy, system=system, planet=planet_pos)
                # Calculate travel time based on distance and effective fleet speed
                try:
                    from src.core.config import SYSTEMS_PER_GALAXY, POSITIONS_PER_SYSTEM
                except Exception:
                    SYSTEMS_PER_GALAXY, POSITIONS_PER_SYSTEM = 499, 15

                # Distance in abstract units: linearized across galaxy/system/planet
                dg = abs(int(target.galaxy) - int(origin.galaxy))
                ds = abs(int(target.system) - int(origin.system))
                dp = abs(int(target.planet) - int(origin.planet))
                distance_units = dg * SYSTEMS_PER_GALAXY * POSITIONS_PER_SYSTEM + ds * POSITIONS_PER_SYSTEM + dp

                # Determine effective speed (units per hour)
                # Use research-influenced ship speeds via existing helper
                research_comp = None
                try:
                    research_comp = self.world.component_for_entity(ent, Research)
                except Exception:
                    research_comp = None
                ship_stats = self._calculate_ship_stats(research_comp) or {}

                # If a composition was provided, use the slowest ship among it; else, use fastest owned ship; fallback to light_fighter base
                def _get_speed_for(ship_type: str) -> int:
                    try:
                        return int(ship_stats.get(ship_type, {}).get('speed'))
                    except Exception:
                        return 0

                effective_speed = 0
                if isinstance(ships, dict) and ships:
                    speeds = []
                    for st, cnt in ships.items():
                        try:
                            cnt_i = int(cnt)
                        except Exception:
                            cnt_i = 0
                        if cnt_i <= 0:
                            continue
                        s_val = _get_speed_for(str(st))
                        if s_val > 0:
                            speeds.append(s_val)
                    if speeds:
                        effective_speed = min(speeds)  # slowest ship governs fleet speed
                if effective_speed <= 0:
                    # Fallback: check owned ships on the entity and take the fastest available
                    try:
                        owned_fleet = self.world.component_for_entity(ent, Fleet)
                    except Exception:
                        owned_fleet = None
                    owned_speeds = []
                    if owned_fleet is not None:
                        for st in ship_stats.keys():
                            try:
                                if int(getattr(owned_fleet, st, 0)) > 0:
                                    sv = _get_speed_for(st)
                                    if sv > 0:
                                        owned_speeds.append(sv)
                            except Exception:
                                continue
                    if owned_speeds:
                        effective_speed = max(owned_speeds)
                if effective_speed <= 0:
                    # Final fallback: base light fighter speed or 5000
                    effective_speed = int(ship_stats.get('light_fighter', {}).get('speed', 5000)) or 5000

                # Apply optional user speed factor (0 < factor <= 1.0)
                try:
                    user_factor = float(speed) if speed is not None else 1.0
                except Exception:
                    user_factor = 1.0
                if user_factor <= 0:
                    user_factor = 1.0
                if user_factor > 1.0:
                    user_factor = 1.0
                effective_speed = max(1.0, effective_speed * user_factor)

                # Convert distance and speed to seconds; interpret speed as units/hour
                duration_seconds = 1
                try:
                    duration_seconds = int((float(distance_units) / float(effective_speed)) * 3600)
                    if duration_seconds < 1:
                        duration_seconds = 1
                except Exception:
                    duration_seconds = 1

                movement = _FM(
                    origin=origin,
                    target=target,
                    departure_time=now,
                    arrival_time=now + timedelta(seconds=duration_seconds),
                    speed=float(effective_speed),
                    mission=str(mission),
                    owner_id=int(user_id),
                    recalled=False,
                )
                try:
                    self.world.add_component(ent, movement)
                except Exception:
                    # If adding fails, do not crash
                    pass
                try:
                    logger.info(
                        "fleet_dispatch_queued",
                        extra={
                            "action_type": "fleet_dispatch",
                            "user_id": user_id,
                            "target": {"g": galaxy, "s": system, "p": planet_pos},
                            "mission": mission,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                except Exception:
                    pass
                # If this is an attack mission, notify the defender of incoming attack (best-effort)
                try:
                    if str(mission).lower() == "attack":
                        # Find defender by matching target coordinates to a player's active Position
                        defender_id = None
                        for dent, (dp, dpos) in self.world.get_components(Player, Position):
                            try:
                                if int(dpos.galaxy) == int(galaxy) and int(dpos.system) == int(system) and int(dpos.planet) == int(planet_pos):
                                    defender_id = int(dp.user_id)
                                    break
                            except Exception:
                                continue
                        if defender_id:
                            try:
                                from src.api.ws import send_to_user as _send
                                _send(defender_id, {
                                    "type": "incoming_attack",
                                    "attacker_user_id": int(user_id),
                                    "origin": {"galaxy": origin.galaxy, "system": origin.system, "planet": origin.planet},
                                    "target": {"galaxy": galaxy, "system": system, "planet": planet_pos},
                                    "eta": movement.arrival_time.isoformat(),
                                    "ts": now.isoformat(),
                                })
                            except Exception:
                                pass
                            # Persist offline notification (best-effort)
                            try:
                                from src.core.notifications import create_notification as _notify
                                _notify(defender_id, "incoming_attack", {
                                    "attacker_user_id": int(user_id),
                                    "origin": {"galaxy": origin.galaxy, "system": origin.system, "planet": origin.planet},
                                    "target": {"galaxy": galaxy, "system": system, "planet": planet_pos},
                                    "eta": movement.arrival_time.isoformat(),
                                }, priority="critical")
                            except Exception:
                                pass
                except Exception:
                    pass
            except Exception:
                pass
            return
        return

    def _handle_fleet_recall(self, user_id: int, fleet_id: Optional[int]) -> bool:
        """Recall an in-flight fleet back to its origin.

        Current ECS model tracks a single FleetMovement per player entity. The fleet_id
        parameter is accepted for API shape compatibility but is not used to select
        between multiple fleets yet.

        Returns True if a recall was applied or was already in recalled state, False otherwise.
        """
        try:
            from src.models import Player as _P, FleetMovement as _FM
        except Exception:
            return False

        now = datetime.now()
        # Find the player's entity that has an active FleetMovement
        for ent, (p, mv) in self.world.get_components(_P, _FM):
            try:
                if int(p.user_id) != int(user_id):
                    continue
            except Exception:
                continue

            # If already arrived or past ETA, nothing to recall
            try:
                if now >= mv.arrival_time:
                    return False
            except Exception:
                return False

            # If already recalled, treat as idempotent success
            try:
                if bool(getattr(mv, 'recalled', False)):
                    return True
            except Exception:
                pass

            # Compute return ETA as elapsed outbound time
            try:
                elapsed = now - mv.departure_time
                seconds = int(max(1, elapsed.total_seconds()))
            except Exception:
                seconds = 1

            # Flip destination to origin and mark recalled
            try:
                mv.target = mv.origin
                mv.recalled = True
                mv.departure_time = now
                from datetime import timedelta as _td
                mv.arrival_time = now + _td(seconds=seconds)
            except Exception:
                return False

            # Log
            try:
                logger.info(
                    "fleet_recall_queued",
                    extra={
                        "action_type": "fleet_recall",
                        "user_id": user_id,
                        "entity": ent,
                        "timestamp": datetime.now().isoformat(),
                        "return_eta": mv.arrival_time.isoformat(),
                    },
                )
            except Exception:
                pass
            return True
        return False

    def _calculate_building_cost(self, building_type: str, level: int) -> Dict[str, int]:
        """Calculate the cost of a building upgrade."""
        from src.core.config import BASE_BUILDING_COSTS
        if building_type not in BASE_BUILDING_COSTS:
            return {'metal': 0, 'crystal': 0, 'deuterium': 0}

        base = BASE_BUILDING_COSTS[building_type]
        multiplier = 1.5 ** level
        return {
            'metal': int(base['metal'] * multiplier),
            'crystal': int(base['crystal'] * multiplier),
            'deuterium': int(base['deuterium'] * multiplier),
        }

    def _calculate_build_time(self, building_type: str, level: int) -> int:
        """Calculate build time in seconds."""
        from src.core.config import BASE_BUILD_TIMES
        base_time = BASE_BUILD_TIMES.get(building_type, 60)
        return int(base_time * (1.2 ** level))

    def _calculate_research_cost(self, research_type: str, level: int) -> Dict[str, int]:
        """Calculate the cost of a research upgrade based on current level.

        Uses exponential growth similar to buildings but with a 1.6 multiplier by default.
        """
        from src.core.config import BASE_RESEARCH_COSTS
        base = BASE_RESEARCH_COSTS.get(research_type, {'metal': 0, 'crystal': 0, 'deuterium': 0})
        multiplier = 1.6 ** level
        return {
            'metal': int(base['metal'] * multiplier),
            'crystal': int(base['crystal'] * multiplier),
            'deuterium': int(base['deuterium'] * multiplier),
        }

    def _calculate_research_time(self, research_type: str, level: int) -> int:
        """Calculate research time in seconds based on current level."""
        from src.core.config import BASE_RESEARCH_TIMES
        base_time = BASE_RESEARCH_TIMES.get(research_type, 120)
        # Slightly faster growth than buildings
        return int(base_time * (1.25 ** level))

    def _calculate_ship_stats(self, research: Research) -> Dict[str, Dict[str, int]]:
        """Derive ship stats based on research levels and base stats from config.

        Returns a mapping: ship_type -> {attack, shield, speed, cargo}
        """
        try:
            from src.core.config import BASE_SHIP_STATS, SHIP_STAT_BONUSES
        except Exception:
            return {}
        laser = int(getattr(research, 'laser', 0)) if research else 0
        ion = int(getattr(research, 'ion', 0)) if research else 0
        hyper = int(getattr(research, 'hyperspace', 0)) if research else 0
        plasma = int(getattr(research, 'plasma', 0)) if research else 0

        attack_bonus = 1.0 + laser * SHIP_STAT_BONUSES.get('laser_attack_per_level', 0.0) + plasma * SHIP_STAT_BONUSES.get('plasma_attack_per_level', 0.0)
        shield_bonus = 1.0 + ion * SHIP_STAT_BONUSES.get('ion_shield_per_level', 0.0)
        speed_bonus = 1.0 + hyper * SHIP_STAT_BONUSES.get('hyperspace_speed_per_level', 0.0)
        cargo_bonus = 1.0 + hyper * SHIP_STAT_BONUSES.get('hyperspace_cargo_per_level', 0.0)

        stats: Dict[str, Dict[str, int]] = {}
        for ship, base in BASE_SHIP_STATS.items():
            stats[ship] = {
                'attack': int(base['attack'] * attack_bonus),
                'shield': int(base['shield'] * shield_bonus),
                'speed': int(base['speed'] * speed_bonus),
                'cargo': int(base['cargo'] * cargo_bonus),
            }
        return stats

    def get_player_data(self, user_id: int) -> Optional[Dict]:
        """Get all data for a specific player."""
        for ent, (player, position, resources, buildings, build_queue, fleet, research, planet) in self.world.get_components(
            Player, Position, Resources, Buildings, BuildQueue, Fleet, Research, Planet
        ):
            if player.user_id == user_id:
                # Optional ship build queue
                ship_build_queue_items = []
                try:
                    from src.models import ShipBuildQueue as _SBQ
                    sbq = self.world.component_for_entity(ent, _SBQ)
                    if sbq and getattr(sbq, 'items', None):
                        for item in sbq.items:
                            ship_build_queue_items.append({
                                'type': item.get('type'),
                                'count': int(item.get('count', 1)),
                                'completion_time': item.get('completion_time').isoformat() if item.get('completion_time') else None,
                                'cost': item.get('cost'),
                            })
                except Exception:
                    pass
                return {
                    'player': {
                        'name': player.name,
                        'user_id': player.user_id,
                        'last_active': player.last_active.isoformat(),
                    },
                    'position': {
                        'galaxy': position.galaxy,
                        'system': position.system,
                        'planet': position.planet,
                    },
                    'resources': {
                        'metal': resources.metal,
                        'crystal': resources.crystal,
                        'deuterium': resources.deuterium,
                    },
                    'buildings': {
                        'metal_mine': buildings.metal_mine,
                        'crystal_mine': buildings.crystal_mine,
                        'deuterium_synthesizer': buildings.deuterium_synthesizer,
                        'solar_plant': buildings.solar_plant,
                        'robot_factory': buildings.robot_factory,
                        'shipyard': buildings.shipyard,
                    },
                    'build_queue': [
                        {
                            'type': item['type'],
                            'completion_time': item['completion_time'].isoformat(),
                            'cost': item['cost'],
                        }
                        for item in build_queue.items
                    ],
                    'ship_build_queue': ship_build_queue_items,
                    'fleet': {
                        'light_fighter': fleet.light_fighter,
                        'heavy_fighter': fleet.heavy_fighter,
                        'cruiser': fleet.cruiser,
                        'battleship': fleet.battleship,
                        'bomber': fleet.bomber,
                        'colony_ship': getattr(fleet, 'colony_ship', 0),
                    },
                    'research': {
                        'energy': research.energy,
                        'laser': research.laser,
                        'ion': research.ion,
                        'hyperspace': research.hyperspace,
                        'plasma': research.plasma,
                        'computer': getattr(research, 'computer', 0),
                    },
                    'ship_stats': self._calculate_ship_stats(research),
                    'planet': {
                        'name': planet.name,
                        'temperature': planet.temperature,
                        'size': planet.size,
                    },
                }
        return None

    def set_active_planet_by_id(self, user_id: int, planet_id: int) -> bool:
        """Switch the active planet for a user by loading the specified planet into ECS.

        Returns True on success, False otherwise (e.g., DB disabled, planet not owned by user).
        """
        try:
            from src.core.sync import load_player_planet_into_world
            ok = load_player_planet_into_world(self.world, user_id, planet_id)
            return bool(ok)
        except Exception:
            return False

    # -----------------
    # Battle Reports API (in-memory)
    # -----------------
    def handle_battle_report(self, report: dict) -> None:
        """Append a battle report to the in-memory store with id and timestamp.

        The report should include attacker_user_id and defender_user_id keys.
        """
        try:
            rid = int(self._next_battle_report_id)
            self._next_battle_report_id += 1
        except Exception:
            rid = 1
            self._next_battle_report_id = 2
        payload = dict(report or {})
        payload["id"] = rid
        payload["created_at"] = datetime.now().isoformat()
        self._battle_reports.append(payload)
        try:
            logger.info(
                "battle_report_stored",
                extra={
                    "action_type": "battle_report_stored",
                    "report_id": rid,
                    "attacker_user_id": payload.get("attacker_user_id"),
                    "defender_user_id": payload.get("defender_user_id"),
                    "timestamp": payload.get("created_at"),
                },
            )
        except Exception:
            pass
        # Emit real-time notifications to participants (best-effort)
        try:
            from src.api.ws import send_to_user as _ws_send
            attacker_id = payload.get("attacker_user_id")
            defender_id = payload.get("defender_user_id")
            event = {
                "type": "battle_report",
                "report_id": rid,
                "created_at": payload.get("created_at"),
                "location": payload.get("location"),
                "outcome": payload.get("outcome"),
            }
            if attacker_id:
                _ws_send(int(attacker_id), event)
            if defender_id:
                _ws_send(int(defender_id), event)
            # Persist offline battle outcome notifications with critical priority (best-effort)
            try:
                from src.core.notifications import create_notification as _notify
                if attacker_id:
                    _notify(int(attacker_id), "battle_report", {
                        "report_id": rid,
                        "location": payload.get("location"),
                        "outcome": payload.get("outcome"),
                    }, priority="critical")
                if defender_id:
                    _notify(int(defender_id), "battle_report", {
                        "report_id": rid,
                        "location": payload.get("location"),
                        "outcome": payload.get("outcome"),
                    }, priority="critical")
            except Exception:
                pass
        except Exception:
            pass

    def list_battle_reports(self, user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return battle reports visible to a user (attacker or defender)."""
        try:
            uid = int(user_id)
        except Exception:
            return []
        reports = [r for r in reversed(self._battle_reports) if r.get("attacker_user_id") == uid or r.get("defender_user_id") == uid]
        start = max(0, int(offset))
        end = max(start, start + int(limit))
        # Return shallow copies to avoid accidental external mutation
        return [dict(r) for r in reports[start:end]]

    def get_battle_report(self, user_id: int, report_id: int) -> dict | None:
        """Return a single battle report if the user is a participant; otherwise None."""
        try:
            uid = int(user_id)
            rid = int(report_id)
        except Exception:
            return None
        for r in self._battle_reports:
            if int(r.get("id", -1)) == rid and (r.get("attacker_user_id") == uid or r.get("defender_user_id") == uid):
                return dict(r)
        return None

    # -----------------
    # Espionage Reports API (in-memory)
    # -----------------
    def handle_espionage_report(self, report: dict) -> None:
        """Append an espionage report to the in-memory store with id and timestamp.

        The report should include attacker_user_id, defender_user_id (optional if unoccupied),
        location {galaxy, system, planet}, and a snapshot payload.
        """
        try:
            rid = int(self._next_espionage_report_id)
            self._next_espionage_report_id += 1
        except Exception:
            rid = 1
            self._next_espionage_report_id = 2
        payload = dict(report or {})
        payload["id"] = rid
        payload["created_at"] = datetime.now().isoformat()
        self._espionage_reports.append(payload)
        try:
            logger.info(
                "espionage_report_stored",
                extra={
                    "action_type": "espionage_report_stored",
                    "report_id": rid,
                    "attacker_user_id": payload.get("attacker_user_id"),
                    "defender_user_id": payload.get("defender_user_id"),
                    "timestamp": payload.get("created_at"),
                },
            )
        except Exception:
            pass

    def list_espionage_reports(self, user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return espionage reports visible to a user (attacker or defender)."""
        try:
            uid = int(user_id)
        except Exception:
            return []
        reports = [r for r in reversed(self._espionage_reports) if r.get("attacker_user_id") == uid or r.get("defender_user_id") == uid]
        start = max(0, int(offset))
        end = max(start, start + int(limit))
        return [dict(r) for r in reports[start:end]]

    def get_espionage_report(self, user_id: int, report_id: int) -> dict | None:
        """Return a single espionage report if the user is a participant; otherwise None."""
        try:
            uid = int(user_id)
            rid = int(report_id)
        except Exception:
            return None
        for r in self._espionage_reports:
            if int(r.get("id", -1)) == rid and (r.get("attacker_user_id") == uid or r.get("defender_user_id") == uid):
                return dict(r)
        return None

    # -----------------
    # Persistence API
    # -----------------
    def save_player_data(self) -> None:
        """Persist ECS state to database best-effort for each player entity.

        Uses sync helpers which already throttle resource writes to once per 60s.
        """
        try:
            for ent, (player, position) in self.world.get_components(Player, Position):
                # Persist resources and production (throttled inside sync)
                try:
                    sync_planet_resources(self.world, ent)
                except Exception:
                    pass
                # Persist notable building levels for durability (idempotent)
                try:
                    from src.models import Buildings as B
                    b = self.world.component_for_entity(ent, B)
                    for attr in ("metal_mine", "crystal_mine", "deuterium_synthesizer", "solar_plant", "robot_factory", "shipyard"):
                        if hasattr(b, attr):
                            lvl = getattr(b, attr)
                            sync_building_level(self.world, ent, attr, int(lvl))
                except Exception:
                    pass
        except Exception:
            # do not fail the loop
            pass

    def load_player_data(self, user_id: Optional[int] = None) -> None:
        """Load player(s) from DB into ECS world.

        If user_id is provided, loads that user; otherwise loads all users.
        """
        try:
            if user_id is None:
                load_all_players_into_world(self.world)
            else:
                load_player_into_world(self.world, user_id)
        except Exception:
            pass

    def queue_command(self, command: Dict) -> None:
        """Queue a command to be processed in the game loop."""
        try:
            action_type = command.get('type')
            user_id = command.get('user_id')
            logger.info(
                "queue_command",
                extra={
                    "action_type": action_type,
                    "user_id": user_id,
                    "timestamp": datetime.now().isoformat(),
                },
            )
        except Exception:
            # Do not fail queuing due to logging issues
            pass
        self.command_queue.put(command)

    
    def list_market_offers(self, status: Optional[str] = "open", limit: int = 50, offset: int = 0) -> list[dict]:
        """List marketplace offers filtered by status.
        
        Args:
            status: Filter by status ('open', 'accepted', 'cancelled') or None for all.
            limit: Max number of results to return.
            offset: Number of results to skip.
        Returns:
            A shallow copy list of offer dicts.
        """
        try:
            offers = [o for o in self._market_offers if status is None or o.get("status") == status]
            return list(offers[offset: offset + max(0, int(limit))])
        except Exception:
            return []

    # -----------------
    # Trade History API (in-memory)
    # -----------------
    def _record_trade_event(self, event: dict) -> None:
        """Record a trade event in memory with id and timestamp and log it.

        Event shape (keys may be None depending on type):
        - type: 'offer_created' | 'trade_completed'
        - offer_id: int
        - seller_user_id: int
        - buyer_user_id: Optional[int]
        - offered_resource, offered_amount, requested_resource, requested_amount
        - status: 'open'|'completed'
        """
        try:
            eid = int(self._next_trade_event_id)
            self._next_trade_event_id += 1
        except Exception:
            eid = 1
            self._next_trade_event_id = 2
        payload = dict(event or {})
        payload["id"] = eid
        if "timestamp" not in payload:
            payload["timestamp"] = datetime.now().isoformat()
        self._trade_history.append(payload)
        try:
            logger.info(
                "trade_event",
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
        # Real-time notification to participants (best-effort)
        try:
            from src.api.ws import send_to_user as _ws_send
            seller_id = payload.get("seller_user_id")
            buyer_id = payload.get("buyer_user_id")
            event = dict(payload)
            event["type"] = "trade_event"
            if seller_id:
                _ws_send(int(seller_id), event)
            if buyer_id:
                _ws_send(int(buyer_id), event)
        except Exception:
            pass

    def list_trade_history(self, user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return trade events relevant to the given user id, newest-first.
        An event is relevant if the user is the seller or buyer.
        """
        try:
            uid = int(user_id)
        except Exception:
            return []
        # Newest-first
        relevant = [e for e in reversed(self._trade_history) if e.get("seller_user_id") == uid or e.get("buyer_user_id") == uid]
        start = max(0, int(offset))
        end = max(start, start + int(limit))
        return [dict(e) for e in relevant[start:end]]

    def _handle_trade_create_offer(
        self,
        user_id: int,
        offered_resource: Optional[str],
        offered_amount: int,
        requested_resource: Optional[str],
        requested_amount: int,
    ) -> Optional[int]:
        """Create a trade offer, reserving the seller's offered resources in escrow.
        
        Returns the offer id on success, or None on failure.
        """
        try:
            offered_resource = str(offered_resource or "").lower()
            requested_resource = str(requested_resource or "").lower()
            offered_amount = int(offered_amount)
            requested_amount = int(requested_amount)
        except Exception:
            return None

        valid_resources = {"metal", "crystal", "deuterium"}
        if offered_resource not in valid_resources or requested_resource not in valid_resources:
            return None
        if offered_amount <= 0 or requested_amount <= 0:
            return None

        # Locate seller entity and resources
        seller_ent = None
        seller_res = None
        for ent, (p, r) in self.world.get_components(Player, Resources):
            if getattr(p, 'user_id', None) == user_id:
                seller_ent = ent
                seller_res = r
                break
        if seller_ent is None or seller_res is None:
            return None

        # Verify sufficient resources and deduct into escrow
        current_amount = int(getattr(seller_res, offered_resource, 0))
        if current_amount < offered_amount:
            return None
        try:
            setattr(seller_res, offered_resource, current_amount - offered_amount)
        except Exception:
            return None

        # Create offer in escrow
        try:
            oid = int(self._next_offer_id)
            self._next_offer_id += 1
        except Exception:
            oid = 1
            self._next_offer_id = 2

        offer = {
            "id": oid,
            "seller_user_id": int(user_id),
            "offered_resource": offered_resource,
            "offered_amount": int(offered_amount),
            "requested_resource": requested_resource,
            "requested_amount": int(requested_amount),
            "status": "open",
            "created_at": datetime.now().isoformat(),
        }
        self._market_offers.append(offer)
        # Record trade history event (offer created)
        try:
            self._record_trade_event({
                "type": "offer_created",
                "offer_id": offer["id"],
                "seller_user_id": int(user_id),
                "buyer_user_id": None,
                "offered_resource": offered_resource,
                "offered_amount": int(offered_amount),
                "requested_resource": requested_resource,
                "requested_amount": int(requested_amount),
                "status": "open",
            })
        except Exception:
            pass
        try:
            logger.info(
                "trade_offer_created",
                extra={
                    "action_type": "trade_offer_created",
                    "user_id": user_id,
                    "offer_id": offer["id"],
                    "offered": {offered_resource: offered_amount},
                    "requested": {requested_resource: requested_amount},
                    "timestamp": offer["created_at"],
                },
            )
        except Exception:
            pass
        return offer["id"]

    def _handle_trade_accept_offer(self, buyer_user_id: int, offer_id: int) -> bool:
        """Accept an open trade offer, transferring resources atomically.
        
        Returns True on success, False otherwise.
        """
        # Find the offer
        offer = None
        for o in self._market_offers:
            if int(o.get("id", -1)) == int(offer_id):
                offer = o
                break
        if offer is None or offer.get("status") != "open":
            return False

        seller_id = int(offer["seller_user_id"]) if "seller_user_id" in offer else -1
        if int(buyer_user_id) == seller_id:
            # cannot accept own offer
            return False

        # Locate buyer and seller resources
        buyer_res = None
        seller_res = None
        for ent, (p, r) in self.world.get_components(Player, Resources):
            uid = getattr(p, 'user_id', None)
            if uid == buyer_user_id:
                buyer_res = r
            if uid == seller_id:
                seller_res = r
        if buyer_res is None or seller_res is None:
            return False

        offered_resource = offer["offered_resource"]
        requested_resource = offer["requested_resource"]
        offered_amount = int(offer["offered_amount"])
        requested_amount = int(offer["requested_amount"])

        # Validate buyer has enough to pay
        try:
            buyer_has = int(getattr(buyer_res, requested_resource, 0))
        except Exception:
            buyer_has = 0
        if buyer_has < requested_amount:
            return False

        # Apply transfers: buyer -> seller (requested), escrow -> buyer (offered)
        try:
            setattr(buyer_res, requested_resource, buyer_has - requested_amount)
            seller_current_req = int(getattr(seller_res, requested_resource, 0))
            setattr(seller_res, requested_resource, seller_current_req + requested_amount)

            buyer_current_offered = int(getattr(buyer_res, offered_resource, 0))
            setattr(buyer_res, offered_resource, buyer_current_offered + offered_amount)
        except Exception:
            return False

        # Mark offer as accepted
        offer["status"] = "accepted"
        offer["accepted_by"] = int(buyer_user_id)
        offer["accepted_at"] = datetime.now().isoformat()

        # Record trade history event (trade completed)
        try:
            self._record_trade_event({
                "type": "trade_completed",
                "offer_id": int(offer_id),
                "seller_user_id": seller_id,
                "buyer_user_id": int(buyer_user_id),
                "offered_resource": offered_resource,
                "offered_amount": int(offered_amount),
                "requested_resource": requested_resource,
                "requested_amount": int(requested_amount),
                "status": "completed",
            })
        except Exception:
            pass

        try:
            logger.info(
                "trade_offer_accepted",
                extra={
                    "action_type": "trade_offer_accepted",
                    "offer_id": int(offer_id),
                    "seller_user_id": seller_id,
                    "buyer_user_id": int(buyer_user_id),
                    "timestamp": offer["accepted_at"],
                },
            )
        except Exception:
            pass
        return True
