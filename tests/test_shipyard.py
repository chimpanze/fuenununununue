import esper
from datetime import datetime, timedelta

from src.models import ShipBuildQueue, Fleet
from src.systems import ShipyardSystem


def test_shipyard_system_completes_ship_queue():
    world = esper.World()
    ship_queue = ShipBuildQueue(items=[{
        'type': 'light_fighter',
        'count': 3,
        'completion_time': datetime.now() - timedelta(seconds=1),
        'cost': {'metal': 0, 'crystal': 0, 'deuterium': 0},
    }])
    fleet = Fleet(light_fighter=0)

    world.add_processor(ShipyardSystem())
    e = world.create_entity(ship_queue, fleet)

    world.process()

    assert len(ship_queue.items) == 0
    assert fleet.light_fighter == 3
