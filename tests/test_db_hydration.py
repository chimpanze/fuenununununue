import os
import time
import pytest
from fastapi.testclient import TestClient


def _unique_username(prefix: str = "user") -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
def test_db_backed_hydration_flow_register_choose_start_then_get_player():
    """
    Integration test for DB-backed hydration path.

    Skips automatically when:
    - Database is not enabled (ENABLE_DB != true or DB deps missing), or
    - REQUIRE_START_CHOICE is not enabled (flow differs in that case).
    """
    # Import here to avoid side-effects before potential TestClient startup
    from src.core.database import is_db_enabled
    from src.core.config import REQUIRE_START_CHOICE
    from src.main import app

    if not is_db_enabled() or not REQUIRE_START_CHOICE:
        pytest.skip("DB not enabled or REQUIRE_START_CHOICE is false; skipping DB hydration test")

    client = TestClient(app)
    with client:
        # 1) Register a new user
        username = _unique_username()
        r = client.post(
            "/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": "P@ssw0rd!"},
        )
        assert r.status_code in (200, 201), r.text
        user_id = int(r.json()["id"])

        # 2) Login to get token
        r = client.post("/auth/login", json={"username": username, "password": "P@ssw0rd!"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]

        # 3) Pick a free coordinate (galaxy/system constrained for speed)
        r = client.get("/planets/available", params={"galaxy": 1, "system": 1, "limit": 1})
        assert r.status_code == 200, r.text
        available = r.json().get("available", [])
        assert available, "Expected at least one available coordinate"
        coord = available[0]

        # 4) Choose start (persist in DB)
        payload = {"galaxy": coord["galaxy"], "system": coord["system"], "position": coord["position"], "name": "Starter"}
        r = client.post(f"/player/{user_id}/choose-start", json=payload, headers=_auth_headers(token))
        assert r.status_code == 200, r.text

        # 5) GET /player/{id} should hydrate and return 200
        r = client.get(f"/player/{user_id}", headers=_auth_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("position", {}).get("galaxy") == coord["galaxy"]

        # 6) /game-status should report at least 1 entity
        r = client.get("/game-status")
        assert r.status_code == 200, r.text
        assert int(r.json().get("total_entities", 0)) >= 1
