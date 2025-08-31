import time
from fastapi.testclient import TestClient
from src.main import app


def test_metrics_http_and_ticks():
    with TestClient(app) as client:
        # Trigger at least one HTTP request
        r = client.get("/")
        assert r.status_code == 200

        # Fetch metrics snapshot
        r = client.get("/metrics")
        assert r.status_code == 200
        snap = r.json()
        assert "http" in snap and "game_loop" in snap
        assert snap["http"]["total_count"] >= 1
        assert isinstance(snap["http"]["by_route"], dict)

        # Wait until at least one game loop tick is recorded
        deadline = time.time() + 2.5
        ticks = snap["game_loop"].get("ticks", 0)
        while ticks < 1 and time.time() < deadline:
            time.sleep(0.2)
            ticks = client.get("/metrics").json()["game_loop"].get("ticks", 0)
        assert ticks >= 1
