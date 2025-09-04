from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union


def utc_now() -> datetime:
    """Return current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure the given datetime is timezone-aware in UTC.

    - If dt is None, returns None.
    - If dt is naive, interpret it as local time and convert to UTC.
    - If dt has a timezone, convert to UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Treat naive timestamps as local time, then convert to UTC
        try:
            local_tz = datetime.now().astimezone().tzinfo
            return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
        except Exception:
            # Fallback: assume UTC if local tz resolution fails
            return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def isoformat_utc(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to RFC3339/ISO8601 string with 'Z' suffix for UTC.

    Returns None if dt is None.
    """
    if dt is None:
        return None
    dt_utc = ensure_aware_utc(dt)
    # Use 'Z' for UTC
    s = dt_utc.isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def parse_utc(value: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Parse an ISO8601 string (supporting trailing 'Z') or pass-through datetime into aware UTC.

    Returns None if value is None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_aware_utc(value)
    s = str(value).strip()
    if not s:
        return None
    # Normalize trailing 'Z' to +00:00 for fromisoformat
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        # Fallback: try parsing without timezone; assume UTC
        try:
            dt = datetime.fromisoformat(s.replace('Z', ''))
        except Exception:
            return None
    return ensure_aware_utc(dt)


__all__ = [
    "utc_now",
    "ensure_aware_utc",
    "isoformat_utc",
    "parse_utc",
]
