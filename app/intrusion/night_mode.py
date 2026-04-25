"""Night-mode windowing utility for intrusion detection.

Zones can be marked `is_night_only=True`. Those zones only trigger alarms
when the current time falls within the configured night window.

The window may wrap midnight (e.g. "22:00" -> "07:00" is valid).
"""

from __future__ import annotations

from datetime import datetime, time


def _parse_hhmm(s: str) -> time:
    """Parse "HH:MM" into a time object. Raises on invalid input."""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM format: {s!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"Invalid time: {s!r}")
    return time(h, m)


def is_night_mode_active(
    start_hhmm: str,
    end_hhmm: str,
    now: datetime | None = None,
) -> bool:
    """Return True if `now` falls within [start, end).

    Handles midnight wrap correctly:
      - 22:00 → 07:00 means the window spans midnight.
      - 09:00 → 17:00 means a daytime window (no wrap).
      - 00:00 → 00:00 means "always off".

    The window is half-open: start is inclusive, end is exclusive.
    Invalid input strings fall back to "always off" (safer default).
    """
    if now is None:
        now = datetime.now()

    try:
        start = _parse_hhmm(start_hhmm)
        end = _parse_hhmm(end_hhmm)
    except (ValueError, AttributeError):
        return False

    # Degenerate case: equal times -> no window
    if start == end:
        return False

    current = now.time()

    if start < end:
        # Normal window, no wrap
        return start <= current < end
    else:
        # Wraps midnight: active if after start OR before end
        return current >= start or current < end
