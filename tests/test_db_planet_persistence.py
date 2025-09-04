import asyncio
import time
import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_planet_persisted_and_hydration_matches_db():
    """
    After choose-start, verify that:
    - A planet row exists in the DB for the user, and
    - The ECS-hydrated player position matches the DB values.

    Skips when DB is disabled or not reachable.
    """
    from src.core.database import is_db_enabled, check_database
    from src.core.database import get_readonly_async_session
    from src.models.database import Planet as ORMPlanet
    from src.main import app

    if not is_db_enabled():
        pytest.skip("DB not enabled")

    try:
        db_ok = asyncio.run(check_database())
    except Exception:
        db_ok = False
    if not db_ok:
        pytest.skip("DB not reachable")

    client = TestClient(app)
    with client:
        # Register & login
        username = f"dbcheck_{int(time.time() * 1000)}"
        r = client.post(
            "/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": "P@ssw0rd!"},
        )
        assert r.status_code in (200, 201), r.text
        user_id = int(r.json()["id"])
        r = client.post("/auth/login", json={"username": username, "password": "P@ssw0rd!"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Pick a free coordinate
        r = client.get("/planets/available", params={"galaxy": 1, "system": 1, "limit": 1})
        assert r.status_code == 200, r.text
        coord = r.json()["available"][0]

        # Choose start
        payload = {
            "galaxy": coord["galaxy"],
            "system": coord["system"],
            "position": coord["position"],
            "name": "Starter",
        }
        r = client.post(f"/player/{user_id}/choose-start", json=payload, headers=headers)
        assert r.status_code == 200, r.text

        # Query DB to confirm persistence
        async def _fetch_planet():
            async for session in get_readonly_async_session():
                result = await session.execute(
                    ORMPlanet.__table__.select().where(ORMPlanet.owner_id == user_id)
                )
                rows = result.all()
                return rows
        rows = asyncio.run(_fetch_planet())
        assert rows, "Expected a persisted planet row"
        # SQLAlchemy Core Row -> mapping
        db_row = rows[0]._mapping
        assert db_row["galaxy"] == coord["galaxy"]
        assert db_row["system"] == coord["system"]
        assert db_row["position"] == coord["position"]

        # Now confirm ECS-hydrated values via GET /player/{id}
        r = client.get(f"/player/{user_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        pos = body.get("position", {})
        assert pos.get("galaxy") == coord["galaxy"]
        assert pos.get("system") == coord["system"]
        assert pos.get("position") == coord["position"]
