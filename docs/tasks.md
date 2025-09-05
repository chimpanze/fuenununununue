# Actionable Improvement Tasks Checklist

Below is an enumerated checklist derived from docs/issue.md. Each item is actionable, ordered for safe implementation, and covers both architectural and code-level improvements.

1. [x] Confirm and document environment variables and dependencies required to enable DB: set ENABLE_DB=true, set DATABASE_URL, and install greenlet + asyncpg (README and docs/requirements.md).
2. [x] Introduce a single source of truth for runtime config: ensure src/core/config.py defines ENABLE_DB, DEV_CREATE_ALL, TICK_RATE, SAVE_INTERVAL_SECONDS, PERSIST_INTERVAL_SECONDS, and expose them via typed getters with sane defaults.
3. [x] Capture the FastAPI server’s running event loop at startup and store it in a central module (e.g., src/core/state.py or src/core/sync.py) as persistence_loop for DB operations.
4. [x] Replace asyncio.run(...) calls in src/core/sync.py with asyncio.run_coroutine_threadsafe(coro, persistence_loop) for all DB persistence executed from the game loop thread.
5. [x] Remove/avoid loop.create_task(...) calls from the game thread in src/core/sync.py; ensure all off-thread DB calls target the owning loop via run_coroutine_threadsafe.
6. [x] Verify that SQLAlchemy AsyncEngine/session factories are only created inside the owning loop (start_db) and are not accessed directly from other threads; update helper functions accordingly.
7. [x] Add a guard in src/core/database.py to raise a clear error when DB usage is attempted while DB is disabled (ENABLE_DB!=true) and a hard requirement path is invoked.
8. [x] Make init_db() optional: add DEV_CREATE_ALL config flag; run Base.metadata.create_all() only when DEV_CREATE_ALL=true, otherwise rely solely on Alembic migrations.
9. [x] Remove the unconditional init_db() call from FastAPI lifespan (or guard it behind DEV_CREATE_ALL) to prevent unintended schema DDL in non-dev environments.
10. [x] Ensure GameWorld and galaxy initialization do not perform DB I/O: audit src/core/game.py, src/core/state.py, and src/systems/planet_creation.py to keep startup in-memory only.
11. [x] If any DB touches occur during GameWorld.__init__ or galaxy init, move them to an explicit post-startup hook executed after start_db() in the FastAPI lifespan.
12. [x] Consolidate final save on shutdown: remove the redundant double-save by choosing either GameWorld.stop_game_loop() final save or routes.lifespan explicit save, not both.
13. [x] Increase observability during persistence: add DEBUG-level logs around src/core/sync.py and src/core/database.py to log thread name, loop identity, and target entity IDs for each DB operation.
14. [x] Add a one-time startup log summarizing configuration (ENABLE_DB, DEV_CREATE_ALL, tick rate, persist intervals) to avoid surprises in different environments.
15. [x] Shorten persistence intervals for tests: allow tests to override PERSIST_INTERVAL_SECONDS/SAVE_INTERVAL_SECONDS via env vars or test config to accelerate feedback.
16. [x] Audit cleanup/cron-like jobs (daily cleanup triggered from game loop) to ensure they also route DB work through persistence_loop and do not spawn their own loops.
17. [x] Align Alembic heads with model metadata: run alembic heads/upgrade in local dev, fix any divergences between models and migrations, and document the workflow in docs/developer_persistence_guide.md.
18. [x] Add unit tests for sync wrappers to assert they call run_coroutine_threadsafe with the captured loop when invoked from a non-async thread.
19. [x] Add integration tests using FastAPI TestClient to validate: server starts, game loop ticks, and persistence operations succeed without cross-loop errors.
20. [x] Add a regression test simulating the prior bug: attempt persistence from the game thread without the loop set and assert a clear error or handled path.
21. [x] Provide a feature toggle or noop path for full in-memory mode: when ENABLE_DB=false, ensure endpoints and systems either degrade gracefully or raise explicit, documented errors where persistence is mandatory.
22. [x] Document startup/shutdown ordering and lifecycle in docs/plan.md (or a new docs/lifecycle.md): include event loop ownership, when DB starts, when game loop starts/stops, and where persistence occurs.
23. [x] Update README.md with quick-start instructions for both modes: in-memory only and full DB-backed mode, including env examples and docker-compose usage.
24. [x] Validate that SessionLocal / replica sessionmakers are not leaked across threads; add comments and type hints clarifying thread/loop affinity expectations.
25. [x] Ensure src/api/routes.py lifespan captures and stores the server loop at startup (e.g., loop = asyncio.get_running_loop()) and exposes it to sync.py via a setter.
26. [x] Review websocket/event broadcasting (src/api/ws.py if applicable) for any similar cross-loop assumptions; route async calls via the server loop when invoked off-thread.
27. [x] Add structured logging (logger.bind-like context or key/value pairs) for critical persistence operations to ease tracing in multi-threaded runs.
28. [x] Revisit default intervals: consider reducing default SAVE_INTERVAL_SECONDS/PERSIST_INTERVAL_SECONDS in dev/test to avoid “nothing happens for up to a minute” confusion; keep production defaults conservative.
29. [x] Add CI checks (pytest -q) to run unit tests and a minimal API smoke test to guard against regressions in event loop and DB initialization flow.
30. [x] Perform a manual end-to-end verification: start the server, enable DB, dispatch a few game actions, observe that persistence happens reliably without loop errors, and record observations in docs/issue.md as “Resolved Notes.”

---

## ECS Gameplay Balance & Systems Tasks

The following actionable, prioritized tasks focus on gameplay systems: resource generation, energy production/consumption, ship building, research, fleet/battle, and per-planet building ownership. They reference current code under src/systems, src/core, and src/models to ground implementation.

1) Resource Production and Energy Balance
- [x] Define and document production formulas in docs/requirements.md and align with code in src/systems/resource_production.py and GameWorld._apply_offline_resource_accrual. Include examples and edge cases (zero/negative energy). 
- [x] Parameterize energy curve: add non-linear scaling for solar_plant (e.g., ENERGY_SOLAR_BASE * 1.1^level) and per-mine consumption growth (ENERGY_CONSUMPTION growth per level) via src/core/config.py; keep defaults backward compatible.
- [x] Introduce building-level base rates in config for metal/crystal/deuterium and reduce reliance on static ResourceProduction.metal_rate, etc. Provide per-planet modifiers using Planet.temperature/size.
- [x] Add storage capacity buildings (e.g., metal_storage, crystal_storage, deuterium_tank) to src/models.Buildings and cap accrual above capacity; expose base capacities and per-level growth in config.
- [x] Energy deficit behavior: today factor = min(1, produced/required). Add a soft floor (e.g., 25%) and warnings; surface this via notifications and /game-status.
- [x] Plasma/Energy research balance pass: tune PLASMA_PRODUCTION_BONUS and ENERGY_TECH_ENERGY_BONUS_PER_LEVEL; add tests to keep hourly outputs within target ranges for early levels.
- [x] Ensure research effects are applied in both online ticks and offline accrual paths consistently (already mirrored; add tests).

2) Building Construction (Per Planet) and Build Times
- [x] Enforce per-planet building ownership end-to-end: verify API routes mutate the active planet only and persistence keys include planet_id; add tests for multi-planet users.
- [x] Introduce additional utility buildings with clear purpose (robot_factory reduces build time, research_lab unlocks research speed, metal/crystal storage, fusion_reactor producing energy with deuterium consumption). Extend src/models.Buildings, config costs/times, and prerequisites.
- [x] Wire robot_factory and research_lab effects into build and research time calculations using BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL and new per-building bonuses in config.
- [x] Add demolition/refund rules and a demolish queue path in GameWorld (parse_demolish_building already exists; ensure system handles it or design a separate flow).
- [x] Create balancing tests: time to level 5 mines under starter rates, time to first ship, ensuring no single building is dominant.

3) Shipyard and Fleet Limits
- [x] Review BASE_SHIP_COSTS/TIMES in config for early-game pacing; raise colony_ship baseline to match colonization expectations.
- [x] Add shipyard level scaling to build speed (e.g., -5% per level) and robot_factory contribution; reflect in GameWorld._handle_build_ships time calc.
- [x] Enforce fleet size caps using BASE_MAX_FLEET_SIZE and FLEET_SIZE_PER_COMPUTER_LEVEL before enqueueing ship builds; include per-planet check and a test.
- [x] Add shipyard queue size limit per shipyard level; return validation errors via API and tests.
- [x] Extend ShipyardSystem to send WS events per completion, and include batched completions within a tick in one message.

4) Research Tree and Effects
- [x] Expand RESEARCH_PREREQUISITES in config (e.g., ion requires laser 4; hyperspace requires energy 6 + laser 6; plasma requires energy 8 + ion 5). Add tests asserting prerequisite enforcement in GameWorld._handle_start_research.
- [x] Add research lab building influencing research time; include per-planet labs, aggregate one active planet’s lab per research (decide: local or global research? Keep research as player-wide but labs affect time based on active planet).
- [x] Balance SHIP_STAT_BONUSES to avoid runaway scaling; add tests that derived ship stats remain within sane ranges at levels 0–10.

5) Planet Generation, Environment Modifiers, and Per‑Planet Identity
- [x] Use Planet.temperature to affect deuterium production efficiency; map a temperature->multiplier curve in config.
- [x] Use Planet.size to affect max buildable levels or capacity efficiency; define caps or soft penalties beyond size thresholds.
- [x] Ensure set_active_planet_by_id and colonization flows always attach distinct Buildings/Queues per planet and never leak across planets; add regression tests for switching planets and building queues.

6) Economy and Market Interactions
- [x] Define target exchange ratios (metal:crystal:deuterium) and add soft guidance to market UI/API; add tests to ensure trading cannot trivially bypass energy/production constraints.
- [x] Add transaction taxes or fees configurable in config to stabilize economy; test persistence and rounding.

7) Observability and Telemetry for Gameplay
- [x] Add metrics: production per resource per hour, energy deficit rate, queue wait times, average research/ship build durations; export via src/core/metrics and log at DEBUG.
- [x] Add in-game notifications for energy deficit, full storage, queue complete, and research complete (WS + offline notifications), with rate limits.

8) Testing Matrix and Invariants
- [x] Create tests/test_production_balance.py with scenarios for early/mid game outputs and energy behaviors.
- [x] Add tests for per-planet building isolation: two planets under same player do not share buildings/queues/resources.
- [x] Add tests for shipyard and research prerequisite enforcement and queue size limits.
- [x] Add regression tests for offline accrual consistency matching online tick calculations.

9) Migration and Backward Compatibility
- [x] When adding Buildings fields (storage, lab, fusion_reactor), add DB migration and hydration paths in src/core/sync to persist/load new fields.
- [x] Provide config feature flags to enable/disable new buildings for phased rollout in tests.

Ownership notes:
- Primary files impacted: src/systems/resource_production.py, src/systems/building_construction.py, src/systems/shipyard.py, src/systems/research.py, src/core/game.py (time/cost calcs and command handlers), src/core/config.py (balance knobs), src/models/components.py (new buildings/components).
- Ensure every change maintains per-planet building ownership; research remains player-scoped unless explicitly changed.
