# Developer Guide: Adding New Persistence Operations

This guide explains where to place database (DB) code, how to avoid cross-loop issues with the game loop thread, and how to test persistence safely.

## Where persistence code lives
- Use `src/core/sync.py` for DB-centric helpers that bridge ECS components and SQLAlchemy ORM.
- Keep ECS systems in `src/systems/` pure (no direct DB calls). Systems mutate in-memory components; the loop triggers persistence periodically.
- Database models are defined in `src/models/database.py`. Avoid importing heavy modules at the top level of systems.

## Session management (single-session pattern)
- Acquire exactly one session per logical operation using `async with SessionLocal() as session:`.
- Use the helper `await _ensure_user_and_planet_in_session(session, ...)` to create or fetch users/planets bound to the same session.
- Avoid nested session usage and redundant re-fetches. With the in-session helper, the returned ORM instances are already bound and safe to modify directly.

## Calling from the game loop (sync wrappers)
- Persistence helpers are implemented as async functions and exposed via synchronous wrappers in src/core/sync.py.
- The FastAPI server loop is captured at startup and stored via set_persistence_loop(loop).
- Synchronous wrappers submit coroutines to the owning loop using asyncio.run_coroutine_threadsafe(coro, persistence_loop) from the game loop thread. They never create tasks on the wrong loop.
- Prefer direct await inside FastAPI request handlers. Off-thread (game loop) callers must go through the sync wrappers which schedule onto the captured loop.

## Throttling & timing
- Writes are throttled per planet via `PERSIST_INTERVAL_SECONDS` (see `src/core/config.py`).
- `GameWorld.save_player_data()` invokes persistence periodically based on `SAVE_INTERVAL_SECONDS` and records metrics.

## Error handling & logging
- Treat persistence as best-effort; never let exceptions crash the game loop.
- Use `logger.debug` for transient DB issues during periodic syncs; keep `logger.warning` for genuine failures where user action is blocked or data integrity is at risk.
- Record metrics for save counts and durations via `src/core/metrics.py`.

## Alembic workflow
- Ensure your database URL is set in DATABASE_URL; use a sync driver for Alembic commands (postgresql://...). If using asyncpg, Alembic env maps it to sync automatically.
- Inspect heads: alembic heads
- Generate a new revision when models change: alembic revision --autogenerate -m "<message>"
- Review the generated migration for accuracy; do not rely blindly on autogenerate.
- Upgrade to head locally: alembic upgrade head
- If divergences arise, prefer creating a corrective migration rather than editing past revisions.
- In dev, you may set DEV_CREATE_ALL=true to create tables without migrations; production relies solely on Alembic.

## Testing patterns
- Use `pytest` with `fastapi.testclient.TestClient` to exercise startup/shutdown and loop-related behavior.
- For concurrency, simulate overlapping calls using `threading.Thread` while invoking `game_world.save_player_data()` to validate lock usage (see `tests/test_persistence_concurrency.py`).
- For unit tests that don’t require the DB, test systems and calculations in isolation.
- If DB interactions are needed, prefer short-lived clients to trigger app lifespan events and autoload hooks.

## Common pitfalls
- Do not perform long-running DB operations inside the tick critical section. Batch or schedule them post-tick. As of 2025-09-03, periodic saves are triggered from the loop via a lightweight background thread to avoid blocking tick cadence.
- Ensure autoload applies offline accrual before the first tick so resources reflect downtime.
- Avoid global loop capture; rely on `asyncio.run` in non-async threads and direct `await` in async contexts.

## Design decision: asyncio DB access vs small thread pool (2025-09-03)
- Decision: Keep `asyncio.run(...)` in the game loop thread for DB-bound helpers instead of introducing a dedicated thread pool.
- Rationale:
  - Current async SQLAlchemy usage integrates cleanly with `asyncio.run` from a non-async loop thread and direct `await` in FastAPI handlers; minimal moving parts.
  - Locks and throttling ensure we do not flood the DB; profiling indicates no immediate need for a pooled executor.
  - Where latency could affect tick timing, we trigger work (e.g., periodic save) in a small background thread, preserving tick cadence without complicating DB access paths.
- Future revisit: If DB latency under load increases, consider a tiny thread pool for persistence batching, but keep the API the same to enable drop-in replacement.

## Example skeleton
```python
# In src/core/sync.py
async def upsert_something_by_entity(world, ent):
    if not _db_available():
        return
    # Extract components
    from src.models import Player, Position, Planet as PlanetComp
    player = world.component_for_entity(ent, Player)
    pos = world.component_for_entity(ent, Position)
    pmeta = world.component_for_entity(ent, PlanetComp)

    try:
        async with SessionLocal() as session:
            planet = await _ensure_user_and_planet_in_session(
                session, player.user_id, player.name, pos.galaxy, pos.system, pos.planet, pmeta.name
            )
            if planet is None:
                return
            # perform ORM updates bound to `session`
            await session.commit()
    except Exception:
        # log at debug if transient; warning if genuine failure
        logger.debug("upsert_something failed (transient)")
```
