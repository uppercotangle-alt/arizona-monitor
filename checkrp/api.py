"""One shared HTTP client: never infers fresh source data from fresh HTTP response."""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime
import aiohttp
from .core import API_URL, UTC, Snapshot, parse_api

log = logging.getLogger(__name__)


class APIError(Exception):
    pass


class APIClient:
    def __init__(self, session: aiohttp.ClientSession, min_interval: int = 12):
        self.session = session
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_attempt = 0.0
        self.latest: Snapshot | None = None
        self.last_error: str | None = None

    async def fetch(self) -> Snapshot:
        async with self._lock:
            clock = time.monotonic()
            if clock - self._last_attempt < self.min_interval:
                if self.latest is not None:
                    return self.latest
                raise APIError(self.last_error or "API temporarily unavailable")
            self._last_attempt = clock
            try:
                async with self.session.get(
                    API_URL,
                    params={"_": str(int(time.time() * 1000))},
                    headers={"Cache-Control": "no-cache", "Accept": "application/json"},
                ) as response:
                    response.raise_for_status()
                    data = await response.json(content_type=None)
                snapshot = parse_api(data, datetime.now(UTC))
                self.latest = snapshot
                self.last_error = None
                return snapshot
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:140]}"
                log.warning("Monitoring API error: %s", self.last_error)
                raise APIError("Мониторинг временно недоступен") from exc
