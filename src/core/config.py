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

# Periodic persistence interval in seconds for saving player data
# Optional fast-intervals toggle for dev/test without changing production defaults.
_DEV_FAST = os.environ.get("DEV_FAST_INTERVALS", "false").lower() == "true"
_DEFAULT_SAVE = "5" if _DEV_FAST else "60"
SAVE_INTERVAL_SECONDS: int = int(os.environ.get("SAVE_INTERVAL_SECONDS", _DEFAULT_SAVE))

# Per-planet persistence throttling interval (to limit write frequency per planet)
# Used by src.core.sync to throttle resource and building persistence; keep aligned with SAVE_INTERVAL_SECONDS by default.
PERSIST_INTERVAL_SECONDS: int = int(os.environ.get("PERSIST_INTERVAL_SECONDS", str(SAVE_INTERVAL_SECONDS)))

# Cleanup configuration: threshold for inactive players (days)
CLEANUP_DAYS: int = int(os.environ.get("CLEANUP_DAYS", "30"))

# Database configuration
# Global feature toggle for DB integration (tests/dev can disable)
ENABLE_DB: bool = os.environ.get("ENABLE_DB", "false").lower() == "true"
# In dev, optionally create all tables at startup without Alembic
DEV_CREATE_ALL: bool = os.environ.get("DEV_CREATE_ALL", "false").lower() == "true"
# Use PostgreSQL (asyncpg) by default for SQLAlchemy AsyncIO
# Example: postgresql+asyncpg://user:pass@localhost:5432/dbname
DATABASE_URL: str = os.environ.get("DATABASE_URL", "postgresql+asyncpg://ogame:ogame@localhost:5432/ogame")
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
    'research_lab': {'metal': 200, 'crystal': 400, 'deuterium': 0},
    'fusion_reactor': {'metal': 900, 'crystal': 500, 'deuterium': 200},
    # Storage buildings
    'metal_storage': {'metal': 1000, 'crystal': 0, 'deuterium': 0},
    'crystal_storage': {'metal': 800, 'crystal': 200, 'deuterium': 0},
    'deuterium_tank': {'metal': 800, 'crystal': 0, 'deuterium': 200},
}

# Base build times in seconds
BASE_BUILD_TIMES = {
    'metal_mine': 60,
    'crystal_mine': 80,
    'deuterium_synthesizer': 100,
    'solar_plant': 50,
    'robot_factory': 300,
    'shipyard': 400,
    'research_lab': 240,
    'fusion_reactor': 360,
    # Storage buildings
    'metal_storage': 120,
    'crystal_storage': 120,
    'deuterium_tank': 120,
}

# Building prerequisites map: building -> {required_building: min_level}
PREREQUISITES = {
    # Example: shipyard requires robot factory level 2
    'shipyard': {'robot_factory': 2},
    # Fusion reactor requires at least some deuterium infrastructure
    'fusion_reactor': {'deuterium_synthesizer': 1},
}

# Energy system parameters
# Base energy produced by solar plant per level (approximate)
ENERGY_SOLAR_BASE: float = 20.0
# Optional non-linear growth for solar plant energy production per level.
# Effective formula: ENERGY_SOLAR_BASE * level * (ENERGY_SOLAR_GROWTH ** max(0, level-1))
# Default 1.0 preserves legacy linear behavior.
ENERGY_SOLAR_GROWTH: float = float(os.environ.get("ENERGY_SOLAR_GROWTH", "1.0"))
# Fusion reactor energy production (per level) and deuterium consumption
FUSION_ENERGY_BASE: float = float(os.environ.get("FUSION_ENERGY_BASE", "30.0"))
FUSION_ENERGY_GROWTH: float = float(os.environ.get("FUSION_ENERGY_GROWTH", "1.0"))
# Deuterium consumption per level per hour when fusion reactor is active
FUSION_DEUTERIUM_CONSUMPTION_PER_LEVEL: float = float(os.environ.get("FUSION_DEUTERIUM_CONSUMPTION_PER_LEVEL", "5.0"))
# Base energy consumption per building per level
ENERGY_CONSUMPTION = {
    'metal_mine': 3.0,
    'crystal_mine': 2.0,
    'deuterium_synthesizer': 2.0,
}
# Optional non-linear growth for per-level energy consumption.
# Effective formula per building: BASE * level * (ENERGY_CONSUMPTION_GROWTH ** max(0, level-1))
# Default 1.0 preserves legacy linear behavior.
ENERGY_CONSUMPTION_GROWTH: float = float(os.environ.get("ENERGY_CONSUMPTION_GROWTH", "1.0"))

# Soft floor for energy deficit production scaling (fraction 0..1)
# Applied only when ENERGY_REQUIRED > 0 and ENERGY_PRODUCED > 0; zero energy still yields factor=0.
ENERGY_DEFICIT_SOFT_FLOOR: float = float(os.environ.get("ENERGY_DEFICIT_SOFT_FLOOR", "0.25"))
# Threshold at or below which to emit energy deficit warnings
ENERGY_DEFICIT_NOTIFY_THRESHOLD: float = float(os.environ.get("ENERGY_DEFICIT_NOTIFY_THRESHOLD", "0.25"))
# Cooldown in seconds for repeated energy deficit warnings per user/planet
ENERGY_DEFICIT_NOTIFICATION_COOLDOWN_SECONDS: int = int(os.environ.get("ENERGY_DEFICIT_NOTIFICATION_COOLDOWN_SECONDS", "300"))


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
# Expanded per docs/tasks.md #66: ion requires laser 4; hyperspace requires energy 6 + laser 6; plasma requires energy 8 + ion 5
RESEARCH_PREREQUISITES = {
    'ion': {
        'laser': 4,
    },
    'hyperspace': {
        'energy': 6,
        'laser': 6,
    },
    'plasma': {
        'energy': 8,
        'ion': 5,
    },
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
# Robot Factory reduces building construction time (fraction per level)
ROBOT_FACTORY_BUILD_TIME_REDUCTION_PER_LEVEL: float = float(os.environ.get("ROBOT_FACTORY_BUILD_TIME_REDUCTION_PER_LEVEL", "0.02"))
# Shipyard level reduces ship build time (fraction per level)
SHIPYARD_BUILD_TIME_REDUCTION_PER_LEVEL: float = float(os.environ.get("SHIPYARD_BUILD_TIME_REDUCTION_PER_LEVEL", "0.05"))
# Research Lab reduces research time (fraction per level)
RESEARCH_LAB_TIME_REDUCTION_PER_LEVEL: float = float(os.environ.get("RESEARCH_LAB_TIME_REDUCTION_PER_LEVEL", "0.03"))
# Minimum clamp factor for build time after reductions (e.g., 0.5 = cannot go below 50% of base)
MIN_BUILD_TIME_FACTOR: float = 0.5
# Minimum clamp factor for research time after reductions
MIN_RESEARCH_TIME_FACTOR: float = 0.5

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
    'colony_ship': {'metal': 450, 'crystal': 225, 'deuterium': 0},  # Raised baseline (docs/tasks.md #59)
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
# Shipyard queue size limit: base + per-level growth
SHIPYARD_QUEUE_BASE_LIMIT: int = int(os.environ.get("SHIPYARD_QUEUE_BASE_LIMIT", "2"))
SHIPYARD_QUEUE_PER_LEVEL: int = int(os.environ.get("SHIPYARD_QUEUE_PER_LEVEL", "1"))

# Universe dimensions (from docs/requirements.md PM-003)
GALAXY_COUNT: int = int(os.environ.get("GALAXY_COUNT", "9"))
SYSTEMS_PER_GALAXY: int = int(os.environ.get("SYSTEMS_PER_GALAXY", "499"))
POSITIONS_PER_SYSTEM: int = int(os.environ.get("POSITIONS_PER_SYSTEM", "15"))

# Economy and Market configuration (soft guidance)
# Target exchange ratios (relative weights). Interpreted as metal:crystal:deuterium guidance.
# Example default: 3:2:1 implies 3 metal ~= 2 crystal ~= 1 deuterium in value terms.
EXCHANGE_RATIOS = {
    'metal': float(os.environ.get('EXCHANGE_RATIO_METAL', '3.0')),
    'crystal': float(os.environ.get('EXCHANGE_RATIO_CRYSTAL', '2.0')),
    'deuterium': float(os.environ.get('EXCHANGE_RATIO_DEUTERIUM', '1.0')),
}
# Transaction fee rate applied to seller proceeds (0.0..1.0). Default 0.0 (no fee).
TRADE_TRANSACTION_FEE_RATE: float = float(os.environ.get('TRADE_TRANSACTION_FEE_RATE', '0.0'))

# Feature flags for phased rollout of newer buildings/systems
FEATURE_ENABLE_STORAGE_BUILDINGS: bool = os.environ.get('FEATURE_ENABLE_STORAGE_BUILDINGS', 'true').lower() == 'true'
FEATURE_ENABLE_FUSION_REACTOR: bool = os.environ.get('FEATURE_ENABLE_FUSION_REACTOR', 'true').lower() == 'true'
FEATURE_ENABLE_ROBOT_FACTORY: bool = os.environ.get('FEATURE_ENABLE_ROBOT_FACTORY', 'true').lower() == 'true'
FEATURE_ENABLE_RESEARCH_LAB: bool = os.environ.get('FEATURE_ENABLE_RESEARCH_LAB', 'true').lower() == 'true'

# Galaxy seeding configuration
# Target maximum concurrent players expected in the universe (used for planning scale)
MAX_PLAYERS: int = int(os.environ.get("MAX_PLAYERS", "512"))
# Number of empty planets (coordinates) to pre-seed randomly across the galaxy at startup
# Default: 2x MAX_PLAYERS (can be tuned via env)
INITIAL_PLANETS: int = int(os.environ.get("INITIAL_PLANETS", str(MAX_PLAYERS * 2)))


# Starter flow configuration
# If True, registration will NOT auto-create a homeworld; users must choose start via endpoint
REQUIRE_START_CHOICE: bool = os.environ.get("REQUIRE_START_CHOICE", "false").lower() == "true"
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

# Base per-building production rates (per hour) used when USE_CONFIG_PRODUCTION_RATES is true.
BASE_PRODUCTION_RATES = {
    'metal_mine': float(os.environ.get('BASE_METAL_MINE_RATE', '30.0')),
    'crystal_mine': float(os.environ.get('BASE_CRYSTAL_MINE_RATE', '20.0')),
    'deuterium_synthesizer': float(os.environ.get('BASE_DEUTERIUM_SYNTH_RATE', '10.0')),
}
# Feature flag to use config base rates instead of ResourceProduction component rates.
USE_CONFIG_PRODUCTION_RATES: bool = os.environ.get('USE_CONFIG_PRODUCTION_RATES', 'false').lower() == 'true'

# Storage capacity configuration
# Base capacity when storage level is 0 and exponential growth per level.
STORAGE_BASE_CAPACITY = {
    'metal': int(os.environ.get('BASE_METAL_CAPACITY', '100000')),
    'crystal': int(os.environ.get('BASE_CRYSTAL_CAPACITY', '75000')),
    'deuterium': int(os.environ.get('BASE_DEUTERIUM_CAPACITY', '50000')),
}
STORAGE_CAPACITY_GROWTH = {
    'metal': float(os.environ.get('METAL_STORAGE_GROWTH', '2.0')),
    'crystal': float(os.environ.get('CRYSTAL_STORAGE_GROWTH', '2.0')),
    'deuterium': float(os.environ.get('DEUTERIUM_TANK_GROWTH', '2.0')),
}

# Planet modifier helpers.
# Temperature affects deuterium production efficiency (docs/tasks.md #71).
# Cold planets yield more deuterium; very hot planets yield less.
# Piecewise-linear curve chosen for simplicity and backward-compatibility.
# Ranges (deg C) -> multiplier:
#   <= -40: 1.20
#   -40..0:  1.10
#   0..25:   1.00
#   25..50:  0.90
#   > 50:    0.80
# These defaults can be tuned later via env-driven mapping if needed.
def temperature_multiplier(temperature_c: int) -> float:
    """Return a multiplier (>=0) reflecting deuterium efficiency by temperature.

    Note: This multiplier is applied only to deuterium production in systems,
    keeping metal/crystal unaffected by temperature to minimize balance impact.
    """
    try:
        t = int(temperature_c)
        if t <= -40:
            return 1.20
        if t <= 0:
            return 1.10
        if t <= 25:
            return 1.00
        if t <= 50:
            return 0.90
        return 0.80
    except Exception:
        return 1.0

# Size multiplier affects production and storage capacity efficiency (docs/tasks.md #72).
# Simple piecewise curve chosen for clarity and backward-compatible ranges:
#   <= 150 fields:   0.90 (tight planet; less capacity/efficiency)
#   151..175 fields: 1.00 (baseline)
#   > 175 fields:    1.10 (spacious planet; more capacity/efficiency)
# This multiplier is applied in ResourceProductionSystem to both base production
# and storage capacity calculations.

def size_multiplier(size: int) -> float:
    """Return a multiplier for production/capacity based on planet size.

    Args:
        size: planet size in fields (int)
    Returns:
        A non-negative multiplier.
    """
    try:
        s = int(size)
        if s <= 150:
            return 0.90
        if s <= 175:
            return 1.00
        return 1.10
    except Exception:
        return 1.0


# --- Typed getters (single source of truth) ---
from typing import cast

def get_enable_db() -> bool:
    return bool(ENABLE_DB)

def get_dev_create_all() -> bool:
    return bool(DEV_CREATE_ALL)

def get_tick_rate() -> float:
    return float(TICK_RATE)

def get_save_interval_seconds() -> int:
    return int(SAVE_INTERVAL_SECONDS)

def get_persist_interval_seconds() -> int:
    return int(PERSIST_INTERVAL_SECONDS)
