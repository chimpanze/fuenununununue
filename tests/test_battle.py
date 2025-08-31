from datetime import datetime, timedelta
import esper

from src.models import Battle, Position
from src.systems import BattleSystem


def test_battle_system_attacker_wins():
    world = esper.World()
    world.add_processor(BattleSystem())

    battle = Battle(
        attacker_id=1,
        defender_id=2,
        location=Position(galaxy=1, system=1, planet=1),
        scheduled_time=datetime.now() - timedelta(seconds=1),
        attacker_ships={"light_fighter": 2},
        defender_ships={"light_fighter": 1},
    )

    e = world.create_entity(battle)

    world.process()

    resolved = world.component_for_entity(e, Battle)
    assert resolved.resolved is True
    assert resolved.outcome.get("winner") == "attacker"
    assert resolved.outcome.get("attacker_power", 0) > resolved.outcome.get("defender_power", 0)


def test_battle_system_draw():
    world = esper.World()
    world.add_processor(BattleSystem())

    battle = Battle(
        attacker_id=3,
        defender_id=4,
        location=Position(galaxy=1, system=1, planet=2),
        scheduled_time=datetime.now() - timedelta(seconds=1),
        attacker_ships={"light_fighter": 1},
        defender_ships={"light_fighter": 1},
    )

    e = world.create_entity(battle)

    world.process()

    resolved = world.component_for_entity(e, Battle)
    assert resolved.resolved is True
    assert resolved.outcome.get("winner") == "draw"
    assert resolved.outcome.get("attacker_power", 0) == resolved.outcome.get("defender_power", 0)
