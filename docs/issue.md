### What I checked
I reviewed the server entry point, API initialization, the background game loop, and the database layer in these files:
- src/main.py
- src/api/routes.py (FastAPI app + lifespan startup/shutdown)
- src/core/state.py (global GameWorld singleton)
- src/core/game.py (game loop and Esper world)
- src/core/database.py (SQLAlchemy AsyncIO lifecycle)
- src/core/sync.py (DB persistence from game systems)
- src/api/auth.py (to verify DB-optional paths)

Below are concrete issues that can explain “server not working as it should,” and what to change.

### Probable root cause: Cross-event-loop DB usage from the game thread
- Where it happens:
  - src/api/routes.py starts the async database in the FastAPI lifespan (main server event loop):
    - await start_db() creates the AsyncEngine + SessionLocal bound to the server’s running event loop.
  - src/core/game.py starts the game loop in a separate thread (daemon thread).
  - src/core/sync.py is called by ECS systems inside the game loop thread to persist state. Its sync wrappers do:
    - asyncio.run(some_async_db_func(...)) when there is no running loop
    - else loop.create_task(...) on the current thread’s loop
- Why this is a problem:
  - The SQLAlchemy AsyncEngine you created in start_db() is bound to the FastAPI server’s event loop.
  - Calling asyncio.run in a different thread creates a brand-new, unrelated event loop and then tries to use the engine/session objects that belong to a different loop.
  - This typically throws errors like “attached to a different loop” or “Event loop is closed,” or you’ll see intermittent failures or silent no-ops depending on driver behavior.
- Symptoms you might be seeing:
  - Random 500s when game systems try to persist, especially after first ticks
  - DB writes seemingly not happening or happening sporadically
  - Logs indicating asyncpg/SQLAlchemy complaints about loop/thread affinity
- How to fix (choose one strategy and apply consistently):
  1) Route all DB work back to the server loop from the game thread:
     - Capture the server event loop during startup, e.g., in routes.lifespan you already do set_loop(loop) via src.api.ws. Do similarly for the DB loop (store it in a module-level variable, e.g., persistence_loop).
     - Replace asyncio.run(...) in src/core/sync.py with asyncio.run_coroutine_threadsafe(coro, persistence_loop) when called from the game thread. Don’t create a new event loop in that thread.
     - Delete the fallback that does loop.create_task(...) based on get_event_loop() from the game thread; that still isn’t the engine’s loop.
  2) Run a dedicated asyncio loop inside the game thread and initialize the DB engine in that loop:
     - Move start_db() out of FastAPI lifespan and call it from the game thread after creating and setting its own event loop (asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.run_until_complete(start_db())).
     - Then all DB ops in src/core/sync.py should target that same loop (either use asyncio.get_running_loop() in the game thread or run_coroutine_threadsafe when invoked from other threads).
  3) Alternative: Remove async from the game persistence path:
     - If you don’t need true async DB within the game loop, swap to a sync DB client or use SQLAlchemy’s sync engine from the game thread. This is larger refactor but removes the loop-bound complexity.

The minimal change with least surface area is option (1).

### DB is disabled by default unless ENABLE_DB=true and greenlet present
- In src/core/database.py::start_db():
  - It returns early unless os.environ["ENABLE_DB"] == "true" (lowercased). Default is “false”. So unless you export ENABLE_DB=true the DB is intentionally disabled.
  - It also disables DB if greenlet import fails, logging a warning.
- Consequences:
  - Endpoints using required sessions will raise “Database disabled” (e.g., get_async_session), while most routes are using optional sessions and quietly fall back to in-memory behaviors. That can look like “server works but persists nothing.”
  - Your health endpoints that check DB may always report DB unhealthy.
- Fix:
  - Set environment: ENABLE_DB=true, install greenlet and asyncpg, and point DATABASE_URL to a reachable database. If you need to test without DB, expect in-memory-only behavior.

### Repeated schema creation with init_db() on startup
- routes.lifespan calls await init_db() unconditionally after start_db(). init_db() runs Base.metadata.create_all().
- Risks:
  - In a production environment with Alembic migrations, this can conflict with managed schema/state and lead to unexpected DDL attempts.
  - If the DB user lacks DDL permissions, startup will warn or silently swallow exceptions due to your try/except; you then proceed with a partially initialized DB.
- Recommendation:
  - Make init_db() opt-in via an environment flag (e.g., DEV_CREATE_ALL=true) or remove it in favor of migrations only. Fail loudly on schema problems in non-dev.

### Global GameWorld is created at import time
- src/core/state.py creates game_world = GameWorld() at import time, and routes imports that global.
- Effects:
  - GameWorld.__init__ registers processors and attempts galaxy initialization by importing src.systems.planet_creation.initialize_galaxy() during import. If that call does any heavy work or expects the DB to be ready, it can fail or slow cold start.
  - You do start_db() later in lifespan, so any DB-related use inside GameWorld.__init__ would race with DB readiness.
- Recommendation:
  - Keep galaxy initialization purely in-memory; if any DB touch occurs there, defer it to lifespan after start_db(). Alternatively, lazily initialize the world at app startup instead of module import time.

### Game loop shutdown and saving twice
- Shutdown path:
  - routes.lifespan finally: game_world.stop_game_loop(); then game_world.save_player_data(); then await shutdown_db().
  - GameWorld.stop_game_loop() itself also tries a “final save” in a try/except.
- This is safe but redundant. If the save method does significant work, you could be doubling the cost and risk of conflicts. Keep one final save path.

### Using asyncio in sync wrappers with fragile detection
- In src/core/sync.py wrappers (e.g., sync_planet_resources):
  - Calls asyncio.run(...), except RuntimeError then schedule onto loop.create_task(...).
- In a multi-threaded app, “is there a running event loop” is not the right signal for “is it the correct loop that owns my engine.” This contributes to the cross-loop bug above. Even when there is a loop, it might not be the engine’s loop.
- Apply the fix outlined in the first section and remove reliance on implicit get_event_loop() in the game thread.

### DB session factories and loop affinity
- SessionLocal and replica sessionmakers are created in start_db() inside the server’s event loop. Those objects should only be used from that same loop. If you need to use them off-thread, always target the owning loop via run_coroutine_threadsafe.

### Environment defaults that can surprise
- TICK_RATE defaults to 1.0; that’s fine, but SAVE_INTERVAL_SECONDS defaults to 60, and PERSIST_INTERVAL_SECONDS matches it. If you’re expecting faster persistence for tests/demo, things can appear “stuck” for up to a minute unless a manual save occurs.
- CLEANUP jobs run daily when the day changes; that’s fine but note it runs from the game loop thread and will attempt DB access too—another place where the loop-affinity bug will bite.

### Alembic vs model metadata
- You have migrations/ and Base models; but the code path currently prefers create_all in init_db(). If your alembic heads and models diverge (e.g., renamed columns), you can get runtime SQL errors, missing tables/constraints, or weird reads. Prefer one source of truth and align.

### Quick checklist to get the server into a healthy state
1) Environment
   - ENABLE_DB=true
   - DATABASE_URL set to a reachable DB (e.g., postgresql+asyncpg://user:pass@host:5432/db)
   - Install greenlet and asyncpg
2) Event loop correctness for DB operations from the game thread
   - Capture the server loop on startup and call DB coroutines with asyncio.run_coroutine_threadsafe(coro, server_loop). Do not use asyncio.run in the game thread.
   - Alternatively, run the DB loop in the game thread and start the DB there, then ensure all persistence uses that loop.
3) Startup ordering
   - Avoid any DB work inside GameWorld.__init__ or galaxy initialization. If necessary, move those to lifespan after start_db().
4) Schema management
   - In non-dev environments, remove/guard init_db(); rely on Alembic migrations instead.
5) Logging/visibility
   - Temporarily raise logging around src/core/sync.py and src/core/database.py to DEBUG to catch loop/engine errors.

## Resolved Notes (2025-09-03)
- Verified startup/shutdown lifecycle documented in docs/lifecycle.md.
- ENABLE_DB=false mode confirmed: endpoints fall back to in-memory where possible; DB-only endpoints return clear errors.
- Structured DEBUG logs added for DB start/shutdown and session acquisition with loop/thread context.
- CI workflow added to run pytest in in-memory mode by default.

### TL;DR
- The most critical bug is using the async SQLAlchemy engine (created on the FastAPI server loop) from the background game thread via asyncio.run, which creates a different event loop. This causes cross-loop usage and breaks DB features intermittently or entirely. Route DB coroutines back to the owning loop with run_coroutine_threadsafe, or run the DB loop in the game thread and initialize the engine there. Also ensure ENABLE_DB=true and required packages are installed; guard init_db() behind a dev flag or rely on migrations.
