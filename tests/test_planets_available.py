from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient, username: str = "user_pa", email: str = "pa@example.com", password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_planets_available_basic_filters_and_pagination():
    with TestClient(app) as client:
        # Register to occupy 1:1:1 (either in DB or ECS depending on env)
        _register_and_login(client, username="pa_user1", email="pa1@example.com")

        # Request available planets in galaxy=1, system=1 with small limit
        r = client.get("/planets/available", params={"galaxy": 1, "system": 1, "limit": 5})
        assert r.status_code == 200, r.text
        data = r.json()
        available = data.get("available", [])
        assert isinstance(available, list)
        assert len(available) <= 5
        # Expect that 1:1:1 is excluded, hence the first should be position 2 or greater
        assert all(item["galaxy"] == 1 and item["system"] == 1 for item in available)
        if available:
            assert available[0]["position"] >= 2

        # Test offset moves the window forward
        r2 = client.get("/planets/available", params={"galaxy": 1, "system": 1, "limit": 3, "offset": 3})
        assert r2.status_code == 200
        data2 = r2.json()
        available2 = data2.get("available", [])
        assert len(available2) <= 3
        # If both responses have items, they should differ based on offset
        if available and available2:
            assert available2[0] != available[0]


def test_planets_available_invalid_params():
    client = TestClient(app)
    # Invalid galaxy (0)
    r = client.get("/planets/available", params={"galaxy": 0})
    assert r.status_code == 422  # validation error from FastAPI for ge=1
    # Invalid system (0)
    r = client.get("/planets/available", params={"system": 0})
    assert r.status_code == 422  # validation error from FastAPI for ge=1
    # Limit bounds respected
    r = client.get("/planets/available", params={"limit": 0})
    assert r.status_code == 422  # validation error from FastAPI for ge=1
