"""Deterministic passenger-connection assumptions shared by planning and simulation."""

from __future__ import annotations

import hashlib


def connection_buffer_minutes(seed: int, flight_id: str) -> int:
    """Return a stable 18–33 minute transfer buffer without process-randomized hashes."""
    digest = hashlib.blake2s(f"{seed}:{flight_id}".encode(), digest_size=2).digest()
    return 18 + int.from_bytes(digest, "big") % 16


def connection_risk_margin_minutes(weather_severity: int) -> int:
    """Approximate a conservative P90 disruption margin for connection protection."""
    return max(4, min(15, round(6 + weather_severity * 1.5)))


def risk_adjusted_connection_buffer_minutes(raw_buffer_minutes: int, weather_severity: int) -> int:
    """Return usable connection buffer after a conservative weather-risk reserve."""
    return max(5, int(raw_buffer_minutes) - connection_risk_margin_minutes(weather_severity))


def is_tight_connection_group(raw_buffer_minutes: int, weather_severity: int, threshold_minutes: int = 15) -> bool:
    """Flag passenger groups whose P90-adjusted transfer buffer needs active protection."""
    return risk_adjusted_connection_buffer_minutes(raw_buffer_minutes, weather_severity) <= threshold_minutes
