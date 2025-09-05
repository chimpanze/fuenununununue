from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from queue import Queue
from typing import Dict, Optional
import logging
import esper
from dataclasses import fields
from src.core.time_utils import utc_now, ensure_aware_utc, parse_utc

from src.core.sync import (
    sync_planet_resources,
    sync_building_level,
    spend_resources_atomic,
    load_player_into_world,
    load_all_players_into_world,
    cleanup_inactive_players,
    enqueue_ship_build,
    load_ship_queue_items,
    finalize_overdue_ship_builds,
    enqueue_build_queue,
    load_build_queue_items,
    enqueue_research,
    load_research_queue_items,
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
from src.core.config import TRADE_TRANSACTION_FEE_RATE
from src.core.commands import (
    parse_build_building,
    parse_demolish_building,
    parse_cancel_build_queue,
    parse_update_activity,
    parse_start_research,
    parse_build_ships,
    parse_colonize,
    parse_fleet_dispatch,
    parse_fleet_recall,
    parse_trade_create_offer,
    parse_trade_accept_offer,
)


class GameWorld:
    def __init__(self) -> None:
        # Initialize an Esper World instance
        self.world = esper.World() if hasattr(esper, "World") else esper
        self.running = False
        self.game_thread: Optional[threading.Thread] = None
        self.command_queue: Queue = Queue()

        # Lifecycle flags
        self.loaded: bool = False

        # Persistence cadence trackers
        self._last_save_ts: float = 0.0
        self._last_cleanup_day: Optional[int] = None
        # Lightweight lock to prevent overlapping saves
        self._save_lock: threading.Lock = threading.Lock()

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

        # Removed file-backed hydration for market offers and reports.
        # Open offers will be hydrated from the database in load_player_data when DB is enabled.
        # In-memory stores remain empty by default when DB is disabled.

        # No default test data; players are created via registration or tests
        # Initialize galaxy (config-driven; idempotent)
        try:
            from src.systems.planet_creation import initialize_galaxy
            initialize_galaxy()
        except Exception:
            logger.warning(
                "Failed to initialize galaxy",
                extra={
                    "user_id": None,
                    "entity_id": None,
                    "action_context": "startup:initialize_galaxy",
                },
                exc_info=True,
            )


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
        # Final save best-effort on shutdown
        try:
            self.save_player_data()
        except Exception:
            logger.warning(
                "Final save on stop failed",
                extra={
                    "user_id": None,
                    "entity_id": None,
                    "action_context": "shutdown:final_save",
                },
                exc_info=True,
            )

    def _game_loop(self) -> None:
        """Main game loop - processes all systems every tick.

        Uses time.monotonic() for scheduling to avoid wall-clock adjustments
        impacting cadence. Records tick duration and start-time jitter.
        """
        from src.core.config import TICK_RATE

        period_s = TICK_RATE  # treated as seconds per tick (default 1.0)
        next_tick = time.monotonic()
        while self.running:
            planned_start = next_tick
            actual_start = time.monotonic()
            jitter_s = actual_start - planned_start

            # Process queued commands
            self._process_commands()

            # Process all ECS systems
            self.world.process()

            # Periodic persistence (every ~60s, wall-clock based)
            try:
                import time as _t
                from src.core.config import SAVE_INTERVAL_SECONDS
                if _t.time() - self._last_save_ts >= float(SAVE_INTERVAL_SECONDS):
                    self.save_player_data()
            except Exception:
                logger.warning(
                    "Periodic save failed",
                    extra={
                        "user_id": None,
                        "entity_id": None,
                        "action_context": "loop:periodic_save",
                    },
                    exc_info=True,
                )

            # Daily cleanup job (once per day)
            try:
                from datetime import datetime as _dt, timezone as _tz
                from src.core.config import CLEANUP_DAYS
                day = _dt.now(_tz.utc).timetuple().tm_yday
                if self._last_cleanup_day != day:
                    cleanup_inactive_players(days=int(CLEANUP_DAYS))
                    self._last_cleanup_day = day
            except Exception:
                logger.warning(
                    "Daily cleanup job failed",
                    extra={
                        "user_id": None,
                        "entity_id": None,
                        "action_context": "loop:daily_cleanup",
                    },
                    exc_info=True,
                )

            # Maintain tick cadence using monotonic clock
            end_time = time.monotonic()
            elapsed = end_time - actual_start
            try:
                metrics.record_tick(elapsed, jitter_s=jitter_s)
                try:
                    logger.debug(
                        "tick_complete",
                        extra={
                            "duration_ms": elapsed * 1000.0,
                            "jitter_ms": abs(jitter_s) * 1000.0,
                        },
                    )
                except Exception:
                    pass
            except Exception:
                logger.warning(
                    "Recording tick metrics failed",
                    extra={
                        "user_id": None,
                        "entity_id": None,
                        "action_context": "loop:metrics_record_tick",
                    },
                    exc_info=True,
                )

            next_tick = planned_start + period_s
            sleep_time = max(0.0, next_tick - time.monotonic())
            time.sleep(sleep_time)

    def run_cleanup_now(self, days: Optional[int] = None) -> int:
        """Run the inactive players cleanup immediately.

        This is a test hook to make the daily cleanup job invokable on demand
        without waiting for the day boundary in the game loop. Returns the
        number of cleaned-up players (best-effort; 0 on failure).
        """
        try:
            from src.core.config import CLEANUP_DAYS as _CLEANUP_DAYS
            d = int(days) if days is not None else int(_CLEANUP_DAYS)
        except Exception:
            d = 30
        try:
            count = cleanup_inactive_players(days=d)
            try:
                logger.info(
                    "cleanup_now",
                    extra={
                        "days": d,
                        "removed_count": count,
                        "action_context": "manual:cleanup",
                    },
                )
            except Exception:
                pass
            return int(count or 0)
        except Exception:
            logger.warning(
                "Manual cleanup failed",
                extra={
                    "user_id": None,
                    "entity_id": None,
                    "action_context": "manual:cleanup",
                },
                exc_info=True,
            )
            return 0

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
            uid, building_type = parse_build_building(command)
            self._handle_build_building(uid, building_type)
        elif cmd_type == 'demolish_building':
            uid, building_type = parse_demolish_building(command)
            self._handle_demolish_building(uid, building_type)
        elif cmd_type == 'cancel_build_queue':
            uid, index = parse_cancel_build_queue(command)
            self._handle_cancel_build_queue(uid, index)
        elif cmd_type == 'update_player_activity':
            uid = parse_update_activity(command)
            self._handle_update_activity(uid)
        elif cmd_type == 'start_research':
            uid, research_type = parse_start_research(command)
            self._handle_start_research(uid, research_type)
        elif cmd_type == 'build_ships':
            uid, ship_type, qty = parse_build_ships(command)
            self._handle_build_ships(uid, ship_type, qty)
        elif cmd_type == 'colonize':
            uid, galaxy, system, position, planet_name = parse_colonize(command)
            self._handle_colonize(uid, galaxy, system, position, planet_name)
        elif cmd_type == 'fleet_dispatch':
            uid, galaxy, system, position, mission, speed, ships = parse_fleet_dispatch(command)
            self._handle_fleet_dispatch(uid, galaxy, system, position, mission, speed, ships)
        elif cmd_type == 'fleet_recall':
            uid, fleet_id = parse_fleet_recall(command)
            self._handle_fleet_recall(uid, fleet_id)
        elif cmd_type == 'trade_create_offer':
            uid, offered_resource, offered_amount, requested_resource, requested_amount = parse_trade_create_offer(command)
            self._handle_trade_create_offer(uid, offered_resource, offered_amount, requested_resource, requested_amount)
        elif cmd_type == 'trade_accept_offer':
            uid, offer_id = parse_trade_accept_offer(command)
            self._handle_trade_accept_offer(uid, offer_id)

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
            # Apply build time reductions: hyperspace research (player) and robot_factory (planet)
            try:
                from src.models import Research as _R, Buildings as _B
                from src.core.config import BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL, ROBOT_FACTORY_BUILD_TIME_REDUCTION_PER_LEVEL, MIN_BUILD_TIME_FACTOR
                r = self.world.component_for_entity(ent, _R)
                hyper_lvl = int(getattr(r, 'hyperspace', 0)) if r is not None else 0
                bld_comp = self.world.component_for_entity(ent, _B)
                rf_lvl = int(getattr(bld_comp, 'robot_factory', 0)) if bld_comp is not None else 0
                factor = (1.0 - BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL * hyper_lvl) * (1.0 - ROBOT_FACTORY_BUILD_TIME_REDUCTION_PER_LEVEL * rf_lvl)
                factor = max(MIN_BUILD_TIME_FACTOR, factor)
                build_time = int(max(1, build_time * factor))
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
                # Use naive local datetime for compatibility with tests; systems normalize to UTC when processing
                completion_time = datetime.now() + timedelta(seconds=build_time)
                # Planned duration metric
                try:
                    metrics.record_timer("queue.build.planned_s", float(build_time))
                except Exception:
                    pass
                build_queue.items.append({
                    'type': building_type,
                    'completion_time': completion_time,
                    'cost': cost,
                    'queued_at': datetime.now(),
                    'expected_duration_s': int(build_time),
                })

                # Persist to DB queue (best-effort)
                try:
                    new_level = int(current_level) + 1
                    enqueue_build_queue(self.world, ent, building_type, new_level, completion_time)
                except Exception:
                    pass

                logger.info(f"Started building {building_type} for user {user_id}")
                return

        logger.warning(f"Could not build {building_type} for user {user_id}")

    def _handle_update_activity(self, user_id: int) -> None:
        """Update player's last activity time."""
        for ent, (player,) in self.world.get_components(Player):
            if player.user_id == user_id:
                player.last_active = utc_now()
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
            # Apply research time reduction via research_lab on active planet
            try:
                from src.models import Buildings as _B
                from src.core.config import RESEARCH_LAB_TIME_REDUCTION_PER_LEVEL, MIN_RESEARCH_TIME_FACTOR
                bld_comp = self.world.component_for_entity(ent, _B)
                lab_lvl = int(getattr(bld_comp, 'research_lab', 0)) if bld_comp is not None else 0
                factor = max(MIN_RESEARCH_TIME_FACTOR, 1.0 - RESEARCH_LAB_TIME_REDUCTION_PER_LEVEL * lab_lvl)
                duration = int(max(1, duration * factor))
            except Exception:
                pass
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
                # Planned duration metric
                try:
                    metrics.record_timer("queue.research.planned_s", float(duration))
                except Exception:
                    pass
                research_queue.items.append({
                    'type': research_type,
                    'completion_time': completion_time,
                    'cost': cost,
                    'queued_at': datetime.now(),
                    'expected_duration_s': int(duration),
                })
                # Persist to DB research queue (best-effort)
                try:
                    new_level = int(current_level) + 1
                    enqueue_research(self.world, ent, research_type, new_level, completion_time)
                except Exception:
                    pass
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
            # Apply combined reductions: hyperspace research, shipyard level, and robot factory level
            try:
                from src.models import Research as _R
                from src.core.config import SHIPYARD_BUILD_TIME_REDUCTION_PER_LEVEL, ROBOT_FACTORY_BUILD_TIME_REDUCTION_PER_LEVEL
                r = self.world.component_for_entity(ent, _R)
                hyper_lvl = int(getattr(r, 'hyperspace', 0)) if r is not None else 0
                # Base multiplicative factors (each cannot reduce below MIN_BUILD_TIME_FACTOR when combined)
                hyper_factor = max(0.0, 1.0 - BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL * hyper_lvl)
                shipyard_factor = 1.0
                robot_factor = 1.0
                try:
                    # Use existing shipyard_level from above and robot_factory level from Buildings
                    shipyard_factor = max(0.0, 1.0 - SHIPYARD_BUILD_TIME_REDUCTION_PER_LEVEL * max(0, shipyard_level))
                    robot_lvl = int(getattr(buildings, 'robot_factory', 0)) if hasattr(buildings, 'robot_factory') else 0
                    robot_factor = max(0.0, 1.0 - ROBOT_FACTORY_BUILD_TIME_REDUCTION_PER_LEVEL * max(0, robot_lvl))
                except Exception:
                    pass
                combined = hyper_factor * shipyard_factor * robot_factor
                final_factor = max(MIN_BUILD_TIME_FACTOR, combined)
                duration = int(max(1, duration * final_factor))
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
                # Enforce shipyard queue size limit before enqueueing
                try:
                    from src.core.config import SHIPYARD_QUEUE_BASE_LIMIT, SHIPYARD_QUEUE_PER_LEVEL
                    current_len = 0
                    if getattr(ship_queue, 'items', None):
                        current_len = len(ship_queue.items)
                    queue_limit = int(SHIPYARD_QUEUE_BASE_LIMIT) + int(SHIPYARD_QUEUE_PER_LEVEL) * max(0, int(shipyard_level))
                    if current_len >= queue_limit:
                        try:
                            logger.info(
                                "shipyard_queue_full",
                                extra={
                                    "action_type": "shipyard_queue_full",
                                    "user_id": user_id,
                                    "current_len": current_len,
                                    "queue_limit": queue_limit,
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )
                        except Exception:
                            pass
                        return
                except Exception:
                    pass
                # Queue the construction
                completion_time = datetime.now() + timedelta(seconds=duration)
                # Planned duration metric
                try:
                    metrics.record_timer("queue.ship.planned_s", float(duration))
                except Exception:
                    pass
                ship_queue.items.append({
                    'type': ship_type,
                    'count': quantity,
                    'completion_time': completion_time,
                    'cost': total_cost,
                    'queued_at': datetime.now(),
                    'expected_duration_s': int(duration),
                })
                # Persist to DB best-effort when enabled
                try:
                    enqueue_ship_build(self.world, ent, ship_type, quantity, completion_time)
                except Exception:
                    pass
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
                # Persist updated fleet counts best-effort
                try:
                    from src.core.sync import upsert_fleet as _upsert_fleet
                    _upsert_fleet(self.world, ent_match)
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
                now = utc_now()
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
                # Persist mission best-effort
                try:
                    from src.core.sync import upsert_fleet_mission as _upsert_mission
                    _upsert_mission(self.world, ent, movement)
                except Exception:
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

        now = utc_now()
        # Find the player's entity that has an active FleetMovement
        for ent, (p, mv) in self.world.get_components(_P, _FM):
            # Normalize existing movement timestamps to aware UTC during recall handling
            try:
                mv.arrival_time = ensure_aware_utc(getattr(mv, 'arrival_time', None))
                mv.departure_time = ensure_aware_utc(getattr(mv, 'departure_time', None))
            except Exception:
                pass
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

            # Persist mission update best-effort
            try:
                from src.core.sync import upsert_fleet_mission as _upsert_mission
                _upsert_mission(self.world, ent, mv)
            except Exception:
                pass
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
                        'research_lab': getattr(buildings, 'research_lab', 0),
                        'fusion_reactor': getattr(buildings, 'fusion_reactor', 0),
                        'metal_storage': getattr(buildings, 'metal_storage', 0),
                        'crystal_storage': getattr(buildings, 'crystal_storage', 0),
                        'deuterium_tank': getattr(buildings, 'deuterium_tank', 0),
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
        """Store a battle report. Uses DB when enabled; otherwise in-memory.

        The report should include attacker_user_id and defender_user_id keys.
        """
        payload = dict(report or {})
        rid = None
        created_iso = None
        used_db = False
        # Try DB-backed storage first via sync helper
        try:
            from src.core.sync import create_battle_report as _create_br
            res = _create_br(payload)
            if res:
                rid, created_iso = res
                used_db = True
        except Exception:
            used_db = False

        if not used_db:
            # In-memory fallback
            try:
                rid = int(self._next_battle_report_id)
                self._next_battle_report_id += 1
            except Exception:
                rid = 1
                self._next_battle_report_id = 2
            payload["id"] = rid
            payload["created_at"] = datetime.now().isoformat()
            self._battle_reports.append(payload)
        else:
            payload["id"] = rid
            payload["created_at"] = created_iso

        # Log and notify (best-effort)
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
        """Return battle reports visible to a user (attacker or defender).
        When DB is enabled, callers should prefer API route's DB path; this is an in-memory fallback.
        """
        try:
            uid = int(user_id)
        except Exception:
            return []
        reports = [r for r in reversed(self._battle_reports) if r.get("attacker_user_id") == uid or r.get("defender_user_id") == uid]
        start = max(0, int(offset))
        end = max(start, start + int(limit))
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
        """Store an espionage report. Uses DB when enabled; otherwise in-memory.

        The report should include attacker_user_id, defender_user_id (optional if unoccupied),
        location {galaxy, system, planet}, and a snapshot payload.
        """
        payload = dict(report or {})
        rid = None
        created_iso = None
        used_db = False
        # Try DB-backed storage first via sync helper
        try:
            from src.core.sync import create_espionage_report as _create_er
            res = _create_er(payload)
            if res:
                rid, created_iso = res
                used_db = True
        except Exception:
            used_db = False

        if not used_db:
            try:
                rid = int(self._next_espionage_report_id)
                self._next_espionage_report_id += 1
            except Exception:
                rid = 1
                self._next_espionage_report_id = 2
            payload["id"] = rid
            payload["created_at"] = datetime.now().isoformat()
            self._espionage_reports.append(payload)
        else:
            payload["id"] = rid
            payload["created_at"] = created_iso

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
        Protected by a lightweight lock to prevent overlapping runs.
        """
        import time as _t
        if not hasattr(self, "_save_lock"):
            # Fallback for legacy instances
            self._save_lock = threading.Lock()
        if not self._save_lock.acquire(blocking=False):
            # Skip if a save is already in progress
            return
        _start = _t.perf_counter()
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
                    for attr in ("metal_mine", "crystal_mine", "deuterium_synthesizer", "solar_plant", "robot_factory", "shipyard", "metal_storage", "crystal_storage", "deuterium_tank", "research_lab", "fusion_reactor"):
                        if hasattr(b, attr):
                            lvl = getattr(b, attr)
                            sync_building_level(self.world, ent, attr, int(lvl))
                except Exception:
                    pass
        except Exception:
            # do not fail the loop
            pass
        finally:
            try:
                self._save_lock.release()
            except Exception:
                pass
            try:
                duration_s = _t.perf_counter() - _start
                from src.core.metrics import metrics as _metrics
                _metrics.increment_event("save.count", 1)
                _metrics.record_timer("save.duration_s", duration_s)
                self._last_save_ts = _t.time()
                try:
                    logger.info(
                        "save_complete",
                        extra={
                            "action_type": "save",
                            "duration_ms": duration_s * 1000.0,
                        },
                    )
                except Exception:
                    pass
            except Exception:
                pass

    def _apply_offline_resource_accrual(self) -> None:
        """Apply immediate resource accrual for all loaded entities based on elapsed time.

        Mirrors ResourceProductionSystem.process but runs once on autoload to ensure
        resources reflect time since last_update before the first tick.
        """
        try:
            now = utc_now()
            from src.models import Resources as _Res, ResourceProduction as _Prod, Buildings as _Bld, Research as _Resh, Planet as _Pln
            # Import config constants locally to avoid widening module imports
            from src.core.config import (
                ENERGY_SOLAR_BASE as _ENERGY_SOLAR_BASE,
                ENERGY_CONSUMPTION as _ENERGY_CONSUMPTION,
                PLASMA_PRODUCTION_BONUS as _PLASMA_BONUS,
                ENERGY_TECH_ENERGY_BONUS_PER_LEVEL as _ENERGY_BONUS_PER_LVL,
                ENERGY_SOLAR_GROWTH as _ENERGY_SOLAR_GROWTH,
                ENERGY_CONSUMPTION_GROWTH as _ENERGY_CONSUMPTION_GROWTH,
                BASE_PRODUCTION_RATES as _BASE_PRODUCTION_RATES,
                USE_CONFIG_PRODUCTION_RATES as _USE_CONFIG_PRODUCTION_RATES,
                temperature_multiplier as _temperature_multiplier,
                size_multiplier as _size_multiplier,
            )
            getter = getattr(self.world, "get_components", esper.get_components)
            for ent, (resources, production, buildings) in getter(_Res, _Prod, _Bld):
                try:
                    last_update_utc = ensure_aware_utc(production.last_update)
                    hours = (now - last_update_utc).total_seconds() / 3600.0
                    if hours <= 0:
                        continue
                    # Optional research bonuses
                    plasma_lvl = 0
                    energy_lvl = 0
                    try:
                        research = self.world.component_for_entity(ent, _Resh)
                        plasma_lvl = int(getattr(research, 'plasma', 0))
                        energy_lvl = int(getattr(research, 'energy', 0))
                    except Exception:
                        pass
                    # Energy balance factor
                    energy_bonus_factor = 1.0 + (_ENERGY_BONUS_PER_LVL * energy_lvl)
                    sp_lvl = max(0, int(getattr(buildings, 'solar_plant', 0)))
                    energy_produced = (
                        _ENERGY_SOLAR_BASE * sp_lvl * (_ENERGY_SOLAR_GROWTH ** max(0, sp_lvl - 1))
                    ) * energy_bonus_factor
                    # Consumption with optional non-linear growth per level
                    def _consumption(_base: float, _lvl: int) -> float:
                        _lvl = max(0, int(_lvl))
                        return _base * _lvl * (_ENERGY_CONSUMPTION_GROWTH ** max(0, _lvl - 1))
                    energy_required = 0.0
                    energy_required += _consumption(_ENERGY_CONSUMPTION.get('metal_mine', 0.0), getattr(buildings, 'metal_mine', 0))
                    energy_required += _consumption(_ENERGY_CONSUMPTION.get('crystal_mine', 0.0), getattr(buildings, 'crystal_mine', 0))
                    energy_required += _consumption(_ENERGY_CONSUMPTION.get('deuterium_synthesizer', 0.0), getattr(buildings, 'deuterium_synthesizer', 0))
                    # Apply energy factor with soft floor when there is some production and some requirement
                    if energy_required <= 0:
                        factor_raw = 1.0
                        factor = 1.0
                    elif energy_produced <= 0:
                        factor_raw = 0.0
                        factor = 0.0
                    else:
                        factor_raw = min(1.0, energy_produced / energy_required)
                        from src.core.config import ENERGY_DEFICIT_SOFT_FLOOR as _SOFT_FLOOR
                        factor = max(float(_SOFT_FLOOR), float(factor_raw))

                    # Determine base production rates (config-driven if enabled)
                    if _USE_CONFIG_PRODUCTION_RATES:
                        base_metal = _BASE_PRODUCTION_RATES.get('metal_mine', production.metal_rate)
                        base_crystal = _BASE_PRODUCTION_RATES.get('crystal_mine', production.crystal_rate)
                        base_deut = _BASE_PRODUCTION_RATES.get('deuterium_synthesizer', production.deuterium_rate)
                    else:
                        base_metal = production.metal_rate
                        base_crystal = production.crystal_rate
                        base_deut = production.deuterium_rate

                    # Planet modifiers (neutral 1.0 by default)
                    _temp_mult = 1.0
                    _size_mult = 1.0
                    try:
                        _planet = self.world.component_for_entity(ent, _Pln)
                        _temp_mult = float(_temperature_multiplier(int(getattr(_planet, 'temperature', 25))))
                        _size_mult = float(_size_multiplier(int(getattr(_planet, 'size', 163))))
                    except Exception:
                        pass
                    # Apply size multiplier to all resources; temperature only to deuterium
                    _planet_mult_size = _size_mult

                    # Base production with building multipliers and planet/energy modifiers
                    metal_prod = base_metal * (1.1 ** max(0, getattr(buildings, 'metal_mine', 0))) * hours * factor * _planet_mult_size
                    crystal_prod = base_crystal * (1.1 ** max(0, getattr(buildings, 'crystal_mine', 0))) * hours * factor * _planet_mult_size
                    deuterium_prod = base_deut * (1.1 ** max(0, getattr(buildings, 'deuterium_synthesizer', 0))) * hours * factor * _planet_mult_size * _temp_mult
                    if plasma_lvl > 0:
                        metal_prod *= (1.0 + _PLASMA_BONUS.get('metal', 0.0) * plasma_lvl)
                        crystal_prod *= (1.0 + _PLASMA_BONUS.get('crystal', 0.0) * plasma_lvl)
                        deuterium_prod *= (1.0 + _PLASMA_BONUS.get('deuterium', 0.0) * plasma_lvl)
                    d_metal = int(round(metal_prod))
                    d_crystal = int(round(crystal_prod))
                    d_deut = int(round(deuterium_prod))

                    # Capacity clamping based on storage building levels
                    try:
                        from src.core.config import STORAGE_BASE_CAPACITY as _SBC, STORAGE_CAPACITY_GROWTH as _SCG
                        ms_lvl = max(0, int(getattr(buildings, 'metal_storage', 0)))
                        cs_lvl = max(0, int(getattr(buildings, 'crystal_storage', 0)))
                        dt_lvl = max(0, int(getattr(buildings, 'deuterium_tank', 0)))
                        cap_m = int(_SBC.get('metal', 0) * (_SCG.get('metal', 1.0) ** ms_lvl))
                        cap_c = int(_SBC.get('crystal', 0) * (_SCG.get('crystal', 1.0) ** cs_lvl))
                        cap_d = int(_SBC.get('deuterium', 0) * (_SCG.get('deuterium', 1.0) ** dt_lvl))
                    except Exception:
                        cap_m = cap_c = cap_d = 2**31 - 1

                    # Apply deltas with clamping
                    before_m = resources.metal
                    before_c = resources.crystal
                    before_d = resources.deuterium
                    add_m = max(0, min(d_metal, max(0, cap_m - before_m)))
                    add_c = max(0, min(d_crystal, max(0, cap_c - before_c)))
                    add_d = max(0, min(d_deut, max(0, cap_d - before_d)))
                    if add_m or add_c or add_d:
                        resources.metal = before_m + add_m
                        resources.crystal = before_c + add_c
                        resources.deuterium = before_d + add_d
                    # Reset last_update to now to avoid double-accrual on first tick
                    production.last_update = now
                except Exception:
                    # Continue with next entity on any error to avoid breaking startup
                    continue
        except Exception:
            # Top-level guard; this helper is best-effort
            pass

    def load_player_data(self, user_id: Optional[int] = None) -> None:
        """Load player(s) from DB into ECS world.

        If user_id is provided, loads that user; otherwise loads all users.
        Also hydrates in-memory ID counters from DB maxima when DB is enabled to prevent collisions.
        """
        try:
            if user_id is None:
                load_all_players_into_world(self.world)
            else:
                load_player_into_world(self.world, user_id)
        except Exception:
            pass
        # Best-effort hydrate in-memory ID counters when DB is enabled
        try:
            from src.core.database import is_db_enabled as _is_db_enabled, SessionLocal as _SessionLocal
            if _is_db_enabled() and _SessionLocal is not None and user_id is None:
                import asyncio
                async def _hydrate_ids_and_set():
                    from sqlalchemy import select, func
                    from src.models.database import TradeOffer as _TO, TradeEvent as _TE, BattleReport as _BR, EspionageReport as _ER
                    async with _SessionLocal() as session:
                        # Reconcile next IDs from DB maxima
                        max_offer = (await session.execute(select(func.max(_TO.id)))).scalar() or 0
                        max_event = (await session.execute(select(func.max(_TE.id)))).scalar() or 0
                        max_battle = (await session.execute(select(func.max(_BR.id)))).scalar() or 0
                        max_esp = (await session.execute(select(func.max(_ER.id)))).scalar() or 0
                        try:
                            if max_offer:
                                self._next_offer_id = int(max_offer) + 1
                            if max_event:
                                self._next_trade_event_id = int(max_event) + 1
                            if max_battle:
                                self._next_battle_report_id = int(max_battle) + 1
                            if max_esp:
                                self._next_espionage_report_id = int(max_esp) + 1
                        except Exception:
                            pass
                        # Hydrate open market offers into in-memory ECS for gameplay operations (acceptance/escrow)
                        try:
                            # Load open offers newest first and merge without duplication
                            result = await session.execute(
                                select(_TO).where(_TO.status == 'open').order_by(_TO.created_at.desc())
                            )
                            rows = result.scalars().all()
                            existing_ids = {int(o.get('id')) for o in self._market_offers if 'id' in o}
                            for o in rows:
                                oid = int(getattr(o, 'id'))
                                if oid in existing_ids:
                                    continue
                                self._market_offers.append({
                                    'id': oid,
                                    'seller_user_id': int(getattr(o, 'seller_user_id')),
                                    'offered_resource': getattr(o, 'offered_resource'),
                                    'offered_amount': int(getattr(o, 'offered_amount')),
                                    'requested_resource': getattr(o, 'requested_resource'),
                                    'requested_amount': int(getattr(o, 'requested_amount')),
                                    'status': getattr(o, 'status'),
                                    'accepted_by': int(getattr(o, 'accepted_by')) if getattr(o, 'accepted_by') is not None else None,
                                    'created_at': getattr(o, 'created_at').isoformat() if getattr(o, 'created_at', None) else None,
                                    'accepted_at': getattr(o, 'accepted_at').isoformat() if getattr(o, 'accepted_at', None) else None,
                                })
                        except Exception:
                            # Best-effort hydration; continue on error
                            pass
                try:
                    import asyncio as _asyncio
                    try:
                        # If a loop is running (e.g., FastAPI lifespan), schedule the task
                        loop = _asyncio.get_running_loop()
                        loop.create_task(_hydrate_ids_and_set())
                    except RuntimeError:
                        # No running loop: safe to run synchronously
                        _asyncio.run(_hydrate_ids_and_set())
                except Exception:
                    # Best-effort; ignore any hydration errors
                    pass
        except Exception:
            pass
        # Hydrate ship build queue from DB when enabled; finalize overdue items immediately
        try:
            from src.core.database import is_db_enabled as _is_db_enabled
            if _is_db_enabled():
                for ent, (player, sbq, fleet) in self.world.get_components(Player, ShipBuildQueue, Fleet):
                    try:
                        items = load_ship_queue_items(self.world, ent) or []
                    except Exception:
                        items = []
                    if items and not getattr(sbq, 'items', None):
                        sbq.items = list(items)
                    # Finalize overdue items: apply to fleet and mark DB rows complete
                    try:
                        finalize_overdue_ship_builds(self.world, ent)
                    except Exception:
                        pass
                    # Remove any overdue items from in-memory queue to avoid double-processing
                    try:
                        now = utc_now()
                        while getattr(sbq, 'items', None):
                            ct = ensure_aware_utc(sbq.items[0].get('completion_time'))
                            if not ct or now < ct:
                                break
                            sbq.items.pop(0)
                    except Exception:
                        pass
        except Exception:
            pass

        # File-backed hydration for build/research queues removed (Postgres-only persistence)
        # Previously: file-backed load of queues; now removed.
        # Rationale: DB is the single source of truth; ECS will manage runtime queues, and systems
        # will persist changes via DB-backed helpers when implemented.
        # No action needed here.

        # Hydrate building and research queues from DB when enabled
        try:
            from src.core.database import is_db_enabled as _is_db_enabled
            if _is_db_enabled():
                # Building queue per planet
                from src.models import BuildQueue as _BQ
                for ent, (player, bq) in self.world.get_components(Player, _BQ):
                    try:
                        items = load_build_queue_items(self.world, ent) or []
                    except Exception:
                        items = []
                    if items and not getattr(bq, 'items', None):
                        bq.items = list(items)
                # Research queue per user
                from src.models import ResearchQueue as _RQ
                for ent, (player, rq) in self.world.get_components(Player, _RQ):
                    try:
                        ritems = load_research_queue_items(self.world, ent) or []
                    except Exception:
                        ritems = []
                    if ritems and not getattr(rq, 'items', None):
                        rq.items = list(ritems)
        except Exception:
            pass

        # Hydrate fleet missions from DB and finalize overdue ones immediately
        try:
            from src.core.database import is_db_enabled as _is_db_enabled
            if _is_db_enabled():
                from datetime import datetime as _dt
                from src.core.sync import load_fleet_mission as _load_mission, delete_fleet_mission as _delete_mission
                from src.models import FleetMovement as _FM, Position as _Pos
                for ent, (player, pos) in self.world.get_components(Player, Position):
                    # Skip if a movement component already attached (e.g., during same-process restart)
                    try:
                        _existing = self.world.component_for_entity(ent, _FM)
                        if _existing:
                            continue
                    except Exception:
                        pass
                    data = None
                    try:
                        data = _load_mission(self.world, ent)
                    except Exception:
                        data = None
                    if not data:
                        continue
                    # Build movement component
                    try:
                        origin = _Pos(galaxy=int(data['origin']['galaxy']), system=int(data['origin']['system']), planet=int(data['origin']['planet']))
                        target = _Pos(galaxy=int(data['target']['galaxy']), system=int(data['target']['system']), planet=int(data['target']['planet']))
                        dep = data.get('departure_time')
                        arr = data.get('arrival_time')
                        # Normalize tz-aware datetimes to naive for consistent comparisons
                        if hasattr(dep, 'tzinfo') and dep.tzinfo is not None:
                            dep = dep.replace(tzinfo=None)
                        if hasattr(arr, 'tzinfo') and arr.tzinfo is not None:
                            arr = arr.replace(tzinfo=None)
                        mv = _FM(
                            origin=origin,
                            target=target,
                            departure_time=dep,
                            arrival_time=arr,
                            speed=float(data.get('speed') or 1.0),
                            mission=str(data.get('mission') or 'transfer'),
                            owner_id=int(getattr(player, 'user_id', 0) or 0),
                            recalled=bool(data.get('recalled') or False),
                        )
                    except Exception:
                        continue
                    # If overdue, finalize immediately by applying target position and deleting mission
                    try:
                        now = _dt.now()
                        if now >= mv.arrival_time:
                            # Apply position to target (for recalled, target is origin already)
                            try:
                                pos.galaxy = int(mv.target.galaxy)
                                pos.system = int(mv.target.system)
                                pos.planet = int(mv.target.planet)
                            except Exception:
                                pass
                            try:
                                _delete_mission(self.world, ent)
                            except Exception:
                                pass
                            continue
                    except Exception:
                        pass
                    # Otherwise, attach component so system continues processing
                    try:
                        self.world.add_component(ent, mv)
                    except Exception:
                        pass
        except Exception:
            # Best-effort hydration; safe to ignore errors
            pass

        # Apply offline resource accrual immediately after hydration/finalization
        try:
            self._apply_offline_resource_accrual()
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
        """Record a trade event via the centralized service.

        Event shape (keys may be None depending on type):
        - type: 'offer_created' | 'trade_completed'
        - offer_id: int
        - seller_user_id: int
        - buyer_user_id: Optional[int]
        - offered_resource, offered_amount, requested_resource, requested_amount
        - status: 'open'|'completed'
        """
        # Preserve previous behavior: if DB is enabled, avoid in-memory duplication
        try:
            from src.core.database import is_db_enabled as _is_db_enabled
            if _is_db_enabled():
                return
        except Exception:
            pass
        try:
            # Delegate to service (handles in-memory and WS emission)
            from src.core.trade_events import record_trade_event_sync  # lazy import to avoid cycles
            record_trade_event_sync(event, gw=self)
        except Exception:
            # Preserve previous best-effort behavior
            try:
                logger.debug("record_trade_event_service_failed")
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

        # Apply transfers: buyer -> seller (requested minus fee), escrow -> buyer (offered)
        try:
            # Buyer pays full requested amount
            setattr(buyer_res, requested_resource, buyer_has - requested_amount)

            # Calculate fee and net to seller
            fee_rate = float(TRADE_TRANSACTION_FEE_RATE)
            fee_amount = int(requested_amount * fee_rate) if fee_rate > 0.0 else 0
            if fee_amount < 0:
                fee_amount = 0
            if fee_amount > requested_amount:
                fee_amount = requested_amount
            net_to_seller = requested_amount - fee_amount

            # Seller receives net; fee is burned (not added to any player)
            seller_current_req = int(getattr(seller_res, requested_resource, 0))
            setattr(seller_res, requested_resource, seller_current_req + net_to_seller)

            # Buyer receives offered resource from escrow
            buyer_current_offered = int(getattr(buyer_res, offered_resource, 0))
            setattr(buyer_res, offered_resource, buyer_current_offered + offered_amount)
        except Exception:
            return False

        # Metrics: track fee collected (units of requested resource)
        try:
            if fee_amount:
                metrics.increment_event("trade.fee_collected", fee_amount)
        except Exception:
            pass

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
