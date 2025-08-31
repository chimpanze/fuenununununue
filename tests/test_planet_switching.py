import time as _t
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.core.database import is_db_enabled


pytestmark = pytest.mark.skipif(not is_db_enabled(), reason="Planet switching requires database layer enabled")


def _register_and_login(client: TestClient, username: str, email: str, password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_switch_active_planet_after_colonization():
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="switch_user", email="switch@example.com")

        # Queue building one colony ship
        r = client.post(
            f"/player/{uid}/build-ships",
            headers={"Authorization": f"Bearer {token}"},
            json={"ship_type": "colony_ship", "quantity": 1},
        )
        assert r.status_code == 200, r.text

        # Wait for shipyard to complete the colony ship
        _t.sleep(1.5)

        # Dispatch colonize mission to position 2 in the same system
        r = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={"galaxy": 1, "system": 1, "position": 2, "mission": "colonize"},
        )
        assert r.status_code == 200, r.text

        # Wait for travel + colonization completion
        _t.sleep(2.2)

        # List planets to find the new planet's ID
        r = client.get(f"/player/{uid}/planets", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        planets = r.json().get("planets", [])
        assert len(planets) >= 2  # homeworld + new colony
        target = None
        for p in planets:
            if p.get("galaxy") == 1 and p.get("system") == 1 and p.get("position") == 2:
                target = p
                break
        assert target is not None, "Newly colonized planet not found in listing"
        planet_id = target.get("id")
        assert isinstance(planet_id, int), "Planet ID must be present when DB is enabled"

        # Switch active planet to the newly colonized one
        r = client.post(
            f"/player/{uid}/planets/{planet_id}/select",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("planet_id") == planet_id
        pos = body.get("position", {})
        assert pos.get("galaxy") == 1 and pos.get("system") == 1 and pos.get("planet") == 2

        # Confirm via /player snapshot
        r = client.get(f"/player/{uid}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        position = data.get("position", {})
        assert position.get("galaxy") == 1
        assert position.get("system") == 1
        assert position.get("planet") == 2
