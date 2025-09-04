import asyncio
from unittest.mock import patch

import src.core.sync as sync


def test_persistence_no_loop_set_is_handled_gracefully():
    # Simulate DB enabled (if disabled, wrapper returns early anyway)
    # Force loop to None and ensure no exception and no scheduling
    prev = getattr(sync, "_persistence_loop", None)
    try:
        sync._persistence_loop = None  # type: ignore[attr-defined]
        with patch("asyncio.run_coroutine_threadsafe") as run_threadsafe:
            # Call a public wrapper that uses _submit
            sync.sync_planet_resources_payload(
                user_id=1,
                username="u",
                galaxy=1,
                system=1,
                position=1,
                planet_name="Home",
                metal=0,
                crystal=0,
                deuterium=0,
                metal_rate=0.0,
                crystal_rate=0.0,
                deuterium_rate=0.0,
                last_update="1970-01-01T00:00:00Z",
            )
            run_threadsafe.assert_not_called()
    finally:
        sync._persistence_loop = prev  # type: ignore[attr-defined]
