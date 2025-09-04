"""Alembic environment configuration.

This lightweight env.py references SQLAlchemy metadata from src.models.database:Base
and supports both offline and online migrations. For async URLs, a sync driver is used
when required by Alembic (e.g., asyncpg -> postgresql).
"""
from __future__ import annotations

from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import pool, create_engine

from src.models.database import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# target_metadata = None

target_metadata = Base.metadata


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://ogame:ogame@localhost:5432/ogame")
    # Use sync driver for Alembic engine when using async URLs
    if url.startswith("postgresql+asyncpg"):
        url = url.replace("postgresql+asyncpg", "postgresql")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(get_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
