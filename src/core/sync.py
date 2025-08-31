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
from typing import Optional, Dict

try:
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError
    from src.core.database import SessionLocal, is_db_enabled
    from src.models.database import User as ORMUser, Planet as ORMPlanet, Building as ORMBuilding
    _DB_AVAILABLE = is_db_enabled()
except Exception:  # pragma: no cover - defensive import for environments without deps
    _DB_AVAILABLE = False

logger = logging.getLogger(__name__)

def _db_available() -> bool:
    try:
        from src.core.database import is_db_enabled as _is_db_enabled
        return bool(_is_db_enabled())
    except Exception:
        return False

# Persistence throttling (per planet key) to avoid excessive writes
PERSIST_INTERVAL_SECONDS: int = 60
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

    Avoid creating an unawaited coroutine when already inside a running loop.
    """
    try:
        # If there is a running loop, schedule task and return best-effort success
        loop = asyncio.get_running_loop()
        loop.create_task(_create_colony(user_id, username, galaxy, system, position, planet_name))
        return True
    except RuntimeError:
        # No running loop; safe to run synchronously
        return asyncio.run(_create_colony(user_id, username, galaxy, system, position, planet_name))
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


async def _ensure_user_and_planet(user_id: int, username: str, galaxy: int, system: int, position: int,
                                  planet_name: str, resources: Optional[dict] = None) -> Optional[ORMPlanet]:
    if not _db_available():
        return None
    try:
        async with SessionLocal() as session:
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

            await session.commit()
            return planet
    except SQLAlchemyError as exc:  # pragma: no cover - resilience path
        logger.warning("DB ensure_user_and_planet failed: %s", exc)
        return None


async def sync_planet_resources_by_entity(world, ent) -> None:
    """Persist planet resource amounts and production metadata for the given entity.

    Expects the entity to have components: Player, Position, Planet, Resources, ResourceProduction.
    """
    if not _DB_AVAILABLE:
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
            planet = await _ensure_user_and_planet(
                player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name,
                resources={"metal": res.metal, "crystal": res.crystal, "deuterium": res.deuterium},
            )
            if planet is None:
                return
            # Re-fetch within current session to update
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.id == planet.id))
            planet = result.scalar_one()
            planet.metal = res.metal
            planet.crystal = res.crystal
            planet.deuterium = res.deuterium
            planet.metal_rate = float(prod.metal_rate)
            planet.crystal_rate = float(prod.crystal_rate)
            planet.deuterium_rate = float(prod.deuterium_rate)
            planet.last_update = prod.last_update
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("DB sync_planet_resources failed: %s", exc)


async def upsert_building_level_by_entity(world, ent, building_type: str, level: int) -> None:
    """Upsert a Building row for the entity's planet and set its level."""
    if not _DB_AVAILABLE:
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
            planet = await _ensure_user_and_planet(
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
        logger.warning("DB upsert_building_level failed: %s", exc)


def sync_planet_resources(world, ent) -> None:
    """Synchronous wrapper safe to call from Esper systems."""
    if not _DB_AVAILABLE:
        return
    try:
        asyncio.run(sync_planet_resources_by_entity(world, ent))
    except RuntimeError:
        # Already inside an event loop (unlikely in our game thread); schedule task
        loop = asyncio.get_event_loop()
        loop.create_task(sync_planet_resources_by_entity(world, ent))
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_planet_resources wrapper failed: %s", exc)


def sync_building_level(world, ent, building_type: str, level: int) -> None:
    """Synchronous wrapper to upsert building level for given entity."""
    if not _DB_AVAILABLE:
        return
    try:
        asyncio.run(upsert_building_level_by_entity(world, ent, building_type, level))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(upsert_building_level_by_entity(world, ent, building_type, level))
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_building_level wrapper failed: %s", exc)


# -----------------
# Payload-based API
# -----------------
async def sync_planet_resources_with_payload(user_id: int, username: str, galaxy: int, system: int, position: int,
                                             planet_name: str, metal: int, crystal: int, deuterium: int,
                                             metal_rate: float, crystal_rate: float, deuterium_rate: float,
                                             last_update) -> None:
    if not _DB_AVAILABLE:
        return
    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(
                user_id, username, galaxy, system, position, planet_name,
                resources={"metal": metal, "crystal": crystal, "deuterium": deuterium},
            )
            if planet is None:
                return
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.id == planet.id))
            planet = result.scalar_one()
            planet.metal = metal
            planet.crystal = crystal
            planet.deuterium = deuterium
            planet.metal_rate = float(metal_rate)
            planet.crystal_rate = float(crystal_rate)
            planet.deuterium_rate = float(deuterium_rate)
            planet.last_update = last_update
            await session.commit()
    except SQLAlchemyError as exc:  # pragma: no cover
        logger.warning("DB sync_planet_resources_with_payload failed: %s", exc)


def sync_planet_resources_payload(user_id: int, username: str, galaxy: int, system: int, position: int,
                                  planet_name: str, metal: int, crystal: int, deuterium: int,
                                  metal_rate: float, crystal_rate: float, deuterium_rate: float,
                                  last_update) -> None:
    if not _DB_AVAILABLE:
        return
    try:
        asyncio.run(sync_planet_resources_with_payload(
            user_id, username, galaxy, system, position, planet_name,
            metal, crystal, deuterium, metal_rate, crystal_rate, deuterium_rate, last_update
        ))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(sync_planet_resources_with_payload(
            user_id, username, galaxy, system, position, planet_name,
            metal, crystal, deuterium, metal_rate, crystal_rate, deuterium_rate, last_update
        ))
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_planet_resources_payload wrapper failed: %s", exc)


async def upsert_building_level_with_payload(user_id: int, username: str, galaxy: int, system: int, position: int,
                                             planet_name: str, building_type: str, level: int) -> None:
    if not _DB_AVAILABLE:
        return
    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet(user_id, username, galaxy, system, position, planet_name)
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
    if not _DB_AVAILABLE:
        return
    try:
        asyncio.run(upsert_building_level_with_payload(user_id, username, galaxy, system, position, planet_name, building_type, level))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(upsert_building_level_with_payload(user_id, username, galaxy, system, position, planet_name, building_type, level))
    except Exception as exc:  # pragma: no cover
        logger.debug("sync_building_level_payload wrapper failed: %s", exc)


# -----------------
# Loaders & Atomic Ops & Cleanup
# -----------------
async def _load_player_planet_into_world(world, user_id: int, planet_id: int) -> bool:
    """Load a specific planet for the user into ECS, replacing in-place components.

    Returns True on success, False if DB unavailable or validation fails.
    """
    if not _DB_AVAILABLE:
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
    if not _DB_AVAILABLE:
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
    if not _DB_AVAILABLE:
        return
    try:
        asyncio.run(_load_player_into_world(world, user_id))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(_load_player_into_world(world, user_id))
    except Exception as exc:
        logger.debug("load_player_into_world wrapper failed: %s", exc)


def load_player_planet_into_world(world, user_id: int, planet_id: int) -> bool:
    """Synchronous wrapper to load a specific planet for a user into ECS.

    Returns True if the planet was loaded, False otherwise (e.g., DB disabled, validation failed).
    """
    if not _DB_AVAILABLE:
        return False
    try:
        return asyncio.run(_load_player_planet_into_world(world, user_id, planet_id))
    except RuntimeError:
        # Inside running loop; schedule and assume success for responsiveness
        loop = asyncio.get_event_loop()
        loop.create_task(_load_player_planet_into_world(world, user_id, planet_id))
        return True
    except Exception as exc:  # pragma: no cover
        logger.debug("load_player_planet_into_world wrapper failed: %s", exc)
        return False


async def _load_all_players_into_world(world) -> None:
    if not _DB_AVAILABLE:
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
    if not _DB_AVAILABLE:
        return
    try:
        asyncio.run(_load_all_players_into_world(world))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(_load_all_players_into_world(world))
    except Exception as exc:
        logger.debug("load_all_players_into_world wrapper failed: %s", exc)


# Atomic resource spend
async def _spend_resources_atomic_by_entity(world, ent, cost: Dict[str, int]) -> None:
    if not _DB_AVAILABLE:
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
    if not _DB_AVAILABLE:
        return
    try:
        asyncio.run(_spend_resources_atomic_by_entity(world, ent, cost))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(_spend_resources_atomic_by_entity(world, ent, cost))
    except Exception as exc:
        logger.debug("spend_resources_atomic wrapper failed: %s", exc)


# Cleanup inactive users
async def _cleanup_inactive_players(days: int = 30) -> int:
    if not _DB_AVAILABLE:
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
    if not _DB_AVAILABLE:
        return 0
    try:
        return asyncio.run(_cleanup_inactive_players(days))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        task = loop.create_task(_cleanup_inactive_players(days))
        return 0
    except Exception as exc:
        logger.debug("cleanup_inactive_players wrapper failed: %s", exc)
        return 0
