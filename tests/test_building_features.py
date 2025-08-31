import esper
from datetime import datetime, timedelta

from src.core.game import GameWorld
from src.models import Resources, ResourceProduction, Buildings, BuildQueue, Player, Position, Fleet, Research, Planet
from src.systems import ResourceProductionSystem


def test_energy_scaling_zero_without_solar():
    """Production should be zero when there is no energy available."""
    world = esper.World()
    res = Resources(metal=0, crystal=0, deuterium=0)
    prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    # No solar plant, so energy_produced = 0 → factor = 0
    bld = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1, solar_plant=0)

    world.add_processor(ResourceProductionSystem())
    _ = world.create_entity(res, prod, bld)

    world.process()

    assert res.metal == 0
    assert res.crystal == 0
    assert res.deuterium == 0


def test_demolish_building_reduces_level_and_refunds():
    gw = GameWorld()
    # Create a player entity
    resources = Resources()
    buildings = Buildings(metal_mine=2)  # start at level 2
    gw.world.create_entity(
        Player(name="Demo", user_id=10), Position(), resources, ResourceProduction(), buildings, BuildQueue(), Fleet(), Research(), Planet(name="Homeworld", owner_id=10)
    )

    # Issue demolition command
    gw.queue_command({'type': 'demolish_building', 'user_id': 10, 'building_type': 'metal_mine'})
    gw._process_commands()

    data = gw.get_player_data(10)
    assert data is not None
    assert data["buildings"]["metal_mine"] == 1

    # Refund should be 30% of level-1 cost
    refund_base = gw._calculate_building_cost('metal_mine', 1)
    expected_metal_refund = int(refund_base['metal'] * 0.3)
    expected_crystal_refund = int(refund_base['crystal'] * 0.3)
    # Starting resources were 500/300/100
    assert data["resources"]["metal"] >= 500 + expected_metal_refund
    assert data["resources"]["crystal"] >= 300 + expected_crystal_refund


def test_cancel_build_queue_refunds_half():
    gw = GameWorld()
    # Create a player entity
    resources = Resources()
    buildings = Buildings()  # metal_mine starts at level 1
    gw.world.create_entity(
        Player(name="Cancel", user_id=11), Position(), resources, ResourceProduction(), buildings, BuildQueue(), Fleet(), Research(), Planet(name="Homeworld", owner_id=11)
    )

    # Queue a build for metal_mine
    gw.queue_command({'type': 'build_building', 'user_id': 11, 'building_type': 'metal_mine'})
    gw._process_commands()

    # Ensure it was queued
    data = gw.get_player_data(11)
    assert len(data["build_queue"]) == 1

    # Compute expected refund: cost at current level (level 1 for metal_mine)
    cost = gw._calculate_building_cost('metal_mine', 1)
    # After spending, metal = 500 - cost_metal; after cancel refund half
    expected_metal = 500 - cost['metal'] + int(cost['metal'] * 0.5)
    expected_crystal = 300 - cost['crystal'] + int(cost['crystal'] * 0.5)
    expected_deut = 100 - cost['deuterium'] + int(cost['deuterium'] * 0.5)

    # Cancel the queue item at index 0
    gw.queue_command({'type': 'cancel_build_queue', 'user_id': 11, 'index': 0})
    gw._process_commands()

    data2 = gw.get_player_data(11)
    assert len(data2["build_queue"]) == 0
    assert data2["resources"]["metal"] == expected_metal
    assert data2["resources"]["crystal"] == expected_crystal
    assert data2["resources"]["deuterium"] == expected_deut
