"""Centralized configuration for the Ogame-like server.

Follows docs/plan.md guidance to keep tick rate and other constants out of the app entrypoint.
This module is intentionally minimal to satisfy early roadmap tasks without
introducing environment dependencies.
"""
from __future__ import annotations

import os
from typing import List, Optional

# Tick rate for the background game loop (ticks per second)
TICK_RATE: float = float(os.environ.get("TICK_RATE", "1.0"))

# Database configuration
# Use async driver by default for SQLAlchemy 2.0 AsyncIO (aiosqlite for dev)
# Example for Postgres: postgresql+asyncpg://user:pass@localhost:5432/dbname
DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
# Optional read replicas: comma-separated URLs, e.g., "postgresql+asyncpg://ro1,..."
# If empty, reads fall back to primary automatically.
READ_REPLICA_URLS: List[str] = [u.strip() for u in os.environ.get("READ_REPLICA_URLS", "").split(",") if u.strip()]
# Async SQLAlchemy engine/pool settings
DB_ECHO: bool = os.environ.get("DB_ECHO", "false").lower() == "true"
DB_POOL_PRE_PING: bool = os.environ.get("DB_POOL_PRE_PING", "true").lower() == "true"
DB_POOL_SIZE: int = int(os.environ.get("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW: int = int(os.environ.get("DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT: int = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE: int = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

# Auth / Security configuration
JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24h
RATE_LIMIT_PER_MINUTE: int = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "100"))

# CORS configuration
CORS_ALLOW_ORIGINS: List[str] = [orig.strip() for orig in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")]
CORS_ALLOW_CREDENTIALS: bool = os.environ.get("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"
CORS_ALLOW_METHODS: List[str] = [m.strip() for m in os.environ.get("CORS_ALLOW_METHODS", "*").split(",")]
CORS_ALLOW_HEADERS: List[str] = [h.strip() for h in os.environ.get("CORS_ALLOW_HEADERS", "*").split(",")]

# Base building costs (kept here to allow future tuning/testing)
BASE_BUILDING_COSTS = {
    'metal_mine': {'metal': 60, 'crystal': 15, 'deuterium': 0},
    'crystal_mine': {'metal': 48, 'crystal': 24, 'deuterium': 0},
    'deuterium_synthesizer': {'metal': 225, 'crystal': 75, 'deuterium': 0},
    'solar_plant': {'metal': 75, 'crystal': 30, 'deuterium': 0},
    'robot_factory': {'metal': 400, 'crystal': 120, 'deuterium': 200},
    'shipyard': {'metal': 400, 'crystal': 200, 'deuterium': 100},
}

# Base build times in seconds
BASE_BUILD_TIMES = {
    'metal_mine': 60,
    'crystal_mine': 80,
    'deuterium_synthesizer': 100,
    'solar_plant': 50,
    'robot_factory': 300,
    'shipyard': 400,
}

# Building prerequisites map: building -> {required_building: min_level}
PREREQUISITES = {
    # Example: shipyard requires robot factory level 2
    'shipyard': {'robot_factory': 2},
}

# Energy system parameters
# Base energy produced by solar plant per level (approximate)
ENERGY_SOLAR_BASE: float = 20.0
# Base energy consumption per building per level
ENERGY_CONSUMPTION = {
    'metal_mine': 3.0,
    'crystal_mine': 2.0,
    'deuterium_synthesizer': 2.0,
}


# Research base costs (per level 0 -> level 1 baseline)
BASE_RESEARCH_COSTS = {
    'energy': {'metal': 100, 'crystal': 50, 'deuterium': 0},
    'laser': {'metal': 200, 'crystal': 100, 'deuterium': 0},
    'ion': {'metal': 1000, 'crystal': 300, 'deuterium': 100},
    'hyperspace': {'metal': 2000, 'crystal': 1500, 'deuterium': 500},
    'plasma': {'metal': 4000, 'crystal': 2000, 'deuterium': 1000},
    'computer': {'metal': 500, 'crystal': 250, 'deuterium': 0},
}

# Research base times in seconds (baseline for level 1)
BASE_RESEARCH_TIMES = {
    'energy': 120,
    'laser': 180,
    'ion': 300,
    'hyperspace': 600,
    'plasma': 900,
    'computer': 240,
}

# Research prerequisites map: research -> {required_research: min_level}
# Example: plasma technology requires energy technology level 8
RESEARCH_PREREQUISITES = {
    'plasma': {'energy': 8},
}

# Research effects configuration
# Production bonuses from plasma technology per level (fractions, e.g., 0.01 = +1%)
PLASMA_PRODUCTION_BONUS = {
    'metal': 0.01,
    'crystal': 0.006,
    'deuterium': 0.02,
}
# Energy technology increases total produced energy per level (fraction)
ENERGY_TECH_ENERGY_BONUS_PER_LEVEL: float = 0.02
# Hyperspace technology reduces building construction times (fraction per level)
BUILD_TIME_REDUCTION_PER_HYPERSPACE_LEVEL: float = 0.02
# Minimum clamp factor for build time after reductions (e.g., 0.5 = cannot go below 50% of base)
MIN_BUILD_TIME_FACTOR: float = 0.5

# Base ship stats used to derive final stats with research modifiers
BASE_SHIP_STATS = {
    'light_fighter': {'attack': 50, 'shield': 10, 'speed': 12500, 'cargo': 50},
    'heavy_fighter': {'attack': 150, 'shield': 25, 'speed': 10000, 'cargo': 100},
    'cruiser': {'attack': 400, 'shield': 50, 'speed': 15000, 'cargo': 800},
    'battleship': {'attack': 1000, 'shield': 200, 'speed': 10000, 'cargo': 1500},
    'bomber': {'attack': 500, 'shield': 500, 'speed': 5000, 'cargo': 500},
}
# Research multipliers for ship stats (fractions per level)
SHIP_STAT_BONUSES = {
    'laser_attack_per_level': 0.01,      # +1% attack per laser level
    'ion_shield_per_level': 0.01,        # +1% shield per ion level
    'hyperspace_speed_per_level': 0.02,  # +2% speed per hyperspace level
    'hyperspace_cargo_per_level': 0.02,  # +2% cargo per hyperspace level
    'plasma_attack_per_level': 0.005,    # +0.5% attack per plasma level
}

# Base ship build costs (per unit)
BASE_SHIP_COSTS = {
    'light_fighter': {'metal': 300, 'crystal': 150, 'deuterium': 0},
    'heavy_fighter': {'metal': 600, 'crystal': 400, 'deuterium': 0},
    'cruiser': {'metal': 2000, 'crystal': 1500, 'deuterium': 200},
    'battleship': {'metal': 6000, 'crystal': 4000, 'deuterium': 0},
    'bomber': {'metal': 5000, 'crystal': 3000, 'deuterium': 1000},
    'colony_ship': {'metal': 300, 'crystal': 150, 'deuterium': 0},
}

# Base ship build times in seconds (per unit)
BASE_SHIP_TIMES = {
    'light_fighter': 60,
    'heavy_fighter': 120,
    'cruiser': 300,
    'battleship': 600,
    'bomber': 900,
    'colony_ship': 1,
}

# Colonization settings
# Additional time required after arrival to complete colonization (seconds)
COLONIZATION_TIME_SECONDS: int = int(os.environ.get("COLONIZATION_TIME_SECONDS", "1"))

# Fleet size limits based on Computer Technology
# Max total ships (sum of all types) allowed at any time on a planet
BASE_MAX_FLEET_SIZE: int = int(os.environ.get("BASE_MAX_FLEET_SIZE", "50"))
FLEET_SIZE_PER_COMPUTER_LEVEL: int = int(os.environ.get("FLEET_SIZE_PER_COMPUTER_LEVEL", "10"))

# Universe dimensions (from docs/requirements.md PM-003)
GALAXY_COUNT: int = int(os.environ.get("GALAXY_COUNT", "9"))
SYSTEMS_PER_GALAXY: int = int(os.environ.get("SYSTEMS_PER_GALAXY", "499"))
POSITIONS_PER_SYSTEM: int = int(os.environ.get("POSITIONS_PER_SYSTEM", "15"))

# Galaxy seeding configuration
# Target maximum concurrent players expected in the universe (used for planning scale)
MAX_PLAYERS: int = int(os.environ.get("MAX_PLAYERS", "512"))
# Number of empty planets (coordinates) to pre-seed randomly across the galaxy at startup
# Default: 2x MAX_PLAYERS (can be tuned via env)
INITIAL_PLANETS: int = int(os.environ.get("INITIAL_PLANETS", str(MAX_PLAYERS * 2)))

# Starter flow configuration
# If True, registration will NOT auto-create a homeworld; users must choose start via endpoint
REQUIRE_START_CHOICE: bool = os.environ.get("REQUIRE_START_CHOICE", "true").lower() == "true"
# Default starter planet name
STARTER_PLANET_NAME: str = os.environ.get("STARTER_PLANET_NAME", "Homeworld")
# Default starter resources (applied on initial planet creation when DB path is used)
STARTER_INIT_RESOURCES = {
    'metal': int(os.environ.get('STARTER_METAL', '500')),
    'crystal': int(os.environ.get('STARTER_CRYSTAL', '300')),
    'deuterium': int(os.environ.get('STARTER_DEUTERIUM', '100')),
}
# Ranges for generated planet attributes
PLANET_SIZE_MIN: int = int(os.environ.get('PLANET_SIZE_MIN', '140'))
PLANET_SIZE_MAX: int = int(os.environ.get('PLANET_SIZE_MAX', '200'))
PLANET_TEMPERATURE_MIN: int = int(os.environ.get('PLANET_TEMPERATURE_MIN', '-40'))
PLANET_TEMPERATURE_MAX: int = int(os.environ.get('PLANET_TEMPERATURE_MAX', '60'))
