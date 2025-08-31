import esper
from datetime import datetime, timedelta

from src.models import Resources, ResourceProduction, Buildings, Research
from src.systems import ResourceProductionSystem
from src.core.game import GameWorld


def test_plasma_research_increases_production():
    # Baseline world without plasma
    w1 = esper.World()
    res1 = Resources(metal=0, crystal=0, deuterium=0)
    prod1 = ResourceProduction(metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
                               last_update=datetime.now() - timedelta(hours=1))
    bld1 = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1, solar_plant=1)
    w1.add_processor(ResourceProductionSystem())
    e1 = w1.create_entity(res1, prod1, bld1)

    # Research-boosted world with plasma level 5
    w2 = esper.World()
    res2 = Resources(metal=0, crystal=0, deuterium=0)
    prod2 = ResourceProduction(metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
                               last_update=datetime.now() - timedelta(hours=1))
    bld2 = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1, solar_plant=1)
    r2 = Research(plasma=5)
    w2.add_processor(ResourceProductionSystem())
    e2 = w2.create_entity(res2, prod2, bld2, r2)

    # Process both worlds
    w1.process()
    w2.process()

    assert res2.metal > res1.metal
    assert res2.crystal > res1.crystal
    assert res2.deuterium > res1.deuterium


def test_hyperspace_reduces_build_time_when_queueing():
    gw = GameWorld()
    # Create a player with hyperspace research level 5
    from src.models import Player, Position, BuildQueue, Fleet, ResearchQueue, Planet
    player_id = 123
    gw.world.create_entity(
        Player(name="Tester", user_id=player_id),
        Position(),
        Resources(metal=100000, crystal=100000, deuterium=100000),
        ResourceProduction(),
        Buildings(),
        BuildQueue(),
        Fleet(),
        Research(hyperspace=5),
        ResearchQueue(),
        Planet(name="Homeworld", owner_id=player_id),
    )

    # Enqueue a metal_mine build and inspect the computed duration
    gw.queue_command({'type': 'build_building', 'user_id': player_id, 'building_type': 'metal_mine'})
    gw._process_commands()

    # Find the entity's queue and base time
    for _, (player, buildings, bq) in gw.world.get_components(
        Player, Buildings, BuildQueue
    ):
        if player.user_id != player_id:
            continue
        current_level = getattr(buildings, 'metal_mine', 0)
        base_time = gw._calculate_build_time('metal_mine', current_level)
        item = bq.items[0]
        duration = int((item['completion_time'] - datetime.now()).total_seconds())
        # Expect <= 90% of base time due to hyperspace 5 with 2%/level reduction
        assert duration <= int(base_time * 0.9) + 1
        break


def test_ship_stats_reflect_research_levels():
    gw = GameWorld()
    r = Research(laser=10, ion=5, hyperspace=3, plasma=2)
    stats = gw._calculate_ship_stats(r)
    # Battleship base: attack=1000, shield=200, speed=10000, cargo=1500
    # Attack bonus: 10% (laser) + 1% (plasma*0.5%*2) = 11% => 1110
    assert stats['battleship']['attack'] == int(1000 * 1.11)
    # Shield bonus: 5% ion => 210
    assert stats['battleship']['shield'] == int(200 * 1.05)
    # Speed bonus: 3 * 2% = 6% => 10600
    assert stats['battleship']['speed'] == int(10000 * 1.06)
    # Cargo bonus: 6% => 1590
    assert stats['battleship']['cargo'] == int(1500 * 1.06)
