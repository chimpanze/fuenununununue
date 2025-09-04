import asyncio
from unittest.mock import patch, MagicMock

import src.core.sync as sync


def test_submit_uses_run_coroutine_threadsafe_when_loop_set():
    loop = asyncio.new_event_loop()
    prev = getattr(sync, "_persistence_loop", None)
    try:
        sync.set_persistence_loop(loop)
        coro = object()  # avoid creating a real coroutine; we only assert scheduling
        with patch("asyncio.run_coroutine_threadsafe") as run_threadsafe:
            sync._submit(coro, op="unit-test")
            run_threadsafe.assert_called_once()
            args, kwargs = run_threadsafe.call_args
            assert args[0] is coro
            assert args[1] is loop
    finally:
        # restore prev loop and close
        sync._persistence_loop = prev  # type: ignore[attr-defined]
        try:
            loop.close()
        except Exception:
            pass


def test_submit_noop_when_loop_missing():
    # Ensure no exception and no call when loop is not set
    prev = getattr(sync, "_persistence_loop", None)
    try:
        sync._persistence_loop = None  # type: ignore[attr-defined]
        coro = object()
        with patch("asyncio.run_coroutine_threadsafe") as run_threadsafe:
            sync._submit(coro, op="no-loop")
            run_threadsafe.assert_not_called()
    finally:
        sync._persistence_loop = prev  # type: ignore[attr-defined]


essential_timeout = 0.01


def test_submit_and_wait_returns_result():
    loop = asyncio.new_event_loop()
    prev = getattr(sync, "_persistence_loop", None)
    try:
        sync.set_persistence_loop(loop)

        fake_coro = object()

        with patch("asyncio.run_coroutine_threadsafe") as run_threadsafe:
            # Create a fake future that returns quickly
            fut = MagicMock()
            fut.result.return_value = 42
            run_threadsafe.return_value = fut
            res = sync._submit_and_wait(fake_coro, timeout=essential_timeout, op="wait-test")
            assert res == 42
            run_threadsafe.assert_called_once()
            args, _ = run_threadsafe.call_args
            assert args[0] is fake_coro
            assert args[1] is loop
    finally:
        sync._persistence_loop = prev  # type: ignore[attr-defined]
        try:
            loop.close()
        except Exception:
            pass


def test_submit_and_wait_noop_when_loop_missing():
    prev = getattr(sync, "_persistence_loop", None)
    try:
        sync._persistence_loop = None  # type: ignore[attr-defined]
        fake_coro = object()
        with patch("asyncio.run_coroutine_threadsafe") as run_threadsafe:
            res = sync._submit_and_wait(fake_coro, timeout=essential_timeout, default=None, op="no-loop-wait")
            assert res is None
            run_threadsafe.assert_not_called()
    finally:
        sync._persistence_loop = prev  # type: ignore[attr-defined]
