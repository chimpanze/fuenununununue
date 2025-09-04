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
    Also retains a bounded sample window to estimate tail latencies (p95/p99).
    """

    count: int = 0
    total_s: float = 0.0
    min_s: float = float("inf")
    max_s: float = 0.0
    last_s: float = 0.0
    # Bounded recent samples for percentile estimation
    _samples: list[float] = field(default_factory=list)
    _max_samples: int = 256

    def add(self, duration_s: float) -> None:
        self.count += 1
        self.total_s += duration_s
        self.last_s = duration_s
        if duration_s < self.min_s:
            self.min_s = duration_s
        if duration_s > self.max_s:
            self.max_s = duration_s
        # Maintain bounded sample buffer
        self._samples.append(duration_s)
        if len(self._samples) > self._max_samples:
            # Drop oldest
            self._samples.pop(0)

    def _percentile_ms(self, p: float) -> float:
        if not self._samples:
            return 0.0
        data = sorted(self._samples)
        k = max(0, min(len(data) - 1, int(round((p / 100.0) * (len(data) - 1)))))
        return data[k] * 1000.0

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
            "p95_ms": self._percentile_ms(95.0),
            "p99_ms": self._percentile_ms(99.0),
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
        # Tick jitter metrics (absolute deviation between planned and actual start)
        self._tick_jitter: Stat = Stat()

        # DB/event counters (e.g., persistence operations)
        self._events: Dict[str, int] = {}
        # Generic timers by name (durations recorded as Stat)
        self._timers: Dict[str, Stat] = {}

        # Process start time
        self._start_monotonic: float = time.monotonic()
        self._start_time_s: float = time.time()

    def increment_event(self, key: str, count: int = 1) -> None:
        """Increment a named event counter by count (default 1).

        Safe to call from any thread; keys are arbitrary strings like
        'db.trade_event_recorded' or 'db.ship_build_completed'.
        """
        if not key:
            return
        with self._lock:
            self._events[key] = int(self._events.get(key, 0)) + int(count)

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

    def record_tick(self, duration_s: float, jitter_s: Optional[float] = None) -> None:
        with self._lock:
            self._tick_stats.add(duration_s)
            if jitter_s is not None:
                # store absolute jitter magnitude for readability
                self._tick_jitter.add(abs(jitter_s))
            self._tick_total += 1

    def record_timer(self, name: str, duration_s: float) -> None:
        """Record a one-shot timer duration under the given name.

        Useful for operations like 'save.duration_s' or 'autoload.duration_s'.
        """
        if not name:
            return
        with self._lock:
            stat = self._timers.get(name)
            if stat is None:
                stat = self._timers[name] = Stat()
            stat.add(float(duration_s))

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
            jitter = self._tick_jitter.as_dict_ms()
            # Timers snapshot
            timers_by_name: Dict[str, Dict[str, Any]] = {}
            for name, stat in self._timers.items():
                timers_by_name[name] = stat.as_dict_ms()

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
                    "jitter": jitter,
                },
                "events": dict(self._events),
                "timers": timers_by_name,
            }


# Singleton instance exported for app-wide use
metrics = MetricsCollector()

__all__ = ["metrics", "MetricsCollector", "Stat"]
