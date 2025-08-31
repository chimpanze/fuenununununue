from fastapi.testclient import TestClient
import time as _t

from src.main import app


def _register_and_login(client: TestClient, username: str = "colonist", email: str = "colonist@example.com", password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_build_colony_ship_and_colonize():
    with TestClient(app) as client:
        uid, token = _register_and_login(client)

        # Queue building one colony ship
        r = client.post(
            f"/player/{uid}/build-ships",
            headers={"Authorization": f"Bearer {token}"},
            json={"ship_type": "colony_ship", "quantity": 1},
        )
        assert r.status_code == 200, r.text

        # Wait for shipyard system to complete (base time ~1s per config)
        _t.sleep(1.5)

        # Verify colony ship present in fleet
        r = client.get(f"/player/{uid}/fleet", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        data = r.json()
        fleet = data.get("fleet", {})
        assert fleet.get("colony_ship", 0) >= 1

        # Dispatch a colonize mission to adjacent position
        r = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={"galaxy": 1, "system": 1, "position": 2, "mission": "colonize"},
        )
        assert r.status_code == 200, r.text

        # Wait for travel + colonization time to elapse
        _t.sleep(2.2)

        # After colonization completes, the colony ship should be decremented
        r = client.get(f"/player/{uid}/fleet", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        after_fleet = r.json().get("fleet", {})
        assert after_fleet.get("colony_ship", 0) == max(0, fleet.get("colony_ship", 0) - 1)
