"""Optional live weather and airspace snapshots with bounded, safe fallback."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings, settings
from .schemas import ScenarioSpec
from .store import SessionStore

AWC_METAR = "https://aviationweather.gov/api/data/metar"
AWC_TAF = "https://aviationweather.gov/api/data/taf"
OPENSKY_STATES = "https://opensky-network.org/api/states/all"
OPENSKY_TOKEN = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
VHHH_BOUNDS = {"lamin": 21.85, "lomin": 113.55, "lamax": 22.75, "lomax": 114.55}


async def _get_cached_or_fetch(
    store: SessionStore,
    key: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl_seconds: int = 60,
) -> tuple[Any, str]:
    cached = store.get_cache(key, time.time())
    if cached is not None:
        return cached.get("value"), "cache"
    async with httpx.AsyncClient(timeout=5.5, follow_redirects=True) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        value = response.json()
    store.set_cache(key, time.time() + ttl_seconds, {"value": value})
    return value, "live"


async def _opensky_token(config: Settings) -> str | None:
    if not config.opensky_client_id or not config.opensky_client_secret:
        return None
    async with httpx.AsyncClient(timeout=5.5) as client:
        response = await client.post(
            OPENSKY_TOKEN,
            data={
                "grant_type": "client_credentials",
                "client_id": config.opensky_client_id,
                "client_secret": config.opensky_client_secret,
            },
        )
        response.raise_for_status()
        return response.json().get("access_token")


def _weather_severity(raw: str) -> int:
    text = raw.upper()
    if any(token in text for token in ("+TS", "FC", "SQ")):
        return 5
    if any(token in text for token in ("TS", "+RA", "GR")):
        return 4
    if any(token in text for token in ("RA", "SH", "FG")):
        return 3
    if any(token in text for token in ("BR", "DZ")):
        return 2
    return 1


def _parse_opensky(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    aircraft: list[dict[str, Any]] = []
    for row in (payload or {}).get("states") or []:
        if len(row) < 14:
            continue
        aircraft.append({
            "icao24": row[0],
            "callsign": (row[1] or "").strip() or row[0],
            "country": row[2],
            "longitude": row[5],
            "latitude": row[6],
            "altitude_m": row[7],
            "on_ground": bool(row[8]),
            "velocity_mps": row[9],
            "heading": row[10],
            "vertical_rate": row[11],
            "last_contact": row[4],
        })
    return aircraft[:80]


async def fetch_live_snapshot(
    store: SessionStore,
    config: Settings = settings,
    icao: str = "VHHH",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "airport": icao,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "metar": None,
        "taf": None,
        "aircraft": [],
        "sources": {},
        "warnings": [],
    }
    try:
        metar, source = await _get_cached_or_fetch(
            store,
            f"awc:metar:{icao}",
            AWC_METAR,
            params={"ids": icao, "format": "json"},
            ttl_seconds=60,
        )
        result["metar"] = metar[0] if isinstance(metar, list) and metar else metar
        result["sources"]["metar"] = source
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        result["warnings"].append(f"METAR 暂不可用：{type(exc).__name__}")
    try:
        taf, source = await _get_cached_or_fetch(
            store,
            f"awc:taf:{icao}",
            AWC_TAF,
            params={"ids": icao, "format": "json"},
            ttl_seconds=300,
        )
        result["taf"] = taf[0] if isinstance(taf, list) and taf else taf
        result["sources"]["taf"] = source
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        result["warnings"].append(f"TAF 暂不可用：{type(exc).__name__}")
    try:
        token = await _opensky_token(config)
        headers = {"Authorization": f"Bearer {token}"} if token else None
        states, source = await _get_cached_or_fetch(
            store,
            "opensky:vhhh:bbox",
            OPENSKY_STATES,
            params=VHHH_BOUNDS,
            headers=headers,
            ttl_seconds=60,
        )
        result["aircraft"] = _parse_opensky(states)
        result["sources"]["airspace"] = source if token else f"{source}-anonymous"
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        result["warnings"].append(f"OpenSky 暂不可用：{type(exc).__name__}")
    return result


def apply_weather_snapshot(scenario: ScenarioSpec, snapshot: dict[str, Any]) -> ScenarioSpec:
    updated = scenario.model_copy(deep=True)
    metar = snapshot.get("metar") or {}
    raw = str(metar.get("rawOb") or metar.get("raw_text") or "")
    if raw:
        updated.weather.raw_report = raw
        updated.weather.source = "AWC METAR"
        updated.weather.observed_at = datetime.now(timezone.utc)
        updated.weather.severity = _weather_severity(raw)
        updated.weather.label = {
            1: "实时：正常",
            2: "实时：轻度低能见度",
            3: "实时：降水/低能见度",
            4: "实时：雷暴或强降水",
            5: "实时：严重对流",
        }[updated.weather.severity]
        visibility = metar.get("visib")
        try:
            if visibility is not None:
                updated.weather.visibility_km = max(.1, min(100, float(visibility) * 1.60934))
        except (TypeError, ValueError):
            pass
    updated.airport = "Hong Kong International (exercise twin)"
    updated.airport_icao = "VHHH"
    return updated


def live_data_badge(snapshot: dict[str, Any]) -> str:
    if snapshot.get("metar") or snapshot.get("aircraft"):
        return "实时位置 + 合成运营数据"
    return "实时数据不可用 · 已降级为合成数据"
