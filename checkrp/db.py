"""SQLite history, preferences and 2-source-update state confirmation.

One process / one connection: transactional synchronous operations are short,
not blocking for a meaningful period at our scale. Persistent disk strongly
recommended on ephemeral container hosts.
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from .core import MAX_DATA_AGE, SERVERS, SLOT_LIMIT, UTC, Sample, Snapshot, dt_parse, dt_str, slot_cross


@dataclass(frozen=True)
class Event:
    server_id: int
    kind: str  # down|up
    at: datetime
    since: datetime | None
    duration_seconds: int | None


@dataclass(frozen=True)
class Preference:
    chat_id: int
    enabled: bool
    status_25: bool
    status_58: bool
    slots_25: bool
    slots_58: bool
    payday: bool
    daily: bool
    threshold_25: int
    threshold_58: int

    def allow_status(self, sid: int) -> bool:
        return self.enabled and sid in SERVERS and (self.status_25 if sid == 25 else self.status_58)

    def allow_slots(self, sid: int) -> bool:
        return self.enabled and sid in SERVERS and (self.slots_25 if sid == 25 else self.slots_58)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS prefs (
  chat_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
  status_25 INTEGER NOT NULL DEFAULT 1, status_52 INTEGER NOT NULL DEFAULT 1,
  slots_25 INTEGER NOT NULL DEFAULT 0, slots_52 INTEGER NOT NULL DEFAULT 0,
  payday INTEGER NOT NULL DEFAULT 0, daily INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS samples (
  server_id INTEGER NOT NULL, source_at TEXT NOT NULL, fetched_at TEXT NOT NULL,
  online INTEGER NOT NULL, max_slots INTEGER NOT NULL, responding INTEGER NOT NULL,
  PRIMARY KEY (server_id, source_at)
);
CREATE INDEX IF NOT EXISTS sample_search ON samples(server_id, source_at);
CREATE TABLE IF NOT EXISTS states (
  server_id INTEGER PRIMARY KEY, last_source TEXT NOT NULL,
  stable INTEGER NOT NULL, pending INTEGER, pending_count INTEGER NOT NULL DEFAULT 0,
  pending_since TEXT, outage_since TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL,
  kind TEXT NOT NULL, happened_at TEXT NOT NULL,
  since_at TEXT, duration_seconds INTEGER,
  UNIQUE (server_id, kind, happened_at)
);
CREATE TABLE IF NOT EXISTS slot_state (
  chat_id INTEGER NOT NULL, server_id INTEGER NOT NULL,
  below INTEGER NOT NULL, PRIMARY KEY(chat_id, server_id)
);
CREATE TABLE IF NOT EXISTS sent_jobs (
  chat_id INTEGER NOT NULL, kind TEXT NOT NULL, bucket TEXT NOT NULL,
  sent_at TEXT NOT NULL, PRIMARY KEY(chat_id, kind, bucket)
);
"""
ALLOWED_PREFS = {"status_25", "status_58", "slots_25", "slots_58", "payday", "daily"}
ALLOWED_THRESHOLDS = {25: (950, 980, 990), 58: (700, 735, 740)}


class DB:
    def __init__(self, filename: str):
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Safe in-place v3 -> v4 migration. Never overwrite old Show Low data.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(prefs)")}
        for column, kind, default in (
            ("status_58", "INTEGER", 0),  # Explicit opt-in for existing subscribers.
            ("slots_58", "INTEGER", 0),
            ("threshold_25", "INTEGER", 980),
            ("threshold_58", "INTEGER", 735),
        ):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE prefs ADD COLUMN {column} {kind} NOT NULL DEFAULT {default}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _pref(self, row: sqlite3.Row) -> Preference:
        return Preference(int(row["chat_id"]), bool(row["enabled"]),
                          bool(row["status_25"]), bool(row["status_58"]),
                          bool(row["slots_25"]), bool(row["slots_58"]),
                          bool(row["payday"]), bool(row["daily"]),
                          int(row["threshold_25"]), int(row["threshold_58"]))

    def preference(self, chat: int) -> Preference:
        self.conn.execute("INSERT OR IGNORE INTO prefs(chat_id) VALUES (?)", (chat,))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM prefs WHERE chat_id=?", (chat,)).fetchone()
        return self._pref(row)

    def set_enabled(self, chat: int, value: bool) -> Preference:
        self.preference(chat)
        self.conn.execute("UPDATE prefs SET enabled=? WHERE chat_id=?", (int(value), chat))
        if not value:
            self.conn.execute("DELETE FROM slot_state WHERE chat_id=?", (chat,))
        self.conn.commit()
        return self.preference(chat)

    def toggle(self, chat: int, field: str) -> Preference:
        if field not in ALLOWED_PREFS:
            raise ValueError("Unknown setting")
        self.preference(chat)
        self.conn.execute(f"UPDATE prefs SET {field}=1-{field} WHERE chat_id=?", (chat,))
        if field.startswith("slots_"):
            self.conn.execute("DELETE FROM slot_state WHERE chat_id=? AND server_id=?", (chat, int(field[-2:])))
        self.conn.commit()
        return self.preference(chat)

    def set_threshold(self, chat: int, sid: int, threshold: int) -> Preference:
        if sid not in ALLOWED_THRESHOLDS or threshold not in ALLOWED_THRESHOLDS[sid]:
            raise ValueError("Unknown threshold")
        self.preference(chat)
        with self.conn:
            self.conn.execute(f"UPDATE prefs SET threshold_{sid}=? WHERE chat_id=?", (threshold, chat))
            self.conn.execute("DELETE FROM slot_state WHERE chat_id=? AND server_id=?", (chat, sid))
        return self.preference(chat)

    def subscribers(self) -> list[Preference]:
        rows = self.conn.execute("SELECT * FROM prefs WHERE enabled=1").fetchall()
        return [self._pref(row) for row in rows]

    def disable(self, chat: int) -> None:
        self.conn.execute("UPDATE prefs SET enabled=0 WHERE chat_id=?", (chat,))
        self.conn.commit()

    def last_10min_delta(self, sid: int, latest: Sample) -> tuple[int, int] | None:
        return self.delta(sid, latest, 10)

    def delta(self, sid: int, latest: Sample, minutes: int) -> tuple[int, int] | None:
        # Require a reading minutes to minutes+5 before the latest sample.
        if not latest.responding:
            return None
        target = latest.source_at - timedelta(minutes=minutes)
        old = self.conn.execute(
            "SELECT online, source_at FROM samples WHERE server_id=? AND source_at<=? "
            "AND source_at>=? AND responding=1 ORDER BY source_at DESC LIMIT 1",
            (sid, dt_str(target), dt_str(target - timedelta(minutes=5)))
        ).fetchone()
        if not old:
            return None
        return latest.online - int(old["online"]), latest.online

    def collect(self, snap: Snapshot, max_age: int = MAX_DATA_AGE) -> tuple[list[Event], list[Sample]]:
        """Atomic ingest of unique timestamps, no alerts on API errors or stale feed."""
        if snap.source_problem:
            return [], []
        events: list[Event] = []
        new_samples: list[Sample] = []
        with self.conn:
            for sid, sample in snap.servers.items():
                if not sample.fresh(snap.fetched_at, max_age):
                    continue
                source = dt_str(sample.source_at)
                state = self.conn.execute("SELECT * FROM states WHERE server_id=?", (sid,)).fetchone()
                # A source may serve an older snapshot after a newer one. It must
                # not move history, the restart detector, or slot alerts backward.
                if state is not None and source <= state["last_source"]:
                    continue
                inserted = self.conn.execute(
                    "INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?,?)",
                    (sid, source, dt_str(snap.fetched_at), sample.online,
                     sample.max_slots, int(sample.responding))
                )
                if not inserted.rowcount:
                    continue
                new_samples.append(sample)
                if state is None:
                    self.conn.execute("INSERT INTO states(server_id,last_source,stable) VALUES (?,?,?)",
                                      (sid, source, int(sample.responding)))
                    continue
                stable = bool(state["stable"])
                if sample.responding == stable:
                    self.conn.execute(
                        "UPDATE states SET last_source=?,pending=NULL,pending_count=0,pending_since=NULL "
                        "WHERE server_id=?", (source, sid))
                    continue
                same_candidate = state["pending"] is not None and bool(state["pending"]) == sample.responding
                streak = int(state["pending_count"]) + 1 if same_candidate else 1
                first = state["pending_since"] if same_candidate else source
                if streak < 2:
                    self.conn.execute(
                        "UPDATE states SET last_source=?,pending=?,pending_count=?,pending_since=? WHERE server_id=?",
                        (source, int(sample.responding), streak, first, sid))
                    continue
                if sample.responding:
                    out_since = dt_parse(state["outage_since"]) if state["outage_since"] else None
                    duration = max(0, int((sample.source_at - out_since).total_seconds())) if out_since else None
                    event = Event(sid, "up", sample.source_at, out_since, duration)
                    out_since_str = None
                else:
                    event = Event(sid, "down", sample.source_at, dt_parse(first), None)
                    out_since_str = first
                self.conn.execute(
                    "UPDATE states SET last_source=?, stable=?,pending=NULL,pending_count=0,pending_since=NULL,"
                    "outage_since=? WHERE server_id=?",
                    (source, int(sample.responding), out_since_str, sid))
                self.conn.execute(
                    "INSERT OR IGNORE INTO events(server_id,kind,happened_at,since_at,duration_seconds) "
                    "VALUES (?,?,?,?,?)",
                    (sid, event.kind, source, dt_str(event.since) if event.since else None, event.duration_seconds))
                events.append(event)
        return events, new_samples

    def slot_alert_chats(self, sample: Sample, threshold: int | None = None) -> list[tuple[int, int]]:
        if not sample.responding:
            return []
        if sample.server_id not in SERVERS:
            return []
        result: list[tuple[int, int]] = []
        column = f"slots_{sample.server_id}"
        limit_column = f"threshold_{sample.server_id}"
        with self.conn:
            rows = self.conn.execute(f"SELECT chat_id, {limit_column} AS threshold FROM prefs WHERE enabled=1 AND {column}=1").fetchall()
            for row in rows:
                chat = int(row[0])
                prev = self.conn.execute(
                    "SELECT below FROM slot_state WHERE chat_id=? AND server_id=?",
                    (chat, sample.server_id)).fetchone()
                chosen = int(row["threshold"]) if threshold is None else threshold
                fire, below = slot_cross(None if prev is None else bool(prev[0]), sample.online, chosen)
                self.conn.execute(
                    "INSERT INTO slot_state(chat_id,server_id,below) VALUES (?,?,?) "
                    "ON CONFLICT(chat_id,server_id) DO UPDATE SET below=excluded.below",
                    (chat, sample.server_id, int(below)))
                if fire:
                    result.append((chat, chosen))
        return result

    def recent_events(self, limit: int = 12, archive: bool = False) -> list[Event]:
        # Show Low ID 52 is retained for read-only archive. Not monitored by v4.
        if archive:
            rows = self.conn.execute("SELECT * FROM events WHERE server_id=52 ORDER BY happened_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM events WHERE server_id IN (25,58) ORDER BY happened_at DESC LIMIT ?", (limit,)).fetchall()
        return [Event(int(row["server_id"]), row["kind"], dt_parse(row["happened_at"]),
                      dt_parse(row["since_at"]) if row["since_at"] else None,
                      row["duration_seconds"]) for row in rows]

    def points(self, sid: int, since: datetime) -> list[tuple[datetime, int, bool]]:
        rows = self.conn.execute(
            "SELECT source_at,online,responding FROM samples WHERE server_id=? AND source_at>=? "
            "ORDER BY source_at", (sid, dt_str(since))
        ).fetchall()
        return [(dt_parse(row["source_at"]), int(row["online"]), bool(row["responding"])) for row in rows]

    def stats(self, sid: int, since: datetime, until: datetime | None = None) -> dict[str, int | float | None]:
        if until is None:
            until = datetime.now(UTC)
        stat = self.conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN responding=1 THEN 1 ELSE 0 END) AS answered,"
            "AVG(CASE WHEN responding=1 THEN online END) AS avg_online,"
            "MAX(CASE WHEN responding=1 THEN online END) AS peak,"
            "MIN(CASE WHEN responding=1 THEN online END) AS minimum "
            "FROM samples WHERE server_id=? AND source_at>=? AND source_at<=?",
            (sid, dt_str(since), dt_str(until))
        ).fetchone()
        downs = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE server_id=? AND kind='down' AND happened_at>=? AND happened_at<=?",
            (sid, dt_str(since), dt_str(until))
        ).fetchone()[0]
        # Duration of confirmed outages, clipped to the requested reporting window.
        completed = self.conn.execute(
            "SELECT since_at,happened_at FROM events WHERE server_id=? AND kind='up' "
            "AND since_at IS NOT NULL AND since_at<? AND happened_at>?",
            (sid, dt_str(until), dt_str(since))
        ).fetchall()
        intervals = [(dt_parse(row["since_at"]), dt_parse(row["happened_at"])) for row in completed]
        ongoing = self.conn.execute("SELECT stable,outage_since,last_source FROM states WHERE server_id=?", (sid,)).fetchone()
        if ongoing and not bool(ongoing["stable"]) and ongoing["outage_since"]:
            # Never imply that downtime continued after the last actual API
            # observation when the source stopped updating altogether.
            intervals.append((dt_parse(ongoing["outage_since"]), min(until, dt_parse(ongoing["last_source"]))))
        durations = [int((min(until, b) - max(since, a)).total_seconds()) for a, b in intervals
                     if max(since, a) < min(until, b)]
        result = dict(stat)
        result["downs"] = int(downs)
        result["downtime_seconds"] = sum(durations)
        result["longest_downtime_seconds"] = max(durations, default=0)
        return result

    def was_sent(self, chat: int, kind: str, bucket: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sent_jobs WHERE chat_id=? AND kind=? AND bucket=?",
            (chat, kind, bucket)).fetchone() is not None

    def mark_sent(self, chat: int, kind: str, bucket: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO sent_jobs VALUES (?,?,?,?)",
                (chat, kind, bucket, dt_str(datetime.now(UTC))))

    def prune(self, now: datetime) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM samples WHERE source_at<?", (dt_str(now - timedelta(days=15)),))
            self.conn.execute("DELETE FROM sent_jobs WHERE sent_at<?", (dt_str(now - timedelta(days=30)),))
