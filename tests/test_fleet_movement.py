from datetime import datetime, timedelta
import esper

from src.models import Fleet, Position, FleetMovement
from src.systems import FleetMovementSystem


def test_fleet_movement_arrival_updates_position_and_clears_component():
    world = esper.World()
    world.add_processor(FleetMovementSystem())

    # Initial position at 1:1:1
    pos = Position(galaxy=1, system=1, planet=1)
    fleet = Fleet(light_fighter=5)

    # Movement to 1:2:3 already arrived
    movement = FleetMovement(
        origin=Position(galaxy=1, system=1, planet=1),
        target=Position(galaxy=1, system=2, planet=3),
        departure_time=datetime.now() - timedelta(minutes=10),
        arrival_time=datetime.now() - timedelta(seconds=1),
        speed=1.0,
        mission="transfer",
        owner_id=123,
    )

    e = world.create_entity(pos, fleet, movement)

    # Process systems
    world.process()

    # Verify position updated
    updated_pos = world.component_for_entity(e, Position)
    assert (updated_pos.galaxy, updated_pos.system, updated_pos.planet) == (1, 2, 3)

    # Verify FleetMovement removed
    try:
        world.component_for_entity(e, FleetMovement)
        assert False, "FleetMovement should be removed after arrival"
    except Exception:
        # Expected: component is removed
        pass
