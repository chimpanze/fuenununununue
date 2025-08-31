from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient, username: str, email: str, password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_get_player_planets_lists_homeworld():
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="pp_user1", email="pp1@example.com")
        r = client.get(f"/player/{uid}/planets", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "planets" in body
        planets = body["planets"]
        assert isinstance(planets, list)
        assert len(planets) >= 1
        # Check key fields exist in first item (DB-enabled may include id/last_update)
        p0 = planets[0]
        assert "name" in p0 and "galaxy" in p0 and "system" in p0 and "position" in p0
        assert "resources" in p0 and all(k in p0["resources"] for k in ("metal", "crystal", "deuterium"))


def test_get_player_planets_forbidden_on_user_mismatch():
    with TestClient(app) as client:
        uid1, token1 = _register_and_login(client, username="pp_user2", email="pp2@example.com")
        uid2, token2 = _register_and_login(client, username="pp_user3", email="pp3@example.com")
        # Use user2 token to access user1 planets -> should be 403
        r = client.get(f"/player/{uid1}/planets", headers={"Authorization": f"Bearer {token2}"})
        assert r.status_code == 403, r.text
