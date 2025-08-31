from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from src.main import app
from src.core.state import game_world
from src.models import Battle, Position


def _register_and_login(client: TestClient, username: str, email: str, password: str = "Password123!"):
    r = client.post("/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_battle_report_generated_and_listed():
    # Use context manager to ensure startup/shutdown events run
    with TestClient(app) as client:
        # Register two users
        atk_id, atk_token = _register_and_login(client, "attacker", "atk@example.com")
        def_id, def_token = _register_and_login(client, "defender", "def@example.com")

        # Create a battle scheduled in the past so the loop resolves it quickly
        battle = Battle(
            attacker_id=atk_id,
            defender_id=def_id,
            location=Position(galaxy=1, system=1, planet=1),
            scheduled_time=datetime.now() - timedelta(seconds=1),
            attacker_ships={"light_fighter": 3},
            defender_ships={"light_fighter": 1},
        )
        # Inject into the ECS world
        game_world.world.create_entity(battle)

        # Allow the background loop to process; poll up to ~2 seconds
        import time as _t
        reports = []
        for _ in range(10):
            _t.sleep(0.2)
            r = client.get(
                f"/player/{atk_id}/battle-reports",
                headers={"Authorization": f"Bearer {atk_token}"},
            )
            assert r.status_code == 200, r.text
            reports = r.json().get("reports", [])
            if reports:
                break

        assert len(reports) >= 1
        report = reports[0]
        assert report.get("outcome", {}).get("winner") in {"attacker", "defender", "draw"}
        report_id = report.get("id")
        assert isinstance(report_id, int)

        # Detail fetch by attacker
        r = client.get(
            f"/player/{atk_id}/battle-reports/{report_id}",
            headers={"Authorization": f"Bearer {atk_token}"},
        )
        assert r.status_code == 200, r.text
        detail = r.json()
        assert detail.get("id") == report_id
        assert detail.get("attacker_user_id") == atk_id or detail.get("defender_user_id") == atk_id

        # Defender can also see the report
        r = client.get(
            f"/player/{def_id}/battle-reports/{report_id}",
            headers={"Authorization": f"Bearer {def_token}"},
        )
        assert r.status_code == 200

        # A third user should not be able to access the report
        other_id, other_token = _register_and_login(client, "other", "other@example.com")
        r = client.get(
            f"/player/{other_id}/battle-reports/{report_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert r.status_code == 404
