import esper
from datetime import datetime, timedelta

from src.models import Research, ResearchQueue
from src.systems import ResearchSystem


def test_research_system_completes_queue_and_increments_level():
    world = esper.World()
    research = Research(energy=0)
    queue = ResearchQueue(items=[{
        'type': 'energy',
        'completion_time': datetime.now() - timedelta(seconds=1),
        'cost': {'metal': 100, 'crystal': 50, 'deuterium': 0},
    }])

    world.add_processor(ResearchSystem())
    e = world.create_entity(queue, research)

    world.process()

    assert len(queue.items) == 0
    assert research.energy == 1
