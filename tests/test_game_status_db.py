import asyncio
import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_game_status_database_ok_when_db_up():
    """
    Verifies that /game-status reports database.status == "ok" when the
    database layer is enabled AND reachable.

    - Skips when DB is disabled (ENABLE_DB != true or missing deps)
    - Skips when DB is enabled but not reachable in the current environment
    """
    from src.core.database import is_db_enabled, check_database
    from src.main import app

    if not is_db_enabled():
        pytest.skip("DB not enabled; skipping database status check")

    # Determine if the DB is reachable before asserting endpoint state
    try:
        try:
            loop = asyncio.get_event_loop()
            db_ok = loop.run_until_complete(check_database())
        except RuntimeError:
            # No running loop; create a temporary one
            db_ok = asyncio.run(check_database())
    except Exception:
        db_ok = False

    if not db_ok:
        pytest.skip("DB not reachable; skipping database status check")

    client = TestClient(app)
    with client:
        r = client.get("/game-status")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("database", {}).get("status") == "ok"
