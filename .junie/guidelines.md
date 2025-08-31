# Project Development Guidelines (.junie)

This document collects practical, project-specific guidance for building, testing, and extending the current repository. It assumes an experienced developer audience and focuses on details unique to this codebase.

Repository layout (current):
- server.py — Monolithic FastAPI app with an ECS-like game loop built on `esper`.
- docs/tasks.md — Roadmap/TODO list for the intended architecture and features.

See docs/tasks.md for the broader plan; this file captures how to work effectively with what exists today and how to evolve it safely.


## 1) Build / Configuration Instructions

Environment:
- Python: 3.10+ recommended.
- Optional tools: uv, venv, or pipx for environment management.

Dependencies (runtime):
- fastapi
- uvicorn
- esper

Install minimal runtime deps (examples):
- Standard venv:
  - python -m venv .venv
  - source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
  - pip install --upgrade pip
  - pip install fastapi uvicorn esper

Run the server:
- Using uvicorn CLI (preferred during dev):
  - uvicorn server:app --host 0.0.0.0 --port 8000 --reload
- Or run as a script (server.py has a __main__ guard):
  - python server.py

Endpoints (non-exhaustive):
- GET / — health banner.
- GET /player/{user_id}
- POST /player/{user_id}/build — queues a building for construction.
- GET /building-costs/{building_type}
- GET /game-status

CORS:
- Broadly allowed for now (allow_origins=["*"]). Tighten for production.

Process/loop model:
- A global GameWorld is instantiated at import time.
- The game loop starts on FastAPI startup event and runs in a daemon thread at ~1 Hz (tick_rate=1.0).
- Commands are pushed via HTTP to a thread-safe Queue and consumed each tick.

Important quirk regarding `esper`:
- server.py currently sets `self.world = esper` and then uses world methods like `add_processor`, `create_entity`, `get_components`. `esper`’s canonical usage is via `esper.World()`. If you encounter an AttributeError at runtime, adjust the world initialization to `self.world = esper.World()`. This guideline does not alter code, but flags the likely fix.

Logging:
- Configured at INFO level at import; tune as needed via logging.basicConfig or uvicorn log config.


## 2) Testing Information

Two practical testing setups are supported; choose based on your environment constraints.

A) Zero-dependency smoke tests with unittest (built-in):
- Use when you cannot or do not wish to install FastAPI/uvicorn/esper.
- Discovery pattern: test files under tests/ matching test_*.py.
- Run:
  - python -m unittest discover -s tests -p 'test_*.py' -q

Adding a new unittest:
- Create tests/test_something.py with:
  - import unittest
  - Write tests that do not import heavy external deps unless installed.

Example that was validated in this repo (then removed as per instructions):

```
# File: tests/test_smoke.py
import unittest
from pathlib import Path

class TestRepositorySmoke(unittest.TestCase):
    def test_docs_tasks_exists_and_has_project_structure(self):
        root = Path(__file__).resolve().parents[1]
        tasks = root / "docs" / "tasks.md"
        self.assertTrue(tasks.exists())
        content = tasks.read_text(encoding="utf-8")
        self.assertIn("Project Structure", content)

    def test_server_file_present_and_mentions_fastapi(self):
        root = Path(__file__).resolve().parents[1]
        server = root / "server.py"
        self.assertTrue(server.exists())
        text = server.read_text(encoding="utf-8")
        self.assertIn("FastAPI(", text)
```

- This test suite was executed successfully via unittest discovery before documenting it here.

B) Full API tests (requires deps):
- Once fastapi/uvicorn installed, you can use FastAPI’s TestClient (requests-based) or httpx.
- Example with TestClient:

```
from fastapi.testclient import TestClient
import server

def test_root():
    client = TestClient(server.app)
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "running"
```

- For async endpoints and more control, prefer httpx.AsyncClient.

Notes on test isolation & the game loop:
- The loop starts only on app startup (FastAPI event). Importing server does not start it; creating TestClient triggers startup/shutdown events by default.
- If you need to avoid threads in tests, you can monkeypatch GameWorld.start_game_loop / stop_game_loop to no-ops.

Optional: pytest
- pip install pytest
- pytest -q
- If mixing unittest-style tests with pytest is desired, it will discover them by default.


## 3) Additional Development Information

Architecture reality vs. plan:
- Current code is monolithic (server.py). docs/tasks.md outlines the target modular structure (src/, tests/, migrations/, config/…).
- When starting that migration, keep server.py as the integration façade (e.g., src/api/main.py re-export app) to avoid breaking external run commands.

Concurrency & lifecycle:
- The game loop runs in a daemon thread; ensure clean shutdown (startup/shutdown events in FastAPI already call stop_game_loop).
- Commands are queued via Queue; design commands as small, idempotent operations.
- Avoid long blocking operations in processors; prefer async handoff, or increase tick granularity.

Calculations & systems:
- Tick rate is 1 Hz; resource production uses last_update timestamp and building levels with a 1.1^level multiplier.
- Build queue completes when current_time >= completion_time; completion increments building level.

Style & typing:
- Follow PEP 8; prefer type hints across public APIs.
- Use module-level logger = logging.getLogger(__name__) and structured log messages for key events.

Configuration:
- No config module exists yet. If you add one, centralize: tick rate, production rates, base costs, and CORS.
- Consider environment variables with sensible defaults; wire via a small config module to keep server.py clean.

Testing strategy going forward:
- Keep fast unit tests that don’t require the event loop or network; test processors and calculations in isolation.
- For API tests, use TestClient; prefer short-lived clients inside tests to ensure startup/shutdown fire per test module.
- Use fixtures (pytest) or setUp/tearDown (unittest) to reset GameWorld state between tests.

Known pitfalls to watch for:
- `esper` world initialization (see note above) — fix early if you start running the server.
- Global state in GameWorld may complicate parallel tests; consider refactoring to a factory for per-test worlds.

Migration pointers (aligning with docs/tasks.md):
- Introduce src/ with modules: src/core/, src/models/, src/systems/, src/api/; move server.py logic incrementally.
- Add requirements.txt (fastapi, uvicorn, esper, pytest, httpx, etc.) and pin minimum versions.
- Add pytest.ini and tests/ with unit and integration layers as outlined in docs/tasks.md.
- Consider Docker/compose later for DB and service orchestration.

---

Operational checklist for new contributors:
- Create venv and install fastapi/uvicorn/esper if you plan to run the server.
- Use unittest discovery for zero-dependency smoke tests; switch to pytest after adding it to the stack.
- Be mindful of the global GameWorld and threaded loop in tests.
- Consult docs/tasks.md for prioritized next steps.
