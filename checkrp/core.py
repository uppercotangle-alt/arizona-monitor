"""Config-independent monitoring models and time rules. No Telegram dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_URL = "https://online.g-rp.me/api/v1/projects/arizona-rp"
TZ = ZoneInfo("Europe/Moscow")
UTC = timezone.utc
SERVERS = {25: "Yuma", 58: "Vice City"}
SERVER_ICONS = {25: "🌵", 58: "🌴"}
SERVER_LIMITS = {25: 980, 58: 735}
ARCHIVED_SERVERS = {52: "Show Low"}
MAX_DATA_AGE = 180
SLOT_LIMIT = 980


def dt_parse(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("updated_at must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("updated_at without timezone")
    return parsed.astimezone(UTC)


def dt_str(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Sample:
    server_id: int
    name: str
    online: int
    max_slots: int
    responding: bool
    source_at: datetime
    peak_hour: int = 0
    peak_day: int = 0

    def fresh(self, now: datetime, max_age: int = MAX_DATA_AGE) -> bool:
        age = (now.astimezone(UTC) - self.source_at).total_seconds()
        return -60 <= age <= max_age


@dataclass(frozen=True)
class Snapshot:
    servers: dict[int, Sample]
    fetched_at: datetime
    source_problem: bool
    total_servers: int


def parse_api(data: object, now: datetime) -> Snapshot:
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("API: missing servers list")
    raw = data["servers"]
    if len(raw) < 2:
        raise ValueError("API: incomplete server list")
    by_id: dict[int, Sample] = {}
    unavailable = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("responding") is False:
            unavailable += 1
        sid = item.get("id")
        if type(sid) is not int or sid not in SERVERS:
            continue
        required = ("online", "max_slots", "responding", "updated_at")
        if any(field not in item for field in required):
            raise ValueError(f"API: incomplete server {sid}")
        if not isinstance(item["responding"], bool):
            raise ValueError("API: invalid responding")
        online, max_slots = item["online"], item["max_slots"]
        if type(online) is not int or type(max_slots) is not int:
            raise ValueError("API: invalid player count")
        if not (0 <= online <= 100000 and 1 <= max_slots <= 100000):
            raise ValueError("API: nonsensical counts")
        by_id[sid] = Sample(
            server_id=sid, name=SERVERS[sid], online=online,
            max_slots=max_slots, responding=item["responding"],
            source_at=dt_parse(item["updated_at"]),
            peak_hour=nonnegative_int(item.get("peak_hour")),
            peak_day=nonnegative_int(item.get("peak_day")),
        )
    if set(by_id) != set(SERVERS):
        raise ValueError("API: Yuma or Vice City not found")
    # When roughly half of the *whole* project is down simultaneously, treat
    # the feed as suspicious rather than broadcasting false restart alerts.
    source_problem = len(raw) >= 10 and unavailable / len(raw) >= 0.45
    return Snapshot(by_id, now.astimezone(UTC), source_problem, len(raw))


def nonnegative_int(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def next_payday(now: datetime) -> datetime:
    local = now.astimezone(TZ)
    base = local.replace(second=0, microsecond=0)
    if local.minute < 30:
        target = base.replace(minute=30)
    else:
        target = (base + timedelta(hours=1)).replace(minute=0)
    # An exact payday moment looks forward to the following payday.
    if target <= local:
        target += timedelta(minutes=30)
    return target


def payday_reminder(now: datetime) -> tuple[str, datetime] | None:
    local = now.astimezone(TZ)
    if local.minute not in (20, 50):
        return None
    payday = (local.replace(second=0, microsecond=0) + timedelta(minutes=10))
    return payday.strftime("%Y-%m-%d %H:%M"), payday


def daily_window(now: datetime) -> tuple[datetime, datetime] | None:
    local = now.astimezone(TZ)
    if local.hour != 21 or local.minute >= 15:
        return None
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC), now.astimezone(UTC)


def slot_cross(previous_below: bool | None, current_online: int, threshold: int = SLOT_LIMIT) -> tuple[bool, bool]:
    current_below = current_online < threshold
    # If the subscription was just enabled and there is room already, tell the
    # user once. Afterwards notify only on transitions from >= threshold.
    return (current_below and previous_below is not True), current_below
