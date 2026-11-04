"""HTTP-only source; input validation, freshness and anti-cache requests."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .config import API_URL, SERVERS

log = logging.getLogger(__name__)
UTC = timezone.utc


class APIError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ServerInfo:
    id: int
    name: str
    online: int
    max_slots: int
    responding: bool
    updated_at: datetime
    peak_hour: int | None = None
    peak_day: int | None = None

    def freshness_seconds(self, now: datetime) -> float:
        return (now - self.updated_at).total_seconds()

    def is_fresh(self, now: datetime, max_age_seconds: int) -> bool:
        return -120 <= self.freshness_seconds(now) <= max_age_seconds


@dataclass(frozen=True, slots=True)
class Snapshot:
    servers: dict[int, ServerInfo]
    fetched_at: datetime
    total_servers: int
    responding_servers: int

    @property
    def probable_source_outage(self) -> bool:
        # If most servers suddenly appear offline, do not generate restart alerts.
        return self.total_servers >= 10 and self.responding_servers / self.total_servers < 0.25


def _number(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"invalid {field}")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing updated_at")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid updated_at") from exc
    if ts.tzinfo is None:
        raise ValueError("updated_at must have UTC offset")
    return ts.astimezone(UTC)


def parse_payload(payload: Any, fetched_at: datetime) -> Snapshot:
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise APIError("API returned an unexpected JSON format")
    rows = payload["servers"]
    if not rows:
        raise APIError("API returned an empty server list")
    target: dict[int, ServerInfo] = {}
    all_status: list[bool] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        if type(item.get("responding")) is bool:
            all_status.append(item["responding"])
        sid = item.get("id")
        if type(sid) is not int or sid not in SERVERS:
            continue
        try:
            status = item.get("responding")
            if type(status) is not bool:
                raise ValueError("invalid responding")
            target[sid] = ServerInfo(
                id=sid,
                name=SERVERS[sid],
                online=_number(item.get("online"), "online"),
                max_slots=_number(item.get("max_slots"), "max_slots", minimum=1),
                responding=status,
                updated_at=_timestamp(item.get("updated_at")),
                peak_hour=(item["peak_hour"] if type(item.get("peak_hour")) is int else None),
                peak_day=(item["peak_day"] if type(item.get("peak_day")) is int else None),
            )
            if target[sid].online > target[sid].max_slots:
                raise ValueError("online exceeds max_slots")
        except (ValueError, TypeError) as exc:
            log.warning("Ignoring invalid server entry %s: %s", sid, exc)
            target.pop(sid, None)
    if not target:
        raise APIError("API did not return valid Yuma or Show Low data")
    return Snapshot(target, fetched_at, len(all_status), sum(all_status))


class APIClient:
    """Shared short throttle to avoid flooding source when users spam buttons."""

    def __init__(self, session: aiohttp.ClientSession, min_interval: float = 4.0):
        self.session = session
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self.last_snapshot: Snapshot | None = None
        self.last_error: str | None = None
        self.last_attempt_monotonic = 0.0
        self.last_attempt_at: datetime | None = None

    async def fetch(self) -> Snapshot:
        async with self._lock:
            now_mono = time.monotonic()
            if now_mono - self.last_attempt_monotonic < self.min_interval:
                if self.last_error is not None:
                    raise APIError("Сервис мониторинга временно недоступен")
                if self.last_snapshot is not None:
                    return self.last_snapshot
            self.last_attempt_monotonic = now_mono
            self.last_attempt_at = datetime.now(UTC)
            try:
                # Upstream proxies are allowed to ignore no-cache; updated_at exposes this.
                async with self.session.get(
                    API_URL,
                    params={"_": str(time.time_ns())},
                    headers={
                        "Accept": "application/json",
                        "Cache-Control": "no-cache, no-store, max-age=0",
                        "Pragma": "no-cache",
                    },
                ) as resp:
                    if resp.status != 200:
                        raise APIError(f"HTTP {resp.status}")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.content.iter_chunked(65536):
                        total += len(chunk)
                        if total > 1_000_000:
                            raise APIError("API response is too large")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                payload = json.loads(body)
                snap = parse_payload(payload, datetime.now(UTC))
                self.last_snapshot = snap
                self.last_error = None
                return snap
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError,
                    APIError, UnicodeDecodeError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("API fetch failed: %s", self.last_error)
                raise APIError("Сервис мониторинга временно недоступен") from exc
