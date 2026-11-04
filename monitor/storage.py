from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .logic import Transition


@dataclass(frozen=True, slots=True)
class EventRow:
    name: str
    kind: str
    at: str
    downtime_seconds: int | None


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('lost','restored')),
                at TEXT NOT NULL,
                downtime_seconds INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_events_at ON events(id DESC);
        """)
        await self.db.commit()

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None

    def _connection(self) -> aiosqlite.Connection:
        if self.db is None:
            raise RuntimeError("database is not open")
        return self.db

    async def subscribe(self, chat_id: int) -> None:
        async with self._lock:
            db = self._connection()
            await db.execute("INSERT OR IGNORE INTO subscribers(chat_id, created_at) VALUES(?, ?)",
                             (chat_id, datetime.now(timezone.utc).isoformat()))
            await db.commit()

    async def unsubscribe(self, chat_id: int) -> None:
        async with self._lock:
            db = self._connection()
            await db.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))
            await db.commit()

    async def subscribed(self, chat_id: int) -> bool:
        async with self._lock:
            cur = await self._connection().execute("SELECT 1 FROM subscribers WHERE chat_id=?", (chat_id,))
            row = await cur.fetchone()
            await cur.close()
            return row is not None

    async def all_subscribers(self) -> list[int]:
        async with self._lock:
            cur = await self._connection().execute("SELECT chat_id FROM subscribers")
            rows = await cur.fetchall()
            await cur.close()
            return [row[0] for row in rows]

    async def count_subscribers(self) -> int:
        async with self._lock:
            cur = await self._connection().execute("SELECT COUNT(*) FROM subscribers")
            result = await cur.fetchone()
            await cur.close()
            return int(result[0]) if result else 0

    async def save_event(self, event: Transition) -> None:
        async with self._lock:
            db = self._connection()
            await db.execute(
                "INSERT INTO events(server_id,name,kind,at,downtime_seconds) VALUES(?,?,?,?,?)",
                (event.server_id, event.name, event.kind, event.detected_at.isoformat(), event.downtime_seconds),
            )
            await db.commit()

    async def recent_events(self, limit: int = 8) -> list[EventRow]:
        limit = min(max(limit, 1), 50)
        async with self._lock:
            cur = await self._connection().execute(
                "SELECT name,kind,at,downtime_seconds FROM events ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = await cur.fetchall()
            await cur.close()
            return [EventRow(*row) for row in rows]
