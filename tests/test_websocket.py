from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient, username: str = "wsuser", email: str = "ws@example.com", password: str = "Password123!") -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_websocket_welcome_and_ping():
    with TestClient(app) as client:
        uid, token = _register_and_login(client)
        with client.websocket_connect(f"/ws?token={token}") as websocket:
            # Receive welcome message
            msg = websocket.receive_json()
            assert msg["type"] == "welcome"
            assert msg["user_id"] == uid
            # Send ping and expect pong
            websocket.send_text("ping")
            msg2 = websocket.receive_json()
            assert msg2["type"] == "pong"
            assert "server_time" in msg2
