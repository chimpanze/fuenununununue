from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient, username: str = "fleetuser1", email: str = "fleet1@example.com", password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_fleet_dispatch_endpoint_accepts_basic_payload():
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="fleetdisp", email="fleetdisp@example.com")
        r = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "galaxy": 1,
                "system": 1,
                "position": 2,
                "mission": "transfer",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("message", "").startswith("Fleet dispatch queued")
        assert body.get("target", {}).get("position") == 2


def test_fleet_dispatch_rejects_invalid_coordinates():
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="fleetdisp2", email="fleetdisp2@example.com")
        r = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "galaxy": 0,
                "system": 1,
                "position": 2,
                "mission": "transfer",
            },
        )
        assert r.status_code == 400
        assert "Invalid coordinates" in r.text
