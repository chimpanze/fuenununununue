from datetime import datetime

from src.core.game import GameWorld
from src.models import Player, Position, Resources, ResourceProduction, Buildings, BuildQueue, Fleet, Research, ResearchQueue, Planet


def test_research_prerequisites_block_plasma_until_energy8_and_ion5():
    gw = GameWorld()
    # Create player with insufficient ion research (energy ok)
    gw.world.create_entity(
        Player(name="TestR", user_id=10),
        Position(),
        Resources(metal=100000, crystal=100000, deuterium=100000),
        ResourceProduction(),
        Buildings(),
        BuildQueue(),
        Fleet(),
        Research(energy=8, ion=4),
        ResearchQueue(),
        Planet(name="Homeworld", owner_id=10),
    )

    gw.queue_command({'type': 'start_research', 'user_id': 10, 'research_type': 'plasma'})
    gw._process_commands()

    # Snapshot research queue; should be empty due to unmet prereqs
    for _, (player, rq) in gw.world.get_components(Player, ResearchQueue):
        if player.user_id == 10:
            assert len(rq.items) == 0
            break


def test_research_prerequisites_allow_plasma_with_energy8_and_ion5():
    gw = GameWorld()
    # Provide ample resources to afford plasma level 1
    res = Resources(metal=100000, crystal=100000, deuterium=100000)
    gw.world.create_entity(
        Player(name="TestR2", user_id=11),
        Position(),
        res,
        ResourceProduction(),
        Buildings(),
        BuildQueue(),
        Fleet(),
        Research(energy=8, ion=5),
        ResearchQueue(),
        Planet(name="Homeworld", owner_id=11),
    )

    gw.queue_command({'type': 'start_research', 'user_id': 11, 'research_type': 'plasma'})
    gw._process_commands()

    # Snapshot research queue; should contain one plasma item
    found = False
    for _, (player, rq) in gw.world.get_components(Player, ResearchQueue):
        if player.user_id == 11:
            assert len(rq.items) == 1
            assert rq.items[0]['type'] == 'plasma'
            assert rq.items[0]['completion_time'] > datetime.now()
            found = True
            break
    assert found
