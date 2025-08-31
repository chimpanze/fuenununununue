from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any


@dataclass
class Position:
    """Represents galaxy/system/planet coordinates within the universe."""
    galaxy: int = 1
    system: int = 1
    planet: int = 1


@dataclass
class Player:
    """Represents a player profile and activity metadata.

    Attributes:
        name: Display name of the player.
        user_id: Unique player identifier.
        last_active: Timestamp of the last observed activity.
    """
    name: str
    user_id: int
    last_active: datetime = field(default_factory=datetime.now)


@dataclass
class Resources:
    """Holds current resource amounts for a player/planet."""
    metal: int = 500
    crystal: int = 300
    deuterium: int = 100


@dataclass
class ResourceProduction:
    """Production rates and last update timestamp used by the production system.

    Rates are expressed per hour; systems convert to elapsed time.
    """
    metal_rate: float = 30.0  # per hour
    crystal_rate: float = 20.0
    deuterium_rate: float = 10.0
    last_update: datetime = field(default_factory=datetime.now)


@dataclass
class Buildings:
    """Levels for all building types present on a planet."""
    metal_mine: int = 1
    crystal_mine: int = 1
    deuterium_synthesizer: int = 1
    solar_plant: int = 1
    robot_factory: int = 0
    shipyard: int = 0


@dataclass
class BuildQueue:
    """FIFO queue of pending building constructions for a planet."""
    items: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ShipBuildQueue:
    """FIFO queue of pending ship constructions for a planet.

    Each item is a dict with at least keys:
      - 'type': ship type name (e.g., 'light_fighter')
      - 'count': number of ships to complete (defaults to 1 if omitted)
      - 'completion_time': datetime when the construction completes
      - optional 'cost': a dict of resource costs (for API/UI visibility)
    """
    items: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Fleet:
    """Counts of each owned ship type stationed at the planet."""
    light_fighter: int = 0
    heavy_fighter: int = 0
    cruiser: int = 0
    battleship: int = 0
    bomber: int = 0
    colony_ship: int = 0


@dataclass
class FleetMovement:
    """Represents a fleet currently in transit between two coordinates.

    Attributes:
        origin: Starting coordinates.
        target: Destination coordinates.
        departure_time: Timestamp when movement started.
        arrival_time: Timestamp when movement will arrive at destination.
        speed: Effective speed units per second (for reference/telemetry).
        mission: Mission type (e.g., 'attack', 'transport', 'colonize').
        owner_id: The user ID owning the fleet.
        recalled: If True, fleet is returning to origin; target should be origin and
                  arrival_time adjusted by the system when recall is initiated.
    """
    origin: Position
    target: Position
    departure_time: datetime
    arrival_time: datetime
    speed: float = 1.0
    mission: str = "transfer"
    owner_id: int = 0
    recalled: bool = False


@dataclass
class Research:
    """Research levels owned by the player that influence various systems."""
    energy: int = 0
    laser: int = 0
    ion: int = 0
    hyperspace: int = 0
    plasma: int = 0
    computer: int = 0


@dataclass
class ResearchQueue:
    """FIFO queue of pending research tasks for a player.

    Each item is a dict with at least keys:
      - 'type': research field name (e.g., 'energy', 'laser')
      - 'completion_time': datetime when the research completes
      - optional 'cost': a dict of resource costs (for API/UI visibility)
    """
    items: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Planet:
    """Planet metadata and environment characteristics."""
    name: str
    owner_id: int
    temperature: int = 25
    size: int = 163


@dataclass
class Battle:
    """Represents a scheduled battle between an attacker and a defender.

    Attributes:
        attacker_id: User ID of the attacking player.
        defender_id: User ID of the defending player.
        location: Coordinates where the battle occurs.
        scheduled_time: When the combat should be resolved.
        attacker_ships: Mapping ship_type -> count for attacker fleet snapshot.
        defender_ships: Mapping ship_type -> count for defender fleet snapshot.
        resolved: Whether the battle has been processed already.
        outcome: Arbitrary dict with resolution details (winner, losses, etc.).
    """
    attacker_id: int
    defender_id: int
    location: Position
    scheduled_time: datetime
    attacker_ships: Dict[str, int] = field(default_factory=dict)
    defender_ships: Dict[str, int] = field(default_factory=dict)
    resolved: bool = False
    outcome: Dict[str, Any] = field(default_factory=dict)
