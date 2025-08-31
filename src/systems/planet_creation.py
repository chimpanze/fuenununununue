from __future__ import annotations

"""Planet/Galaxy initialization utilities.

This module seeds a pool of empty planet coordinates distributed randomly across
the configured galaxy bounds. It does not persist rows in the database; instead
it maintains an in-memory set that API endpoints can use to offer available
starting/colonization targets. This keeps changes minimal without DB schema
migrations.
"""

import logging
import random
import threading
from typing import Optional, Iterable, List, Set, Tuple, Dict

logger = logging.getLogger(__name__)

# Seeded coordinate pool and synchronization
_seed_lock = threading.Lock()
_seeded: Optional[List[Tuple[int, int, int]]] = None  # list of (g, s, p)


def _seed_if_needed() -> None:
    global _seeded
    if _seeded is not None:
        return
    try:
        from src.core.config import (
            GALAXY_COUNT,
            SYSTEMS_PER_GALAXY,
            POSITIONS_PER_SYSTEM,
            INITIAL_PLANETS,
        )
    except Exception:
        GALAXY_COUNT, SYSTEMS_PER_GALAXY, POSITIONS_PER_SYSTEM, INITIAL_PLANETS = 9, 499, 15, 1024

    total_slots = int(GALAXY_COUNT) * int(SYSTEMS_PER_GALAXY) * int(POSITIONS_PER_SYSTEM)
    target = max(0, min(int(INITIAL_PLANETS), total_slots))

    # Generate unique random coordinates without replacement
    coords_set: Set[Tuple[int, int, int]] = set()
    rnd = random.Random()
    # Derive a seed from config to vary but be reasonably unpredictable across runs
    try:
        rnd.seed()
    except Exception:
        pass

    # If target is a large fraction of total, consider generating deterministically and shuffling
    if target > total_slots // 2:
        all_coords = [
            (g, s, p)
            for g in range(1, int(GALAXY_COUNT) + 1)
            for s in range(1, int(SYSTEMS_PER_GALAXY) + 1)
            for p in range(1, int(POSITIONS_PER_SYSTEM) + 1)
        ]
        rnd.shuffle(all_coords)
        coords_set.update(all_coords[:target])
    else:
        while len(coords_set) < target:
            g = rnd.randint(1, int(GALAXY_COUNT))
            s = rnd.randint(1, int(SYSTEMS_PER_GALAXY))
            p = rnd.randint(1, int(POSITIONS_PER_SYSTEM))
            coords_set.add((g, s, p))

    # Store as a list sorted for deterministic pagination
    _seeded = sorted(coords_set)

    try:
        logger.info(
            "galaxy_seeded",
            extra={
                "action_type": "galaxy_seeded",
                "seeded_planets": len(_seeded),
            },
        )
    except Exception:
        pass


def initialize_galaxy() -> None:
    """Initialize the galaxy based on configuration and seed empty planets.

    Idempotent and safe to call multiple times.
    """
    with _seed_lock:
        _seed_if_needed()


def seeded_pool_ready() -> bool:
    return _seeded is not None and len(_seeded or []) > 0


def list_available_from_seed(
    occupied: Iterable[Tuple[int, int, int]],
    galaxy: Optional[int] = None,
    system: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, int]]:
    """Return available coordinates from the seeded pool.

    Filters by optional galaxy/system and excludes occupied coordinates.
    Applies pagination via offset/limit.
    """
    with _seed_lock:
        if _seeded is None:
            return []
        occ: Set[Tuple[int, int, int]] = set((int(g), int(s), int(p)) for g, s, p in occupied)
        filtered: List[Tuple[int, int, int]] = []
        for g, s, p in _seeded:
            if galaxy is not None and g != int(galaxy):
                continue
            if system is not None and s != int(system):
                continue
            if (g, s, p) in occ:
                continue
            filtered.append((g, s, p))
        window = filtered[offset: offset + limit]
        return [{"galaxy": g, "system": s, "position": p} for g, s, p in window]
