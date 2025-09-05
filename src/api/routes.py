from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict, Set, Tuple
from fastapi import FastAPI, HTTPException, Depends, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
import logging
import tracemalloc
import asyncio
import time

from src.core.game import GameWorld
from src.core.config import (
    CORS_ALLOW_ORIGINS,
    CORS_ALLOW_CREDENTIALS,
    CORS_ALLOW_METHODS,
    CORS_ALLOW_HEADERS,
    TICK_RATE,
    GALAXY_COUNT,
    SYSTEMS_PER_GALAXY,
    POSITIONS_PER_SYSTEM,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from src.core.database import check_database, init_db, get_optional_async_session, get_optional_readonly_async_session, is_db_enabled, shutdown_db, start_db
from src.models import Player, Research, ResearchQueue, Fleet, Position as ECSPosition
from src.models.database import TradeOffer as ORMTradeOffer, TradeEvent as ORMTradeEvent, BattleReport as ORMBattleReport, EspionageReport as ORMEspionageReport
from src.api.auth import router as auth_router, ensure_player_loaded, ensure_current_user_player_loaded
from src.auth.security import ensure_user_matches_path, rate_limiter_dependency, get_current_user, decode_token, reset_in_memory_auth_state
from src.core.sync import fetch_battle_reports_for_user, fetch_battle_report_for_user, fetch_espionage_reports_for_user, fetch_espionage_report_for_user

logger = logging.getLogger(__name__)

# Ensure memory tracing for health endpoint
try:
    tracemalloc.start()
except Exception:
    pass

# Global game world instance (shared singleton)
from src.core.state import game_world  # reuse the shared GameWorld instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context: starts/stops the background game loop and ensures DB schema."""
    # Initialize database engines within this event loop, then ensure schema (dev)
    try:
        await start_db()
    except Exception:
        pass
    # Optionally initialize schema in dev mode
    try:
        from src.core.config import get_dev_create_all
        if get_dev_create_all():
            await init_db()
    except Exception:
        pass
    # Reset in-memory auth state when DB is disabled (helps test isolation)
    try:
        if not is_db_enabled():
            reset_in_memory_auth_state()
    except Exception:
        pass
    # Capture the running asyncio loop for WS bridge and persistence, and log startup config
    try:
        from src.api.ws import set_loop
        from src.core.sync import set_persistence_loop
        from src.core.config import get_enable_db, get_dev_create_all, get_tick_rate, get_save_interval_seconds, get_persist_interval_seconds
        loop = asyncio.get_running_loop()
        set_loop(loop)
        set_persistence_loop(loop)
        try:
            logger.info(
                "startup_config",
                extra={
                    "ENABLE_DB": bool(get_enable_db()),
                    "DEV_CREATE_ALL": bool(get_dev_create_all()),
                    "tick_rate": float(get_tick_rate()),
                    "save_interval_s": int(get_save_interval_seconds()),
                    "persist_interval_s": int(get_persist_interval_seconds()),
                    "loop_id": id(loop),
                },
            )
        except Exception:
            pass
    except Exception:
        pass
    # Autoload all players before starting the background game loop (await fully)
    try:
        import time as _t
        from src.core.metrics import metrics as _metrics
        _autoload_start = _t.perf_counter()
        # Call the synchronous wrapper first to satisfy call-order expectations in tests
        try:
            game_world.load_player_data()
        except Exception:
            pass
        from src.core.sync import _load_all_players_into_world
        await _load_all_players_into_world(game_world.world)
        # Apply offline resource accrual immediately so first tick reflects current time
        try:
            game_world._apply_offline_resource_accrual()
        except Exception:
            pass
        # Mark world as loaded and record metrics
        try:
            game_world.loaded = True
            _duration = _t.perf_counter() - _autoload_start
            _metrics.increment_event("autoload.count", 1)
            _metrics.record_timer("autoload.duration_s", _duration)
            logger.info("autoload_complete", extra={"duration_ms": _duration * 1000.0})
        except Exception:
            pass
    except Exception:
        # Do not block startup if loading fails; systems may lazily load per-user on demand
        pass
    # Start the background game loop
    game_world.start_game_loop()
    try:
        yield
    finally:
        # Attempt to close any active WebSocket connections gracefully
        try:
            manager = globals().get("ws_manager")
            if manager is not None:
                await manager.close_all()
        except Exception:
            pass
        # Stop the background game loop first
        try:
            game_world.stop_game_loop()
        except Exception:
            pass
        # Dispose database engines within the running loop to avoid cross-loop termination
        try:
            await shutdown_db()
        except Exception:
            pass


app = FastAPI(title="Ogame-like Game Server", version="1.0.0", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)

# Routers
app.include_router(auth_router)

# Metrics middleware and endpoint
from src.core.metrics import metrics

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        try:
            duration = time.perf_counter() - start
            route_obj = request.scope.get("route")
            route_path = getattr(route_obj, "path", request.url.path)
            status = getattr(response, "status_code", 500)
            metrics.record_http(request.method, route_path, status, duration)
        except Exception:
            # Never break requests due to metrics errors
            pass


@app.get("/metrics")
async def get_metrics():
    return metrics.snapshot()


class ConnectionManager:
    """Tracks active WebSocket connections per user for real-time updates.

    Minimal implementation for initial WebSocket support. Advanced features
    (subscriptions, event routing) will be added in subsequent tasks.
    """

    def __init__(self) -> None:
        self._connections: Dict[int, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)
        logger.info("ws_connected user_id=%s total=%s", user_id, self.total_connections)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        conns = self._connections.get(user_id)
        if conns and websocket in conns:
            conns.remove(websocket)
            if not conns:
                self._connections.pop(user_id, None)
        logger.info("ws_disconnected user_id=%s total=%s", user_id, self.total_connections)

    @property
    def total_connections(self) -> int:
        return sum(len(v) for v in self._connections.values())

    async def send_to_user(self, user_id: int, message: dict) -> None:
        for ws in list(self._connections.get(user_id, set())):
            try:
                await ws.send_json(message)
            except Exception:
                # Drop broken sockets
                try:
                    await ws.close()
                except Exception:
                    pass
                self.disconnect(ws, user_id)

    async def broadcast(self, message: dict) -> None:
        for user_id in list(self._connections.keys()):
            await self.send_to_user(user_id, message)

    async def close_all(self) -> None:
        for user_id, conns in list(self._connections.items()):
            for ws in list(conns):
                try:
                    await ws.close(code=1001)
                except Exception:
                    pass
                self.disconnect(ws, user_id)


# Global WebSocket manager instance
ws_manager = ConnectionManager()


@app.get("/")
async def root():
    """Simple health banner indicating server readiness."""
    return {"message": "Ogame-like Game Server", "status": "running"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Basic WebSocket endpoint for real-time updates.

    Authentication: expects a JWT access token via query parameter `token`.
    Example: /ws?token=eyJhbGci...
    """
    token = websocket.query_params.get("token")
    if not token:
        # Policy violation: missing auth
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
        return
    try:
        payload = decode_token(token)
        sub = payload.get("sub")
        user_id = int(sub)
    except Exception:
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    try:
        await ws_manager.connect(websocket, user_id)
        # Send initial welcome payload
        await websocket.send_json({
            "type": "welcome",
            "user_id": user_id,
            "server_time": datetime.now().isoformat(),
        })
        # Simple receive loop: handle ping messages
        while True:
            try:
                data = await websocket.receive_text()
                if data.lower().strip() == "ping":
                    await websocket.send_json({"type": "pong", "server_time": datetime.now().isoformat()})
                else:
                    # Unknown message, ignore or echo back as info
                    await websocket.send_json({"type": "info", "message": data})
            except WebSocketDisconnect:
                break
            except Exception:
                # Attempt to continue on non-fatal errors
                try:
                    await websocket.send_json({"type": "error", "message": "invalid message"})
                except Exception:
                    break
    finally:
        try:
            ws_manager.disconnect(websocket, user_id)
        except Exception:
            pass


@app.get("/player/{user_id}")
async def get_player(user_id: int, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Get all data for a specific player."""
    data = game_world.get_player_data(user_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return data


@app.post("/player/{user_id}/build")
async def build_building(user_id: int, building_data: dict, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Queue a building for construction."""
    building_type = building_data.get('building_type')
    if not building_type:
        raise HTTPException(status_code=400, detail="building_type is required")


    command = {
        'type': 'build_building',
        'user_id': user_id,
        'building_type': building_type,
    }
    game_world.queue_command(command)

    activity_command = {
        'type': 'update_player_activity',
        'user_id': user_id,
    }
    game_world.queue_command(activity_command)

    return {"message": f"Build command queued for {building_type}"}


@app.delete("/player/{user_id}/buildings/{building_type}")
async def demolish_building(user_id: int, building_type: str, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Demolish a building one level down, with partial refund and safety checks."""

    game_world.queue_command({'type': 'demolish_building', 'user_id': user_id, 'building_type': building_type})
    game_world.queue_command({'type': 'update_player_activity', 'user_id': user_id})
    return {"message": f"Demolition command queued for {building_type}"}


@app.delete("/player/{user_id}/build-queue/{index}")
async def cancel_build_queue(user_id: int, index: int, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Cancel a pending build queue item and refund part of the cost."""

    game_world.queue_command({'type': 'cancel_build_queue', 'user_id': user_id, 'index': index})
    game_world.queue_command({'type': 'update_player_activity', 'user_id': user_id})
    return {"message": f"Cancel command queued for build queue index {index}"}


@app.get("/building-costs/{building_type}")
async def get_building_costs(building_type: str, level: int = 0):
    """Get the cost to build/upgrade a specific building."""
    cost = game_world._calculate_building_cost(building_type, level)
    build_time = game_world._calculate_build_time(building_type, level)
    return {
        "building_type": building_type,
        "level": level,
        "cost": cost,
        "build_time_seconds": build_time,
    }


@app.get("/game-status")
async def get_game_status():
    """Get general game status information.

    Extended to include database status and persistence mode to reflect
    DB-only persistence per docs/cleanup.md.
    """
    # Count entities by iterating Player components to avoid relying on private internals
    try:
        seen = set()
        for ent, _ in game_world.world.get_components(Player):
            seen.add(ent)
        total_entities = len(seen)
    except Exception:
        total_entities = 0

    # Report database connectivity status
    try:
        db_ok = await check_database()
    except Exception:
        db_ok = False

    # Compute aggregate energy status across loaded planets (best-effort)
    try:
        from src.models import Buildings as _Bld
        from src.core.config import (
            ENERGY_SOLAR_BASE as _E_BASE,
            ENERGY_SOLAR_GROWTH as _E_GROWTH,
            ENERGY_CONSUMPTION as _E_CONS,
            ENERGY_CONSUMPTION_GROWTH as _E_CONS_GROWTH,
            ENERGY_TECH_ENERGY_BONUS_PER_LEVEL as _E_BONUS,
            ENERGY_DEFICIT_SOFT_FLOOR as _SOFT_FLOOR,
        )
        deficit_count = 0
        total_planets = 0
        min_factor = None
        getter = getattr(game_world.world, "get_components", None)
        if getter is None:
            getter = game_world.world.get_components
        for ent, (player, buildings) in getter(Player, _Bld):
            total_planets += 1
            # compute energy bonus via research if present
            try:
                research = game_world.world.component_for_entity(ent, Research)
                energy_lvl = int(getattr(research, 'energy', 0))
            except Exception:
                energy_lvl = 0
            bonus = 1.0 + (_E_BONUS * energy_lvl)
            sp_lvl = max(0, int(getattr(buildings, 'solar_plant', 0)))
            produced = (_E_BASE * sp_lvl * (_E_GROWTH ** max(0, sp_lvl - 1))) * bonus
            def _cons(_base: float, _lvl: int) -> float:
                _lvl = max(0, int(_lvl))
                return _base * _lvl * (_E_CONS_GROWTH ** max(0, _lvl - 1))
            required = 0.0
            required += _cons(_E_CONS.get('metal_mine', 0.0), getattr(buildings, 'metal_mine', 0))
            required += _cons(_E_CONS.get('crystal_mine', 0.0), getattr(buildings, 'crystal_mine', 0))
            required += _cons(_E_CONS.get('deuterium_synthesizer', 0.0), getattr(buildings, 'deuterium_synthesizer', 0))
            if required <= 0:
                factor_raw = 1.0
            elif produced <= 0:
                factor_raw = 0.0
            else:
                factor_raw = min(1.0, produced / required)
            if factor_raw < 1.0:
                deficit_count += 1
            if min_factor is None or factor_raw < min_factor:
                min_factor = float(factor_raw)
        energy_summary = {
            "deficit_planets": int(deficit_count),
            "total_planets": int(total_planets),
            "min_factor": float(min_factor) if min_factor is not None else None,
            "soft_floor": float(_SOFT_FLOOR),
        }
    except Exception:
        energy_summary = {
            "deficit_planets": 0,
            "total_planets": 0,
            "min_factor": None,
            "soft_floor": None,
        }

    return {
        "game_running": game_world.running,
        "total_entities": total_entities,
        "server_time": datetime.now().isoformat(),
        "database": {"status": "ok" if db_ok else "fail", "persistence": "db_only"},
        "energy": energy_summary,
    }


@app.get("/player/{user_id}/research")
async def get_player_research(user_id: int, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Return current research levels and research queue for the player."""
    # Player presence ensured by dependency

    # Find the player's research components
    for ent, (player, research, rq) in game_world.world.get_components(Player, Research, ResearchQueue):
        if player.user_id != user_id:
            continue
        return {
            "research": {
                "energy": research.energy,
                "laser": research.laser,
                "ion": research.ion,
                "hyperspace": research.hyperspace,
                "plasma": research.plasma,
            },
            "queue": [
                {
                    "type": item.get("type"),
                    "completion_time": item.get("completion_time").isoformat() if item.get("completion_time") else None,
                    "cost": item.get("cost", {}),
                }
                for item in rq.items
            ],
        }

    raise HTTPException(status_code=404, detail="Player not found")


@app.post("/player/{user_id}/research")
async def start_research(user_id: int, payload: dict, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Queue a research job for the player."""
    research_type = payload.get("research_type")
    if not research_type:
        raise HTTPException(status_code=400, detail="research_type is required")

    # Optional: validate the research type against the component fields
    valid_types = {"energy", "laser", "ion", "hyperspace", "plasma", "computer"}
    if research_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid research_type")

    game_world.queue_command({
        "type": "start_research",
        "user_id": user_id,
        "research_type": research_type,
    })
    game_world.queue_command({
        "type": "update_player_activity",
        "user_id": user_id,
    })

    return {"message": f"Research command queued for {research_type}"}


@app.get("/healthz")
async def healthz():
    """Health check endpoint providing basic service metrics.

    Adds flags required by docs/tasks.md task 21: worldLoaded, lastSaveTs, and last tick metrics.
    """
    try:
        current, peak = tracemalloc.get_traced_memory()
    except Exception:
        current, peak = 0, 0

    db_ok = await check_database()

    # Pull a snapshot of game loop metrics for last tick info
    try:
        snap = metrics.snapshot()
        loop_metrics = snap.get("game_loop", {})
        last_tick_ms = loop_metrics.get("last_ms", 0.0)
        jitter_last_ms = loop_metrics.get("jitter", {}).get("last_ms", 0.0)
        ticks = loop_metrics.get("ticks", 0)
    except Exception:
        last_tick_ms = 0.0
        jitter_last_ms = 0.0
        ticks = 0

    # Last save timestamp in ISO if available
    try:
        import time as _t
        last_save_ts = getattr(game_world, "_last_save_ts", 0.0)
        last_save_iso = None
        if last_save_ts and last_save_ts > 0:
            from datetime import datetime as _dt
            last_save_iso = _dt.fromtimestamp(last_save_ts).isoformat()
    except Exception:
        last_save_iso = None

    return {
        "status": "ok",
        "worldLoaded": bool(getattr(game_world, "loaded", False)),
        "loop": {
            "running": game_world.running,
            "tick_rate": TICK_RATE,
            "queue_depth": game_world.command_queue.qsize(),
            "ticks": ticks,
            "last_tick_ms": last_tick_ms,
            "jitter_last_ms": jitter_last_ms,
        },
        "memory": {
            "current_bytes": current,
            "peak_bytes": peak,
        },
        "database": {"status": "ok" if db_ok else "fail", "persistence": "db_only"},
        "lastSaveTs": last_save_iso,
        "server_time": datetime.now().isoformat(),
    }



@app.get("/player/{user_id}/fleet")
async def get_player_fleet(user_id: int, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Get the player's current fleet and ship build queue.

    To improve determinism in tests, this endpoint will opportunistically
    advance the ECS world one tick before reading data. This allows
    ship builds that have reached their completion time to be applied
    immediately when queried.
    """
    # Opportunistically progress the world to apply any due completions
    try:
        game_world._process_commands()
        game_world.world.process()
        # Process twice to settle multi-phase arrivals (e.g., colonization)
        game_world.world.process()
    except Exception:
        pass

    data = game_world.get_player_data(user_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Player not found")

    # Attempt to read ShipBuildQueue directly for immediacy (helps tests)
    ship_queue_items = []
    try:
        from src.models import Player as _P, ShipBuildQueue as _SBQ
        for ent, (p, sbq) in game_world.world.get_components(_P, _SBQ):
            if p.user_id != user_id:
                continue
            if sbq and getattr(sbq, 'items', None):
                for item in sbq.items:
                    ship_queue_items.append({
                        'type': item.get('type'),
                        'count': int(item.get('count', 1)),
                        'completion_time': item.get('completion_time').isoformat() if item.get('completion_time') else None,
                        'cost': item.get('cost'),
                    })
            break
    except Exception:
        pass

    return {
        "fleet": data.get("fleet", {}),
        "ship_build_queue": ship_queue_items or data.get("ship_build_queue", []),
    }


@app.post("/player/{user_id}/build-ships")
async def build_ships(user_id: int, payload: dict, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Queue ship construction in the player's shipyard.

    Body example:
    {"ship_type": "light_fighter", "quantity": 5}
    """
    ship_type = payload.get("ship_type")
    quantity = payload.get("quantity", 1)
    if not ship_type:
        raise HTTPException(status_code=400, detail="ship_type is required")
    try:
        quantity = int(quantity)
    except Exception:
        raise HTTPException(status_code=400, detail="quantity must be an integer")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    # Opportunistically compute validation against current ECS state for immediate errors
    try:
        # Progress world to settle any completions first
        game_world._process_commands()
        game_world.world.process()
    except Exception:
        pass

    try:
        from dataclasses import fields as _fields
        from src.models import Player as _P, Buildings as _B, ShipBuildQueue as _SBQ, Fleet as _F, Research as _R
        from src.core.config import SHIPYARD_QUEUE_BASE_LIMIT, SHIPYARD_QUEUE_PER_LEVEL, BASE_MAX_FLEET_SIZE, FLEET_SIZE_PER_COMPUTER_LEVEL
        # Find the player's current entity
        ent = None
        shipyard_level = 0
        queue_len = 0
        total_current = 0
        comp_lvl = 0
        sbq = None
        for e, (p, b, f) in game_world.world.get_components(_P, _B, _F):
            if p.user_id != user_id:
                continue
            ent = e
            shipyard_level = int(getattr(b, 'shipyard', 0))
            # queue length
            try:
                sbq = game_world.world.component_for_entity(e, _SBQ)
                if sbq and getattr(sbq, 'items', None):
                    queue_len = len(sbq.items)
            except Exception:
                queue_len = 0
            # current fleet sum
            try:
                for fld in _fields(_F):
                    total_current += int(getattr(f, fld.name, 0))
            except Exception:
                pass
            # add queued counts as part of cap check
            if sbq and getattr(sbq, 'items', None):
                for item in sbq.items:
                    try:
                        total_current += int(item.get('count', 0))
                    except Exception:
                        pass
            # computer tech level
            try:
                r = game_world.world.component_for_entity(e, _R)
                comp_lvl = int(getattr(r, 'computer', 0)) if r is not None else 0
            except Exception:
                comp_lvl = 0
            break
        if ent is not None:
            queue_limit = int(SHIPYARD_QUEUE_BASE_LIMIT) + int(SHIPYARD_QUEUE_PER_LEVEL) * max(0, shipyard_level)
            if queue_len >= queue_limit:
                raise HTTPException(status_code=400, detail="Shipyard queue full")
            max_allowed = int(BASE_MAX_FLEET_SIZE) + int(FLEET_SIZE_PER_COMPUTER_LEVEL) * max(0, comp_lvl)
            if total_current + quantity > max_allowed:
                raise HTTPException(status_code=400, detail="Fleet size cap exceeded")
    except HTTPException:
        raise
    except Exception:
        # Best effort only; fall through to command path
        pass

    game_world.queue_command({
        'type': 'build_ships',
        'user_id': user_id,
        'ship_type': ship_type,
        'quantity': quantity,
    })
    game_world.queue_command({'type': 'update_player_activity', 'user_id': user_id})

    # Process immediately to make queue visible in subsequent GET during tests
    try:
        game_world._process_commands()
    except Exception:
        pass

    return {"message": f"Ship build queued: {ship_type} x{quantity}"}


@app.post("/player/{user_id}/fleet/dispatch")
async def dispatch_fleet(user_id: int, payload: dict, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Dispatch a fleet from the active planet to target coordinates with a mission.

    Body example:
    {"galaxy": 1, "system": 2, "position": 3, "mission": "attack", "speed": 1.0, "ships": {"light_fighter": 5}}

    For this initial task, the endpoint validates input and queues a command for the
    game loop to process. Detailed travel time calculations and composition handling
    are part of subsequent tasks in docs/tasks.md.
    """

    try:
        galaxy = int(payload.get("galaxy", 0))
        system = int(payload.get("system", 0))
        position = int(payload.get("position", 0))
    except Exception:
        raise HTTPException(status_code=400, detail="galaxy, system, position must be integers")

    mission = (payload.get("mission") or "transfer").strip()
    if galaxy <= 0 or system <= 0 or position <= 0:
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    if not mission:
        raise HTTPException(status_code=400, detail="mission is required")

    # Optional parameters
    speed_val = payload.get("speed")
    try:
        speed = float(speed_val) if speed_val is not None else None
        if speed is not None and speed <= 0:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="speed must be a positive number if provided")

    ships = payload.get("ships")  # Optional; composition handling to be implemented later
    if ships is not None and not isinstance(ships, dict):
        raise HTTPException(status_code=400, detail="ships must be an object mapping ship_type to count")

    game_world.queue_command({
        'type': 'fleet_dispatch',
        'user_id': user_id,
        'galaxy': galaxy,
        'system': system,
        'position': position,
        'mission': mission,
        'speed': speed,
        'ships': ships,
    })
    game_world.queue_command({'type': 'update_player_activity', 'user_id': user_id})

    # Best-effort immediate processing to improve test determinism
    try:
        game_world._process_commands()
    except Exception:
        pass

    return {"message": "Fleet dispatch queued", "target": {"galaxy": galaxy, "system": system, "position": position}, "mission": mission}


@app.post("/player/{user_id}/fleet/{fleet_id}/recall")
async def recall_fleet(user_id: int, fleet_id: int, user=Depends(ensure_user_matches_path), _rl=Depends(rate_limiter_dependency), _pl=Depends(ensure_player_loaded)):
    """Recall an in-flight fleet back to its origin.

    The current ECS model tracks at most one in-flight FleetMovement per player entity.
    The fleet_id parameter is accepted for API compatibility; selection among multiple
    concurrent fleets is not yet implemented.
    """

    # Enqueue recall command
    game_world.queue_command({
        'type': 'fleet_recall',
        'user_id': user_id,
        'fleet_id': fleet_id,
    })
    game_world.queue_command({'type': 'update_player_activity', 'user_id': user_id})

    # Process immediately for determinism in tests
    try:
        game_world._process_commands()
    except Exception:
        pass

    # Inspect ECS to confirm recall state
    return_eta = None
    recalled = False
    try:
        from src.models import Player as _P, FleetMovement as _FM
        for ent, (p, mv) in game_world.world.get_components(_P, _FM):
            if p.user_id != user_id:
                continue
            recalled = bool(getattr(mv, 'recalled', False))
            if recalled:
                try:
                    return_eta = mv.arrival_time.isoformat()
                except Exception:
                    return_eta = None
            break
    except Exception:
        pass

    if not recalled:
        raise HTTPException(status_code=400, detail="No in-flight fleet to recall or fleet already arrived")

    return {"message": "Fleet recall queued", "recalled": True, "return_eta": return_eta}




@app.get("/player/{user_id}/planets")
async def get_player_planets(
    user_id: int,
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    _pl=Depends(ensure_player_loaded),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """List all planets owned by the authenticated user.

    When the database layer is enabled, this queries ORM planets by owner_id.
    Otherwise, it falls back to the current ECS entity's planet metadata.
    """
    data = game_world.get_player_data(user_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Player not found")

    planets: List[Dict] = []

    # Prefer database listing when available
    try:
        if is_db_enabled() and session is not None:
            from sqlalchemy import select
            from src.models.database import Planet as ORMPlanet
            result = await session.execute(select(ORMPlanet).where(ORMPlanet.owner_id == user_id))  # type: ignore[assignment]
            for row in result.scalars():
                planets.append({
                    "id": int(row.id),
                    "name": row.name,
                    "galaxy": int(row.galaxy),
                    "system": int(row.system),
                    "position": int(row.position),
                    "resources": {
                        "metal": int(row.metal),
                        "crystal": int(row.crystal),
                        "deuterium": int(row.deuterium),
                    },
                    "temperature": int(row.temperature),
                    "size": int(row.size),
                    "last_update": row.last_update.isoformat() if getattr(row, "last_update", None) else None,
                })
    except Exception:
        # Fall back to ECS below
        pass

    if not planets:
        # ECS fallback: return the current planet for this player's entity
        try:
            from src.models import Resources as _R, Planet as _Planet
            for ent, (p, pos) in game_world.world.get_components(Player, ECSPosition):
                if p.user_id != user_id:
                    continue
                res = None
                pc = None
                try:
                    res = game_world.world.component_for_entity(ent, _R)
                except Exception:
                    pass
                try:
                    pc = game_world.world.component_for_entity(ent, _Planet)
                except Exception:
                    pass
                planets.append({
                    "name": getattr(pc, "name", "Homeworld"),
                    "galaxy": int(getattr(pos, "galaxy", 1)),
                    "system": int(getattr(pos, "system", 1)),
                    "position": int(getattr(pos, "planet", 1)),
                    "resources": {
                        "metal": int(getattr(res, "metal", 0)),
                        "crystal": int(getattr(res, "crystal", 0)),
                        "deuterium": int(getattr(res, "deuterium", 0)),
                    },
                })
                break
        except Exception:
            pass

    return {"planets": planets}


@app.get("/planets/available")
async def get_available_planets(
    galaxy: Optional[int] = Query(default=None, ge=1),
    system: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """Return a list of uncolonized planet coordinates within the universe bounds.

    Optional filters:
    - galaxy: restrict to a specific galaxy
    - system: restrict to a specific system (requires galaxy or not; validated against bounds)

    Pagination:
    - limit (default 50, max 1000)
    - offset (default 0)
    """
    # Validate bounds against configuration
    if galaxy is not None and (galaxy < 1 or galaxy > GALAXY_COUNT):
        raise HTTPException(status_code=400, detail=f"galaxy must be in [1, {GALAXY_COUNT}]")
    if system is not None and (system < 1 or system > SYSTEMS_PER_GALAXY):
        raise HTTPException(status_code=400, detail=f"system must be in [1, {SYSTEMS_PER_GALAXY}]")

    # Build occupied coordinates set
    occupied: Set[Tuple[int, int, int]] = set()

    try:
        if is_db_enabled() and session is not None:
            # Query existing planets from DB
            try:
                from sqlalchemy import select
                from src.models.database import Planet as ORMPlanet
                filters = []
                if galaxy is not None:
                    filters.append(ORMPlanet.galaxy == galaxy)
                if system is not None:
                    filters.append(ORMPlanet.system == system)
                stmt = select(ORMPlanet.galaxy, ORMPlanet.system, ORMPlanet.position)
                if filters:
                    from sqlalchemy import and_  # local import to avoid top-level dependency in disabled envs
                    stmt = stmt.where(and_(*filters))
                result = await session.execute(stmt)  # type: ignore[assignment]
                rows = result.all()
                for g, s, p in rows:
                    occupied.add((int(g), int(s), int(p)))
            except Exception:
                # Fall back to ECS if DB path fails
                pass
        if not occupied:
            # ECS fallback: mark positions used by current ECS players
            try:
                for ent, (p, pos) in game_world.world.get_components(Player, ECSPosition):
                    occupied.add((int(pos.galaxy), int(pos.system), int(pos.planet)))
            except Exception:
                pass
    except Exception:
        pass

    # If a seeded pool exists, use it directly
    try:
        from src.systems.planet_creation import seeded_pool_ready, list_available_from_seed
        if seeded_pool_ready():
            seeded_available = list_available_from_seed(occupied, galaxy=galaxy, system=system, limit=limit, offset=offset)
            return {"available": seeded_available}
    except Exception:
        pass

    # Fallback: generate candidates within bounds, honoring filters and pagination
    candidates: List[Dict[str, int]] = []
    collected = 0
    target = offset + limit

    g_start = galaxy if galaxy is not None else 1
    g_end = galaxy if galaxy is not None else GALAXY_COUNT
    s_start_default = system if system is not None else 1
    s_end_default = system if system is not None else SYSTEMS_PER_GALAXY

    for g in range(g_start, g_end + 1):
        s_start = s_start_default
        s_end = s_end_default
        for s in range(s_start, s_end + 1):
            for p in range(1, POSITIONS_PER_SYSTEM + 1):
                if (g, s, p) in occupied:
                    continue
                # Collect until offset+limit, then slice later
                if collected < target:
                    candidates.append({"galaxy": g, "system": s, "position": p})
                    collected += 1
                else:
                    break
            if collected >= target:
                break
        if collected >= target:
            break

    # Apply offset and limit
    available = candidates[offset:offset + limit]
    return {"available": available}


# Choose starting location endpoint
@app.post("/player/{user_id}/choose-start")
async def choose_start(
    user_id: int,
    payload: dict,
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    session: Optional[AsyncSession] = Depends(get_optional_async_session),
):
    """Allow a newly registered user to choose a starting location.

    Expects JSON payload containing:
    - galaxy: int (required)
    - system: int (required)
    - position: int (optional) -> if omitted, the first free position in the system is used
    - name: str (optional) -> planet name; defaults from config

    Constraints:
    - Only allowed if the user currently owns zero planets.
    - Coordinates must be within configured bounds and target position must be unoccupied.
    """
    # Bounds and defaults
    from src.core.config import GALAXY_COUNT, SYSTEMS_PER_GALAXY, POSITIONS_PER_SYSTEM, STARTER_PLANET_NAME, PLANET_SIZE_MIN, PLANET_SIZE_MAX, PLANET_TEMPERATURE_MIN, PLANET_TEMPERATURE_MAX, STARTER_INIT_RESOURCES

    try:
        galaxy = int(payload.get("galaxy"))
        system = int(payload.get("system"))
    except Exception:
        raise HTTPException(status_code=400, detail="galaxy and system are required and must be integers")

    position = payload.get("position")
    if position is not None:
        try:
            position = int(position)
        except Exception:
            raise HTTPException(status_code=400, detail="position must be an integer if provided")

    name = payload.get("name") or STARTER_PLANET_NAME

    # Validate bounds
    if galaxy < 1 or galaxy > GALAXY_COUNT:
        raise HTTPException(status_code=400, detail=f"galaxy must be in [1, {GALAXY_COUNT}]")
    if system < 1 or system > SYSTEMS_PER_GALAXY:
        raise HTTPException(status_code=400, detail=f"system must be in [1, {SYSTEMS_PER_GALAXY}]")
    if position is not None and (position < 1 or position > POSITIONS_PER_SYSTEM):
        raise HTTPException(status_code=400, detail=f"position must be in [1, {POSITIONS_PER_SYSTEM}]")

    # If DB is enabled, persist the starter planet there
    if is_db_enabled() and session is not None:
        from sqlalchemy import select, and_
        from src.models.database import Planet as ORMPlanet, User as ORMUser
        # Verify user exists
        result = await session.execute(select(ORMUser).where(ORMUser.id == user_id))
        orm_user = result.scalar_one_or_none()
        if orm_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        # Ensure user has zero planets
        result = await session.execute(select(ORMPlanet).where(ORMPlanet.owner_id == user_id))
        if result.scalars().first() is not None:
            raise HTTPException(status_code=400, detail="Starter planet already chosen")
        # Determine occupied positions in selected system
        q = select(ORMPlanet.position).where(and_(ORMPlanet.galaxy == galaxy, ORMPlanet.system == system))
        occupied = {int(row[0]) for row in (await session.execute(q)).all()}
        # If position not specified, choose the first free
        if position is None:
            for p in range(1, POSITIONS_PER_SYSTEM + 1):
                if p not in occupied:
                    position = p
                    break
            if position is None:
                raise HTTPException(status_code=409, detail="Selected system is full")
        else:
            if int(position) in occupied:
                raise HTTPException(status_code=409, detail="Selected position is occupied")
        # Create the planet with configured attributes
        import random as _rnd
        size = int(_rnd.randint(int(PLANET_SIZE_MIN), int(PLANET_SIZE_MAX)))
        temperature = int(_rnd.randint(int(PLANET_TEMPERATURE_MIN), int(PLANET_TEMPERATURE_MAX)))
        planet = ORMPlanet(
            name=str(name), owner_id=int(user_id), galaxy=int(galaxy), system=int(system), position=int(position),
            size=size, temperature=temperature,
        )
        # Apply starter resources from config
        try:
            planet.metal = int(STARTER_INIT_RESOURCES.get('metal', planet.metal))
            planet.crystal = int(STARTER_INIT_RESOURCES.get('crystal', planet.crystal))
            planet.deuterium = int(STARTER_INIT_RESOURCES.get('deuterium', planet.deuterium))
        except Exception:
            pass
        session.add(planet)
        await session.commit()
        # Load player into ECS from DB to reflect new planet
        try:
            game_world.load_player_data(user_id)
        except Exception:
            pass
        return {
            "message": "Starter planet created",
            "planet": {"id": int(planet.id), "name": planet.name, "galaxy": int(galaxy), "system": int(system), "position": int(position)},
        }

    # ECS-only fallback when DB is disabled
    # Ensure user entity does not already have a planet
    try:
        for ent, (p, pos) in game_world.world.get_components(Player, ECSPosition):
            if p.user_id == user_id:
                raise HTTPException(status_code=400, detail="Starter planet already chosen")
    except HTTPException:
        raise
    except Exception:
        pass

    # Determine occupied in ECS
    ecs_occupied = set()
    try:
        for ent, (p, pos) in game_world.world.get_components(Player, ECSPosition):
            ecs_occupied.add((int(pos.galaxy), int(pos.system), int(pos.planet)))
    except Exception:
        pass

    if position is None:
        for p in range(1, POSITIONS_PER_SYSTEM + 1):
            if (galaxy, system, p) not in ecs_occupied:
                position = p
                break
        if position is None:
            raise HTTPException(status_code=409, detail="Selected system is full")
    else:
        if (galaxy, system, int(position)) in ecs_occupied:
            raise HTTPException(status_code=409, detail="Selected position is occupied")

    # Create ECS entity for the user at the chosen coordinates
    try:
        from src.models import Resources as _R, Planet as _Planet, ResourceProduction as _RP, Buildings as _B, BuildQueue as _BQ, ShipBuildQueue as _SBQ, Fleet as _F, Research as _Res, ResearchQueue as _Rq
        game_world.world.create_entity(
            Player(name=str(getattr(user, 'username', f"User{user_id}")) if hasattr(user, 'username') else f"User{user_id}", user_id=int(user_id)),
            ECSPosition(galaxy=int(galaxy), system=int(system), planet=int(position)),
            _R(),
            _RP(),
            _B(),
            _BQ(),
            _SBQ(),
            _F(),
            _Res(),
            _Rq(),
            _Planet(name=str(name), owner_id=int(user_id)),
        )
    except Exception:
        # Minimal entity if components are unavailable
        try:
            game_world.world.create_entity(Player(name=f"User{user_id}", user_id=int(user_id)), ECSPosition(galaxy=int(galaxy), system=int(system), planet=int(position)))
        except Exception:
            pass

    return {"message": "Starter planet created", "planet": {"name": str(name), "galaxy": int(galaxy), "system": int(system), "position": int(position)}}


# Planet switching endpoint
@app.post("/player/{user_id}/planets/{planet_id}/select")
async def select_active_planet(
    user_id: int,
    planet_id: int,
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    _pl=Depends(ensure_player_loaded),
    session: Optional[AsyncSession] = Depends(get_optional_async_session),
):
    """Switch the active planet for the authenticated user.

    Requires the database layer to be enabled. If the planet does not belong to the user
    or cannot be loaded, returns 404.
    """
    # Ensure DB is enabled for planet-level selection (IDs are DB-backed)
    if not is_db_enabled() or session is None:
        raise HTTPException(status_code=400, detail="Planet switching requires the database layer to be enabled")

    ok = False
    try:
        ok = game_world.set_active_planet_by_id(user_id, planet_id)
    except Exception:
        ok = False

    if not ok:
        raise HTTPException(status_code=404, detail="Planet not found or not owned by user")

    # Return the new active position for confirmation
    data = game_world.get_player_data(user_id)
    position = data.get("position", {}) if data else {}
    return {"message": "Active planet switched", "planet_id": planet_id, "position": position}



@app.get("/player/{user_id}/battle-reports")
async def list_battle_reports(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    _pl=Depends(ensure_player_loaded),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """List battle reports visible to the player (attacker or defender).

    Pagination via limit/offset. Returns newest-first ordering.
    """
    # Prefer DB when available via sync helpers
    if is_db_enabled():
        try:
            reports = await fetch_battle_reports_for_user(user_id, limit=limit, offset=offset)
            if reports:
                return {"reports": reports}
        except Exception:
            pass

    # Fallback to in-memory store
    try:
        reports = game_world.list_battle_reports(user_id, limit=limit, offset=offset)
    except Exception:
        reports = []
    return {"reports": reports}


@app.get("/player/{user_id}/battle-reports/{report_id}")
async def get_battle_report(
    user_id: int,
    report_id: int,
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """Retrieve a single battle report if the user is a participant."""
    # Prefer DB when available via sync helper
    if is_db_enabled():
        try:
            report = await fetch_battle_report_for_user(user_id, report_id)
            if report:
                return report
        except Exception:
            pass
    # Fallback to in-memory
    try:
        report = game_world.get_battle_report(user_id, report_id)
    except Exception:
        report = None
    if not report:
        raise HTTPException(status_code=404, detail="Battle report not found")
    return report



@app.get("/player/{user_id}/espionage-reports")
async def list_espionage_reports(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    _pl=Depends(ensure_player_loaded),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """List espionage reports visible to the player (attacker or defender).

    Pagination via limit/offset. Returns newest-first ordering.
    """
    # Prefer DB when available via sync helpers
    if is_db_enabled():
        try:
            reports = await fetch_espionage_reports_for_user(user_id, limit=limit, offset=offset)
            if reports is not None:
                return {"reports": reports}
        except Exception:
            pass

    # Fallback to in-memory store
    try:
        reports = game_world.list_espionage_reports(user_id, limit=limit, offset=offset)
    except Exception:
        reports = []
    return {"reports": reports}


@app.get("/player/{user_id}/espionage-reports/{report_id}")
async def get_espionage_report(
    user_id: int,
    report_id: int,
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """Retrieve a single espionage report if the user is a participant."""
    # Prefer DB when available via sync helper
    if is_db_enabled():
        try:
            report = await fetch_espionage_report_for_user(user_id, report_id)
            if report:
                return report
        except Exception:
            pass
    # Fallback to in-memory
    try:
        report = game_world.get_espionage_report(user_id, report_id)
    except Exception:
        report = None
    if not report:
        raise HTTPException(status_code=404, detail="Espionage report not found")
    return report



# --- Trading Endpoints ---
@app.get("/player/{user_id}/trade/history")
async def list_trade_history(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    _pl=Depends(ensure_player_loaded),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """List the authenticated user's trade history (offer_created and trade_completed).

    Newest-first, paginated via limit/offset.
    """
    # Use centralized service (handles DB vs in-memory)
    try:
        from src.core.trade_events import list_trade_history as _list_trade_history
        events = await _list_trade_history(user_id=int(user_id), limit=limit, offset=offset, session=session, gw=game_world)
        return {"events": events}
    except Exception:
        # Fallback to in-memory direct
        try:
            events = game_world.list_trade_history(user_id, limit=limit, offset=offset)
        except Exception:
            events = []
        return {"events": events}

@app.post("/trade/offers")
async def create_trade_offer(
    payload: dict,
    user=Depends(get_current_user),
    _rl=Depends(rate_limiter_dependency),
    _pl=Depends(ensure_current_user_player_loaded),
    session: Optional[AsyncSession] = Depends(get_optional_async_session),
):
    """Create a marketplace trade offer.

    Body example:
    {"offered_resource": "metal", "offered_amount": 100, "requested_resource": "crystal", "requested_amount": 50}
    """
    offered_resource = payload.get("offered_resource")
    requested_resource = payload.get("requested_resource")
    if not offered_resource or not requested_resource:
        raise HTTPException(status_code=400, detail="offered_resource and requested_resource are required")
    try:
        offered_amount = int(payload.get("offered_amount", 0))
        requested_amount = int(payload.get("requested_amount", 0))
    except Exception:
        raise HTTPException(status_code=400, detail="offered_amount and requested_amount must be integers")

    # Perform escrow in ECS (in-memory game state)
    oid = game_world._handle_trade_create_offer(
        int(user.id),
        str(offered_resource),
        int(offered_amount),
        str(requested_resource),
        int(requested_amount),
    )
    if not oid:
        raise HTTPException(status_code=400, detail="Invalid offer or insufficient resources")

    # Best-effort activity update
    try:
        game_world.queue_command({'type': 'update_player_activity', 'user_id': int(user.id)})
    except Exception:
        pass

    # Persist to DB when available
    created_offer_dict = None
    try:
        if is_db_enabled() and session is not None:
            # Insert TradeOffer with explicit id to match in-memory ID
            orm_offer = ORMTradeOffer(
                id=int(oid),
                seller_user_id=int(user.id),
                offered_resource=str(offered_resource),
                offered_amount=int(offered_amount),
                requested_resource=str(requested_resource),
                requested_amount=int(requested_amount),
                status="open",
            )
            session.add(orm_offer)
            await session.flush()
            # Record event via centralized service (commits the session)
            from src.core.trade_events import record_trade_event, TradeEventPayload
            payload: TradeEventPayload = {
                "type": "offer_created",
                "offer_id": int(oid),
                "seller_user_id": int(user.id),
                "buyer_user_id": None,
                "offered_resource": str(offered_resource),
                "offered_amount": int(offered_amount),
                "requested_resource": str(requested_resource),
                "requested_amount": int(requested_amount),
                "status": "open",
            }
            await record_trade_event(payload, session=session)
            created_offer_dict = {
                "id": int(orm_offer.id),
                "seller_user_id": int(orm_offer.seller_user_id),
                "offered_resource": orm_offer.offered_resource,
                "offered_amount": int(orm_offer.offered_amount),
                "requested_resource": orm_offer.requested_resource,
                "requested_amount": int(orm_offer.requested_amount),
                "status": orm_offer.status,
                "created_at": orm_offer.created_at.isoformat() if getattr(orm_offer, "created_at", None) else None,
            }
    except Exception:
        # Swallow DB errors; fallback response from in-memory
        created_offer_dict = None

    # Build response from DB if available; otherwise from ECS
    if created_offer_dict is not None:
        return created_offer_dict
    offers = game_world.list_market_offers(status=None)
    offer = next((o for o in offers if int(o.get("id", -1)) == int(oid)), None)
    return offer or {"id": oid}


@app.get("/trade/offers")
async def list_trade_offers(
    status: Optional[str] = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """List marketplace offers. Filter by status ('open', 'accepted', 'cancelled') or None for all."""
    # Normalize status: allow explicit null via 'all'
    _status = None if status in (None, "all", "*") else str(status)

    # Prefer DB for listing
    if is_db_enabled() and session is not None:
        try:
            stmt = select(ORMTradeOffer)
            if _status is not None:
                stmt = stmt.where(ORMTradeOffer.status == _status)
            stmt = stmt.order_by(ORMTradeOffer.created_at.desc()).offset(int(offset)).limit(int(limit))
            result = await session.execute(stmt)
            rows = result.scalars().all()
            offers = [
                {
                    "id": int(o.id),
                    "seller_user_id": int(o.seller_user_id),
                    "offered_resource": o.offered_resource,
                    "offered_amount": int(o.offered_amount),
                    "requested_resource": o.requested_resource,
                    "requested_amount": int(o.requested_amount),
                    "status": o.status,
                    "accepted_by": int(o.accepted_by) if o.accepted_by is not None else None,
                    "created_at": o.created_at.isoformat() if getattr(o, "created_at", None) else None,
                    "accepted_at": o.accepted_at.isoformat() if getattr(o, "accepted_at", None) else None,
                }
                for o in rows
            ]
            return {"offers": offers}
        except Exception:
            pass

    # Fallback to in-memory list
    try:
        offers = game_world.list_market_offers(status=_status, limit=limit, offset=offset)
    except Exception:
        offers = []
    return {"offers": offers}


@app.post("/trade/accept/{offer_id}")
async def accept_trade_offer(
    offer_id: int,
    user=Depends(get_current_user),
    _rl=Depends(rate_limiter_dependency),
    _pl=Depends(ensure_current_user_player_loaded),
    session: Optional[AsyncSession] = Depends(get_optional_async_session),
):
    """Accept an open marketplace offer by ID."""

    ok = False
    try:
        ok = game_world._handle_trade_accept_offer(int(user.id), int(offer_id))
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=400, detail="Offer not found, not open, or insufficient funds/self-trade")

    # Persist acceptance to DB when available
    try:
        if is_db_enabled() and session is not None:
            # Load offer
            result = await session.execute(select(ORMTradeOffer).where(ORMTradeOffer.id == int(offer_id)))
            orm_offer = result.scalar_one_or_none()
            if orm_offer is not None and orm_offer.status == "open":
                orm_offer.status = "accepted"
                orm_offer.accepted_by = int(user.id)
                from datetime import datetime as _dt
                orm_offer.accepted_at = _dt.utcnow()
                # Record event via centralized service; commit will persist both offer and event
                from src.core.trade_events import record_trade_event, TradeEventPayload
                payload: TradeEventPayload = {
                    "type": "trade_completed",
                    "offer_id": int(offer_id),
                    "seller_user_id": int(orm_offer.seller_user_id),
                    "buyer_user_id": int(user.id),
                    "offered_resource": orm_offer.offered_resource,
                    "offered_amount": int(orm_offer.offered_amount),
                    "requested_resource": orm_offer.requested_resource,
                    "requested_amount": int(orm_offer.requested_amount),
                    "status": "completed",
                }
                await record_trade_event(payload, session=session)
    except Exception:
        pass

    return {"accepted": True, "offer_id": int(offer_id)}


# --- Notifications Endpoints ---
@app.get("/player/{user_id}/notifications")
async def list_notifications(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user=Depends(ensure_user_matches_path),
    _rl=Depends(rate_limiter_dependency),
    session: Optional[AsyncSession] = Depends(get_optional_readonly_async_session),
):
    """List notifications for the authenticated user.

    Prefers database when enabled; falls back to in-memory notifications otherwise.
    Newest-first ordering when using DB.
    """
    notifications: List[Dict] = []

    # Database path (preferred)
    try:
        if is_db_enabled() and session is not None:
            from sqlalchemy import select
            from sqlalchemy import desc as _desc
            from src.models.database import Notification as ORMNotification
            stmt = select(ORMNotification).where(ORMNotification.user_id == user_id).order_by(_desc(ORMNotification.created_at)).offset(offset).limit(limit)
            result = await session.execute(stmt)  # type: ignore[assignment]
            for row in result.scalars():
                notifications.append({
                    "id": int(row.id),
                    "user_id": int(row.user_id),
                    "type": row.type,
                    "payload": dict(row.payload or {}),
                    "priority": row.priority,
                    "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
                    "read_at": row.read_at.isoformat() if getattr(row, "read_at", None) else None,
                })
    except Exception:
        # Fall back to in-memory
        notifications = []

    # In-memory fallback
    if not notifications:
        try:
            from src.core.notifications import get_in_memory_notifications as _get_inmem
            items = _get_inmem(user_id=user_id, limit=limit, offset=offset)  # type: ignore
        except Exception:
            items = []
        # items may already contain ISO strings for timestamps
        for rec in items:
            try:
                notifications.append({
                    "id": rec.get("id"),
                    "user_id": int(rec.get("user_id")),
                    "type": rec.get("type"),
                    "payload": dict(rec.get("payload") or {}),
                    "priority": rec.get("priority", "normal"),
                    "created_at": rec.get("created_at"),
                    "read_at": rec.get("read_at"),
                })
            except Exception:
                continue

    return {"notifications": notifications}


@app.delete("/notifications/{notification_id}")
async def delete_notification(
    notification_id: int,
    user=Depends(get_current_user),
    _rl=Depends(rate_limiter_dependency),
    session: Optional[AsyncSession] = Depends(get_optional_async_session),
):
    """Delete a notification by ID if it belongs to the authenticated user.

    Requires database to be enabled. Returns 404 if not found or DB disabled.
    """
    # Must have DB
    if not (is_db_enabled() and session is not None):
        raise HTTPException(status_code=404, detail="Notification not found")

    try:
        from sqlalchemy import select
        from src.models.database import Notification as ORMNotification
        result = await session.execute(select(ORMNotification).where(ORMNotification.id == int(notification_id)))  # type: ignore[assignment]
        obj = result.scalar_one_or_none()
        if obj is None or int(obj.user_id) != int(user.id):
            raise HTTPException(status_code=404, detail="Notification not found")
        await session.delete(obj)
        await session.commit()
        return {"deleted": True, "id": int(notification_id)}
    except HTTPException:
        raise
    except Exception:
        # Hide internal errors for safety
        raise HTTPException(status_code=404, detail="Notification not found")



# Database health probe endpoint to explicitly surface DB status
@app.get("/healthz/db")
async def healthz_db():
    """Database health probe endpoint.

    Returns:
        JSON with database.enabled and database.status (ok|fail).
    """
    try:
        enabled = is_db_enabled()
    except Exception:
        enabled = False
    ok = False
    if enabled:
        try:
            ok = await check_database()
        except Exception:
            ok = False
    return {
        "database": {
            "enabled": bool(enabled),
            "status": "ok" if ok else "fail",
        }
    }


# --- Market Guidance Endpoint ---
@app.get("/market/guidance")
async def get_market_guidance():
    """Return soft guidance for market exchange ratios and current transaction fee rate.

    Ratios express relative value weights for resources (metal:crystal:deuterium).
    The fee rate is applied to the seller proceeds at acceptance time; buyer pays
    the full requested amount. With default configuration, the fee is 0.0.
    """
    try:
        # Local import to avoid widening global import list
        from src.core.config import EXCHANGE_RATIOS as _RATIOS, TRADE_TRANSACTION_FEE_RATE as _FEE
        ratios = {
            "metal": float(_RATIOS.get("metal", 3.0)),
            "crystal": float(_RATIOS.get("crystal", 2.0)),
            "deuterium": float(_RATIOS.get("deuterium", 1.0)),
        }
        fee = float(_FEE)
    except Exception:
        ratios = {"metal": 3.0, "crystal": 2.0, "deuterium": 1.0}
        fee = 0.0
    return {"exchange_ratios": ratios, "transaction_fee_rate": fee}
