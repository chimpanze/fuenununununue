import esper
from datetime import datetime, timedelta

from src.core.game import GameWorld
from src.models import Resources, ResourceProduction, Buildings, Research, Planet, Player, Position, BuildQueue, Fleet, ResearchQueue
from src.systems import ResourceProductionSystem


def _tick_once(world: esper.World):
    world.process()


def test_offline_accrual_matches_online_tick_one_hour():
    """Offline accrual path should match online tick calculations for a one-hour delta."""
    hours = 1.0

    # Common components
    res1 = Resources(metal=0, crystal=0, deuterium=0)
    prod1 = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=hours)
    )
    bld1 = Buildings(metal_mine=4, crystal_mine=3, deuterium_synthesizer=2, solar_plant=10)
    rsh1 = Research(energy=2, plasma=1)

    # Online tick world
    w_online = esper.World()
    w_online.add_processor(ResourceProductionSystem())
    e1 = w_online.create_entity(res1, prod1, bld1, rsh1)
    _tick_once(w_online)

    # Offline accrual via GameWorld helper
    gw = GameWorld()
    res2 = Resources(metal=0, crystal=0, deuterium=0)
    prod2 = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=hours)
    )
    bld2 = Buildings(metal_mine=4, crystal_mine=3, deuterium_synthesizer=2, solar_plant=10)
    rsh2 = Research(energy=2, plasma=1)

    gw.world.create_entity(
        Player(name="T", user_id=999), Position(), res2, prod2, bld2, BuildQueue(), Fleet(), rsh2, ResearchQueue(), Planet(name="X", owner_id=999)
    )

    # Apply offline accrual
    gw._apply_offline_resource_accrual()

    # Compare results
    assert res1.metal == res2.metal
    assert res1.crystal == res2.crystal
    assert res1.deuterium == res2.deuterium
