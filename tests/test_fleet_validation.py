from datetime import datetime, timedelta

from src.core.game import GameWorld
from src.models import Player, Position, Resources, ResourceProduction, Buildings, BuildQueue, Fleet, Research, Planet, ShipBuildQueue


def _make_world_with_player(user_id: int = 1, fleet_counts=None, computer_level: int = 0):
    gw = GameWorld()
    fleet_counts = fleet_counts or {}
    fleet = Fleet(
        light_fighter=int(fleet_counts.get('light_fighter', 0)),
        heavy_fighter=int(fleet_counts.get('heavy_fighter', 0)),
        cruiser=int(fleet_counts.get('cruiser', 0)),
        battleship=int(fleet_counts.get('battleship', 0)),
        bomber=int(fleet_counts.get('bomber', 0)),
    )
    gw.world.create_entity(
        Player(name="Test", user_id=user_id),
        Position(),
        Resources(metal=999_999, crystal=999_999, deuterium=999_999),
        ResourceProduction(),
        Buildings(shipyard=1),
        BuildQueue(),
        fleet,
        Research(computer=computer_level),
        Planet(name="Homeworld", owner_id=user_id),
    )
    return gw


def test_fleet_cap_blocks_build_when_exceeded():
    # Base cap is 50 with computer=0; start with 49 ships, request 2 -> reject
    gw = _make_world_with_player(user_id=10, fleet_counts={'light_fighter': 49}, computer_level=0)

    gw.queue_command({'type': 'build_ships', 'user_id': 10, 'ship_type': 'light_fighter', 'quantity': 2})
    gw._process_commands()

    # Verify no ship build was queued and fleet unchanged
    # Find entity and read ShipBuildQueue (if present)
    from src.models import Player as _P
    for ent, (p,) in gw.world.get_components(_P):
        if p.user_id != 10:
            continue
        try:
            sbq = gw.world.component_for_entity(ent, ShipBuildQueue)
        except Exception:
            sbq = None
        if sbq is not None:
            assert len(sbq.items) == 0
        # Fleet remains at 49
        fl = gw.world.component_for_entity(ent, Fleet)
        assert fl.light_fighter == 49
        break


def test_fleet_cap_allows_build_up_to_cap():
    # Start with 48 ships, request 2 -> allowed to reach cap
    gw = _make_world_with_player(user_id=11, fleet_counts={'light_fighter': 48}, computer_level=0)

    gw.queue_command({'type': 'build_ships', 'user_id': 11, 'ship_type': 'light_fighter', 'quantity': 2})
    gw._process_commands()

    # Verify the build was queued with count 2
    from src.models import Player as _P
    for ent, (p,) in gw.world.get_components(_P):
        if p.user_id != 11:
            continue
        sbq = gw.world.component_for_entity(ent, ShipBuildQueue)
        assert sbq is not None
        assert any(item.get('type') == 'light_fighter' and int(item.get('count')) == 2 for item in sbq.items)
        break


def test_fleet_cap_counts_queued_ships_too():
    # With current 40 and queued 9, request 2 should be rejected at cap 50
    gw = _make_world_with_player(user_id=12, fleet_counts={'light_fighter': 40}, computer_level=0)

    # Attach an existing ship queue with 9 ships pending
    from src.models import Player as _P
    target_ent = None
    for ent, (p,) in gw.world.get_components(_P):
        if p.user_id == 12:
            target_ent = ent
            break
    assert target_ent is not None
    sbq = ShipBuildQueue(items=[{
        'type': 'light_fighter',
        'count': 9,
        'completion_time': datetime.now() + timedelta(seconds=60),
        'cost': {'metal': 0, 'crystal': 0, 'deuterium': 0},
    }])
    try:
        gw.world.add_component(target_ent, sbq)
    except Exception:
        pass

    # Now attempt to queue 2 more -> reject (40 + 9 + 2 = 51 > 50)
    gw.queue_command({'type': 'build_ships', 'user_id': 12, 'ship_type': 'light_fighter', 'quantity': 2})
    gw._process_commands()

    # Ensure no additional items were added (still only the original 1 item with count 9)
    sbq_after = gw.world.component_for_entity(target_ent, ShipBuildQueue)
    assert sbq_after is not None
    assert len(sbq_after.items) == 1
    assert int(sbq_after.items[0].get('count', 0)) == 9


def test_fleet_cap_increases_with_computer_level():
    # With computer level 2 and base cap 50 + 2*10 = 70, start at 65 and request 5 -> allowed
    gw = _make_world_with_player(user_id=13, fleet_counts={'light_fighter': 65}, computer_level=2)

    gw.queue_command({'type': 'build_ships', 'user_id': 13, 'ship_type': 'light_fighter', 'quantity': 5})
    gw._process_commands()

    # Verify queued successfully
    from src.models import Player as _P
    for ent, (p,) in gw.world.get_components(_P):
        if p.user_id != 13:
            continue
        sbq = gw.world.component_for_entity(ent, ShipBuildQueue)
        assert sbq is not None
        assert any(item.get('type') == 'light_fighter' and int(item.get('count')) == 5 for item in sbq.items)
        break
