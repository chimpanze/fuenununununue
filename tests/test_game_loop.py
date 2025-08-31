import time
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from src.main import app
from src.api.routes import game_world
from src.models import Resources, ResourceProduction, Buildings, Player, Position, BuildQueue, Fleet, Research, Planet


def test_game_loop_starts_and_stops():
    # Lifespan should start the loop
    with TestClient(app) as client:
        assert game_world.running is True
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["status"] == "running"
    # After context, loop is stopped
    assert game_world.running is False


def test_command_queue_processing_updates_activity():
    with TestClient(app):
        # Ensure a player exists in ECS
        game_world.world.create_entity(
            Player(name="LoopUser", user_id=1), Position(), Resources(), ResourceProduction(), Buildings(), BuildQueue(), Fleet(), Research(), Planet(name="Homeworld", owner_id=1)
        )
        before = datetime.now()
        # Enqueue a simple activity update command
        game_world.queue_command({
            "type": "update_player_activity",
            "user_id": 1,
        })
        time.sleep(1.2)  # allow at least one tick
        data = game_world.get_player_data(1)
        assert data is not None
        last_active = datetime.fromisoformat(data["player"]["last_active"])
        assert last_active >= before


def test_systems_execute_during_loop():
    # Create an entity with past production timestamp so that processing increases resources
    with TestClient(app):
        # Create a throwaway entity for production measurement
        res = Resources(metal=0, crystal=0, deuterium=0)
        prod = ResourceProduction(metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
                                  last_update=datetime.now() - timedelta(hours=1))
        bld = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1)
        _ = game_world.world.create_entity(res, prod, bld)
        # Allow one loop tick
        time.sleep(1.2)
        # Resources should have increased due to ResourceProductionSystem
        assert res.metal > 0
        assert res.crystal > 0
        assert res.deuterium > 0
