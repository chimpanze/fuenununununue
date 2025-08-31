from __future__ import annotations

"""Application performance metrics collection.

Provides a lightweight, thread-safe collector for:
- HTTP API response times and counts by method+route and status code
- Game loop (tick) processing durations

No external dependencies. Exposes a singleton `metrics` for convenient use
throughout the app. Metrics are exported as a JSON-safe dict via snapshot().
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, Any


@dataclass
class Stat:
    """Accumulates basic statistics for durations.

    Stores count, total seconds, min/max seconds, and last observed seconds.
    """

    count: int = 0
    total_s: float = 0.0
    min_s: float = float("inf")
    max_s: float = 0.0
    last_s: float = 0.0

    def add(self, duration_s: float) -> None:
        self.count += 1
        self.total_s += duration_s
        self.last_s = duration_s
        if duration_s < self.min_s:
            self.min_s = duration_s
        if duration_s > self.max_s:
            self.max_s = duration_s

    def as_dict_ms(self) -> Dict[str, float | int]:
        # Present values in milliseconds for readability
        avg_ms = (self.total_s / self.count * 1000.0) if self.count else 0.0
        return {
            "count": self.count,
            "total_ms": self.total_s * 1000.0,
            "avg_ms": avg_ms,
            "min_ms": (self.min_s * 1000.0 if self.count else 0.0),
            "max_ms": self.max_s * 1000.0,
            "last_ms": self.last_s * 1000.0,
        }


class MetricsCollector:
    """Thread-safe in-process metrics collector.

    - HTTP metrics keyed by (method, route_template) with per-status counts.
    - Game loop metrics as a single Stat plus total tick counter.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # HTTP metrics: (method, route) -> Stat and status counts
        self._http_stats: Dict[Tuple[str, str], Stat] = {}
        self._http_status_counts: Dict[Tuple[str, str], Dict[str, int]] = {}
        self._http_total: int = 0

        # Game loop metrics
        self._tick_stats: Stat = Stat()
        self._tick_total: int = 0

        # Process start time
        self._start_monotonic: float = time.monotonic()
        self._start_time_s: float = time.time()

    def record_http(self, method: str, route: str, status_code: int, duration_s: float) -> None:
        key = (method.upper(), route)
        sc = str(status_code)
        with self._lock:
            stat = self._http_stats.get(key)
            if stat is None:
                stat = self._http_stats[key] = Stat()
            stat.add(duration_s)
            self._http_status_counts.setdefault(key, {})
            self._http_status_counts[key][sc] = self._http_status_counts[key].get(sc, 0) + 1
            self._http_total += 1

    def record_tick(self, duration_s: float) -> None:
        with self._lock:
            self._tick_stats.add(duration_s)
            self._tick_total += 1

    def uptime_s(self) -> float:
        return max(0.0, time.monotonic() - self._start_monotonic)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            http_by_route: Dict[str, Dict[str, Any]] = {}
            for (method, route), stat in self._http_stats.items():
                key = f"{method}:{route}"
                http_by_route[key] = {
                    **stat.as_dict_ms(),
                    "status_counts": dict(self._http_status_counts.get((method, route), {})),
                }

            tick = self._tick_stats.as_dict_ms()
            return {
                "process": {
                    "started_at": self._start_time_s,
                    "uptime_s": self.uptime_s(),
                },
                "http": {
                    "total_count": self._http_total,
                    "by_route": http_by_route,
                },
                "game_loop": {
                    "ticks": self._tick_total,
                    **tick,
                },
            }


# Singleton instance exported for app-wide use
metrics = MetricsCollector()

__all__ = ["metrics", "MetricsCollector", "Stat"]
