"""
Locust load testing for the Ogame-like server.

Simulates 1000+ concurrent players that:
- Register (or log in if already registered)
- Retrieve game status and player data
- Queue random building constructions

Usage examples:
  # Start your API server first (in another terminal):
  #   uvicorn src.main:app --host 0.0.0.0 --port 8000
  # Then run Locust pointing to the host:
  #   locust -f scripts/load/locustfile.py --host http://127.0.0.1:8000
  # In the web UI, set Users (spawned) to 1000+ and choose a spawn rate.

Environment variables (optional):
- USER_PREFIX: username prefix used for generated users (default: "load")
- WAIT_MIN: minimum wait time between tasks in seconds (default: 0.5)
- WAIT_MAX: maximum wait time between tasks in seconds (default: 1.5)
- BUILDING_TYPES: comma-separated list of building types to use
  (default: "metal_mine,crystal_mine,deuterium_synthesizer,solar_plant,robot_factory,shipyard")

Notes:
- This script keeps runtime-side changes minimal. Locust is not added to requirements.txt; install it separately:
    pip install locust
- Rate limiting in the API (100 req/min per user) may influence observed error rates under high load.
"""
from __future__ import annotations

import os
import random
import uuid
from typing import List, Optional

from locust import FastHttpUser, task, between


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


DEFAULT_BUILDINGS = [
    "metal_mine",
    "crystal_mine",
    "deuterium_synthesizer",
    "solar_plant",
    "robot_factory",
    "shipyard",
]


class GameUser(FastHttpUser):
    """Simulated game user that authenticates and performs gameplay actions.

    Uses FastHttpUser for higher throughput suitable for 1000+ users.
    """

    wait_time = between(_env_float("WAIT_MIN", 0.1), _env_float("WAIT_MAX", 0.5))

    def on_start(self) -> None:
        self._token: Optional[str] = None
        self.user_id: Optional[int] = None
        self.username: str = f"{os.getenv('USER_PREFIX', 'load')}_{uuid.uuid4().hex[:12]}"
        self.password: str = f"Passw0rd!{uuid.uuid4().hex[:6]}"  # >= 8 chars to satisfy validation
        self.email: str = f"{self.username}@example.com"

        # Try to register; if already exists, proceed to login
        self._register_if_possible()
        self._login()
        # Fallback to /auth/me to discover ID if registration didn't return it
        if self.user_id is None:
            self._whoami()

    # ------------ Auth helpers ------------
    def _register_if_possible(self) -> None:
        payload = {
            "username": self.username,
            "email": self.email,
            "password": self.password,
        }
        with self.client.post("/auth/register", json=payload, name="/auth/register", catch_response=True) as resp:
            # In many runs, this may 400 due to username already taken.
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    self.user_id = int(data.get("id"))
                except Exception:
                    pass
            else:
                resp.success()  # Do not treat 4xx here as failure during load prep

    def _login(self) -> None:
        payload = {"username": self.username, "password": self.password}
        with self.client.post("/auth/login", json=payload, name="/auth/login", catch_response=True) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    token = data.get("access_token")
                    if token:
                        self._token = token
                        self.client.headers.update({"Authorization": f"Bearer {token}"})
                except Exception:
                    pass
            else:
                # If login failed (e.g., registration failed with taken username), try a fresh identity once
                if resp.status_code in (400, 401):
                    # Generate a new identity and try register+login once more
                    self.username = f"{os.getenv('USER_PREFIX', 'load')}_{uuid.uuid4().hex[:12]}"
                    self.password = f"Passw0rd!{uuid.uuid4().hex[:6]}"
                    self.email = f"{self.username}@example.com"
                    self._register_if_possible()
                    # Attempt login again
                    with self.client.post("/auth/login", json={"username": self.username, "password": self.password}, name="/auth/login", catch_response=True) as resp2:
                        if resp2.status_code == 200:
                            try:
                                data2 = resp2.json()
                                token = data2.get("access_token")
                                if token:
                                    self._token = token
                                    self.client.headers.update({"Authorization": f"Bearer {token}"})
                            except Exception:
                                pass
                        else:
                            resp2.success()  # avoid polluting stats with setup failures
                resp.success()

    def _whoami(self) -> None:
        with self.client.get("/auth/me", name="/auth/me", catch_response=True) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    self.user_id = int(data.get("id"))
                except Exception:
                    pass
            else:
                resp.success()

    # ------------ Task definitions ------------
    @task(3)
    def game_status(self) -> None:
        self.client.get("/game-status", name="/game-status")

    @task(3)
    def get_player(self) -> None:
        if self.user_id is not None:
            self.client.get(f"/player/{self.user_id}", name="/player/:id")

    @task(2)
    def maybe_queue_build(self) -> None:
        if self.user_id is None:
            return
        raw = os.getenv("BUILDING_TYPES")
        buildings: List[str] = [b.strip() for b in raw.split(",")] if raw else DEFAULT_BUILDINGS
        building = random.choice(buildings)
        payload = {"building_type": building}
        self.client.post(f"/player/{self.user_id}/build", json=payload, name="/player/:id/build")
