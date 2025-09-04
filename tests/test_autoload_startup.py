from fastapi.testclient import TestClient
from src.main import app
from src.api.routes import game_world


def test_autoload_players_called_before_loop(monkeypatch):
    """Verify that during FastAPI startup lifespan we autoload players
    by calling game_world.load_player_data() before starting the game loop.

    This test does not depend on DB availability; it monkeypatches the methods
    to observe call order.
    """
    calls = {"load_called": False, "order_ok": None}

    # Save originals
    orig_load = game_world.load_player_data
    orig_start = game_world.start_game_loop

    def fake_load(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["load_called"] = True
        # Do not call the original to avoid DB coupling in test
        return None

    def fake_start():  # type: ignore[no-untyped-def]
        # When starting the loop, load should already have been called
        calls["order_ok"] = calls["load_called"]
        return orig_start()

    monkeypatch.setattr(game_world, "load_player_data", fake_load)
    monkeypatch.setattr(game_world, "start_game_loop", fake_start)

    # Trigger startup and shutdown
    with TestClient(app):
        pass

    assert calls["load_called"] is True, "Expected load_player_data to be called on startup"
    assert calls["order_ok"] is True, "Expected load_player_data to be called before start_game_loop"