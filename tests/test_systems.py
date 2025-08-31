import esper
from datetime import datetime, timedelta
from src.models import Resources, ResourceProduction, Buildings, BuildQueue
from src.systems import ResourceProductionSystem, BuildingConstructionSystem


def test_resource_production_system_increases_resources():
    world = esper.World()
    res = Resources(metal=0, crystal=0, deuterium=0)
    prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    bld = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1)

    world.add_processor(ResourceProductionSystem())
    e = world.create_entity(res, prod, bld)

    world.process()

    # Expect at least base rates applied for ~1 hour with multiplier > 1
    assert res.metal > 0
    assert res.crystal > 0
    assert res.deuterium > 0


def test_building_construction_system_completes_queue():
    world = esper.World()
    res = Resources(metal=1000, crystal=1000, deuterium=1000)
    bld = Buildings(metal_mine=1)
    queue = BuildQueue(items=[{
        'type': 'metal_mine',
        'completion_time': datetime.now() - timedelta(seconds=1),
        'cost': {'metal': 10, 'crystal': 0, 'deuterium': 0}
    }])

    world.add_processor(BuildingConstructionSystem())
    e = world.create_entity(queue, res, bld)

    world.process()

    assert len(queue.items) == 0
    assert bld.metal_mine == 2
