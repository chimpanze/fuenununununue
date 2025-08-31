# Load Testing with Locust

This directory contains a Locust script to simulate 1000+ concurrent players interacting with the API.

Files:
- locustfile.py — Simulates user registration/login and common gameplay actions.

Prerequisites:
- Ensure the API server is running, e.g.:
  uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
- Install Locust (kept out of runtime requirements):
  pip install locust

Run (Web UI):
- Start Locust:
  locust -f scripts/load/locustfile.py --host http://127.0.0.1:8000
- Open http://localhost:8089 and configure:
  - Users (spawn count): e.g., 1000 or more
  - Spawn rate: e.g., 50

Run (Headless example, 1000 users):
- Example (run for 5 minutes):
  locust -f scripts/load/locustfile.py \
    --host http://127.0.0.1:8000 \
    --users 1000 --spawn-rate 100 \
    --run-time 5m --headless --loglevel INFO

Environment variables:
- USER_PREFIX: username prefix for generated users (default: load)
- WAIT_MIN / WAIT_MAX: user think-time bounds in seconds (defaults: 0.5 / 1.5)
- BUILDING_TYPES: comma-separated list of building types to mix in requests (default includes metal_mine, crystal_mine, deuterium_synthesizer, solar_plant, robot_factory, shipyard)

Notes:
- The API enforces per-user rate limiting (default: 100 req/min). Adjust WAIT_MIN/WAIT_MAX and user counts accordingly to avoid artificial 429s.
- The script uses FastHttpUser for higher throughput and sets Authorization headers automatically after login.
