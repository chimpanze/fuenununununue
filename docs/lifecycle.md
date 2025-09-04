# Application Lifecycle and Event Loop Ownership

This document explains the order of operations during application startup and shutdown, the ownership of the asyncio event loop, and where/when persistence occurs. It complements docs/plan.md and reflects the current code in src/.

## Startup Ordering

1. FastAPI app is created with a lifespan context (src/api/routes.py). ✓
2. On lifespan entry (startup): ✓
   - The FastAPI server’s running asyncio loop is captured via asyncio.get_running_loop(). ✓
   - The database is initialized within that loop by calling src.core.database.start_db(). If ENABLE_DB!=true or greenlet is unavailable, the DB layer stays disabled. ✓
   - If DEV_CREATE_ALL=true, metadata.create_all() is executed via src.core.database.init_db(); otherwise, rely on Alembic migrations. ✓
   - If DB is disabled, in-memory auth state is reset for deterministic tests. ✓
   - The captured loop is propagated to:
     - WebSocket bridge (src/api/ws.py:set_loop)
     - Persistence layer (src/core/sync.py:set_persistence_loop) for safe cross-thread scheduling. ✓
   - All players are autoloaded from DB when enabled; otherwise ECS fallbacks are used. Offline resource accrual is applied immediately. ✓
   - A startup log summarizes key configuration (ENABLE_DB, DEV_CREATE_ALL, tick and persist intervals). ✓
   - Finally, the GameWorld background loop is started in a daemon thread at TICK_RATE Hz. ✓

## Event Loop Ownership and Cross-Thread Calls

- The single FastAPI event loop (captured at startup) owns:
  - SQLAlchemy AsyncEngine and async_sessionmaker instances created in start_db().
  - All async database operations.
- Synchronous game systems run on a background thread (Esper tick). These must NOT call async DB directly. Instead, they delegate via:
  - asyncio.run_coroutine_threadsafe(coro, persistence_loop) where persistence_loop is the captured server loop set in src/core/sync.set_persistence_loop().
- Never create new loops for DB work. Do not call loop.create_task from the game thread; always target the owning loop.

## Persistence Paths

- Writes (planet resources, buildings, fleets, queues, reports): scheduled from sync wrappers using run_coroutine_threadsafe and executed on the server loop.
- Reads for endpoints use FastAPI dependencies:
  - get_async_session(): strict — raises if DB disabled.
  - get_optional_async_session()/get_optional_readonly_async_session(): return None when DB disabled to enable graceful in-memory fallbacks.
- When ENABLE_DB=false, endpoints and systems either:
  - Use in-memory ECS data only, or
  - Return a clear 404/400/503 when persistence is mandatory (e.g., deleting a DB-backed notification).

## Shutdown Ordering

1. Lifespan exit (shutdown) begins. ✓
2. Active WebSocket connections are closed. ✓
3. GameWorld loop is stopped (final save occurs in one place to avoid double-save). ✓
4. Database engines are disposed within the running server loop via src.core.database.shutdown_db() to avoid cross-loop termination errors. ✓

## Configuration Summary

- ENABLE_DB: master toggle; when false, DB code paths are disabled and ECS-only mode is used.
- DEV_CREATE_ALL: if true, ensures schema via metadata.create_all() on startup. Otherwise use Alembic.
- TICK_RATE, SAVE_INTERVAL_SECONDS, PERSIST_INTERVAL_SECONDS: control game loop and persistence cadence. Tests can override via environment variables.

See docs/developer_persistence_guide.md for the persistence workflow and Alembic operations.
