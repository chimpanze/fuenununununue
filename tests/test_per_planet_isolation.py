import esper
from datetime import datetime, timedelta

from src.models import Player, Position, Planet, Resources, ResourceProduction, Buildings, BuildQueue
from src.systems import BuildingConstructionSystem


def test_build_queue_isolated_per_planet():
    """Two planets under the same player must not share build queues or building levels."""
    world = esper.World()
    world.add_processor(BuildingConstructionSystem())

    # Common player id
    uid = 1

    # Planet A with a build completing now
    res_a = Resources()
    prod_a = ResourceProduction(last_update=datetime.now() - timedelta(minutes=5))
    bld_a = Buildings(metal_mine=1)
    bq_a = BuildQueue(items=[{
        "type": "metal_mine",
        "level": 2,
        "queued_at": datetime.now() - timedelta(minutes=10),
        "completion_time": datetime.now() - timedelta(seconds=1),
    }])
    ent_a = world.create_entity(
        Player(name="P", user_id=uid), Position(), res_a, prod_a, bld_a, bq_a, Planet(name="A", owner_id=uid)
    )

    # Planet B with no queue
    res_b = Resources()
    prod_b = ResourceProduction(last_update=datetime.now() - timedelta(minutes=5))
    bld_b = Buildings(metal_mine=1)
    bq_b = BuildQueue(items=[])
    ent_b = world.create_entity(
        Player(name="P", user_id=uid), Position(), res_b, prod_b, bld_b, bq_b, Planet(name="B", owner_id=uid)
    )

    # Process one tick
    world.process()

    # Planet A should have incremented metal_mine; Planet B unchanged
    assert bld_a.metal_mine == 2
    assert bld_b.metal_mine == 1

    # Queues reflect isolation
    assert len(bq_a.items) == 0
    assert len(bq_b.items) == 0