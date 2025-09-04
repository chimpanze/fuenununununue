from __future__ import annotations

"""Database sync utilities bridging ECS components and SQLAlchemy ORM.

These helpers provide minimal, safe persistence of key ECS state to the DB:
- Planet resources and production timestamps
- Building levels per planet

They are designed to be called from synchronous Esper systems; synchronous
wrappers delegate to async implementations using asyncio.run from the
background game loop thread. Errors are logged and swallowed to avoid
impacting game loop stability.
"""

import asyncio
import logging
import threading
from typing import Optional, Dict, List
from src.core.time_utils import utc_now, ensure_aware_utc, parse_utc

try:
    from sqlalchemy import select, update, delete
    from sqlalchemy.exc import SQLAlchemyError
    import src.core.database as db
    from src.models.database import User as ORMUser, Planet as ORMPlanet, Building as ORMBuilding, Fleet as ORMFleet, Research as ORMResearch, ShipBuildQueueItem as ORMSBQ, FleetMission as ORMFleetMission, BattleReport as ORMBattleReport, EspionageReport as ORMEspionageReport, BuildingQueueItem as ORMBQI, ResearchQueueItem as ORMRQI
    _DB_AVAILABLE = db.is_db_enabled()
except Exception:  # pragma: no cover - defensive import for environments without deps
    _DB_AVAILABLE = False

logger = logging.getLogger(__name__)
from src.core.metrics import metrics

# Captured FastAPI server loop used for DB persistence scheduling
_persistence_loop: Optional[asyncio.AbstractEventLoop] = None

def set_persistence_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Record the owning asyncio loop for DB operations.
    Safe to call once on FastAPI startup.
    """
    global _persistence_loop
    _persistence_loop = loop
    try:
        logger.debug(
            "persistence_loop_set",
            extra={
                "thread": threading.current_thread().name,
                "loop_id": id(loop),
            },
        )
    except Exception:
        pass


def _submit(coro, *, default=None, op: str = ""):
    """Schedule a coroutine on the captured persistence loop without waiting.

    Returns the provided default immediately. If the loop is not set, the call is a no-op.
    """
    loop = _persistence_loop
    if loop is None:
        try:
            logger.debug("persistence_loop_missing for %s", op)
        except Exception:
            pass
        return default
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            logger.debug(
                "persistence_submit",
                extra={
                    "op": op or getattr(getattr(coro, "__name__", None), "__str__", lambda: "")(),
                    "thread": threading.current_thread().name,
                    "loop_id": id(loop),
                },
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            logger.debug("persistence_submit_failed %s: %s", op, exc)
        except Exception:
            pass
    return default


def _submit_and_wait(coro, *, timeout: float = 2.0, default=None, op: str = ""):
    """Submit a coroutine to the captured loop and wait for a result briefly.

    Intended for read operations invoked from non-async, non-loop threads during
    startup or administrative flows. Falls back to default on timeout or errors.
    """
    loop = _persistence_loop
    if loop is None:
        try:
            logger.debug("persistence_loop_missing(wait) for %s", op)
        except Exception:
            pass
        return default
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    except Exception as exc:
        try:
            logger.debug("persistence_wait_failed %s: %s", op, exc)
        except Exception:
            pass
        return default


# Dynamic proxy to access SessionLocal at call time to reflect latest value from src.core.database
class _SessionLocalProxy:
    def __call__(self, *args, **kwargs):
        from src.core.database import SessionLocal as _SessionLocal  # dynamic lookup
        if _SessionLocal is None:
            raise RuntimeError("Database SessionLocal is not initialized")
        return _SessionLocal(*args, **kwargs)

# Expose name used throughout this module
SessionLocal = _SessionLocalProxy()

def _db_available() -> bool:
    try:
        from src.core.database import is_db_enabled as _is_db_enabled
        return bool(_is_db_enabled())
    except Exception:
        return False

# Persistence throttling (per planet key) to avoid excessive writes
# Centralized via src.core.config.PERSIST_INTERVAL_SECONDS
from src.core.config import PERSIST_INTERVAL_SECONDS
_last_persist: Dict[str, float] = {}

# -----------------
# Colonization helpers
# -----------------
async def _create_colony(user_id: int, username: str, galaxy: int, system: int, position: int, planet_name: str = "Colony") -> bool:
    """Create a new planet for the user at the given coordinates if unoccupied.

    Returns True on success, False if occupied or DB unavailable.
    """
    if not _db_available():
        # Allow ECS-only success when DB is unavailable
        return True
    try:
        async with SessionLocal() as session:
            # Is there already a planet at these coords? If yes, block.
            result = await session.execute(select(ORMPlanet).where(
                (ORMPlanet.galaxy == galaxy) & (ORMPlanet.system == system) & (ORMPlanet.position == position)
            ))
            existing = result.scalar_one_or_none()
            if existing is not None:
                return False
            # Ensure user exists (minimal)
            result = await session.execute(select(ORMUser).where(ORMUser.id == user_id))
            user = result.scalar_one_or_none()
            if user is None:
                user = ORMUser(id=user_id, username=username, email=None, password_hash=None)
                session.add(user)
                await session.flush()
            # Create planet
            planet = ORMPlanet(
                name=planet_name or "Colony",
                owner_id=user_id,
                galaxy=galaxy,
                system=system,
                position=position,
            )
            session.add(planet)
            await session.commit()
            return True
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("DB create_colony failed: %s", exc)
        return False


def create_colony(user_id: int, username: str, galaxy: int, system: int, position: int, planet_name: str = "Colony") -> bool:
    """Synchronous wrapper for _create_colony used from the game loop thread.

    Schedules work on the captured persistence loop and returns True if queued.
    Falls back to False on errors.
    """
    try:
        _submit(_create_colony(user_id, username, galaxy, system, position, planet_name), op="create_colony")
        return True
    except Exception as exc:  # pragma: no cover
        logger.debug("create_colony wrapper failed: %s", exc)
        return False


def _planet_key(user_id: int, galaxy: int, system: int, position: int) -> str:
    return f"{user_id}:{galaxy}:{system}:{position}"


def _should_persist(user_id: int, galaxy: int, system: int, position: int) -> bool:
    import time
    k = _planet_key(user_id, galaxy, system, position)
    now = time.time()
    last = _last_persist.get(k, 0.0)
    if now - last >= PERSIST_INTERVAL_SECONDS:
        _last_persist[k] = now
        return True
    return False


async def _ensure_user_and_planet_in_session(session, user_id: int, username: str, galaxy: int, system: int, position: int,
                                  planet_name: str, resources: Optional[dict] = None) -> Optional[ORMPlanet]:
    """Ensure user and planet exist within the given session and return the ORMPlanet bound to it."""
    # Ensure user exists
    result = await session.execute(select(ORMUser).where(ORMUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = ORMUser(id=user_id, username=username, email=None, password_hash=None)
        session.add(user)
        await session.flush()

    # Ensure planet exists by owner+coords
    result = await session.execute(
        select(ORMPlanet).where(
            (ORMPlanet.owner_id == user_id) &
            (ORMPlanet.galaxy == galaxy) &
            (ORMPlanet.system == system) &
            (ORMPlanet.position == position)
        )
    )
    planet = result.scalar_one_or_none()
    if planet is None:
        planet = ORMPlanet(
            name=planet_name or "Homeworld",
            owner_id=user_id,
            galaxy=galaxy,
            system=system,
            position=position,
        )
        if resources:
            planet.metal = resources.get("metal", planet.metal)
            planet.crystal = resources.get("crystal", planet.crystal)
            planet.deuterium = resources.get("deuterium", planet.deuterium)
        session.add(planet)
        await session.flush()

    # Apply initial resources if provided for existing planet (first sync)
    if resources and (planet.metal, planet.crystal, planet.deuterium) == (500, 300, 100):
        planet.metal = resources.get("metal", planet.metal)
        planet.crystal = resources.get("crystal", planet.crystal)
        planet.deuterium = resources.get("deuterium", planet.deuterium)

    return planet


async def _ensure_user_and_planet(user_id: int, username: str, galaxy: int, system: int, position: int,
                                  planet_name: str, resources: Optional[dict] = None) -> Optional[ORMPlanet]:
    if not _db_available():
        return None
    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet_in_session(session, user_id, username, galaxy, system, position, planet_name, resources)
            await session.commit()
            return planet
    except SQLAlchemyError as exc:  # pragma: no cover - resilience path
        logger.warning("DB ensure_user_and_planet failed: %s", exc)
        return None


async def sync_planet_resources_by_entity(world, ent) -> None:
    """Persist planet resource amounts and production metadata for the given entity.

    Expects the entity to have components: Player, Position, Planet, Resources, ResourceProduction.
    """
    if not _db_available():
        return
    try:
        from src.models import Player, Position, Planet as PlanetComp, Resources, ResourceProduction
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        res = world.component_for_entity(ent, Resources)
        prod = world.component_for_entity(ent, ResourceProduction)
    except Exception as exc:
        logger.debug("sync_planet_resources_by_entity: missing components for ent %s: %s", ent, exc)
        return

    # Throttle persistence to at most once per planet per interval
    if not _should_persist(player.user_id, pos.galaxy, pos.system, pos.planet):
        return

    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet_in_session(
                session,
                player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name,
                resources={"metal": res.metal, "crystal": res.crystal, "deuterium": res.deuterium},
            )
            if planet is None:
                return
            planet.metal = res.metal
            planet.crystal = res.crystal
            planet.deuterium = res.deuterium
            planet.metal_rate = float(prod.metal_rate)
            planet.crystal_rate = float(prod.crystal_rate)
            planet.deuterium_rate = float(prod.deuterium_rate)
            planet.last_update = ensure_aware_utc(prod.last_update)
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.debug("DB sync_planet_resources failed (transient): %s", exc)


async def upsert_building_level_by_entity(world, ent, building_type: str, level: int) -> None:
    """Upsert a Building row for the entity's planet and set its level."""
    if not _db_available():
        return
    try:
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
    except Exception as exc:
        logger.debug("upsert_building_level_by_entity: missing components for ent %s: %s", ent, exc)
        return

    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet_in_session(
                session,
                player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name
            )
            if planet is None:
                return
            # Find or create building row for this type
            result = await session.execute(
                select(ORMBuilding).where(
                    (ORMBuilding.planet_id == planet.id) & (ORMBuilding.type == building_type)
                )
            )
            b = result.scalar_one_or_none()
            if b is None:
                b = ORMBuilding(planet_id=planet.id, type=building_type, level=int(level))
                session.add(b)
            else:
                b.level = int(level)
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.debug("DB upsert_building_level failed (transient): %s", exc)


def sync_planet_resources(world, ent) -> None:
    """Synchronous wrapper safe to call from Esper systems.

    Off-thread DB work is scheduled on the FastAPI server loop via run_coroutine_threadsafe.
    """
    if not _db_available():
        return
    try:
        _submit(sync_planet_resources_by_entity(world, ent), op="sync_planet_resources_by_entity")
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_planet_resources wrapper failed: %s", exc)


def sync_building_level(world, ent, building_type: str, level: int) -> None:
    """Synchronous wrapper to upsert building level for given entity.

    Off-thread DB work is scheduled on the FastAPI server loop via run_coroutine_threadsafe.
    """
    if not _db_available():
        return
    try:
        _submit(upsert_building_level_by_entity(world, ent, building_type, level), op="upsert_building_level_by_entity")
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_building_level wrapper failed: %s", exc)


# -----------------
# Payload-based API
# -----------------
async def sync_planet_resources_with_payload(user_id: int, username: str, galaxy: int, system: int, position: int,
                                             planet_name: str, metal: int, crystal: int, deuterium: int,
                                             metal_rate: float, crystal_rate: float, deuterium_rate: float,
                                             last_update) -> None:
    if not _db_available():
        return
    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet_in_session(
                session,
                user_id, username, galaxy, system, position, planet_name,
                resources={"metal": metal, "crystal": crystal, "deuterium": deuterium},
            )
            if planet is None:
                return
            planet.metal = metal
            planet.crystal = crystal
            planet.deuterium = deuterium
            planet.metal_rate = float(metal_rate)
            planet.crystal_rate = float(crystal_rate)
            planet.deuterium_rate = float(deuterium_rate)
            planet.last_update = ensure_aware_utc(parse_utc(last_update))
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.debug("DB sync_planet_resources_with_payload failed (transient): %s", exc)


def sync_planet_resources_payload(user_id: int, username: str, galaxy: int, system: int, position: int,
                                  planet_name: str, metal: int, crystal: int, deuterium: int,
                                  metal_rate: float, crystal_rate: float, deuterium_rate: float,
                                  last_update) -> None:
    if not _db_available():
        return
    try:
        _submit(
            sync_planet_resources_with_payload(
                user_id, username, galaxy, system, position, planet_name,
                metal, crystal, deuterium, metal_rate, crystal_rate, deuterium_rate, last_update
            ),
            op="sync_planet_resources_with_payload",
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_planet_resources_payload wrapper failed: %s", exc)


async def upsert_building_level_with_payload(user_id: int, username: str, galaxy: int, system: int, position: int,
                                             planet_name: str, building_type: str, level: int) -> None:
    if not _db_available():
        return
    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet_in_session(session, user_id, username, galaxy, system, position, planet_name)
            if planet is None:
                return
            result = await session.execute(
                select(ORMBuilding).where(
                    (ORMBuilding.planet_id == planet.id) & (ORMBuilding.type == building_type)
                )
            )
            b = result.scalar_one_or_none()
            if b is None:
                b = ORMBuilding(planet_id=planet.id, type=building_type, level=int(level))
                session.add(b)
            else:
                b.level = int(level)
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("DB upsert_building_level_with_payload failed: %s", exc)


def sync_building_level_payload(user_id: int, username: str, galaxy: int, system: int, position: int,
                                planet_name: str, building_type: str, level: int) -> None:
    if not _db_available():
        return
    try:
        _submit(
            upsert_building_level_with_payload(user_id, username, galaxy, system, position, planet_name, building_type, level),
            op="upsert_building_level_with_payload",
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_building_level_payload wrapper failed: %s", exc)


# -----------------
# Fleet persistence helpers
# -----------------
async def _upsert_fleet_by_entity(world, ent) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player as _P, Position as _Pos, Planet as _Pl, Fleet as _Fl
        player = world.component_for_entity(ent, _P)
        pos = world.component_for_entity(ent, _Pos)
        pmeta = world.component_for_entity(ent, _Pl)
        fleet = world.component_for_entity(ent, _Fl)
    except Exception as exc:
        logger.debug("upsert_fleet_by_entity: missing components for ent %s: %s", ent, exc)
        return

    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet_in_session(
                session,
                int(player.user_id), getattr(player, 'name', f"User{player.user_id}"), int(pos.galaxy), int(pos.system), int(pos.planet), getattr(pmeta, 'name', 'Homeworld')
            )
            if planet is None:
                return
            # Get or create Fleet row
            result = await session.execute(select(ORMFleet).where(ORMFleet.planet_id == planet.id))
            orm_fleet = result.scalar_one_or_none()
            values = {
                'light_fighter': int(getattr(fleet, 'light_fighter', 0) or 0),
                'heavy_fighter': int(getattr(fleet, 'heavy_fighter', 0) or 0),
                'cruiser': int(getattr(fleet, 'cruiser', 0) or 0),
                'battleship': int(getattr(fleet, 'battleship', 0) or 0),
                'bomber': int(getattr(fleet, 'bomber', 0) or 0),
                'colony_ship': int(getattr(fleet, 'colony_ship', 0) or 0),
            }
            if orm_fleet is None:
                orm_fleet = ORMFleet(planet_id=planet.id, **values)
                session.add(orm_fleet)
            else:
                for k, v in values.items():
                    setattr(orm_fleet, k, v)
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.debug("upsert_fleet failed (transient): %s", exc)
    except Exception:
        # Defensive; do not break gameplay if components missing
        pass


def upsert_fleet(world, ent) -> None:
    if not _db_available():
        return
    try:
        _submit(_upsert_fleet_by_entity(world, ent), op="upsert_fleet_by_entity")
    except Exception as exc:
        logger.debug("upsert_fleet wrapper failed: %s", exc)


# -----------------
# Loaders & Atomic Ops & Cleanup
# -----------------
async def _load_player_planet_into_world(world, user_id: int, planet_id: int) -> bool:
    """Load a specific planet for the user into ECS, replacing in-place components.

    Returns True on success, False if DB unavailable or validation fails.
    """
    if not _db_available():
        return False
    try:
        from src.models import Player as PlayerComp, Position, Resources, ResourceProduction, Buildings, BuildQueue, ShipBuildQueue as ShipBuildQueueComp, Fleet as FleetComp, Research as ResearchComp, ResearchQueue as ResearchQueueComp, Planet as PlanetComp
        from src.models.database import Fleet as ORMFleet, Research as ORMResearch
        async with SessionLocal() as session:
            # Validate user and planet ownership
            result = await session.execute(select(ORMUser).where(ORMUser.id == user_id))
            orm_user = result.scalar_one_or_none()
            if orm_user is None:
                return False
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.id == planet_id))
            planet = result.scalar_one_or_none()
            if planet is None or int(planet.owner_id) != int(user_id):
                return False
            # Buildings for this planet
            result = await session.execute(select(ORMBuilding).where(ORMBuilding.planet_id == planet.id))
            buildings_map: Dict[str, int] = {b.type: int(b.level) for b in result.scalars().all()}
            # Fleet for this planet
            result = await session.execute(select(ORMFleet).where(ORMFleet.planet_id == planet.id))
            orm_fleet = result.scalar_one_or_none()
            # Research by user
            result = await session.execute(select(ORMResearch).where(ORMResearch.user_id == orm_user.id))
            orm_research = result.scalar_one_or_none()

            # Find existing player entity in world
            ent_found = None
            for ent, (p,) in world.get_components(PlayerComp):
                if p.user_id == orm_user.id:
                    ent_found = ent
                    break
            if ent_found is None:
                # If no entity exists yet, delegate to general loader which creates it
                # and then replace with selected planet afterwards.
                from .sync import _load_player_into_world  # type: ignore
                await _load_player_into_world(world, user_id)
                for ent, (p,) in world.get_components(PlayerComp):
                    if p.user_id == orm_user.id:
                        ent_found = ent
                        break
            if ent_found is None:
                return False

            # Prepare ECS components reflecting the selected planet
            player = PlayerComp(name=orm_user.username or f"User{orm_user.id}", user_id=orm_user.id)
            position = Position(galaxy=planet.galaxy, system=planet.system, planet=planet.position)
            resources = Resources(metal=planet.metal, crystal=planet.crystal, deuterium=planet.deuterium)
            production = ResourceProduction(metal_rate=planet.metal_rate, crystal_rate=planet.crystal_rate, deuterium_rate=planet.deuterium_rate, last_update=planet.last_update)
            buildings = Buildings()
            for key, lvl in buildings_map.items():
                if hasattr(buildings, key):
                    setattr(buildings, key, int(lvl))
            build_queue = BuildQueue()
            ship_queue = ShipBuildQueueComp()
            fleet = FleetComp()
            if orm_fleet:
                for fld in ("light_fighter","heavy_fighter","cruiser","battleship","bomber","colony_ship"):
                    setattr(fleet, fld, getattr(orm_fleet, fld, 0))
            research = ResearchComp()
            if orm_research:
                for fld in ("energy","laser","ion","hyperspace","plasma","computer"):
                    setattr(research, fld, getattr(orm_research, fld, 0))
            research_queue = ResearchQueueComp()
            planet_meta = PlanetComp(name=planet.name, owner_id=orm_user.id)

            # Replace/update components on the existing entity
            from src.models import Player as P, Position as Pos, Resources as Res, ResourceProduction as RP, Buildings as Bld, BuildQueue as BQ, ShipBuildQueue as SBQ, Fleet as Fl, Research as Rs, ResearchQueue as Rq, Planet as Pl
            comps = {
                P: player, Pos: position, Res: resources, RP: production, Bld: buildings, BQ: build_queue, SBQ: ship_queue, Fl: fleet, Rs: research, Rq: research_queue, Pl: planet_meta
            }
            for ctype, newc in comps.items():
                try:
                    old = world.component_for_entity(ent_found, ctype)
                    world.remove_component(ent_found, ctype)
                    world.add_component(ent_found, newc)
                except Exception:
                    try:
                        world.add_component(ent_found, newc)
                    except Exception:
                        pass
            return True
    except Exception as exc:  # pragma: no cover
        logger.warning("load_player_planet_into_world failed: %s", exc)
        return False

async def _load_player_into_world(world, user_id: int) -> None:
    """Load a player's state from DB into ECS world, creating or updating the entity."""
    if not _db_available():
        return
    try:
        from src.models import Player as PlayerComp, Position, Resources, ResourceProduction, Buildings, BuildQueue, ShipBuildQueue as ShipBuildQueueComp, Fleet as FleetComp, Research as ResearchComp, ResearchQueue as ResearchQueueComp, Planet as PlanetComp
        from src.models.database import Fleet as ORMFleet, Research as ORMResearch
        async with SessionLocal() as session:
            result = await session.execute(select(ORMUser).where(ORMUser.id == user_id))
            orm_user = result.scalar_one_or_none()
            if orm_user is None:
                return
            # Choose first planet (or create default planet record if none)
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.owner_id == orm_user.id))
            planet = result.scalars().first()
            if planet is None:
                # Optionally skip auto-creation when start choice is required
                try:
                    from src.core.config import REQUIRE_START_CHOICE
                except Exception:
                    REQUIRE_START_CHOICE = False
                if REQUIRE_START_CHOICE:
                    return
                # Ensure a default planet exists for this user (legacy behavior)
                planet = await _ensure_user_and_planet(orm_user.id, orm_user.username or f"User{orm_user.id}", 1, 1, 1, "Homeworld")
                if planet is None:
                    return
                result = await session.execute(select(ORMPlanet).where(ORMPlanet.id == planet.id))
                planet = result.scalar_one()
            # Buildings
            result = await session.execute(select(ORMBuilding).where(ORMBuilding.planet_id == planet.id))
            buildings_map: Dict[str, int] = {b.type: int(b.level) for b in result.scalars().all()}
            # Fleet
            result = await session.execute(select(ORMFleet).where(ORMFleet.planet_id == planet.id))
            orm_fleet = result.scalar_one_or_none()
            # Research
            result = await session.execute(select(ORMResearch).where(ORMResearch.user_id == orm_user.id))
            orm_research = result.scalar_one_or_none()

            # Find existing entity
            ent_found = None
            for ent, (p,) in world.get_components(PlayerComp):
                if p.user_id == orm_user.id:
                    ent_found = ent
                    break

            # Prepare ECS components
            player = PlayerComp(name=orm_user.username or f"User{orm_user.id}", user_id=orm_user.id)
            position = Position(galaxy=planet.galaxy, system=planet.system, planet=planet.position)
            resources = Resources(metal=planet.metal, crystal=planet.crystal, deuterium=planet.deuterium)
            production = ResourceProduction(metal_rate=planet.metal_rate, crystal_rate=planet.crystal_rate, deuterium_rate=planet.deuterium_rate, last_update=planet.last_update)
            buildings = Buildings()
            for key, lvl in buildings_map.items():
                if hasattr(buildings, key):
                    setattr(buildings, key, int(lvl))
            build_queue = BuildQueue()
            ship_queue = ShipBuildQueueComp()
            fleet = FleetComp()
            if orm_fleet:
                for fld in ("light_fighter","heavy_fighter","cruiser","battleship","bomber","colony_ship"):
                    setattr(fleet, fld, getattr(orm_fleet, fld, 0))
            research = ResearchComp()
            if orm_research:
                for fld in ("energy","laser","ion","hyperspace","plasma","computer"):
                    setattr(research, fld, getattr(orm_research, fld, 0))
            research_queue = ResearchQueueComp()
            planet_meta = PlanetComp(name=planet.name, owner_id=orm_user.id)

            if ent_found is None:
                world.create_entity(player, position, resources, production, buildings, build_queue, ship_queue, fleet, research, research_queue, planet_meta)
            else:
                # Update in-place
                from src.models import Player as P, Position as Pos, Resources as Res, ResourceProduction as RP, Buildings as Bld, BuildQueue as BQ, ShipBuildQueue as SBQ, Fleet as Fl, Research as Rs, ResearchQueue as Rq, Planet as Pl
                comps = {
                    P: player, Pos: position, Res: resources, RP: production, Bld: buildings, BQ: build_queue, SBQ: ship_queue, Fl: fleet, Rs: research, Rq: research_queue, Pl: planet_meta
                }
                for ctype, newc in comps.items():
                    try:
                        old = world.component_for_entity(ent_found, ctype)
                        world.remove_component(ent_found, ctype)
                        world.add_component(ent_found, newc)
                    except Exception:
                        try:
                            world.add_component(ent_found, newc)
                        except Exception:
                            pass
    except Exception as exc:  # pragma: no cover
        logger.warning("load_player_into_world failed: %s", exc)


def load_player_into_world(world, user_id: int) -> None:
    if not _db_available():
        return
    try:
        _submit(_load_player_into_world(world, user_id), op="load_player_into_world")
    except Exception as exc:
        logger.debug("load_player_into_world wrapper failed: %s", exc)


def load_player_planet_into_world(world, user_id: int, planet_id: int) -> bool:
    """Synchronous wrapper to load a specific planet for a user into ECS.

    Submits to the persistence loop and waits briefly for completion.
    Returns True on success, False on timeout or errors.
    """
    if not _db_available():
        return False
    try:
        result = _submit_and_wait(_load_player_planet_into_world(world, user_id, planet_id), timeout=2.0, default=False, op="load_player_planet_into_world")
        return bool(result)
    except Exception as exc:  # pragma: no cover
        logger.debug("load_player_planet_into_world wrapper failed: %s", exc)
        return False


async def _load_all_players_into_world(world) -> None:
    if not _db_available():
        return
    try:
        async with SessionLocal() as session:
            result = await session.execute(select(ORMUser.id))
            ids = [row[0] for row in result.all()]
        for uid in ids:
            await _load_player_into_world(world, uid)
    except Exception as exc:
        logger.warning("load_all_players_into_world failed: %s", exc)


def load_all_players_into_world(world) -> None:
    if not _db_available():
        return
    try:
        _submit(_load_all_players_into_world(world), op="load_all_players_into_world")
    except Exception as exc:
        logger.debug("load_all_players_into_world wrapper failed: %s", exc)


# Atomic resource spend
async def _spend_resources_atomic_by_entity(world, ent, cost: Dict[str, int]) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.id == planet.id))
            planet = result.scalar_one()
            if planet.metal < cost.get("metal", 0) or planet.crystal < cost.get("crystal", 0) or planet.deuterium < cost.get("deuterium", 0):
                return
            planet.metal -= int(cost.get("metal", 0))
            planet.crystal -= int(cost.get("crystal", 0))
            planet.deuterium -= int(cost.get("deuterium", 0))
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("spend_resources_atomic failed: %s", exc)


def spend_resources_atomic(world, ent, cost: Dict[str, int]) -> None:
    if not _db_available():
        return
    try:
        _submit(_spend_resources_atomic_by_entity(world, ent, cost), op="spend_resources_atomic_by_entity")
    except Exception as exc:
        logger.debug("spend_resources_atomic wrapper failed: %s", exc)


# Ship Build Queue persistence helpers
async def _enqueue_ship_build_by_entity(world, ent, ship_type: str, count: int, completion_time) -> None:
    if not _db_available():
        return
    try:
        from datetime import datetime as _dt
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return
            # Re-fetch within session to guarantee attached instance
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.id == planet.id))
            planet = result.scalar_one()
            item = ORMSBQ(
                planet_id=planet.id,
                ship_type=str(ship_type),
                count=int(count),
                completion_time=ensure_aware_utc(parse_utc(completion_time)) if completion_time is not None else utc_now(),
            )
            session.add(item)
            await session.commit()
            try:
                logger.info(
                    "ship_build_enqueued",
                    extra={
                        "action_type": "ship_build_enqueued",
                        "planet_id": int(getattr(planet, "id", 0) or 0),
                        "ship_type": str(getattr(item, "ship_type", "")),
                        "count": int(getattr(item, "count", 0) or 0),
                        "completion_time": ensure_aware_utc(getattr(item, "completion_time", utc_now())).isoformat(),
                    },
                )
            except Exception:
                pass
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("enqueue_ship_build failed: %s", exc)
    except Exception:
        # Defensive: do not break gameplay if components missing
        pass


def enqueue_ship_build(world, ent, ship_type: str, count: int, completion_time) -> None:
    if not _db_available():
        return
    try:
        _submit(_enqueue_ship_build_by_entity(world, ent, ship_type, count, completion_time), op="enqueue_ship_build_by_entity")
    except Exception as exc:
        logger.debug("enqueue_ship_build wrapper failed: %s", exc)


async def _load_ship_queue_items_by_entity(world, ent) -> List[Dict]:
    if not _db_available():
        return []
    try:
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return []
            result = await session.execute(select(ORMSBQ).where((ORMSBQ.planet_id == planet.id) & (ORMSBQ.completed_at == None)).order_by(ORMSBQ.id.asc()))
            rows = result.scalars().all()
            items: List[Dict] = []
            for r in rows:
                items.append({
                    'type': getattr(r, 'ship_type'),
                    'count': int(getattr(r, 'count', 1)),
                    'completion_time': ensure_aware_utc(getattr(r, 'completion_time')),
                })
            return items
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("load_ship_queue_items failed: %s", exc)
        return []
    except Exception:
        return []


def load_ship_queue_items(world, ent) -> List[Dict]:
    if not _db_available():
        return []
    try:
        return _submit_and_wait(_load_ship_queue_items_by_entity(world, ent), timeout=2.0, default=[], op="load_ship_queue_items")
    except Exception as exc:
        logger.debug("load_ship_queue_items wrapper failed: %s", exc)
        return []


async def _complete_next_ship_build_by_entity(world, ent) -> None:
    if not _db_available():
        return
    try:
        from datetime import datetime as _dt
        from sqlalchemy import update
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return
            # Select earliest uncompleted item
            result = await session.execute(
                select(ORMSBQ).where((ORMSBQ.planet_id == planet.id) & (ORMSBQ.completed_at == None)).order_by(ORMSBQ.id.asc()).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            await session.execute(
                update(ORMSBQ).where(ORMSBQ.id == row.id).values(completed_at=utc_now())
            )
            await session.commit()
            try:
                logger.info(
                    "ship_build_completed",
                    extra={
                        "action_type": "ship_build_completed",
                        "queue_item_id": int(getattr(row, "id", 0) or 0),
                        "planet_id": int(getattr(planet, "id", 0) or 0),
                        "ship_type": str(getattr(row, "ship_type", "")),
                        "count": int(getattr(row, "count", 0) or 0),
                    },
                )
            except Exception:
                pass
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("complete_next_ship_build failed: %s", exc)


def complete_next_ship_build(world, ent) -> None:
    if not _db_available():
        return
    try:
        _submit(_complete_next_ship_build_by_entity(world, ent), op="complete_next_ship_build_by_entity")
    except Exception as exc:
        logger.debug("complete_next_ship_build wrapper failed: %s", exc)


async def _finalize_overdue_ship_builds_by_entity(world, ent) -> None:
    if not _db_available():
        return
    try:
        from datetime import datetime as _dt
        from sqlalchemy import update
        from src.models import Player, Position, Planet as PlanetComp, Fleet as FleetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        fleet = world.component_for_entity(ent, FleetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return
            now = utc_now()
            result = await session.execute(
                select(ORMSBQ).where((ORMSBQ.planet_id == planet.id) & (ORMSBQ.completed_at == None) & (ORMSBQ.completion_time <= now)).order_by(ORMSBQ.id.asc())
            )
            rows = result.scalars().all()
            for row in rows:
                # Apply to ECS fleet
                st = getattr(row, 'ship_type')
                cnt = int(getattr(row, 'count', 1))
                if st and hasattr(fleet, st):
                    try:
                        cur = int(getattr(fleet, st))
                        setattr(fleet, st, cur + max(0, cnt))
                    except Exception:
                        pass
                # Mark as completed in DB
                await session.execute(update(ORMSBQ).where(ORMSBQ.id == row.id).values(completed_at=now))
            if rows:
                await session.commit()
                try:
                    logger.info(
                        "ship_builds_finalized",
                        extra={
                            "action_type": "ship_builds_finalized",
                            "planet_id": int(getattr(planet, "id", 0) or 0),
                            "finalized_count": int(len(rows)),
                        },
                    )
                except Exception:
                    pass
                # Persist updated fleet counts reflecting finalized ship builds
                try:
                    await _upsert_fleet_by_entity(world, ent)
                except Exception:
                    pass
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("finalize_overdue_ship_builds failed: %s", exc)


def finalize_overdue_ship_builds(world, ent) -> None:
    if not _db_available():
        return
    try:
        _submit(_finalize_overdue_ship_builds_by_entity(world, ent), op="finalize_overdue_ship_builds_by_entity")
    except Exception as exc:
        logger.debug("finalize_overdue_ship_builds wrapper failed: %s", exc)


# Fleet mission persistence helpers
async def _upsert_fleet_mission_by_entity(world, ent, movement) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player as _P
        player = world.component_for_entity(ent, _P)
        async with SessionLocal() as session:
            # Check for existing mission for this user (one active mission per user in current model)
            result = await session.execute(select(ORMFleetMission).where(ORMFleetMission.user_id == int(player.user_id)))
            row = result.scalar_one_or_none()
            values = {
                'user_id': int(player.user_id),
                'origin_galaxy': int(getattr(movement.origin, 'galaxy', 1)),
                'origin_system': int(getattr(movement.origin, 'system', 1)),
                'origin_planet': int(getattr(movement.origin, 'planet', 1)),
                'target_galaxy': int(getattr(movement.target, 'galaxy', 1)),
                'target_system': int(getattr(movement.target, 'system', 1)),
                'target_planet': int(getattr(movement.target, 'planet', 1)),
                'mission': str(getattr(movement, 'mission', 'transfer')),
                'speed': float(getattr(movement, 'speed', 1.0) or 1.0),
                'recalled': bool(getattr(movement, 'recalled', False)),
                'departure_time': ensure_aware_utc(getattr(movement, 'departure_time')),
                'arrival_time': ensure_aware_utc(getattr(movement, 'arrival_time')),
            }
            if row is None:
                row = ORMFleetMission(**values)
                session.add(row)
            else:
                for k, v in values.items():
                    setattr(row, k, v)
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("upsert_fleet_mission failed: %s", exc)
    except Exception:
        # Defensive; do not break gameplay if components missing
        pass


def upsert_fleet_mission(world, ent, movement) -> None:
    if not _db_available():
        return
    try:
        _submit(_upsert_fleet_mission_by_entity(world, ent, movement), op="upsert_fleet_mission_by_entity")
    except Exception as exc:
        logger.debug("upsert_fleet_mission wrapper failed: %s", exc)


async def _load_fleet_mission_by_entity(world, ent):
    if not _db_available():
        return None
    try:
        from src.models import Player as _P
        player = world.component_for_entity(ent, _P)
        async with SessionLocal() as session:
            result = await session.execute(select(ORMFleetMission).where(ORMFleetMission.user_id == int(player.user_id)))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                'origin': {'galaxy': int(row.origin_galaxy), 'system': int(row.origin_system), 'planet': int(row.origin_planet)},
                'target': {'galaxy': int(row.target_galaxy), 'system': int(row.target_system), 'planet': int(row.target_planet)},
                'mission': getattr(row, 'mission'),
                'speed': float(getattr(row, 'speed', 1.0) or 1.0),
                'recalled': bool(getattr(row, 'recalled', False)),
                'departure_time': ensure_aware_utc(getattr(row, 'departure_time')),
                'arrival_time': ensure_aware_utc(getattr(row, 'arrival_time')),
            }
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("load_fleet_mission failed: %s", exc)
        return None
    except Exception:
        return None


def load_fleet_mission(world, ent):
    if not _db_available():
        return None
    try:
        return _submit_and_wait(_load_fleet_mission_by_entity(world, ent), timeout=2.0, default=None, op="load_fleet_mission")
    except Exception as exc:
        logger.debug("load_fleet_mission wrapper failed: %s", exc)
        return None


async def _delete_fleet_mission_by_entity(world, ent) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player as _P
        player = world.component_for_entity(ent, _P)
        async with SessionLocal() as session:
            await session.execute(delete(ORMFleetMission).where(ORMFleetMission.user_id == int(player.user_id)))
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("delete_fleet_mission failed: %s", exc)
    except Exception:
        pass


def delete_fleet_mission(world, ent) -> None:
    if not _db_available():
        return
    try:
        _submit(_delete_fleet_mission_by_entity(world, ent), op="delete_fleet_mission_by_entity")
    except Exception as exc:
        logger.debug("delete_fleet_mission wrapper failed: %s", exc)


# Cleanup inactive users
async def _cleanup_inactive_players(days: int = 30) -> int:
    if not _db_available():
        return 0
    try:
        from datetime import datetime, timedelta
        from sqlalchemy import delete
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with SessionLocal() as session:
            # Delete users with last_login older than cutoff or never logged in and created_at older
            stmt = delete(ORMUser).where(((ORMUser.last_login != None) & (ORMUser.last_login < cutoff)) | ((ORMUser.last_login == None) & (ORMUser.created_at < cutoff)))
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("cleanup_inactive_players failed: %s", exc)
        return 0


def cleanup_inactive_players(days: int = 30) -> int:
    if not _db_available():
        return 0
    try:
        res = _submit_and_wait(_cleanup_inactive_players(days), timeout=2.0, default=0, op="cleanup_inactive_players")
        return int(res or 0)
    except Exception as exc:
        logger.debug("cleanup_inactive_players wrapper failed: %s", exc)
        return 0


# -----------------
# Battle Reports helpers
# -----------------
async def fetch_battle_reports_for_user(user_id: int, limit: int = 50, offset: int = 0) -> List[dict]:
    """Async: List battle reports visible to a user (attacker or defender), newest-first.

    Returns empty list if DB unavailable.
    """
    if not _db_available():
        return []
    try:
        async with SessionLocal() as session:  # type: ignore[misc]
            from sqlalchemy import select, or_  # local import to avoid widening globals
            stmt = (
                select(ORMBattleReport)
                .where(
                    (ORMBattleReport.attacker_user_id == int(user_id))
                    | (ORMBattleReport.defender_user_id == int(user_id))
                )
                .order_by(ORMBattleReport.created_at.desc())
                .offset(int(offset))
                .limit(int(limit))
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": int(r.id),
                    "attacker_user_id": int(r.attacker_user_id) if r.attacker_user_id is not None else None,
                    "defender_user_id": int(r.defender_user_id) if r.defender_user_id is not None else None,
                    "location": r.location,
                    "outcome": r.outcome,
                    "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
                }
                for r in rows
            ]
    except Exception as exc:  # pragma: no cover - resilience path
        logger.warning("fetch_battle_reports_for_user failed: %s", exc)
        return []


async def fetch_battle_report_for_user(user_id: int, report_id: int) -> Optional[dict]:
    """Async: Retrieve a single battle report if the user is a participant; else None.

    Returns None if DB unavailable.
    """
    if not _db_available():
        return None
    try:
        async with SessionLocal() as session:  # type: ignore[misc]
            from sqlalchemy import select
            result = await session.execute(select(ORMBattleReport).where(ORMBattleReport.id == int(report_id)))
            r = result.scalar_one_or_none()
            if r is not None and (
                int(r.attacker_user_id or -1) == int(user_id) or int(r.defender_user_id or -1) == int(user_id)
            ):
                return {
                    "id": int(r.id),
                    "attacker_user_id": int(r.attacker_user_id) if r.attacker_user_id is not None else None,
                    "defender_user_id": int(r.defender_user_id) if r.defender_user_id is not None else None,
                    "location": r.location,
                    "outcome": r.outcome,
                    "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
                }
            return None
    except Exception as exc:  # pragma: no cover - resilience path
        logger.warning("fetch_battle_report_for_user failed: %s", exc)
        return None


async def _create_battle_report(payload: dict) -> Optional[tuple[int, str]]:
    """Async: Insert a new battle report. Returns (id, created_at_iso) on success, else None."""
    if not _db_available():
        return None
    try:
        async with SessionLocal() as session:  # type: ignore[misc]
            row = ORMBattleReport(
                attacker_user_id=(int(payload.get("attacker_user_id")) if payload.get("attacker_user_id") is not None else None),
                defender_user_id=(int(payload.get("defender_user_id")) if payload.get("defender_user_id") is not None else None),
                location=payload.get("location") or {},
                outcome=payload.get("outcome") or {},
            )
            session.add(row)
            await session.flush()
            await session.commit()
            created_iso = row.created_at.isoformat() if getattr(row, "created_at", None) else utc_now().isoformat()
            try:
                logger.info(
                    "battle_report_created",
                    extra={
                        "action_type": "battle_report_created",
                        "report_id": int(getattr(row, "id", 0) or 0),
                        "attacker_user_id": int(getattr(row, "attacker_user_id", 0) or 0) if getattr(row, "attacker_user_id", None) is not None else None,
                        "defender_user_id": int(getattr(row, "defender_user_id", 0) or 0) if getattr(row, "defender_user_id", None) is not None else None,
                        "timestamp": created_iso,
                    },
                )
            except Exception:
                pass
            metrics.increment_event("db.battle_report_created")
            return int(row.id), created_iso
    except Exception as exc:  # pragma: no cover - resilience path
        logger.warning("_create_battle_report failed: %s", exc)
        return None


def create_battle_report(payload: dict) -> Optional[tuple[int, str]]:
    """Sync wrapper for _create_battle_report used from the game loop thread.

    Fire-and-forget: schedule on the captured persistence loop and return None. If loop missing, no-op.
    """
    try:
        _submit(_create_battle_report(payload), op="create_battle_report")
        return None
    except Exception as exc:  # pragma: no cover
        logger.debug("create_battle_report wrapper failed: %s", exc)
        return None


# -----------------
# Espionage Reports helpers
# -----------------
async def fetch_espionage_reports_for_user(user_id: int, limit: int = 50, offset: int = 0) -> List[dict]:
    """Async: List espionage reports visible to a user (attacker or defender), newest-first.

    Returns empty list if DB unavailable.
    """
    if not _db_available():
        return []
    try:
        async with SessionLocal() as session:  # type: ignore[misc]
            from sqlalchemy import select
            stmt = (
                select(ORMEspionageReport)
                .where(
                    (ORMEspionageReport.attacker_user_id == int(user_id))
                    | (ORMEspionageReport.defender_user_id == int(user_id))
                )
                .order_by(ORMEspionageReport.created_at.desc())
                .offset(int(offset))
                .limit(int(limit))
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": int(r.id),
                    "attacker_user_id": int(r.attacker_user_id) if r.attacker_user_id is not None else None,
                    "defender_user_id": int(r.defender_user_id) if r.defender_user_id is not None else None,
                    "location": r.location,
                    "snapshot": r.snapshot,
                    "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
                }
                for r in rows
            ]
    except Exception as exc:  # pragma: no cover - resilience path
        logger.warning("fetch_espionage_reports_for_user failed: %s", exc)
        return []


async def fetch_espionage_report_for_user(user_id: int, report_id: int) -> Optional[dict]:
    """Async: Retrieve a single espionage report if the user is a participant; else None.

    Returns None if DB unavailable.
    """
    if not _db_available():
        return None
    try:
        async with SessionLocal() as session:  # type: ignore[misc]
            from sqlalchemy import select
            result = await session.execute(select(ORMEspionageReport).where(ORMEspionageReport.id == int(report_id)))
            r = result.scalar_one_or_none()
            if r is not None and (
                int(r.attacker_user_id or -1) == int(user_id) or int(r.defender_user_id or -1) == int(user_id)
            ):
                return {
                    "id": int(r.id),
                    "attacker_user_id": int(r.attacker_user_id) if r.attacker_user_id is not None else None,
                    "defender_user_id": int(r.defender_user_id) if r.defender_user_id is not None else None,
                    "location": r.location,
                    "snapshot": r.snapshot,
                    "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
                }
            return None
    except Exception as exc:  # pragma: no cover - resilience path
        logger.warning("fetch_espionage_report_for_user failed: %s", exc)
        return None


async def _create_espionage_report(payload: dict) -> Optional[tuple[int, str]]:
    """Async: Insert a new espionage report. Returns (id, created_at_iso) on success, else None."""
    if not _db_available():
        return None
    try:
        async with SessionLocal() as session:  # type: ignore[misc]
            row = ORMEspionageReport(
                attacker_user_id=(int(payload.get("attacker_user_id")) if payload.get("attacker_user_id") is not None else None),
                defender_user_id=(int(payload.get("defender_user_id")) if payload.get("defender_user_id") is not None else None),
                location=payload.get("location") or {},
                snapshot=payload.get("snapshot") or {},
            )
            session.add(row)
            await session.flush()
            await session.commit()
            created_iso = row.created_at.isoformat() if getattr(row, "created_at", None) else utc_now().isoformat()
            try:
                logger.info(
                    "espionage_report_created",
                    extra={
                        "action_type": "espionage_report_created",
                        "report_id": int(getattr(row, "id", 0) or 0),
                        "attacker_user_id": int(getattr(row, "attacker_user_id", 0) or 0) if getattr(row, "attacker_user_id", None) is not None else None,
                        "defender_user_id": int(getattr(row, "defender_user_id", 0) or 0) if getattr(row, "defender_user_id", None) is not None else None,
                        "timestamp": created_iso,
                    },
                )
            except Exception:
                pass
            metrics.increment_event("db.espionage_report_created")
            return int(row.id), created_iso
    except Exception as exc:  # pragma: no cover - resilience path
        logger.warning("_create_espionage_report failed: %s", exc)
        return None


def create_espionage_report(payload: dict) -> Optional[tuple[int, str]]:
    """Sync wrapper for _create_espionage_report used from the game loop thread.

    Fire-and-forget via persistence loop.
    """
    try:
        _submit(_create_espionage_report(payload), op="create_espionage_report")
        return None
    except Exception as exc:  # pragma: no cover
        logger.debug("create_espionage_report wrapper failed: %s", exc)
        return None


# Building Queue persistence helpers
async def _enqueue_build_queue_by_entity(world, ent, building_type: str, level: int, completion_time) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.id == planet.id))
            planet = result.scalar_one()
            item = ORMBQI(
                planet_id=planet.id,
                building_type=str(building_type),
                level=int(level),
                enqueued_at=utc_now(),
                complete_at=ensure_aware_utc(parse_utc(completion_time)) if completion_time is not None else utc_now(),
                status="pending",
            )
            session.add(item)
            await session.commit()
            try:
                logger.info(
                    "build_queue_enqueued",
                    extra={
                        "action_type": "build_queue_enqueued",
                        "planet_id": int(getattr(planet, "id", 0) or 0),
                        "building_type": str(getattr(item, "building_type", "")),
                        "level": int(getattr(item, "level", 0) or 0),
                        "complete_at": ensure_aware_utc(getattr(item, "complete_at", utc_now())).isoformat(),
                    },
                )
            except Exception:
                pass
            metrics.increment_event("db.build_queue_enqueued")
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("enqueue_build_queue failed: %s", exc)
    except Exception:
        pass


def enqueue_build_queue(world, ent, building_type: str, level: int, completion_time) -> None:
    if not _db_available():
        return
    try:
        _submit(_enqueue_build_queue_by_entity(world, ent, building_type, level, completion_time), op="enqueue_build_queue_by_entity")
    except Exception as exc:
        logger.debug("enqueue_build_queue wrapper failed: %s", exc)


async def _load_build_queue_items_by_entity(world, ent) -> List[Dict]:
    if not _db_available():
        return []
    try:
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return []
            result = await session.execute(
                select(ORMBQI).where((ORMBQI.planet_id == planet.id) & (ORMBQI.status == "pending")).order_by(ORMBQI.id.asc())
            )
            rows = result.scalars().all()
            items: List[Dict] = []
            for r in rows:
                items.append({
                    'type': getattr(r, 'building_type'),
                    'completion_time': ensure_aware_utc(getattr(r, 'complete_at')),
                })
            return items
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("load_build_queue_items failed: %s", exc)
        return []
    except Exception:
        return []


def load_build_queue_items(world, ent) -> List[Dict]:
    if not _db_available():
        return []
    try:
        res = _submit_and_wait(_load_build_queue_items_by_entity(world, ent), timeout=2.0, default=[], op="load_build_queue_items_by_entity")
        return list(res or [])
    except Exception as exc:
        logger.debug("load_build_queue_items wrapper failed: %s", exc)
        return []


async def _complete_next_build_queue_by_entity(world, ent) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player, Position, Planet as PlanetComp
        player = world.component_for_entity(ent, Player)
        pos = world.component_for_entity(ent, Position)
        pmeta = world.component_for_entity(ent, PlanetComp)
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name)
            if planet is None:
                return
            result = await session.execute(
                select(ORMBQI).where((ORMBQI.planet_id == planet.id) & (ORMBQI.status == "pending")).order_by(ORMBQI.id.asc()).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            await session.execute(update(ORMBQI).where(ORMBQI.id == row.id).values(status="completed"))
            await session.commit()
            try:
                logger.info(
                    "build_queue_completed",
                    extra={
                        "action_type": "build_queue_completed",
                        "queue_item_id": int(getattr(row, "id", 0) or 0),
                        "planet_id": int(getattr(planet, "id", 0) or 0),
                        "building_type": str(getattr(row, "building_type", "")),
                        "level": int(getattr(row, "level", 0) or 0),
                    },
                )
            except Exception:
                pass
            metrics.increment_event("db.build_queue_completed")
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("complete_next_build_queue failed: %s", exc)


def complete_next_build_queue(world, ent) -> None:
    if not _db_available():
        return
    try:
        _submit(_complete_next_build_queue_by_entity(world, ent), op="complete_next_build_queue_by_entity")
    except Exception as exc:
        logger.debug("complete_next_build_queue wrapper failed: %s", exc)


# Research Queue persistence helpers
async def _enqueue_research_by_entity(world, ent, research_type: str, level: int, completion_time) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player
        player = world.component_for_entity(ent, Player)
        async with SessionLocal() as session:
            item = ORMRQI(
                user_id=int(player.user_id),
                research_type=str(research_type),
                level=int(level),
                enqueued_at=utc_now(),
                complete_at=ensure_aware_utc(parse_utc(completion_time)) if completion_time is not None else utc_now(),
                status="pending",
            )
            session.add(item)
            await session.commit()
            try:
                logger.info(
                    "research_enqueued",
                    extra={
                        "action_type": "research_enqueued",
                        "user_id": int(getattr(item, "user_id", 0) or 0),
                        "research_type": str(getattr(item, "research_type", "")),
                        "level": int(getattr(item, "level", 0) or 0),
                        "complete_at": ensure_aware_utc(getattr(item, "complete_at", utc_now())).isoformat(),
                    },
                )
            except Exception:
                pass
            metrics.increment_event("db.research_enqueued")
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("enqueue_research failed: %s", exc)
    except Exception:
        pass


def enqueue_research(world, ent, research_type: str, level: int, completion_time) -> None:
    if not _db_available():
        return
    try:
        _submit(_enqueue_research_by_entity(world, ent, research_type, level, completion_time), op="enqueue_research_by_entity")
    except Exception as exc:
        logger.debug("enqueue_research wrapper failed: %s", exc)


async def _load_research_queue_items_by_entity(world, ent) -> List[Dict]:
    if not _db_available():
        return []
    try:
        from src.models import Player
        player = world.component_for_entity(ent, Player)
        async with SessionLocal() as session:
            result = await session.execute(
                select(ORMRQI).where((ORMRQI.user_id == int(player.user_id)) & (ORMRQI.status == "pending")).order_by(ORMRQI.id.asc())
            )
            rows = result.scalars().all()
            items: List[Dict] = []
            for r in rows:
                items.append({
                    'type': getattr(r, 'research_type'),
                    'completion_time': ensure_aware_utc(getattr(r, 'complete_at')),
                })
            return items
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("load_research_queue_items failed: %s", exc)
        return []
    except Exception:
        return []


def load_research_queue_items(world, ent) -> List[Dict]:
    if not _db_available():
        return []
    try:
        res = _submit_and_wait(_load_research_queue_items_by_entity(world, ent), timeout=2.0, default=[], op="load_research_queue_items_by_entity")
        return list(res or [])
    except Exception as exc:
        logger.debug("load_research_queue_items wrapper failed: %s", exc)
        return []


async def _complete_next_research_by_entity(world, ent) -> None:
    if not _db_available():
        return
    try:
        from src.models import Player
        player = world.component_for_entity(ent, Player)
        async with SessionLocal() as session:
            result = await session.execute(
                select(ORMRQI).where((ORMRQI.user_id == int(player.user_id)) & (ORMRQI.status == "pending")).order_by(ORMRQI.id.asc()).limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            await session.execute(update(ORMRQI).where(ORMRQI.id == row.id).values(status="completed"))
            await session.commit()
            try:
                logger.info(
                    "research_completed",
                    extra={
                        "action_type": "research_completed",
                        "queue_item_id": int(getattr(row, "id", 0) or 0),
                        "user_id": int(getattr(row, "user_id", 0) or 0),
                        "research_type": str(getattr(row, "research_type", "")),
                        "level": int(getattr(row, "level", 0) or 0),
                    },
                )
            except Exception:
                pass
            metrics.increment_event("db.research_completed")
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("complete_next_research failed: %s", exc)


def complete_next_research(world, ent) -> None:
    if not _db_available():
        return
    try:
        _submit(_complete_next_research_by_entity(world, ent), op="complete_next_research_by_entity")
    except Exception as exc:
        logger.debug("complete_next_research wrapper failed: %s", exc)
