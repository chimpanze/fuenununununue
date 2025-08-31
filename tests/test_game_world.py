from src.core.game import GameWorld
from datetime import datetime
from src.models import Player, Position, Resources, ResourceProduction, Buildings, BuildQueue, Fleet, Research, Planet


def test_game_world_get_player_data_returns_player_after_creation():
    gw = GameWorld()
    # Create a player entity explicitly
    gw.world.create_entity(
        Player(name="Test", user_id=1),
        Position(),
        Resources(),
        ResourceProduction(),
        Buildings(),
        BuildQueue(),
        Fleet(),
        Research(),
        Planet(name="Homeworld", owner_id=1),
    )
    data = gw.get_player_data(1)
    assert data is not None
    assert data["player"]["user_id"] == 1


def test_queue_build_command_adds_to_queue_and_processes():
    gw = GameWorld()
    # Create the player entity
    gw.world.create_entity(
        Player(name="Test", user_id=1), Position(), Resources(), ResourceProduction(), Buildings(), BuildQueue(), Fleet(), Research(), Planet(name="Homeworld", owner_id=1)
    )

    # Queue a build command and an activity update
    gw.queue_command({'type': 'build_building', 'user_id': 1, 'building_type': 'metal_mine'})
    gw.queue_command({'type': 'update_player_activity', 'user_id': 1})

    # Process commands deterministically without starting the thread
    gw._process_commands()

    snapshot = gw.get_player_data(1)
    # After queuing, the build queue should have one item
    assert len(snapshot["build_queue"]) == 1

    # And last_active should be a valid ISO timestamp string
    _ = datetime.fromisoformat(snapshot["player"]["last_active"])  # raises if invalid



def test_build_prerequisites_block_shipyard_until_robot_factory_level():
    gw = GameWorld()
    # Create a player entity with insufficient prerequisites
    gw.world.create_entity(
        Player(name="Test", user_id=2),
        Position(),
        Resources(),
        ResourceProduction(),
        Buildings(robot_factory=0),
        BuildQueue(),
        Fleet(),
        Research(),
        Planet(name="Homeworld", owner_id=2),
    )

    # Attempt to queue shipyard build without prerequisites
    gw.queue_command({'type': 'build_building', 'user_id': 2, 'building_type': 'shipyard'})
    gw._process_commands()

    snapshot = gw.get_player_data(2)
    assert len(snapshot["build_queue"]) == 0



def test_build_prerequisites_allow_shipyard_with_robot_factory_level2():
    gw = GameWorld()
    res = Resources()  # defaults should cover first shipyard cost
    gw.world.create_entity(
        Player(name="Test", user_id=3),
        Position(),
        res,
        ResourceProduction(),
        Buildings(robot_factory=2),
        BuildQueue(),
        Fleet(),
        Research(),
        Planet(name="Homeworld", owner_id=3),
    )

    gw.queue_command({'type': 'build_building', 'user_id': 3, 'building_type': 'shipyard'})
    gw._process_commands()

    snapshot = gw.get_player_data(3)
    assert len(snapshot["build_queue"]) == 1
    # Verify resources were deducted according to base costs at level 0
    assert snapshot["resources"]["metal"] == 100
    assert snapshot["resources"]["crystal"] == 100
    assert snapshot["resources"]["deuterium"] == 0
