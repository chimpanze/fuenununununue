from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient, username: str = "recaller1", email: str = "recaller1@example.com", password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_fleet_recall_without_inflight_returns_400():
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="recall_no_fleet", email="recall_no_fleet@example.com")
        r = client.post(f"/player/{uid}/fleet/1/recall", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400
        assert "No in-flight fleet" in r.text


def test_fleet_recall_after_dispatch_returns_200_and_sets_recalled():
    with TestClient(app) as client:
        uid, token = _register_and_login(client, username="recall_with_fleet", email="recall_with_fleet@example.com")
        # Dispatch a fleet to a nearby coordinate
        rd = client.post(
            f"/player/{uid}/fleet/dispatch",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "galaxy": 1,
                "system": 1,
                "position": 2,
                "mission": "transfer",
            },
        )
        assert rd.status_code == 200, rd.text

        # Now recall it
        rr = client.post(f"/player/{uid}/fleet/1/recall", headers={"Authorization": f"Bearer {token}"})
        assert rr.status_code == 200, rr.text
        body = rr.json()
        assert body.get("recalled") is True
        # return_eta should be provided and be an ISO timestamp string
        assert isinstance(body.get("return_eta"), str)
