import threading
from typing import List
from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient):
    r = client.post("/auth/register", json={"username": "threadu", "email": "t@e.com", "password": "Password123!"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    r = client.post("/auth/login", json={"username": "threadu", "password": "Password123!"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return uid, token


def _post_build(client: TestClient, uid: int, token: str, results: List[int]) -> None:
    r = client.post(f"/player/{uid}/build", json={"building_type": "metal_mine"}, headers={"Authorization": f"Bearer {token}"})
    results.append(r.status_code)


def test_concurrent_build_requests_are_handled_thread_safely():
    # Use TestClient context to ensure startup/shutdown
    with TestClient(app) as client:
        uid, token = _register_and_login(client)
        threads: List[threading.Thread] = []
        results: List[int] = []
        for _ in range(10):
            t = threading.Thread(target=_post_build, args=(client, uid, token, results))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        # All requests should be 200 OK
        assert len(results) == 10
        assert all(code == 200 for code in results)
