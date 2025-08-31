from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient, username: str = "user1", email: str = "u1@example.com", password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_root_running():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["message"].startswith("Ogame-like Game Server")


def test_get_player_after_register_and_login():
    with TestClient(app) as client:
        uid, token = _register_and_login(client)
        r = client.get(f"/player/{uid}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["player"]["user_id"] == uid
        assert "resources" in data


def test_building_costs_endpoint_defaults():
    client = TestClient(app)
    r = client.get("/building-costs/metal_mine")
    assert r.status_code == 200
    payload = r.json()
    assert payload["building_type"] == "metal_mine"
    assert payload["level"] == 0
    assert "cost" in payload and "build_time_seconds" in payload


def test_fleet_endpoints_build_ships_and_get_queue():
    import time as _t
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="fleetuser", email="fleet@example.com")
        # Initially, fleet endpoint should exist and return defaults
        r = client.get(f"/player/{uid}/fleet", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "fleet" in body and "ship_build_queue" in body
        # Queue ship build
        r = client.post(
            f"/player/{uid}/build-ships",
            headers={"Authorization": f"Bearer {token}"},
            json={"ship_type": "light_fighter", "quantity": 2},
        )
        assert r.status_code == 200, r.text
        # Allow the background loop to process the queued command
        _t.sleep(0.3)
        r = client.get(f"/player/{uid}/fleet", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        payload = r.json()
        assert any(item.get("type") == "light_fighter" for item in payload.get("ship_build_queue", []))
