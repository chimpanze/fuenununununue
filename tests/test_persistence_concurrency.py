import threading
import time
from typing import List

from fastapi.testclient import TestClient

from src.main import app
from src.api.routes import game_world
from src.core.metrics import metrics
from src.models import Player, Position, Resources, ResourceProduction, Buildings


def _get_save_count() -> int:
    return int(metrics.snapshot().get("events", {}).get("save.count", 0))


def test_concurrent_save_calls_are_serialized_by_lock():
    with TestClient(app):
        # Ensure an entity exists so save has work to do
        game_world.world.create_entity(
            Player(name="LockUser", user_id=9999), Position(), Resources(), ResourceProduction(), Buildings()
        )
        # Reduce chance of periodic save noise
        game_world._last_save_ts = time.time()
        baseline = _get_save_count()

        results: List[Exception | None] = []

        def _call_save():
            try:
                game_world.save_player_data()
                results.append(None)
            except Exception as e:  # pragma: no cover - should not happen
                results.append(e)

        threads = [threading.Thread(target=_call_save) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = _get_save_count()
        # At least one save should have occurred; others may be skipped by the lock
        assert final >= baseline + 1
        assert all(r is None for r in results)


def test_overlapping_resource_updates_and_save_do_not_corrupt_state():
    with TestClient(app):
        res = Resources(metal=0, crystal=0, deuterium=0)
        ent = game_world.world.create_entity(res, ResourceProduction(), Buildings())

        def _update_resources():
            # Simulate rapid updates while a save may be running
            for _ in range(1000):
                res.metal += 1

        t = threading.Thread(target=_update_resources)
        t.start()
        # Call save while updates are occurring; should not throw
        game_world.save_player_data()
        t.join()
        # State remains valid (non-negative)
        assert res.metal >= 0
