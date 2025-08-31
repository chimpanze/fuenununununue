from datetime import datetime, timedelta
import esper

from src.models import Player, Position, Resources, Buildings, Fleet, Planet, FleetMovement
from src.systems import FleetMovementSystem


def test_espionage_generates_report_on_arrival():
    # Prepare an ECS world with FleetMovementSystem
    world = esper.World()
    world.add_processor(FleetMovementSystem())

    # Storage for emitted reports via handler hook
    reports = []
    setattr(world, "handle_espionage_report", lambda payload: reports.append(payload))

    # Defender at target coordinates 1:2:3 with some state
    defender_uid = 42
    d_ent = world.create_entity(
        Player(name="def", user_id=defender_uid),
        Position(galaxy=1, system=2, planet=3),
        Resources(metal=1000, crystal=500, deuterium=200),
        Buildings(metal_mine=5, crystal_mine=4, deuterium_synthesizer=3, solar_plant=4, robot_factory=2, shipyard=1),
        Fleet(light_fighter=3, cruiser=1),
        Planet(name="Target", owner_id=defender_uid),
    )

    # Attacker at another coord dispatches espionage, arrival already due
    attacker_uid = 7
    a_ent = world.create_entity(
        Player(name="att", user_id=attacker_uid),
        Position(galaxy=1, system=1, planet=1),
        Fleet(light_fighter=5),
    )

    mv = FleetMovement(
        origin=Position(galaxy=1, system=1, planet=1),
        target=Position(galaxy=1, system=2, planet=3),
        departure_time=datetime.now() - timedelta(minutes=5),
        arrival_time=datetime.now() - timedelta(seconds=1),
        speed=1.0,
        mission="espionage",
        owner_id=attacker_uid,
    )
    world.add_component(a_ent, mv)

    # Process systems -> should trigger espionage handler and clear movement
    world.process()

    # Expect an espionage report emitted
    assert len(reports) == 1
    report = reports[0]
    assert report.get("attacker_user_id") == attacker_uid
    assert report.get("defender_user_id") == defender_uid
    loc = report.get("location")
    assert loc == {"galaxy": 1, "system": 2, "planet": 3}
    snap = report.get("snapshot") or {}
    # Basic keys present
    assert "planet" in snap and "resources" in snap and "buildings" in snap and "fleet" in snap
