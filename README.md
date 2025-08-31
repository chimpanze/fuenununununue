# Ogame-like Game Server

A FastAPI-based OGame-like backend featuring an ECS-style game loop powered by `esper`. The app exposes a REST/WS API for gameplay actions and runs a background tick loop to process game systems (resources, building, research, fleets, battles, trade, notifications).

- ASGI entrypoint: `src.main:app`
- Tick loop: configurable via `TICK_RATE` (default 1 Hz)
- Database: async SQLAlchemy with optional read-replica support (see Configuration)

## Quickstart

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

1) Local (SQLite default):
- By default DATABASE_URL points to `sqlite+aiosqlite:///./dev.db`. Start the server and the schema will be created automatically in dev paths.

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

## Notes & Tips
- Esper world initialization uses `esper.World()` (already handled). If you see AttributeError, verify dependency versions.
- The game loop is a daemon thread; ensure clean shutdowns in your env (handled by FastAPI lifespan in src/api/routes.py).
- For production, tighten CORS and secrets; prefer Postgres and Alembic migrations.
