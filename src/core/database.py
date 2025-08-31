"""Async database engine and session management.

Provides an async SQLAlchemy engine, session factory, and simple helpers for
initializing the schema (for dev) and checking connectivity. This module does
not run on import; call its functions explicitly from startup hooks if needed.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from src.core.config import DATABASE_URL, READ_REPLICA_URLS
from src.models.database import Base

logger = logging.getLogger(__name__)

# Async engine/session globals; initialize on app startup to bind to the running loop
engine = None  # type: Optional[object]
SessionLocal = None  # will be set to async_sessionmaker when started
_DB_ENABLED = False

# Detect greenlet availability; SQLAlchemy relies on it in several execution paths
try:  # pragma: no cover - environment dependent
    import greenlet  # type: ignore
    _GREENLET_OK = True
except Exception:  # pragma: no cover - tests may not have greenlet installed
    _GREENLET_OK = False


def is_db_enabled() -> bool:
    """Return True if the async DB is usable in this process."""
    return bool(_DB_ENABLED and engine is not None and SessionLocal is not None)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency-style session generator."""
    if not is_db_enabled():
        raise RuntimeError("Database disabled")
    async with SessionLocal() as session:  # type: ignore[misc]
        yield session


async def get_optional_async_session() -> AsyncGenerator[Optional[AsyncSession], None]:
    """Dependency that yields an AsyncSession if DB is enabled, otherwise None.

    This allows endpoints to fall back to in-memory flows when the DB is disabled.
    """
    if not is_db_enabled():
        yield None
        return
    async with SessionLocal() as session:  # type: ignore[misc]
        yield session


# --- Read replica support for GET operations ---
# Build optional read-replica engines and session factories. Fallback to primary if none available.
_replica_engines = []  # type: list
_replica_sessionmakers = []  # type: list
_replicas_enabled = False
_replica_rr_index = 0


def _engine_kwargs_for(url: str) -> dict:
    """Construct engine kwargs appropriate for a given database URL."""
    from src.core.config import (
        DB_ECHO,
        DB_POOL_PRE_PING,
        DB_POOL_SIZE,
        DB_MAX_OVERFLOW,
        DB_POOL_TIMEOUT,
        DB_POOL_RECYCLE,
    )
    engine_kwargs = {
        "echo": DB_ECHO,
        "future": True,
        "pool_pre_ping": DB_POOL_PRE_PING,
    }
    try:
        from sqlalchemy.pool import NullPool  # type: ignore
    except Exception:  # pragma: no cover
        NullPool = None  # type: ignore
    is_sqlite = str(url).startswith("sqlite+")
    if is_sqlite and NullPool is not None:
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs.update({
            "pool_size": DB_POOL_SIZE,
            "max_overflow": DB_MAX_OVERFLOW,
            "pool_timeout": DB_POOL_TIMEOUT,
            "pool_recycle": DB_POOL_RECYCLE,
        })
    return engine_kwargs


# Read-replica engines/sessionmakers are initialized in start_db() to ensure they
# are bound to the running event loop.


def _choose_read_sessionmaker():
    global _replica_rr_index
    if _replicas_enabled and _replica_sessionmakers:
        sm = _replica_sessionmakers[_replica_rr_index % len(_replica_sessionmakers)]
        _replica_rr_index = (_replica_rr_index + 1) % (len(_replica_sessionmakers) or 1)
        return sm
    return SessionLocal  # fallback to primary


async def get_readonly_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a session bound to a read-replica when available.

    Falls back to the primary session factory when replicas are not configured.
    """
    if not is_db_enabled():
        raise RuntimeError("Database disabled")
    sessionmaker = _choose_read_sessionmaker()
    async with sessionmaker() as session:  # type: ignore[misc]
        yield session


async def get_optional_readonly_async_session() -> AsyncGenerator[Optional[AsyncSession], None]:
    """Optional variant of get_readonly_async_session that yields None when DB disabled."""
    if not is_db_enabled():
        yield None
        return
    sessionmaker = _choose_read_sessionmaker()
    async with sessionmaker() as session:  # type: ignore[misc]
        yield session


async def init_db() -> None:
    """Initialize database schema in dev environments using metadata.create_all.

    For production, prefer Alembic migrations instead of create_all.
    """
    if not is_db_enabled():
        logger.warning("init_db called but database is disabled")
        return
    async with engine.begin() as conn:  # type: ignore[assignment]
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema ensured via metadata.create_all")


async def check_database() -> bool:
    """Perform a simple health check against the database connection."""
    if not is_db_enabled():
        return False
    try:
        async with engine.connect() as conn:  # type: ignore[assignment]
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("DB health check failed: %s", exc)
        return False


async def start_db() -> None:
    """Initialize async engines/sessionmakers within the current event loop.

    Safe to call multiple times; a no-op if already started.
    """
    global engine, SessionLocal, _DB_ENABLED, _replica_engines, _replica_sessionmakers, _replicas_enabled
    if not _GREENLET_OK:
        logger.warning("greenlet not available; database layer will remain disabled")
        _DB_ENABLED = False
        return
    if engine is not None and SessionLocal is not None:
        _DB_ENABLED = True
        return
    # Create primary engine and sessionmaker
    kwargs = _engine_kwargs_for(DATABASE_URL)
    engine = create_async_engine(DATABASE_URL, **kwargs)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _DB_ENABLED = True
    # Initialize read-replicas if configured
    _replica_engines = []
    _replica_sessionmakers = []
    _replicas_enabled = False
    if READ_REPLICA_URLS:
        for url in READ_REPLICA_URLS:
            try:
                eng = create_async_engine(url, **_engine_kwargs_for(url))
                _replica_engines.append(eng)
                _replica_sessionmakers.append(async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False))
            except Exception as rex:
                logger.warning("Failed to init read-replica engine for %s: %s", url, rex)
        _replicas_enabled = bool(_replica_sessionmakers)


async def shutdown_db() -> None:
    """Dispose primary and replica async engines within the running event loop.

    This ensures connections are closed on the correct asyncio loop, avoiding
    asyncpg cross-loop termination errors during application shutdown.
    """
    global engine, SessionLocal, _DB_ENABLED, _replica_engines, _replica_sessionmakers, _replicas_enabled
    try:
        # Dispose primary engine
        if engine is not None:
            try:
                await engine.dispose()  # type: ignore[assignment]
            except Exception as exc:
                logger.warning("Error disposing primary DB engine: %s", exc)
        # Dispose replica engines, if any
        try:
            for eng in list(_replica_engines):
                try:
                    await eng.dispose()
                except Exception as rex:
                    logger.warning("Error disposing replica engine: %s", rex)
        except Exception:
            pass
    except Exception:
        # Never break shutdown due to DB cleanup issues
        pass
    finally:
        engine = None
        SessionLocal = None
        _DB_ENABLED = False
        _replica_engines = []
        _replica_sessionmakers = []
        _replicas_enabled = False
