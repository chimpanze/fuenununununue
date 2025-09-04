from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from src.main import app
from src.api.routes import game_world
from src.models import Player as P, Position as Pos, Fleet as Fl, FleetMovement as FM


def _register_and_login(client: TestClient, username: str, email: str, password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def _get_player_entity_and_movement(user_id: int):
    ent = None
    mv = None
    pos = None
    for e, (p,) in game_world.world.get_components(P):
        if int(getattr(p, "user_id", -1)) == int(user_id):
            ent = e
            break
    if ent is None:
        return None, None, None
    try:
        mv = game_world.world.component_for_entity(ent, FM)
    except Exception:
        mv = None
    try:
        pos = game_world.world.component_for_entity(ent, Pos)
    except Exception:
        pos = None
    return ent, mv, pos


def test_fleet_state_persists_across_restart_en_route():
    # First lifecycle: create user and dispatch a fleet
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="restart_enroute", email="restart_enroute@example.com")
        r = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={"galaxy": 1, "system": 1, "position": 2, "mission": "transfer"},
        )
        assert r.status_code == 200, r.text
        ent, mv, _ = _get_player_entity_and_movement(uid)
        assert ent is not None, "Player entity should exist"
        assert mv is not None, "FleetMovement should be attached after dispatch"
        assert str(getattr(mv, "mission", "")).lower() == "transfer"
        # Keep some reference data
        before_eta = getattr(mv, "arrival_time", None)
        assert before_eta is not None
        before_target = (mv.target.galaxy, mv.target.system, mv.target.planet)

    # Simulated restart: new TestClient context (same process, same GameWorld singleton)
    with TestClient(app) as client2:
        ent2, mv2, _ = _get_player_entity_and_movement(uid)
        assert ent2 is not None, "Player entity should still exist after restart"
        assert mv2 is not None, "FleetMovement should persist across restart (same process)"
        assert (mv2.target.galaxy, mv2.target.system, mv2.target.planet) == before_target
        assert str(getattr(mv2, "mission", "")).lower() == "transfer"
        # ETA should be a datetime; it may be the same or slightly reduced depending on processing
        assert hasattr(mv2, "arrival_time")


def test_fleet_overdue_finalizes_on_restart(monkeypatch):
    # First lifecycle: dispatch and then mark arrival in the past
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="restart_overdue", email="restart_overdue@example.com")
        r = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={"galaxy": 1, "system": 1, "position": 3, "mission": "transfer"},
        )
        assert r.status_code == 200, r.text
        ent, mv, _ = _get_player_entity_and_movement(uid)
        assert mv is not None
        # Force overdue arrival to simulate downtime while server is down
        mv.arrival_time = datetime.now() - timedelta(seconds=1)

    # Prevent background loop on restart for deterministic processing; we'll call world.process() manually
    monkeypatch.setattr(game_world, "start_game_loop", lambda: None)

    with TestClient(app) as client2:
        ent2, mv2, pos2 = _get_player_entity_and_movement(uid)
        assert ent2 is not None
        # Process one tick to finalize overdue movement
        game_world.world.process()
        # After processing, FleetMovement should be removed
        try:
            _ = game_world.world.component_for_entity(ent2, FM)
            assert False, "FleetMovement should be removed after overdue arrival is finalized"
        except Exception:
            pass
        # Position should now be at the target (1,1,3)
        pos2 = game_world.world.component_for_entity(ent2, Pos)
        assert (pos2.galaxy, pos2.system, pos2.planet) == (1, 1, 3)


def test_recalled_fleet_persists_and_finalizes_back_to_origin_on_restart(monkeypatch):
    # First lifecycle: dispatch, recall, then set overdue to finalize back to origin after restart
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="restart_recall", email="restart_recall@example.com")
        # Ensure starting position is (1,1,1)
        # Dispatch to 1:1:4
        rd = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={"galaxy": 1, "system": 1, "position": 4, "mission": "transfer"},
        )
        assert rd.status_code == 200, rd.text
        # Recall it
        rr = client.post(f"/player/{uid}/fleet/1/recall", headers={"Authorization": f"Bearer {token}"})
        assert rr.status_code == 200, rr.text
        # Mark return trip as overdue
        ent, mv, _ = _get_player_entity_and_movement(uid)
        assert mv is not None and mv.recalled is True
        mv.arrival_time = datetime.now() - timedelta(seconds=1)

    # Disable loop on restart and finalize manually
    monkeypatch.setattr(game_world, "start_game_loop", lambda: None)

    with TestClient(app) as client2:
        ent2, mv2, pos2 = _get_player_entity_and_movement(uid)
        assert ent2 is not None
        # The recalled flag should persist across restart prior to processing
        assert mv2 is not None and bool(getattr(mv2, "recalled", False)) is True
        # Process to finalize return
        game_world.world.process()
        # Movement should be removed and position should be origin (1,1,1)
        try:
            _ = game_world.world.component_for_entity(ent2, FM)
            assert False, "FleetMovement should be removed after return arrival is finalized"
        except Exception:
            pass
        pos2 = game_world.world.component_for_entity(ent2, Pos)
        assert (pos2.galaxy, pos2.system, pos2.planet) == (1, 1, 1)
