# Ogame-like Game Server

A FastAPI-based OGame-like backend featuring an ECS-style game loop powered by `esper`. The app exposes a REST/WS API for gameplay actions and runs a background tick loop to process game systems (resources, building, research, fleets, battles, trade, notifications).

- ASGI entrypoint: `src.main:app`
- Tick loop: configurable via `TICK_RATE` (default 1 Hz)
- Database: async SQLAlchemy with optional read-replica support (see Configuration)

## Quickstart

Two modes are supported: in-memory only (no DB) and full DB-backed.

- In-memory only (fastest to start; all state is ephemeral):
  1) Create venv and install deps (FastAPI, uvicorn, esper): see below.
  2) Do NOT set ENABLE_DB. Start server:
     
     uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
  
  Endpoints and systems will run entirely in memory; DB-only features return clear errors where applicable.

Prerequisites:
- Python 3.10+

Create a virtualenv and install dependencies:
```
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Run the server (development):
```
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

Docker (optional, with PostgreSQL):
```
docker compose up --build
```
This starts Postgres and the app on http://localhost:8000. The app will create schema at startup in dev; see “Migrations” below for production.

Local DB-enabled run (without Docker Compose):
If you prefer to run the app directly against a local PostgreSQL instance, enable the database layer and provide a connection URL via environment variables, then start uvicorn:

```
export ENABLE_DB=true
export DATABASE_URL=postgresql+asyncpg://ogame:ogame@localhost:5432/ogame
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

Note: ensure PostgreSQL is running locally and accessible at the given URL. The requirements already include greenlet, which SQLAlchemy uses for async execution paths.

OpenAPI schema generation:
```
python scripts/generate_openapi.py --out openapi.yaml
python scripts/generate_openapi.py --format json --out openapi.json
```

## Project Structure

- src/
  - main.py — ASGI entrypoint exporting FastAPI app
  - api/
    - routes.py — REST and WebSocket routes, lifespan startup/shutdown
    - auth.py — register/login/me/logout
    - ws.py — thread-safe WS bridge (run_coroutine_threadsafe)
  - core/
    - game.py — GameWorld, tick thread, command queue, system registration
    - config.py — configuration and gameplay constants (env-driven)
    - database.py — async SQLAlchemy engines/sessions, read replicas
    - state.py — singleton GameWorld for routers
    - metrics.py, notifications.py, sync.py — instrumentation, offline notifications, DB sync helpers
  - models/
    - components.py — ECS components (Player, Resources, Buildings, Research, etc.)
    - database.py — SQLAlchemy ORM models
  - systems/
    - resource_production.py, building_construction.py, research.py, shipyard.py, fleet_movement.py, battle.py, player_activity.py
- tests/ — unit and integration tests (pytest)
- migrations/ — Alembic migrations
- scripts/ — utilities (OpenAPI generation, load testing via Locust)
- docs/ — additional docs (API, plan, requirements, tasks)

See docs/tasks.md for the broader roadmap.

## System Architecture & Game Loop

- ECS with esper:
  - GameWorld constructs an `esper.World()` and registers processors:
    - ResourceProductionSystem
    - BuildingConstructionSystem
    - PlayerActivitySystem
    - ResearchSystem
    - ShipyardSystem
    - FleetMovementSystem
    - BattleSystem
- Loop thread:
  - On app startup, a daemon thread runs `world.process()` every tick (default 1/s).
  - A thread-safe Queue receives commands from REST endpoints; commands are applied before systems each tick.
  - Periodic persistence every ~60s and daily cleanup run from the loop.
- WebSocket bridge:
  - src/api/ws.py captures the asyncio loop on startup and exposes `send_to_user(user_id, payload)` for systems to emit events safely from the loop thread.

## Game Logic Overview

- Resources & Energy (ResourceProductionSystem)
  - Accrues metal/crystal/deuterium based on base rates scaled by building levels (1.1^level), energy balance, and research bonuses.
  - Solar plant produces energy; mines/synthesizer consume energy. Production factor = min(1, produced/required).
  - Research effects: ENERGY increases energy production; PLASMA boosts resource yields per level.
- Building Construction (BuildingConstructionSystem)
  - Build queue per player; first item completes when its completion_time elapses, incrementing building level and emitting `building_complete` + offline notification.
  - Costs and times scale with level (see src/core/config.py and formulas in GameWorld).
- Research (ResearchSystem)
  - Queue-based; prerequisites enforced (e.g., plasma requires energy 8). Research modifies production, build times, and ship stats.
- Shipyard (ShipyardSystem)
  - Queue ships with per-unit costs and times; stats derived from BASE_SHIP_STATS plus research multipliers.
- Fleets (FleetMovementSystem)
  - Travel between coordinates with speed from ship type and research; support recall mid-flight; arrival triggers effects (e.g., transfer/combat).
- Battles (BattleSystem)
  - Resolves combat based on fleet compositions and research-modified stats; generates battle reports and notifications.
- Marketplace & Trade
  - Create/list/accept offers; events recorded in trade history with validation.
- Planets & Colonization
  - Multiple planets per player; universe dimensions configurable; colonization uses colony ships and time delays.
- Player Activity & Notifications
  - Last-activity updates; notifications stored for offline players; WS events for online players.

For implementation details, browse src/systems/* and src/core/game.py.

## API Overview

- REST endpoints include player data, building actions, research, fleets (dispatch/recall), planets (list/available/select), trade, notifications, health/metrics.
- Authentication: JWT (register/login endpoints under /auth). Protected endpoints verify user identity.
- WebSocket: `/ws?token=JWT` — server sends JSON messages with a `type` field: `welcome`, `resource_update`, `building_complete`, `pong`, `error`, etc.
- Detailed endpoint documentation: see docs/API.md and the generated OpenAPI (openapi.yaml/json).

Minimal WebSocket example (browser JavaScript):
```html
<script>
  const token = localStorage.getItem('access_token');
  const ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(token)}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'welcome') console.log('Welcome', msg);
    if (msg.type === 'resource_update') console.log('Resource update', msg);
  };
  ws.onopen = () => ws.send('ping');
</script>
```

## Running & Developing

1) With PostgreSQL (recommended):
- Provide DATABASE_URL pointing to Postgres (e.g., `postgresql+asyncpg://ogame:ogame@localhost:5432/ogame`) or use Docker Compose.

2) With PostgreSQL via Docker Compose:
- `docker compose up --build`
- Environment is set in docker-compose.yml (DATABASE_URL, TICK_RATE, CORS_*).

3) Environment variables (common):
- TICK_RATE: ticks per second (default 1.0)
- DATABASE_URL: async SQLAlchemy URL (e.g., postgresql+asyncpg://user:pass@host/db)
- READ_REPLICA_URLS: optional comma-separated read replica URLs
- JWT_SECRET, ACCESS_TOKEN_EXPIRE_MINUTES, RATE_LIMIT_PER_MINUTE
- CORS_ALLOW_ORIGINS, CORS_ALLOW_METHODS, CORS_ALLOW_HEADERS
- See src/core/config.py for more (ship stats, costs, universe sizes, etc.).

Migrations:
- In development, the app ensures schema via `metadata.create_all()` at startup.
- Alembic migrations exist under `migrations/` for production use; configure Alembic and run `alembic upgrade head` in your deployment pipeline.

OpenAPI generation:
- `python scripts/generate_openapi.py --out openapi.yaml`

Load testing (Locust):
- See scripts/load/README.md; example: `locust -f scripts/load/locustfile.py`.

## Testing

Run tests with pytest:
```
pytest -q
```
Notes:
- Creating a FastAPI TestClient triggers startup/shutdown; the game loop runs in a daemon thread at ~1 Hz during tests.
- To avoid threaded loop in specific tests, monkeypatch GameWorld.start_game_loop/stop_game_loop to no-ops.

## Configuration Reference (selected)

See src/core/config.py for the complete list and defaults. Highlights:
- Game loop: TICK_RATE
- DB: DATABASE_URL, READ_REPLICA_URLS, DB_* pool settings
- Auth: JWT_SECRET, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, RATE_LIMIT_PER_MINUTE
- CORS: CORS_ALLOW_ORIGINS, CORS_ALLOW_METHODS, CORS_ALLOW_HEADERS
- Gameplay constants: BASE_BUILDING_COSTS, BASE_BUILD_TIMES, PREREQUISITES, BASE_RESEARCH_COSTS/TIMES, RESEARCH_PREREQUISITES, PLASMA_PRODUCTION_BONUS, SHIP stats and costs, UNIVERSE dimensions

## Persistence & Restart

This project supports two runtime modes with different persistence guarantees.

- With a database enabled (DATABASE_URL set):
  - What persists across restarts
    - Resources and production metadata: Planet metal/crystal/deuterium amounts, per-hour rates, and last_update are synced periodically (~60s per planet) during the loop and once on graceful shutdown. On startup, players are autoloaded and production resumes from last_update. 
    - Building levels: Core buildings are persisted idempotently (metal_mine, crystal_mine, deuterium_synthesizer, solar_plant, robot_factory, shipyard). Other building types may be read from the DB if present but might not be written every save yet.
    - Research levels: Hydrated on load if present in DB. Changes from completed research are not yet written back by systems and will be lost on restart until persistence is added.
    - Stationed fleets: Hydrated on load if present in DB. Changes from ship construction or combat are not yet written back by systems and will be lost on restart until persistence is added.
    - Notifications: Written to DB (with an in-memory ring buffer fallback for tests) and available after restart.
    - Marketplace: Trade offers and trade events are persisted by the endpoints when DB is available; listing prefers DB, with in-memory as fallback.
  - What does not persist yet (in-memory only, lost on restart)
    - Queues and timers: Building, research, and shipyard queues and their completion_time values are not yet persisted. If the server restarts before completion, those in-flight queue items are dropped. Completed changes that were already written (e.g., a building level persisted on or before shutdown) remain.
    - In-flight fleets: Missions/ETAs/positions exist only in memory; restart cancels those movements. Stationed fleets remain via DB.
    - Reports: Battle and espionage reports are currently stored in-memory only and will be lost on restart.
  - Lifecycle
    - Startup: During FastAPI lifespan startup, the service initializes the DB, then autoloads players into the ECS world before starting the background tick loop so gameplay resumes immediately.
    - Shutdown: On shutdown, the loop is stopped and a final best-effort save is performed to flush last-minute state, then database engines are closed.

- With the database disabled (no DATABASE_URL available):
  - The entire game state is ephemeral and lives in memory. On process restart, all resources/buildings/research/fleets/queues/reports/offers are reset. Notifications are kept only in the in-memory buffer during runtime.
  - This mode is suitable for quick local testing; for durable play, provide a DATABASE_URL (PostgreSQL recommended).

Configuration notes:
- Tick rate is controlled by TICK_RATE (default ~1 Hz). Persistence of resources/buildings is throttled internally (~60s per planet) to reduce write load and also performed on shutdown.
- When DB is available, endpoints prefer DB-backed listings (e.g., trade offers/history) and fall back to in-memory otherwise.

Future work (see docs/tasks.md):
- Persist in-memory stores (battle/espionage reports, marketplace offers, trade history) fully to DB and hydrate on startup.
- Add file-backed fallback (JSON under data/) when DB is disabled so selected state survives local restarts.
- Rebuild in-memory ID counters from persisted max IDs to avoid collisions after restart.

## Notes & Tips
- Esper world initialization uses `esper.World()` (already handled). If you see AttributeError, verify dependency versions.
- The game loop is a daemon thread; ensure clean shutdowns in your env (handled by FastAPI lifespan in src/api/routes.py).
- For production, tighten CORS and secrets; prefer Postgres and Alembic migrations.


## Environment Variables

All configurable environment variables and their default values. Unless stated otherwise, values are read at process start from environment variables.

Core runtime and database:
- TICK_RATE (default: 1.0)
  - Game loop tick frequency in Hertz (ticks per second).
- SAVE_INTERVAL_SECONDS (default: 60)
  - Interval in seconds for periodic persistence (save_player_data) executed from the game loop.
- CLEANUP_DAYS (default: 30)
  - Threshold (days) for the daily cleanup job that prunes inactive players.
- ENABLE_DB (default: false)
  - Master switch for the database layer. When not "true", the DB is disabled even if DATABASE_URL is set. Set to "true" to enable DB engines and persistence.
- DATABASE_URL (default: postgresql+asyncpg://ogame:ogame@localhost:5432/ogame)
  - Async SQLAlchemy URL for the primary database. Examples: postgresql+asyncpg://user:pass@host:5432/dbname
- READ_REPLICA_URLS (default: "")
  - Optional comma-separated list of async SQLAlchemy URLs for read replicas. If empty, reads use the primary DB.
- DB_ECHO (default: false)
  - When "true", SQLAlchemy logs SQL statements.
- DB_POOL_PRE_PING (default: true)
  - Enable pool pre-ping to validate connections before use.
- DB_POOL_SIZE (default: 5)
  - SQLAlchemy pool size for the primary engine.
- DB_MAX_OVERFLOW (default: 10)
  - Maximum overflow size beyond the pool size.
- DB_POOL_TIMEOUT (default: 30)
  - Pool connection acquisition timeout in seconds.
- DB_POOL_RECYCLE (default: 1800)
  - Recycle connections after this many seconds.

Authentication & security:
- JWT_SECRET (default: dev-secret-change-me)
  - Secret key for signing JWTs. Change in production.
- JWT_ALGORITHM (default: HS256)
  - JWT signing algorithm.
- ACCESS_TOKEN_EXPIRE_MINUTES (default: 1440)
  - Access token lifetime in minutes (24 hours by default).
- RATE_LIMIT_PER_MINUTE (default: 100)
  - Simple per-user in-memory rate limit for protected endpoints.

CORS:
- CORS_ALLOW_ORIGINS (default: *)
  - Comma-separated list of allowed origins. Use "*" for all.
- CORS_ALLOW_CREDENTIALS (default: true)
  - Whether to allow credentials in CORS requests.
- CORS_ALLOW_METHODS (default: *)
  - Comma-separated allowed HTTP methods.
- CORS_ALLOW_HEADERS (default: *)
  - Comma-separated allowed HTTP headers.

Gameplay, universe, and balancing knobs:
- COLONIZATION_TIME_SECONDS (default: 1)
  - Additional time required after fleet arrival to complete colonization.
- BASE_MAX_FLEET_SIZE (default: 50)
  - Base maximum number of ships allowed per planet.
- FLEET_SIZE_PER_COMPUTER_LEVEL (default: 10)
  - Additional ships allowed per level of Computer Technology.
- GALAXY_COUNT (default: 9)
  - Number of galaxies.
- SYSTEMS_PER_GALAXY (default: 499)
  - Number of solar systems per galaxy.
- POSITIONS_PER_SYSTEM (default: 15)
  - Number of positions per solar system.
- MAX_PLAYERS (default: 512)
  - Planning knob for expected scale; also used for seeding defaults.
- INITIAL_PLANETS (default: 2 × MAX_PLAYERS; 1024 by default)
  - Number of empty coordinates to pre-seed randomly on startup.
- REQUIRE_START_CHOICE (default: false)
  - When true, registration does not auto-create a homeworld. Users must call the start choice endpoint.
- STARTER_PLANET_NAME (default: Homeworld)
  - Default planet name for newly created homeworlds.
- STARTER_METAL (default: 500)
  - Initial metal resources for starter planet.
- STARTER_CRYSTAL (default: 300)
  - Initial crystal resources for starter planet.
- STARTER_DEUTERIUM (default: 100)
  - Initial deuterium resources for starter planet.
- PLANET_SIZE_MIN (default: 140)
  - Minimum planet size used in generation.
- PLANET_SIZE_MAX (default: 200)
  - Maximum planet size used in generation.
- PLANET_TEMPERATURE_MIN (default: -40)
  - Minimum planet temperature used in generation (°C).
- PLANET_TEMPERATURE_MAX (default: 60)
  - Maximum planet temperature used in generation (°C).

Load testing (scripts/load/locustfile.py):
- USER_PREFIX (default: load)
  - Username prefix used for generated test users.
- WAIT_MIN (default: 0.1)
  - Minimum wait time between tasks for Locust users (seconds).
- WAIT_MAX (default: 0.5)
  - Maximum wait time between tasks for Locust users (seconds).
- BUILDING_TYPES (default: "metal_mine,crystal_mine,deuterium_synthesizer,solar_plant,robot_factory,shipyard")
  - Comma-separated list of building types to randomize during load.

Notes:
- The Docker Compose setup sets ENABLE_DB=true and provides a PostgreSQL DATABASE_URL; for local dev without Docker, defaults to SQLite unless you set ENABLE_DB=true.
- Many values accept strings "true"/"false" (case-insensitive) for booleans.


## Client-side tip: retry/backoff after choose-start

After choosing a starter planet, the server hydrates the player from the database into the in-memory ECS asynchronously. This usually completes quickly but may take up to ~2 seconds in some environments (e.g., Docker). To avoid a race on the first read, add a short retry/backoff before the first GET /player/{id}.

Example (TypeScript/JS):

```ts
async function waitForPlayer(baseUrl: string, userId: number, token: string, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  let delay = 100;
  // Exponential backoff with cap
  while (Date.now() < deadline) {
    const r = await fetch(`${baseUrl}/player/${userId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (r.ok) return await r.json();
    await new Promise((res) => setTimeout(res, delay));
    delay = Math.min(500, Math.round(delay * 1.5));
  }
  throw new Error("Player not yet hydrated; try again shortly");
}
```

Note: The API also includes server-side guards that wait briefly (up to ~2s) to improve first-load success, but adding the above client-side retry makes flows robust across environments.


## Service Lifecycle

This backend follows a clear lifecycle to ensure deterministic startup, stable operation, and safe shutdown.

- Startup (autoload)
  - FastAPI lifespan initializes the database engines (when enabled) and ensures schema in development.
  - All players are autoloaded into the in-memory ECS world before the game loop starts. Autoload duration is recorded in metrics (timers.autoload.duration_s) and a counter is incremented (events.autoload.count).
  - Offline resource accrual is applied immediately after autoload so that resources reflect elapsed time prior to the first tick.

- Running (tick + periodic save)
  - A daemon thread runs the game loop at TICK_RATE Hz (default 1.0). Each tick processes queued commands and ECS systems.
  - Periodic persistence occurs roughly every SAVE_INTERVAL_SECONDS (default 60s). Saves are lightweight, protected by a lock to avoid overlap, and record metrics: events.save.count and timers.save.duration_s.
  - Additional per-planet persistence is throttled via PERSIST_INTERVAL_SECONDS to limit write frequency per planet (see src/core/config.py).
  - Tick duration and jitter are recorded to metrics for observability.

- Shutdown (final save + DB close)
  - On application shutdown, the game loop is stopped, a final save is attempted, and database engines are closed gracefully.

- Health and Metrics
  - GET /healthz includes: worldLoaded flag, lastSaveTs, tick metrics (last_tick_ms, jitter_last_ms), plus DB and memory info.
  - GET /metrics exposes a JSON snapshot of process/http/game_loop/event/timer metrics for scraping or inspection.
