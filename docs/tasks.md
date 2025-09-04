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
